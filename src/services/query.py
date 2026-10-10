"""对话入口：意图与会话 → Agent → 工具 → 保存回答。"""

import logging
import re
from datetime import date
from dataclasses import replace

from src.exceptions import BadRequestException
from src.models import AnswerData, Citation, IntentData
from src.services.agent import run_agent, run_agent_stream
from src.services.agent.tasks import plan_task
from src.services.agent import study_goals
from src.services.evidence_metadata import evidence_reason
from src.services.intent import IntentDecision, resolve_intent
from src.services.llm import OpenAIClient
from src.services.storage.conversation_store import ConversationStore
from src.services.storage.vector_store import ChromaVectorStore

logger = logging.getLogger(__name__)

SUPPORTED_MODES = frozenset({"auto", "qa", "concept", "chapter"})



def _validate(mode: str, course_id: str) -> None:
    if mode not in SUPPORTED_MODES:
        raise BadRequestException(f"仅支持 mode=auto|qa|concept|chapter，收到 mode={mode}")
    if not course_id or not course_id.strip():
        raise BadRequestException("course_id 不能为空")


def _validate_as_of(as_of: str | None) -> None:
    if not as_of:
        return
    try:
        date.fromisoformat(as_of)
    except ValueError as exc:
        raise BadRequestException("as_of 必须是有效日期，格式 YYYY-MM-DD") from exc


def _previous_intent(store: ConversationStore | None, conv_id: str | None) -> dict | None:
    if not store or not conv_id:
        return None
    try:
        return store.get_latest_user_intent(conv_id)
    except Exception:
        logger.warning("读取会话意图失败 conv=%s", conv_id, exc_info=True)
        return None


def _intent_data(intent: IntentDecision) -> IntentData:
    return IntentData(**intent.to_dict())


def _citations(
    raw: list[dict], *, scenario: str | None = None, as_of: str | None = None
) -> list[Citation]:
    return [Citation(**{
        **citation,
        "doc_id": str(citation.get("doc_id") or ""),
        "authority_level": int(citation.get("authority_level") or 30),
        "parse_quality": float(citation.get("parse_quality") or 0.0),
        "selection_reason": evidence_reason(citation, scenario=scenario, as_of=as_of),
    }) for citation in raw]


def _prepare_query(
    question: str, mode: str, llm: OpenAIClient, course_id: str,
    store: ConversationStore | None, conv_id: str | None,
    scenario: str | None, as_of: str | None, scope_override: bool,
) -> tuple[IntentDecision, list[dict] | None]:
    _validate(mode, course_id)
    _validate_as_of(as_of)
    if store and conv_id:
        conversation = store.get_conversation(conv_id)
        if isinstance(conversation, dict) and conversation.get("course_id") != course_id:
            raise BadRequestException("对话不属于当前课程")
    history = _get_history(store, conv_id)
    intent = resolve_intent(
        question, requested_mode=mode, scenario=scenario, as_of=as_of,
        previous=_previous_intent(store, conv_id), llm=None, scope_override=scope_override,
    )
    _validate_as_of(intent.as_of)
    logger.info("查询: course=%s intent=%s layer=%s mode=%s scenario=%s as_of=%s conv=%s q='%s...'",
                course_id, intent.task, intent.layer, intent.mode, intent.scenario or "all",
                intent.as_of or "latest", conv_id, question[:60])
    return intent, history


def _finish_answer(
    result: dict, intent: IntentDecision, store: ConversationStore | None,
    conv_id: str | None, course_id: str, question: str,
) -> AnswerData:
    if result.get("response_kind") == "conversation" or (result.get("response_kind") == "clarification" and not result.get("task")):
        intent = replace(intent, task="conversation", mode="qa", layer="llm_fallback" if result.get("agentic") else "rule",
                         rationale="Agent 选择交流或需求澄清，无课程结论")
    elif result.get("task"):
        intent = replace(intent, task="question_generate" if result["task"]["kind"] != "study_goal" else "study_goal",
                         rationale="Agent 执行受证据约束的课程任务")
    execution = result.get('execution')
    data = AnswerData(
        answer=result["answer"],
        citations=_citations(result["citations"], scenario=intent.scenario, as_of=intent.as_of),
        grounded=result["grounded"], intent=_intent_data(intent),
        steps=result.get("steps", []), tool_calls=result.get("tool_calls", []),
        agentic=result.get("agentic", False), agent_used=result.get("agent_used", False),
        response_kind=result.get("response_kind", "answer"), task=result.get("task"),
        artifacts=result.get("artifacts") or [],
        execution=execution,
    )
    if execution:
        if execution['status'] == 'ready':
            from src.services.storage.agent_run_store import AgentRunStore
            data = AnswerData(**AgentRunStore(store).publish(execution['id'],course_id,data.model_dump()))
    else:
        _save_turn(store,conv_id,course_id,question,result['answer'],result['citations'],result['grounded'],intent.mode,intent=intent.to_dict())
    return data


