"""Semantic interaction, task entry and shared API contracts."""
import json
from unittest.mock import MagicMock, patch

import pytest

from src.services.agent import run_agent, run_agent_stream
from src.services.agent.tasks import plan_task
from src.services.query import ask, ask_stream
from src.services.storage.conversation_store import ConversationStore


def call(name, **args):
    return {"content": "", "tool_calls": [{"id": name, "name": name, "arguments": json.dumps(args, ensure_ascii=False)}]}


def model(*decisions, allowed=True):
    llm = MagicMock(configured=True, model="active-model")
    llm.chat_with_tools.side_effect = decisions
    llm.chat.return_value = json.dumps({"allowed": allowed})
    return llm


@pytest.mark.parametrize("question,topic,answer", [
    ("能介绍下你自己吗？", "identity", "我是溯知，当前配置模型标识 active-model。"),
    ("你都能帮我干些什么？", "capabilities", "我可以据源讲解、出题、组卷和带你复习。"),
    ("谢谢，刚才解释得挺清楚的", "social", "不客气，随时继续交流。"),
])
@pytest.mark.parametrize("stream", [False, True])
def test_semantic_dialogue_uses_model_and_no_retrieval(question, topic, answer, stream):
    llm = model(call("respond_to_user", kind="conversation", topic=topic, answer=answer))
    vs = MagicMock()
    args = dict(question=question, mode="concept", vs=vs, llm=llm, course_id="course")
    with patch("src.services.agent.tools.rag_answer_stream") as rag:
        if stream:
            events = list(ask_stream(**args))
            result = events[-1]["data"]
            assert not any(e.get("phase") == "retrieving" for e in events)
            assert [e["data"]["name"] for e in events if e["type"] == "tool"] == ["respond_to_user"]
        else:
            result = ask(**args).model_dump()
    assert result["answer"] == answer and result["agentic"]
    assert result["response_kind"] == result["intent"]["task"] == "conversation"
    assert not result["grounded"] and not result["citations"]
    llm.chat_with_tools.assert_called_once()
    llm.chat.assert_called_once()
    assert not vs.mock_calls
    rag.assert_not_called()
    system = llm.chat_with_tools.call_args.args[0][0]["content"]
    assert "active-model" in system and "respond_to_user" in system


@pytest.mark.parametrize("allowed", [False, None])
def test_mixed_request_cannot_publish_unverified_direct_course_answer(allowed):
    llm = model(call("respond_to_user", kind="conversation", topic="identity", answer="我是溯知。卷积公式是……"),
                {"content": "卷积公式是……", "tool_calls": []}, allowed=allowed)
    out = run_agent("你是什么AI，顺便告诉我卷积公式", "course", MagicMock(), llm)
    assert out["answer"] == "资料库中未找到相关内容"
    assert not out["grounded"] and not out["citations"]
    assert not out["tool_calls"][0]["ok"]


def test_failed_retrieval_cannot_be_disguised_as_conversation():
    llm = model(call("rag_answer", query="卷积"),
                call("respond_to_user", kind="conversation", topic="social", answer="卷积是……"),
                {"content": "卷积是……", "tool_calls": []})
    with patch("src.services.agent.tools.rag_answer", return_value={"answer": "资料库中未找到相关内容", "grounded": False, "citations": []}):
        out = run_agent("卷积", "course", MagicMock(), llm)
    assert not out["grounded"] and out["response_kind"] == "answer"
    assert not out["tool_calls"][1]["ok"]
    llm.chat.assert_not_called()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("bad_answer", ["<NO_EVIDENCE>", "&lt;NO_EVIDENCE&gt;", "<SOURCES>[1]</SOURCES>", "资料库中未找到相关内容"])
def test_invalid_conversation_reply_is_rejected_and_agent_can_retry(stream, bad_answer):
    answer = "我可以检索课程资料、讲解知识点、出题、组卷和带你复习。"
    llm = model(call("respond_to_user", kind="conversation", topic="capabilities", answer=bad_answer),
                call("respond_to_user", kind="conversation", topic="capabilities", answer=answer))
    vs = MagicMock()
    if stream:
        events = list(run_agent_stream("你都能干什么", "course", vs, llm))
        out = events[-1]["data"]
        assert "".join(e["text"] for e in events if e["type"] == "delta") == answer
    else:
        out = run_agent("你都能干什么", "course", vs, llm)
    assert out["answer"] == answer and out["response_kind"] == "conversation"
    assert [c["ok"] for c in out["tool_calls"]] == [False, True]
    llm.chat.assert_called_once()
    assert not vs.mock_calls


