"""Agent completes evidence-backed question tasks, rather than redirecting."""
import json
from unittest.mock import MagicMock, patch
import pytest
from src.services.query import ask, ask_stream
from src.services.storage.conversation_store import ConversationStore
from src.services.storage.question_bank_store import QuestionBankStore


@pytest.fixture
def runtime(tmp_path):
    conversation = ConversationStore(str(tmp_path / "conversation.db"))
    bank = QuestionBankStore(str(tmp_path / "bank.db"))
    llm = MagicMock(configured=True)
    generated = json.dumps([
        {"stem": f"采样条件{i}？", "options": ["两倍", "一倍", "一半", "任意"],
         "answer": "两倍", "analysis": "资料明确给出两倍条件。", "evidence_ids": ["ev1"]}
        for i in range(2)
    ], ensure_ascii=False)
    llm.chat.side_effect = [generated, json.dumps([{ "index": i+1, "supported": True, "reason": "原文直接支持"} for i in range(2)])]
    llm.chat_with_tools.side_effect = [
        {"content": "", "tool_calls": [{"id": str(i), "name": name, "arguments": "{}"}]}
        for i, name in enumerate(["retrieve_evidence", "generate_question_drafts", "validate_question_drafts", "save_question_drafts"])
    ]
    yield conversation, bank, llm
    bank.close()


def invoke(runtime, question, stream=False, **kwargs):
    conversation, bank, llm = runtime
    args = dict(question=question, mode="auto", vs=MagicMock(), llm=llm, course_id="course",
                conversation_store=conversation, conversation_id="conv", question_bank_store=bank, **kwargs)
    return list(ask_stream(**args)) if stream else ask(**args)


def test_task_clarifies_without_reading_or_writing(runtime):
    out = invoke(runtime, "帮我生成练习题")
    assert out.response_kind == "clarification"
    assert out.task["status"] == "waiting_input"
    assert not out.grounded
    runtime[2].chat_with_tools.assert_not_called()
    assert runtime[1].list_questions("course") == []


def test_task_retrieves_validates_saves_and_returns_artifacts(runtime):
    hit = {"id": "ev1", "text": "采样频率至少为最高频率的两倍。", "score": .9,
           "metadata": {"course_id": "course", "source_file": "第三章.pdf", "chapter": "第三章", "page": 1}}
    with patch("src.services.rag._retrieve", return_value=[hit]) as retrieve:
        events = invoke(runtime, "根据第三章资料生成2道中等难度选择题，保存为草稿", stream=True)
    out = events[-1]["data"]
    assert out["task"]["status"] == "completed"
    assert out["grounded"]
    assert len(out["artifacts"]) == 2
    assert len(runtime[1].list_questions("course")) == 2
    assert retrieve.call_args.args[3] == "course"
    assert any(e["type"] == "task" for e in events)
    retry = invoke(runtime, "继续刚才的任务")
    assert retry.task["status"] == "completed"
    assert len(runtime[1].list_questions("course")) == 2


HIT = {"id": "ev1", "text": "采样频率至少为最高频率的两倍。", "score": .9,
       "metadata": {"course_id": "course", "source_file": "第三章.pdf", "chapter": "第三章", "page": 1}}


def test_preview_requires_save_consent_and_save_is_idempotent(runtime):
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        preview = invoke(runtime, "第三章生成2道选择题，只预览")
        assert preview.task["status"] == "completed"
        assert preview.task["drafts"] and not preview.artifacts
        assert runtime[1].list_questions("course") == []
        saved = invoke(runtime, "保存这些题目")
        assert len(saved.artifacts) == 2
        again = invoke(runtime, "保存这些题目")
        assert again.artifacts == saved.artifacts
        assert len(runtime[1].list_questions("course")) == 2


def test_clarification_can_resume_with_confirmed_parameters(runtime):
    invoke(runtime, "帮我生成练习题")
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        result = invoke(runtime, "第三章，2道选择题")
    assert result.task["status"] == "completed"
    assert not result.artifacts


def test_no_evidence_never_generates_or_saves(runtime):
    with patch("src.services.rag._retrieve", return_value=[]):
        result = invoke(runtime, "第三章生成2道选择题并保存")
    assert result.answer == "资料库中未找到相关内容"
    assert not result.grounded and not result.artifacts
    runtime[2].chat.assert_not_called()


@pytest.mark.parametrize("verdict", [
    [{"index": 1, "supported": False}, {"index": 2, "supported": True}],
    [{"index": {}, "supported": True}],
])
def test_failed_evidence_validation_never_saves(runtime, verdict):
    generated = runtime[2].chat.side_effect
    first = next(generated)
    runtime[2].chat.side_effect = [first, json.dumps(verdict)]
    runtime[2].chat_with_tools.side_effect = list(runtime[2].chat_with_tools.side_effect) + [{"content": "结束", "tool_calls": []}]
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        result = invoke(runtime, "第三章生成2道选择题并保存")
    assert not result.grounded and not result.artifacts
    assert runtime[1].list_questions("course") == []
    assert result.task["status"] == "failed"