def ask(
    question: str,
    mode: str,
    vs: ChromaVectorStore,
    llm: OpenAIClient,
    course_id: str,
    conversation_store: ConversationStore | None = None,
    conversation_id: str | None = None,
    scenario: str | None = None,
    as_of: str | None = None,
    scope_override: bool = False,
    question_bank_store=None,
    *, agentic=True, max_steps=None, top_k=None, score_threshold=None, breakpoints=None,
) -> AnswerData:
    intent, history = _prepare_query(
        question, mode, llm, course_id, conversation_store, conversation_id, scenario, as_of, scope_override,
    )
    options = _prepare_execution(question,intent,conversation_store,conversation_id,course_id,scope_override,
                                 question_bank_store,agentic,llm,breakpoints,max_steps,top_k,score_threshold)
    conversation_id = options.get('execution_request',{}).get('conversation_id',conversation_id)
    result = run_agent(question, course_id, vs, llm, mode=intent.mode,
                       scenario=intent.scenario, as_of=intent.as_of, history=history, **options)
    return _finish_answer(result, intent, conversation_store, conversation_id, course_id, question)


def ask_stream(
    question: str,
    mode: str,
    vs: ChromaVectorStore,
    llm: OpenAIClient,
    course_id: str,
    conversation_store: ConversationStore | None = None,
    conversation_id: str | None = None,
    scenario: str | None = None,
    as_of: str | None = None,
    scope_override: bool = False,
    question_bank_store=None,
    breakpoints=None,
):
    intent, history = _prepare_query(
        question, mode, llm, course_id, conversation_store, conversation_id, scenario, as_of, scope_override,
    )
    options = _prepare_execution(question,intent,conversation_store,conversation_id,course_id,scope_override,
                                 question_bank_store,True,llm,breakpoints)
    conversation_id = options.get('execution_request',{}).get('conversation_id',conversation_id)
    for event in run_agent_stream(question, course_id, vs, llm, mode=intent.mode,
                                  scenario=intent.scenario, as_of=intent.as_of, history=history, **options):
        if event["type"] == "done":
            yield {"type": "done", "data": _finish_answer(
                event["data"], intent, conversation_store, conversation_id, course_id, question,
            ).model_dump()}
        else:
            if event["type"] == "phase":
                event = {**event, "intent": intent.to_dict()}
            yield event


def _durable_options(store, conv_id, course_id, question, intent, breakpoints, agentic, llm):
    if not isinstance(store,ConversationStore) or not agentic or not llm.configured:
        if breakpoints:
            raise BadRequestException('节点断点需要持久化对话和支持工具决策的模型')
        return {}
    from src.services.storage.agent_run_store import AgentRunStore
    from src.services.agent.execution import validate_breakpoints
    if not question.strip():
        raise BadRequestException('问题内容不能为空')
    points = validate_breakpoints(breakpoints or [])
    conv_id = conv_id or store.create_conversation(course_id,question[:40])['id']
    model = getattr(llm,'model',None)
    request = {'conversation_id':conv_id,'question':question,'course_id':course_id,'mode':intent.mode,
               'scenario':intent.scenario,'as_of':intent.as_of,'intent':intent.to_dict(),'model':model if isinstance(model,str) else None}
    runs = AgentRunStore(store)
    # Reserve the conversation BEFORE any business task checkpoint can be changed.
    return {'execution_store':runs,'execution_id':runs.create(conv_id,course_id,request,points),
            'breakpoints':points,'execution_request':request}


def _prepare_execution(question,intent,store,conv_id,course_id,scope_override,bank,agentic,llm,breakpoints,
                       max_steps=None,top_k=None,score_threshold=None):
    if max_steps is not None and max_steps <= 0:
        raise BadRequestException('max_steps 必须大于 0')
    durable = _durable_options(store,conv_id,course_id,question,intent,breakpoints,agentic,llm)
    conv_id = durable.get('execution_request',{}).get('conversation_id',conv_id)
    try:
        return _execution_options(question,intent,store,conv_id,course_id,scope_override,bank,agentic,
                                  max_steps,top_k,score_threshold,reservation=durable.get('execution_id')) | durable
    except Exception:
        if durable:
            durable['execution_store'].control(durable['execution_id'],course_id,'cancel')
        raise