@pytest.mark.parametrize("question,expected_kind", [
    ("你都能干什么", "conversation"),
    ("你都能干什么，顺便解释卷积", "answer"),
])
def test_invalid_direct_reply_recovers_only_pure_product_requests(question, expected_kind):
    llm = model({"content": "<NO_EVIDENCE>", "tool_calls": []})
    out = run_agent(question, "course", MagicMock(), llm)
    assert out["response_kind"] == expected_kind
    assert "<NO_EVIDENCE>" not in out["answer"]
    assert ("出题" in out["answer"]) == (expected_kind == "conversation")
    llm.chat.assert_not_called()


def test_product_fallback_at_round_limit_keeps_failed_tool_observation():
    bad = call("respond_to_user", kind="conversation", topic="capabilities", answer="<NO_EVIDENCE>")
    out = run_agent("你都能干什么", "course", MagicMock(), model(bad, bad), max_steps=1)
    assert out["response_kind"] == "conversation" and "出题" in out["answer"]
    assert len(out["tool_calls"]) == 1 and not out["tool_calls"][0]["ok"]
    assert "fallback: product dialogue" in out["steps"]


def test_mixed_request_with_failed_course_tool_cannot_use_dialogue_fallback():
    llm = model(call("rag_answer", query="你都能干什么"), {"content": "<NO_EVIDENCE>", "tool_calls": []})
    with patch("src.services.agent.tools.rag_answer", return_value={"answer": "资料库中未找到相关内容", "grounded": False, "citations": []}):
        out = run_agent("你都能干什么，解释卷积", "course", MagicMock(), llm)
    assert out["answer"] == "资料库中未找到相关内容" and out["response_kind"] == "answer"


@pytest.mark.parametrize("question", ["替我准备五个考察卷积理解程度的问题", "帮我准备五个考察卷积理解程度的问题"])
def test_semantic_task_entry_clarifies_missing_type_and_is_persisted(tmp_path, question):
    store = ConversationStore(str(tmp_path / "chat.db"))
    conv = store.create_conversation("course")["id"]
    llm = model(call("create_task", kind="question_generate", topic="卷积", count=5))
    result = ask(question, "auto", MagicMock(), llm, "course", store, conv)
    assert result.response_kind == "clarification" and result.agentic
    assert result.task["kind"] == "question_generate"
    assert result.task["spec"]["count"] == 5 and result.task["spec"]["topic"] == "卷积"
    assert not result.task["spec"]["save_requested"]
    assert "题型" in result.answer
    assert store.get_agent_task(conv, "course")["id"] == result.task["id"]
    assert len(store.get_messages(conv)) == 2
    llm.chat_with_tools.assert_called_once()


def test_semantic_task_reenters_task_tools_and_keeps_forced_scope(tmp_path):
    store = ConversationStore(str(tmp_path / "chat.db"))
    conv = store.create_conversation("course")["id"]
    llm = model(call("create_task", kind="question_generate", topic="卷积", count=5, question_type="short_answer", save_requested=True, course_id="other"),
                call("retrieve_evidence"))
    with patch("src.services.rag._retrieve", return_value=[]) as retrieve:
        result = ask("替我准备五个考察卷积理解程度的问题", "auto", MagicMock(), llm, "course", store, conv,
                     scenario="考试", as_of="2026-10-09")
    assert result.task["status"] == "no_evidence" and not result.task["spec"]["save_requested"]
    assert [c["name"] for c in result.tool_calls] == ["create_task", "retrieve_evidence"]
    assert retrieve.call_args.args[3] == "course"
    assert retrieve.call_args.kwargs == {"scenario": "考试", "as_of": "2026-10-09"}
    assert store.get_agent_task(conv, "course")["status"] == "no_evidence"
    schemas = llm.chat_with_tools.call_args_list[1].args[1]
    assert {s["function"]["name"] for s in schemas} == {"retrieve_evidence", "generate_question_drafts", "validate_question_drafts", "save_question_drafts"}
    assert any("本轮是课程任务" in m["content"] for m in llm.chat_with_tools.call_args_list[1].args[0] if m["role"] == "system")


