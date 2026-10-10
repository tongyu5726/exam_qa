"""默认 Agent ↔ tool 决策循环；固定检索图仅供显式兼容调用。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from functools import partial
from datetime import date

from src.config import config
from src.exceptions import BadRequestException, LLMAPIException, LLMTimeoutException, ServiceUnavailableException
from src.services.agent import nodes
from src.services.agent import tasks
from src.services.agent.state import AgentState
from src.services.dialogue import classify_dialogue, dialogue_result
from src.services.evidence_metadata import normalize_scope
from src.services.llm import OpenAIClient
from src.services.storage.vector_store import ChromaVectorStore

logger = logging.getLogger(__name__)

_DEFAULT_MAX_STEPS = 3
_SUPPORTED_MODES = frozenset({"qa", "concept", "chapter"})


def _import_langgraph():
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise ServiceUnavailableException(
            "缺少 langgraph 依赖，请运行 `uv add langgraph` 后重启服务",
            detail=str(exc),
        ) from exc
    return END, StateGraph


def build_agent_graph(vs: ChromaVectorStore, llm: OpenAIClient):
    END, StateGraph = _import_langgraph()
    graph = StateGraph(AgentState)

    graph.add_node("retrieve", partial(nodes.retrieve_node, vs=vs))
    graph.add_node("grade", partial(nodes.grade_node, llm=llm))
    graph.add_node("rewrite", partial(nodes.rewrite_node, llm=llm))
    graph.add_node("generate", partial(nodes.generate_node, llm=llm))
    graph.add_node("refuse", nodes.refuse_node)

    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges(
        "grade",
        lambda state: state.get("decision") or "refuse",
        {
            "generate": "generate",
            "rewrite": "rewrite",
            "refuse": "refuse",
        },
    )
    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("generate", END)
    graph.add_edge("refuse", END)
    return graph.compile()


def build_agentic_graph(vs: ChromaVectorStore, llm: OpenAIClient, bank=None, checkpoint=None, start_task=None):
    """P2-C：模型决定工具调用，图只负责受控路由与循环上限。"""
    END, StateGraph = _import_langgraph()
    graph = StateGraph(AgentState)
    graph.add_node("agent", partial(nodes.agentic_agent_node, llm=llm))
    graph.add_node("tool", partial(nodes.agentic_tool_node, vs=vs, llm=llm, bank=bank, checkpoint=checkpoint, start_task=start_task))
    graph.add_node("refuse", nodes.refuse_node)
    graph.set_entry_point("agent")

    def route(state: AgentState) -> str:
        decision = state.get("decision") or "refuse"
        if decision == "tools":
            return "tool" if int(state.get("tool_rounds", 0)) < int(state["max_steps"]) else "refuse"
        return "finish" if decision == "finish" else "refuse"

    graph.add_conditional_edges("agent", route, {"tool": "tool", "finish": END, "refuse": "refuse"})
    graph.add_edge("tool", "agent")
    graph.add_edge("refuse", END)
    return graph.compile()


def run_agent_stream(
    question: str,
    course_id: str,
    vs: ChromaVectorStore,
    llm: OpenAIClient,
    *,
    mode: str = "qa",
    max_steps: int = _DEFAULT_MAX_STEPS,
    top_k: int | None = None,
    score_threshold: float | None = None,
    scenario: str | None = None,
    as_of: str | None = None,
    agentic: bool = True,
    history: list[dict] | None = None,
    task: dict | None = None,
    question_bank_store=None,
    checkpoint=None,
    start_task=None,
    previous_task: dict | None = None,
    scope_override: bool = False,
    execution_store=None,
    execution_request=None,
    breakpoints=None,
    execution_resume=None,
    execution_id=None,
    execution_mode='run',
) -> Iterator[dict]:
    """执行 Agent，逐步返回阶段事件与最终回答。"""
    if mode not in _SUPPORTED_MODES:
        raise BadRequestException(f"仅支持 mode=qa|concept|chapter，收到 mode={mode}")
    if max_steps <= 0:
        raise BadRequestException("max_steps 必须大于 0")

    question = (question or "").strip()
    if not question:
        raise BadRequestException("问题内容不能为空")
    if not course_id or not course_id.strip():
        raise BadRequestException("course_id 不能为空")
    if as_of:
        try:
            date.fromisoformat(as_of)
        except ValueError as exc:
            raise BadRequestException("as_of 必须是有效日期，格式 YYYY-MM-DD") from exc
    scenario = normalize_scope(scenario) if scenario else None

    dialogue = classify_dialogue(question) if agentic and not task and not llm.configured else None
    if dialogue:
        model = getattr(llm, "model", "")
        result = dialogue_result(dialogue, model if isinstance(model, str) else "")
        yield {"type": "phase", "phase": "routing"}
        yield {"type": "delta", "text": result["answer"]}
        yield {"type": "done", "data": result}
        return

    if top_k is None:
        top_k = max(config.retrieval.top_k, 12) if mode == "concept" else config.retrieval.top_k
    if score_threshold is None:
        score_threshold = config.retrieval.score_threshold

    initial: AgentState = {
        "task": task,
        "previous_task": previous_task,
        "scope_override": scope_override,
        "history": history,
        "question": question,
        "course_id": course_id,
        "mode": mode,
        "top_k": top_k,
        "score_threshold": score_threshold,
        "max_steps": max_steps,
        "attempts": 0,
        "current_query": question,
        "queries": [],
        "steps": [],
        "chunks": [],
        "citations": [],
        "answer": "",
        "grounded": False,
        "decision": "",
        "scenario": scenario,
        "as_of": as_of,
        "messages": [],
        "pending_tool_calls": [],
        "tool_calls": [],
        "tool_rounds": 0,
    }
    used_agentic = bool(agentic and llm.configured)
    execution = None
    if execution_store and used_agentic:
        from src.services.agent.execution import Execution, validate_breakpoints
        run_id = execution_resume or execution_id or execution_store.create(
            execution_request['conversation_id'],course_id,execution_request,validate_breakpoints(breakpoints or []))
        execution = Execution(execution_store,run_id,course_id,resume=bool(execution_resume),mode=execution_mode)
    yield {"type": "phase", "phase": "routing"}
    final = initial
    if task and task["status"] != "running":
        result = tasks.task_result(task,agentic=False)
        if execution:
            execution.owner = execution.store.claim(execution.run_id,course_id,'run')
            result = execution.finish(result)
        yield {"type": "task", "data": tasks.public_task(task)}
        yield {"type": "done", "data": result}
        return
    if task:
        yield {"type": "task", "data": tasks.public_task(task)}
    if used_agentic:
        yield {"type": "phase", "phase": "reasoning"}
        try:
            emitted_calls = 0
            if execution:
                from src.services.agent.execution import build_graph
                compiled = build_graph(vs,llm,question_bank_store,checkpoint,start_task,execution)
                states = execution.stream(compiled,initial)
            else:
                states = ({'type':'state','data':state} for state in build_agentic_graph(vs,llm,question_bank_store,checkpoint,start_task).stream(
                    initial,stream_mode='values',config={'recursion_limit':max_steps*2+5}))
            for event in states:
                if event['type'] != 'state':
                    yield event
                    continue
                state = event['data']
                final = state
                calls = state.get("tool_calls") or []
                for call in calls[emitted_calls:]:
                    yield {"type": "tool", "data": call, "agentic": True}
                emitted_calls = len(calls)
                if state.get("task"):
                    if checkpoint:
                        checkpoint(state["task"])
                    yield {"type": "task", "data": tasks.public_task(state["task"])}
                if state.get("pending_tool_calls"):
                    yield {"type": "phase", "phase": "retrieving" if any(c.get("name") in {"rag_answer", "search_pdf", "read_page", "quote_source", "extract_table", "analyze_chart", "retrieve_evidence"} for c in state["pending_tool_calls"]) else "working"}
                elif state.get("tool_rounds") and not state.get("answer"):
                    yield {"type": "phase", "phase": "reasoning"}
        except (LLMAPIException, LLMTimeoutException) as exc:
            if execution:
                # The failing node remains pending in the native checkpoint. No fallback can overwrite it.
                yield {'type':'execution','data':execution.public()}
                yield {'type':'done','data':execution.status_result(final) | {'answer':'执行失败，可从检查点重试。'}}
                return
            logger.warning("Agent 工具决策不可用，降级执行注册的 RAG 工具: %s", exc)
            used_agentic = False
    if execution and execution.stopped:
        yield {'type':'done','data':execution.status_result(final)}
        return
    task = final.get("task") or task
    if task and not used_agentic:
        # Failed tool decisions must never silently turn a write task into a plain RAG answer.
        final_task = dict(final.get("task") or task)
        if final_task["status"] == "running":
            final_task.update(status="failed", issues=["模型工具决策不可用，可配置支持工具的模型后重试。"])
        if checkpoint:
            final_task = checkpoint(final_task)
        yield {"type": "task", "data": tasks.public_task(final_task)}
        yield {"type": "done", "data": tasks.task_result(final_task, agentic=False) | {"tool_calls": final.get("tool_calls") or []}}
        return
    if not used_agentic and agentic and classify_dialogue(question):
        model = getattr(llm, "model", "")
        result = dialogue_result(classify_dialogue(question), model if isinstance(model, str) else "")
        result["steps"] = [*(final.get("steps") or []), "fallback: product dialogue"]
        result["tool_calls"] = final.get("tool_calls") or []
        yield {"type": "delta", "text": result["answer"]}
        yield {"type": "done", "data": result}
        return
    if not used_agentic:
        if agentic:
            # Compatibility for providers without function calling; keep the same scoped RAG capability.
            from src.services.agent.tools import rag_answer_stream
            for event in rag_answer_stream(question, vs=vs, llm=llm, course_id=course_id,
                    mode=mode, history=history, scenario=scenario, as_of=as_of,
                    top_k=top_k, score_threshold=score_threshold):
                if event["type"] != "done":
                    yield event
                    continue
                final = {**event["data"], "steps": ["fallback: rag_answer"], "tool_calls": [{
                    "name": "rag_answer", "arguments": {"query": question}, "ok": True,
                    "citation_count": len(event["data"]["citations"]), "error": "",
                }]}
        else:
            final = build_agent_graph(vs, llm).invoke(initial, config={"recursion_limit": max_steps * 4 + 5})
    result = {
        "answer": final.get("answer", ""),
        "citations": final.get("citations") or [],
        "grounded": bool(final.get("grounded", False)),
        "steps": final.get("steps") or [],
        "queries": final.get("queries") or [],
        "tool_calls": final.get("tool_calls") or [],
        "agentic": used_agentic and not final.get('used_fallback',False),
        "agent_used": True,
        "response_kind": final.get("response_kind", "answer"),
        "task": tasks.public_task(final["task"]) if final.get("task") else None,
        "artifacts": final.get("artifacts") or [],
    }
    logger.info("Agent 完成: course=%s agentic=%s grounded=%s", course_id, used_agentic, result["grounded"])
    if execution:
        result = execution.finish(result)
    if used_agentic or not agentic:
        if result["answer"]:
            yield {"type": "delta", "text": result["answer"]}
    yield {"type": "done", "data": result}


def run_agent(*args, **kwargs) -> dict:
    """同步与 SSE 共用同一 Agent 执行路径。"""
    for event in run_agent_stream(*args, **kwargs):
        if event["type"] == "done":
            return event["data"]
    raise LLMAPIException("Agent 未返回最终回答")
