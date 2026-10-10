"""Conversation paper tasks reuse evidence, preview without writes, and commit atomically."""
import json
from unittest.mock import MagicMock, patch
import pytest
from src.services.query import ask, ask_stream
from src.services.citations import build_citation
from src.services.storage.conversation_store import ConversationStore
from src.services.storage.question_bank_store import QuestionBankStore
from src.exceptions import BadRequestException

HIT = {"id": "ev1", "text": "采样频率至少为最高频率的两倍。", "score": .9,
       "metadata": {"course_id": "course", "source_file": "第三章.pdf", "chapter": "第三章", "page": 1}}
GOAL = "根据第三章资料组卷，1道选择题每题5分，1道简答题每题10分，总分15分，缺题允许补题，只预览"
TOOLS = ["retrieve_evidence", "prepare_paper_draft", "validate_paper_draft", "save_paper_draft"]


def decisions(names):
    return [{"content": "", "tool_calls": [{"id": str(i), "name": n, "arguments": "{}"}]} for i, n in enumerate(names)]


@pytest.fixture
def runtime(tmp_path):
    bank = QuestionBankStore(str(tmp_path / "bank.db"))
    reused = bank.create_question({"course_id": "course", "chapter": "第三章", "difficulty": "medium",
        "stem": "采样频率至少为最高频率的多少倍？", "question_type": "choice", "options": ["两倍", "一倍", "一半", "任意"],
        "answer": "两倍", "analysis": "原文给出两倍条件", "citations": [build_citation(HIT)]})
    conversation = ConversationStore(str(tmp_path / "conversation.db"))
    llm = MagicMock(configured=True)
    llm.chat_with_tools.side_effect = decisions(TOOLS)
    llm.chat.side_effect = [json.dumps([{"stem": "为什么需要限制最低采样频率？", "answer": "至少为最高频率两倍", "analysis": "按原文条件选择", "evidence_ids": ["ev1"]}]),
                            json.dumps([{"index": 1, "supported": True}, {"index": 2, "supported": True}])]
    yield conversation, bank, llm, reused
    bank.close()


def invoke(runtime, question, stream=False):
    conversation, bank, llm, _ = runtime
    kwargs = dict(question=question, mode="auto", course_id="course", conversation_id="conv",
                  conversation_store=conversation, question_bank_store=bank, vs=MagicMock(), llm=llm)
    return list(ask_stream(**kwargs)) if stream else ask(**kwargs)


def test_mixed_paper_previews_reuses_and_saves_once(runtime):
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        events = invoke(runtime, GOAL, stream=True)
        preview = events[-1]["data"]
        assert preview["task"]["kind"] == "paper_assemble" and preview["grounded"]
        assert preview["task"]["assembly"] == {"reused_count": 1, "generated_count": 1}
        assert preview["task"]["spec"]["total_score"] == 15
        assert len(runtime[1].list_questions("course")) == 1 and not runtime[1].list_papers("course")
        assert any(e["type"] == "task" for e in events)
        assert [e["data"]["name"] for e in events if e["type"] == "tool"] == TOOLS[:3]
        saved = invoke(runtime, "保存这份试卷")
        assert saved.task["status"] == "completed" and saved.artifacts[0]["type"] == "paper"
        paper = runtime[1].get_paper(saved.artifacts[0]["id"], "course")
        assert paper["total_score"] == 15 and paper["question_count"] == 2
        assert paper["items"][0]["question"]["id"] == runtime[3]["id"]
        again = invoke(runtime, "保存试卷")
        assert again.artifacts == saved.artifacts
        assert len(runtime[1].list_papers("course")) == 1 and len(runtime[1].list_questions("course")) == 2


@pytest.mark.parametrize("question", [
    "根据第三章资料组卷", GOAL.replace("总分15分", "总分20分"),
    GOAL.replace("每题5分", "每题0分"), GOAL.replace("1道选择题", "11道选择题"),
])
def test_invalid_or_incomplete_blueprint_clarifies_before_tools(runtime, question):
    result = invoke(runtime, question)
    assert result.response_kind == "clarification"
    runtime[2].chat_with_tools.assert_not_called()
    assert runtime[1].list_papers("course") == []


def test_stale_bank_citation_does_not_get_reused(runtime):
    payload = {**runtime[3], "citations": [{**build_citation(HIT), "doc_id": "deleted_document"}]}
    runtime[1].delete_question(runtime[3]["id"], "course")
    runtime[1].create_question(payload)
    runtime[2].chat_with_tools.side_effect = decisions(TOOLS[:2]) + [{"content": "无法完成", "tool_calls": []}]
    result = None
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        result = invoke(runtime, GOAL.replace("缺题允许补题", "不要补题"))
    assert result.task["status"] == "failed" and not result.artifacts
    runtime[2].chat.assert_not_called()
    assert not runtime[1].list_papers("course")