def test_atomic_save_rolls_back_and_reuses_task_result(runtime):
    bank = runtime[1]
    payload = {"course_id": "course", "stem": "问题", "question_type": "short_answer", "answer": "答案"}
    with pytest.raises(KeyError):
        bank.save_task_questions("atomic", "course", [payload, {"course_id": "course"}])
    assert bank.list_questions("course") == []
    saved = bank.save_task_questions("atomic", "course", [payload])
    assert bank.save_task_questions("atomic", "course", [payload]) == saved


def test_course_history_is_checked_before_task_or_model(runtime):
    from src.exceptions import BadRequestException
    runtime[0].ensure_conversation("conv", "other", "其他课程")
    with pytest.raises(BadRequestException, match="当前课程"):
        invoke(runtime, "第三章生成2道选择题")
    runtime[2].chat_with_tools.assert_not_called()


def test_cancelled_checkpoint_blocks_write(runtime):
    from src.services.query import _prepare_task
    from src.services.intent import IntentDecision
    from src.services.agent.nodes import agentic_tool_node
    task, checkpoint = _prepare_task("第三章生成2道选择题并保存", IntentDecision(task="question_generate"),
                                     runtime[0], "conv", "course")
    invoke(runtime, "取消任务")
    state = {"task": task, "top_k": 5, "pending_tool_calls": [{"id": "save", "name": "save_question_drafts", "arguments": "{}"}]}
    output = agentic_tool_node(state, MagicMock(), runtime[2], bank=runtime[1], checkpoint=checkpoint)
    assert output["task"]["status"] == "cancelled"
    assert runtime[1].list_questions("course") == []


def test_save_rechecks_current_evidence(runtime):
    runtime[2].chat_with_tools.side_effect = list(runtime[2].chat_with_tools.side_effect)[:3]
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        invoke(runtime, "第三章生成2道选择题，只预览")
    runtime[2].chat_with_tools.side_effect = [{"content": "", "tool_calls": [{"id": "save", "name": "save_question_drafts", "arguments": "{}"}]}, {"content": "结束", "tool_calls": []}]
    with patch("src.services.rag._retrieve", return_value=[]):
        result = invoke(runtime, "保存这些题目")
    assert not result.artifacts and not result.grounded
    assert runtime[1].list_questions("course") == []



def test_agent_repairs_unsupported_drafts_before_saving(runtime):
    generated = next(runtime[2].chat.side_effect)
    supported = json.dumps([{"index": i+1, "supported": True} for i in range(2)])
    runtime[2].chat.side_effect = [generated, json.dumps([{"index": i+1, "supported": False} for i in range(2)]), generated, supported]
    runtime[2].chat_with_tools.side_effect = [
        {"content": "", "tool_calls": [{"id": str(i), "name": name, "arguments": "{}"}]}
        for i, name in enumerate(["retrieve_evidence", "generate_question_drafts", "validate_question_drafts",
                                  "generate_question_drafts", "validate_question_drafts", "save_question_drafts"])]
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        result = invoke(runtime, "第三章生成2道选择题并保存")
    assert result.task["status"] == "completed" and len(result.artifacts) == 2
    assert len(result.tool_calls) == 6


def test_agent_endpoint_supports_tasks_and_checkpoints(runtime):
    from fastapi.testclient import TestClient
    from src.main import app
    from src.dependencies import get_vector_store, get_llm_client, get_conversation_store, get_question_bank_store, get_catalog_store
    dependencies = {get_vector_store: lambda: MagicMock(), get_llm_client: lambda: runtime[2],
                    get_conversation_store: lambda: runtime[0], get_question_bank_store: lambda: runtime[1],
                    get_catalog_store: lambda: MagicMock()}
    previous = app.dependency_overrides.copy()
    app.dependency_overrides.update(dependencies)
    try:
        with TestClient(app) as client, patch("src.services.rag._retrieve", return_value=[HIT]):
            response = client.post("/api/v1/agent/run", json={"question": "第三章生成2道选择题并保存", "course_id": "course", "conversation_id": "conv"})
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["response_kind"] == "task_result" and len(data["artifacts"]) == 2
        assert runtime[0].get_agent_task("conv", "course")["status"] == "completed"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)



def test_new_scope_does_not_reuse_saved_batch(runtime):
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        result = invoke(runtime, "第三章生成2道选择题并保存")
    from src.services.query import _prepare_task
    from src.services.intent import IntentDecision
    scoped, _ = _prepare_task("保存这些题目", IntentDecision(scenario="考试"), runtime[0], "conv", "course", True)
    assert scoped["id"] != result.task["id"]
    assert not scoped["validated"] and not scoped["artifacts"] and not scoped["evidence"]


def test_replaced_task_cannot_overwrite_checkpoint(runtime):
    from src.services.query import _prepare_task
    from src.services.intent import IntentDecision
    intent = IntentDecision(task="question_generate", layer="rule")
    old, checkpoint = _prepare_task("第三章生成2道选择题", intent, runtime[0], "conv", "course")
    newer, _ = _prepare_task("第四章生成2道选择题", intent, runtime[0], "conv", "course")
    assert checkpoint(old)["status"] == "cancelled"
    assert runtime[0].get_agent_task("conv", "course")["id"] == newer["id"]
    stopped = invoke(runtime, "请取消任务")
    assert stopped.task["status"] == "cancelled"