def test_interruption_keeps_checkpoint_and_semantic_supplement_resumes_it(tmp_path):
    store = ConversationStore(str(tmp_path / "chat.db"))
    conv = store.create_conversation("course")["id"]
    task = plan_task("关于卷积出5道题", None, requested=True, course_id="course")
    store.save_agent_task(conv, "course", task)
    llm = model(call("respond_to_user", kind="conversation", topic="identity", answer="我是溯知。"))
    out = ask("能介绍下你自己吗？", "auto", MagicMock(), llm, "course", store, conv)
    assert out.response_kind == "conversation"
    assert store.get_agent_task(conv, "course") == task
    llm = model(call("create_task", action="supplement", kind="question_generate", question_type="short_answer"), call("retrieve_evidence"))
    with patch("src.services.rag._retrieve", return_value=[]):
        out = ask("用开放式回答的那种", "auto", MagicMock(), llm, "course", store, conv)
    assert out.task["id"] == task["id"] and out.task["status"] == "no_evidence"
    assert out.task["spec"]["question_type"] == "short_answer"


def test_created_task_decision_failure_never_falls_back_to_rag():
    from src.exceptions import LLMAPIException
    llm = model(call("create_task", kind="question_generate", topic="卷积", count=2, question_type="short_answer"), LLMAPIException())
    with patch("src.services.agent.tools.rag_answer_stream") as rag:
        out = run_agent("准备两道卷积问题", "course", MagicMock(), llm, max_steps=6)
    assert out["task"]["status"] == "failed"
    rag.assert_not_called()


@pytest.mark.parametrize("path", ["/ask", "/agent/run"])
@pytest.mark.parametrize("clear", [False, True])
def test_shared_api_inherits_or_clears_scope_and_publishes_turns(tmp_path, path, clear):
    from fastapi.testclient import TestClient
    from src.main import app
    from src.dependencies import get_vector_store, get_llm_client, get_catalog_store, get_conversation_store, get_question_bank_store
    store = ConversationStore(str(tmp_path / "chat.db"))
    conv = store.create_conversation("course")["id"]
    store.append_message(conv, "user", "卷积定义", intent={"task": "concept", "mode": "concept", "scenario": "考试", "as_of": "2026-10-09"})
    overrides = {get_vector_store: lambda: MagicMock(), get_llm_client: lambda: MagicMock(configured=False),
                 get_catalog_store: lambda: MagicMock(), get_conversation_store: lambda: store,
                 get_question_bank_store: lambda: MagicMock()}
    previous = dict(app.dependency_overrides)
    app.dependency_overrides.update(overrides)
    try:
        with patch("src.services.query.run_agent", return_value={"answer": "资料库中未找到相关内容", "citations": [], "grounded": False}) as runner:
            response = TestClient(app).post("/api/v1" + path, json={"question": "那这个呢", "course_id": "course", "conversation_id": conv, "scope_override": clear})
        assert response.status_code == 200
        assert runner.call_args.kwargs["mode"] == "concept"
        assert runner.call_args.kwargs["scenario"] == ("all" if clear else "考试")
        assert runner.call_args.kwargs["as_of"] == (None if clear else "2026-10-09")
        assert len(store.get_messages(conv)) == 3
        assert response.json()["data"]["intent"]
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.parametrize("human_text", ["为什么不能保存这些题目", "请解释如何保存题目", "先生成题目，不要保存", "他说“保存到题库”，替我准备题目"])
def test_question_about_saving_or_quoted_permission_never_authorizes_write(human_text):
    from src.services.agent import interaction
    state = {"question": human_text, "course_id": "course"}
    task, _ = interaction.create({"kind": "question_generate", "topic": "卷积", "count": 2, "question_type": "choice"}, state)
    assert not task["spec"]["save_requested"]


def test_clarification_uses_agent_and_preserves_confirmed_course_scope(tmp_path):
    store = ConversationStore(str(tmp_path / "chat.db"))
    conv = store.create_conversation("course")["id"]
    previous = {"task": "concept", "mode": "concept", "scenario": "考试", "as_of": "2026-10-09"}
    store.append_message(conv, "user", "卷积定义", intent=previous)
    llm = model(call("respond_to_user", kind="clarification", topic="clarification", answer="你希望我讲解知识点还是准备练习？"))
    out = ask("帮我处理一下", "auto", MagicMock(), llm, "course", store, conv)
    assert out.response_kind == "clarification" and not out.grounded
    assert store.get_latest_user_intent(conv) == previous