def test_paper_atomic_save_rolls_back_new_questions_and_is_idempotent(runtime):
    bank = runtime[1]
    new = {"course_id": "course", "stem": "新简答题", "answer": "答案", "question_type": "short_answer"}
    reuse = {**runtime[3], "reuse_id": runtime[3]["id"], "reuse_updated_at": "stale"}
    with pytest.raises(BadRequestException, match="修改或删除"):
        bank.save_task_paper("atomic", "course", "测试卷", [{"question": new, "score": 10}, {"question": reuse, "score": 5}])
    assert len(bank.list_questions("course")) == 1 and bank.list_papers("course") == []
    items = [{"question": new, "score": 10}]
    paper = bank.save_task_paper("atomic", "course", "测试卷", items)
    assert bank.save_task_paper("atomic", "course", "测试卷", items)["id"] == paper["id"]
    assert len(bank.list_questions("course")) == 2


def test_changed_evidence_blocks_paper_save(runtime):
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        invoke(runtime, GOAL)
    runtime[2].chat_with_tools.side_effect = decisions(["save_paper_draft"]) + [{"content": "无法完成", "tool_calls": []}]
    with patch("src.services.rag._retrieve", return_value=[]):
        result = invoke(runtime, "保存试卷")
    assert not result.grounded and not result.artifacts
    assert not runtime[1].list_papers("course") and len(runtime[1].list_questions("course")) == 1


def test_changed_reused_question_blocks_whole_save(runtime):
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        invoke(runtime, GOAL)
        runtime[1].update_question(runtime[3]["id"], "course", {"answer": "一倍"})
        runtime[2].chat_with_tools.side_effect = decisions(["save_paper_draft"]) + [{"content": "无法完成", "tool_calls": []}]
        result = invoke(runtime, "保存试卷")
    assert not result.artifacts
    assert not runtime[1].list_papers("course") and len(runtime[1].list_questions("course")) == 1



def test_failed_reused_question_is_replaced_before_save(runtime):
    short = json.dumps([{"stem": "如何确定最低采样频率？", "answer": "最高频率两倍", "analysis": "原文支持", "evidence_ids": ["ev1"]}])
    choice = json.dumps([{"stem": "采样下限与信号最高频率有什么关系？", "options": ["两倍", "一倍", "一半", "任意"], "answer": "两倍", "analysis": "原文支持", "evidence_ids": ["ev1"]}])
    runtime[2].chat.side_effect = [short, json.dumps([{"index": 1, "supported": False}, {"index": 2, "supported": True}]),
                                  choice, short, json.dumps([{"index": 1, "supported": True}, {"index": 2, "supported": True}])]
    runtime[2].chat_with_tools.side_effect = decisions(["retrieve_evidence", "prepare_paper_draft", "validate_paper_draft",
                                                        "prepare_paper_draft", "validate_paper_draft", "save_paper_draft"])
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        result = invoke(runtime, GOAL.replace("只预览", "保存试卷"))
    assert result.task["status"] == "completed" and result.grounded
    assert result.task["assembly"] == {"reused_count": 0, "generated_count": 2}
    assert len(result.tool_calls) == 6
    paper = runtime[1].get_paper(result.artifacts[0]["id"], "course")
    assert runtime[3]["id"] not in {i["question"]["id"] for i in paper["items"]}


def test_sufficient_bank_reuses_all_questions_without_generation(runtime):
    runtime[1].create_question({"course_id": "course", "chapter": "第三章", "difficulty": "medium",
        "stem": "最低采样频率如何确定？", "question_type": "short_answer", "answer": "最高频率的两倍",
        "analysis": "原文支持", "citations": [build_citation(HIT)]})
    runtime[2].chat.side_effect = [json.dumps([{"index": 1, "supported": True}, {"index": 2, "supported": True}])]
    with patch("src.services.rag._retrieve", return_value=[HIT]):
        result = invoke(runtime, GOAL.replace("缺题允许补题", "不要补题"))
    assert result.task["assembly"] == {"reused_count": 2, "generated_count": 0}
    assert runtime[2].chat.call_count == 1
    assert len(runtime[1].list_questions("course")) == 2 and not runtime[1].list_papers("course")