def resume_execution(run_id, course_id, mode, store, vs, llm, bank):
    from src.services.storage.agent_run_store import AgentRunStore, conflict
    from src.services.intent import IntentDecision
    runs = AgentRunStore(store)
    record = runs.read(run_id,course_id)
    if record['published']:
        return record['result']
    request = record['request']
    if record['status'] == 'ready':
        result = record['result'] | {'execution':runs.public(run_id,course_id)}
    else:
        if not llm.configured:
            raise conflict('恢复原图需要支持工具决策的模型')
        if request.get('model') and request['model'] != getattr(llm,'model',None):
            raise conflict('执行使用的模型已变更，请恢复原模型或取消后重新提问')
        result = run_agent(request['question'],course_id,vs,llm,mode=request['mode'],
            scenario=request['scenario'],as_of=request['as_of'],execution_store=runs,execution_resume=run_id,execution_mode=mode,
            question_bank_store=bank,checkpoint=lambda value:store.checkpoint_agent_task(request['conversation_id'],course_id,value),
            start_task=lambda value:store.save_agent_task(request['conversation_id'],course_id,value))
    return _finish_answer(result,IntentDecision(**request['intent']),store,request['conversation_id'],course_id,request['question']).model_dump()


def _execution_options(question, intent, store, conv_id, course_id, scope_override, bank,
                       agentic=True, max_steps=None, top_k=None, score_threshold=None, reservation=None):
    if agentic and isinstance(store,ConversationStore) and conv_id:
        from src.services.storage.agent_run_store import AgentRunStore, conflict
        runs = AgentRunStore(store)
        existing = runs.list_public(conv_id,course_id)
        if any(run['id'] != reservation and run['status'] in ('running','paused','interrupted','failed','ready') for run in existing):
            raise conflict('当前对话仍有未结束的执行，请继续或取消后再提问')
        for run in existing:
            if run['status'] == 'cancelled' and run['control'] == 'cancel':
                runs.cancelled_task(run['id'],course_id)
    task, checkpoint = _prepare_task(question, intent, store, conv_id, course_id, scope_override) if agentic else (None, None)
    previous = store.get_agent_task(conv_id, course_id) if store and conv_id and agentic else None
    previous = previous if isinstance(previous, dict) else None
    if store and conv_id:
        def start_task(value):
            store.save_agent_task(conv_id, course_id, value)
        if checkpoint is None:
            checkpoint = lambda value: store.checkpoint_agent_task(conv_id, course_id, value)
    else:
        start_task = None
    return {"task": task, "checkpoint": checkpoint, "start_task": start_task,
            "previous_task": previous, "scope_override": scope_override, "question_bank_store": bank, "agentic": agentic,
            "max_steps": max_steps if max_steps is not None else (6 if agentic else 3),
            "top_k": top_k, "score_threshold": score_threshold}


def _prepare_task(question, intent, store, conv_id, course_id, scope_override=False):
    if intent.task == "conversation":
        return None, None
    previous = store.get_agent_task(conv_id, course_id) if store and conv_id else None
    previous = previous if isinstance(previous, dict) else None
    inheriting = bool(previous and (previous.get("status") == "waiting_input" or study_goals.is_followup(question, previous) or
                                  re.match(r"^\s*(?:请)?(?:继续|重试|恢复|保存|取消|停止)", question)))
    scenario = intent.scenario if scope_override or intent.scenario is not None or not inheriting else previous.get("scenario")
    as_of = intent.as_of if scope_override or intent.as_of is not None or not inheriting else previous.get("as_of")
    task = study_goals.prepare(question, previous, course_id=course_id, scenario=scenario, as_of=as_of)
    if task is None:
        task = plan_task(question, previous if not previous or previous.get("kind") != "study_goal" else None, requested=intent.task == "question_generate" and intent.layer != "context",
                     course_id=course_id, scenario=scenario, as_of=as_of)
    def save_checkpoint(value):
        return store.checkpoint_agent_task(conv_id, course_id, value)
    checkpoint = save_checkpoint if store and conv_id else None
    if task and checkpoint:
        store.save_agent_task(conv_id, course_id, task)
    return task, checkpoint


def _get_history(store: ConversationStore | None, conv_id: str | None) -> list[dict] | None:
    """获取对话历史消息，用于拼入 LLM 上下文。"""
    if not store or not conv_id:
        return None
    return store.get_history(conv_id) or None


def _save_turn(
    store: ConversationStore | None,
    conv_id: str | None,
    course_id: str,
    question: str,
    answer: str,
    citations: list,
    grounded: bool,
    mode: str,
    intent: dict | None = None,
) -> bool:
    """保存一轮问答到对话存储，自动创建不存在的对话。

    失败不阻断回答流程，但会记录 ERROR 日志；返回是否保存成功，便于调用方观测。
    """
    if not store or not conv_id:
        return True
    try:
        first_q = question.strip()
        title = first_q[:40] + ("..." if len(first_q) > 40 else "")
        store.ensure_conversation(conv_id, course_id, title)
        store.append_message(conv_id, "user", question, mode=mode, intent=intent)
        store.append_message(conv_id, "assistant", answer, citations=citations, grounded=grounded, mode=mode)
        return True
    except Exception:
        logger.exception(
            "保存对话消息失败 conv=%s course=%s mode=%s q='%s...'",
            conv_id,
            course_id,
            mode,
            question.strip()[:40],
        )
        return False