@pytest.mark.parametrize("kind,entry", [
    ("paper_assemble", {"topic": "卷积", "rules": [{"question_type": "choice", "count": 2, "score": 5}]}),
    ("study_goal", {"topic": "卷积", "count": 1}),
])
def test_semantic_entry_reuses_paper_and_study_contracts(kind, entry):
    from src.services.agent import interaction
    task, result = interaction.create({"kind": kind, **entry}, {"question": "帮助我巩固卷积", "course_id": "course"})
    assert task["kind"] == kind and task["status"] == "running"
    assert not task["spec"]["save_requested"]
    assert not result["task"]["artifacts"]
    if kind == "study_goal":
        assert task["plan"] == [] and task["quizzes"] == {}
    else:
        assert task["spec"]["total_score"] == 10 and not task["spec"]["allow_generate"]


def test_semantic_supplement_scope_change_invalidates_old_authorization():
    from src.services.agent import interaction
    previous = plan_task("关于卷积出2道题，并保存", None, requested=True, course_id="course", scenario="考试", as_of="2026-10-09")
    previous["evidence"] = [{"id": "old"}]
    previous["validated"] = True
    task, _ = interaction.create({"action": "supplement", "kind": "question_generate", "question_type": "choice"}, {
        "question": "用选择题", "course_id": "course", "previous_task": previous, "scope_override": True,
        "scenario": "all", "as_of": None,
    })
    assert task["id"] != previous["id"]
    assert task["scenario"] == "all" and task["as_of"] is None
    assert not task["evidence"] and not task["validated"]
    assert not task["spec"]["save_requested"]


@pytest.mark.parametrize("malformed", ["wrong", [{"name": "respond_to_user"}], [None]])
def test_invalid_model_protocol_uses_trusted_identity_fallback(malformed):
    llm = model({"content": "", "tool_calls": malformed})
    vs = MagicMock()
    out = run_agent("你是什么ai", "course", vs, llm)
    assert out["response_kind"] == "conversation" and "active-model" in out["answer"]
    assert not out["agentic"] and out["steps"][-1] == "fallback: product dialogue"
    assert not vs.mock_calls


@pytest.mark.parametrize("question,answer", [
    ("能介绍下你自己吗？", "我是溯知，一个课程学习 AI Agent。"),
    ("谢谢，刚才解释得挺清楚的", "不客气。"),
])
def test_model_can_answer_conversation_directly_without_tool_call(question, answer):
    llm = model({"content": answer, "tool_calls": []})
    vs = MagicMock()
    out = ask(question, "concept", vs, llm, "course")
    assert out.answer == answer and out.response_kind == "conversation" and out.agentic
    assert not out.tool_calls and not out.citations and not out.grounded
    assert not vs.mock_calls
    llm.chat.assert_called_once()


def test_course_direct_answer_needs_evidence_even_when_model_skips_tools():
    llm = model({"content": "卷积公式是……", "tool_calls": []}, allowed=False)
    out = run_agent("解释卷积公式", "course", MagicMock(), llm)
    assert out["answer"] == "资料库中未找到相关内容"
    assert not out["grounded"] and not out["citations"]


def test_known_identity_does_not_become_course_refusal_when_model_misroutes():
    llm = model(call("rag_answer", query="你是什么ai"), {"content": "<NO_EVIDENCE>", "tool_calls": []})
    with patch("src.services.agent.tools.rag_answer", return_value={"answer": "资料库中未找到相关内容", "citations": [], "grounded": False}):
        out = run_agent("你是什么ai", "course", MagicMock(), llm)
    assert out["response_kind"] == "conversation" and "active-model" in out["answer"]
    assert out["tool_calls"][0]["name"] == "rag_answer"
    assert out["steps"][-1] == "fallback: product dialogue"


def test_semantic_paper_entry_does_not_invent_missing_scores():
    from src.services.agent import interaction
    task, _ = interaction.create({"kind": "paper_assemble", "topic": "卷积", "rules": [{"question_type": "choice", "count": 2}]},
                                  {"question": "安排两道选择题的卷子", "course_id": "course"})
    assert task["status"] == "waiting_input"
    assert task["spec"]["rules"][0]["score"] is None
    assert "分值" in " ".join(task["missing"])
