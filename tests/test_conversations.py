"""对话持久化存储测试。"""

import pytest
import time
from src.services.storage.conversation_store import ConversationStore


@pytest.fixture
def conv_store(tmp_path):
    return ConversationStore(str(tmp_path / "test_conversations.db"))


class TestConversationStore:
    def test_create_and_get(self, conv_store):
        conv = conv_store.create_conversation("course-1", "Test")
        assert conv["course_id"] == "course-1"
        assert conv["title"] == "Test"
        assert conv_store.get_conversation(conv["id"]) == conv

    def test_list_by_course(self, conv_store):
        conv_store.create_conversation("course-1", "A")
        conv_store.create_conversation("course-1", "B")
        conv_store.create_conversation("course-2", "C")
        assert len(conv_store.list_conversations("course-1")) == 2
        assert len(conv_store.list_conversations("course-2")) == 1

    def test_delete(self, conv_store):
        conv = conv_store.create_conversation("course-1")
        assert conv_store.delete_conversation(conv["id"]) is True
        assert conv_store.get_conversation(conv["id"]) is None
        assert conv_store.delete_conversation("nonexistent") is False

    def test_append_and_history(self, conv_store):
        conv = conv_store.create_conversation("course-1")
        conv_store.append_message(conv["id"], "user", "Q1"); time.sleep(0.01)
        conv_store.append_message(conv["id"], "assistant", "A1", grounded=True); time.sleep(0.01)
        conv_store.append_message(conv["id"], "user", "Q2"); time.sleep(0.01)
        conv_store.append_message(conv["id"], "assistant", "A2")
        history = conv_store.get_history(conv["id"])
        assert len(history) == 4
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Q1"
        assert history[3]["role"] == "assistant"
        assert history[3]["content"] == "A2"

    def test_history_limit(self, conv_store):
        conv = conv_store.create_conversation("course-1")
        for i in range(30):
            conv_store.append_message(conv["id"], "user", "q" + str(i)); time.sleep(0.001)
            conv_store.append_message(conv["id"], "assistant", "a" + str(i)); time.sleep(0.001)
        history = conv_store.get_history(conv["id"], max_messages=10)
        assert len(history) == 10

    def test_ensure_conversation(self, conv_store):
        conv_store.ensure_conversation("custom-id", "course-1", "Custom")
        conv = conv_store.get_conversation("custom-id")
        assert conv is not None
        assert conv["title"] == "Custom"
        conv_store.ensure_conversation("custom-id", "course-1", "NoChange")
        conv2 = conv_store.get_conversation("custom-id")
        assert conv2["title"] == "Custom"

    def test_count_messages(self, conv_store):
        conv = conv_store.create_conversation("course-1")
        assert conv_store.count_messages(conv["id"]) == 0
        conv_store.append_message(conv["id"], "user", "q")
        conv_store.append_message(conv["id"], "assistant", "a")
        assert conv_store.count_messages(conv["id"]) == 2

    def test_get_latest_user_intent(self, conv_store):
        conv = conv_store.create_conversation("course-1")
        intent = {"task": "rule_query", "mode": "qa", "scenario": "考试"}
        conv_store.append_message(conv["id"], "user", "考试能带计算器吗", intent=intent)
        conv_store.append_message(conv["id"], "assistant", "请以规定为准")
        assert conv_store.get_latest_user_intent(conv["id"]) == intent
