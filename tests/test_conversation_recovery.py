import copy
from unittest.mock import patch

from fastapi.testclient import TestClient
from src.main import app
from src.dependencies import get_conversation_store, get_current_user
from src.services.conversation_history import read_conversation
from src.services.storage.conversation_store import ConversationStore
from src.exceptions import NotFoundException
import pytest


@pytest.fixture
def store(tmp_path):
    return ConversationStore(str(tmp_path / "conversations.db"))


def test_same_millisecond_history_keeps_turn_order_and_scope(store):
    store.ensure_conversation("conv", "course")
    with patch("src.services.storage.conversation_store._uid", side_effect=["z", "a"]):
        store.append_message("conv", "user", "问题", intent={"scenario": "exam"})
        store.append_message("conv", "assistant", "回答", citations=[{"page": 1}], grounded=False)
    data = read_conversation(store, "conv", "course")
    assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
    assert data["messages"][0]["intent"] == {"scenario": "exam"}
    assert data["messages"][1]["citations"] == [{"page": 1}]
    assert not data["messages"][1]["grounded"] and data["total_messages"] == 2
    assert [m["role"] for m in store.get_history("conv")] == ["user", "assistant"]


def test_checkpoint_read_is_public_and_does_not_grade_or_change_it(store):
    task = {"id": "task1", "kind": "study_goal", "course_id": "course", "goal": "复习第三章", "status": "waiting_answer", "spec": {}, "plan": [], "notes": [], "criteria": [], "issues": [], "artifacts": [], "completed_steps": [], "active_quiz": "s1", "quizzes": {"s1": {"id": "s1", "submitted": False, "child": {"secret": "private"}, "items": [{"stem": "题目", "options": ["甲", "乙"], "answer": "甲", "analysis": "解析", "citations": []}]}}, "evidence": [{"text": "内部原文"}]}
    before = copy.deepcopy(task)
    store.save_agent_task("conv", "course", task)
    data = read_conversation(store, "conv", "course")
    assert data["task"]["self_test"]["items"] == [{"stem": "题目", "options": ["甲", "乙"]}]
    assert "child" not in data["task"]["self_test"] and "evidence" not in data["task"]
    assert "提交本轮答案" in data["resume_hint"]
    assert store.get_agent_task("conv", "course") == before
    with pytest.raises(NotFoundException):
        read_conversation(store, "conv", "other")


def test_recovery_endpoint_enforces_course_and_deletion_cascades(store):
    store.ensure_conversation("conv", "course")
    store.append_message("conv", "user", "问题")
    app.dependency_overrides[get_conversation_store] = lambda: store
    app.dependency_overrides[get_current_user] = lambda: {}
    try:
        client = TestClient(app)
        assert client.get("/api/v1/conversations/conv", params={"course_id": "course"}).json()["data"]["total_messages"] == 1
        assert client.get("/api/v1/conversations/conv", params={"course_id": "other"}).status_code == 404
        assert client.delete("/api/v1/conversations/conv", params={"course_id": "other"}).status_code == 404
        assert store.get_conversation("conv")
        assert client.delete("/api/v1/conversations/conv", params={"course_id": "course"}).status_code == 200
        assert client.get("/api/v1/conversations/conv", params={"course_id": "course"}).status_code == 404
    finally:
        app.dependency_overrides.pop(get_conversation_store, None)
        app.dependency_overrides.pop(get_current_user, None)
