"""Default chat is an Agent; RAG is a scoped tool."""
import json
from unittest.mock import MagicMock, patch

import pytest

from src.services.query import ask, ask_stream


def client():
    llm = MagicMock(configured=True)
    llm.chat_with_tools.side_effect = [
        {"content": "", "tool_calls": [{"id": "rag1", "name": "rag_answer", "arguments": json.dumps({"query": "卷积定义", "course_id": "other", "mode": "chapter"})}]},
        {"content": "卷积是积分。", "tool_calls": []},
    ]
    return llm


@pytest.mark.parametrize("stream", [False, True])
def test_default_chat_calls_scoped_rag_tool_and_saves_history(stream):
    llm = client()
    store = MagicMock()
    store.get_history.return_value = [{"role": "user", "content": "之前的问题"}]
    store.get_latest_user_intent.return_value = None
    result = {"answer": "卷积是积分。", "grounded": True,
              "citations": [{"source_file": "a.md", "snippet": "卷积是积分。", "score": .9}]}
    with patch("src.services.agent.tools.rag_answer", return_value=result) as rag:
        args = dict(question="卷积定义", mode="concept", vs=MagicMock(), llm=llm,
                    course_id="course", conversation_store=store, conversation_id="conv",
                    scenario="考试", as_of="2026-10-09")
        if stream:
            events = list(ask_stream(**args))
            out = events[-1]["data"]
            assert events[0]["type"] == "phase"
            assert any(e["type"] == "delta" for e in events)
        else:
            out = ask(**args).model_dump()
    assert out["grounded"] is True
    assert out["agentic"] is True
    assert out["tool_calls"][0]["name"] == "rag_answer"
    assert rag.call_args.kwargs["course_id"] == "course"
    assert rag.call_args.kwargs["mode"] == "concept"
    assert rag.call_args.kwargs["scenario"] == "考试"
    assert rag.call_args.kwargs["as_of"] == "2026-10-09"
    assert rag.call_args.kwargs["history"] == store.get_history.return_value
    assert llm.chat_with_tools.call_args_list[0].args[0][1]["content"] == "之前的问题"
    assert store.append_message.call_count == 2


def test_agent_abstains_even_after_a_tool_returned_citations():
    llm = client()
    llm.chat_with_tools.side_effect = [llm.chat_with_tools.side_effect.__next__(),
                                        {"content": "<NO_EVIDENCE>", "tool_calls": []}]
    with patch("src.services.agent.tools.rag_answer", return_value={
        "answer": "证据", "grounded": True,
        "citations": [{"source_file": "a.md", "snippet": "证据", "score": .9}],
    }):
        out = ask("卷积定义", "qa", MagicMock(), llm, "course")
    assert out.answer == "资料库中未找到相关内容"
    assert out.grounded is False
    assert out.citations == []


@pytest.mark.parametrize("mode", ["qa", "concept", "chapter"])
def test_registered_rag_reuses_generation_and_grounding(mode):
    llm = client()
    llm.chat.return_value = "卷积是积分。\n<SOURCES>1</SOURCES>"
    with patch("src.services.rag._retrieve", return_value=[{
        "text": "卷积是积分。", "score": .9, "metadata": {"source_file": "a.md", "page": 2},
    }]) as retrieve:
        out = ask("卷积定义", mode, MagicMock(), llm, "course")
    assert out.grounded and out.agentic
    assert out.citations[0].page == 2
    assert retrieve.call_args.args[1] == mode
    assert retrieve.call_args.kwargs["top_k"] >= (12 if mode == "concept" else 1)
    assert llm.chat.call_count == 1


@pytest.mark.parametrize("stream", [False, True])
def test_ask_http_defaults_to_agent(stream):
    from fastapi.testclient import TestClient
    from src.main import app
    from src.dependencies import get_vector_store, get_llm_client, get_catalog_store, get_conversation_store
    llm = client()
    overrides = {get_vector_store: lambda: MagicMock(), get_llm_client: lambda: llm,
                 get_catalog_store: lambda: MagicMock(), get_conversation_store: lambda: MagicMock()}
    previous = dict(app.dependency_overrides)
    app.dependency_overrides.update(overrides)
    try:
        with patch("src.services.agent.tools.rag_answer", return_value={
            "answer": "卷积是积分。", "grounded": True,
            "citations": [{"source_file": "a.md", "snippet": "卷积是积分。", "score": .9}],
        }):
            response = TestClient(app).post("/api/v1/ask", json={
                "question": "卷积定义", "course_id": "course", "stream": stream,
            })
        assert response.status_code == 200
        data = ([json.loads(line[6:]) for line in response.text.splitlines()
                 if line.startswith("data: ")][-1]["data"] if stream else response.json()["data"])
        assert data["agentic"] and data["agent_used"]
        assert data["tool_calls"][0]["name"] == "rag_answer"
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_failed_rag_evidence_cannot_ground_agent_answer():
    llm = client()
    with patch("src.services.agent.tools.rag_answer", return_value={
        "answer": "资料库中未找到相关内容", "grounded": False,
        "citations": [{"source_file": "a.md", "snippet": "无关", "score": .9}],
    }):
        out = ask("卷积定义", "qa", MagicMock(), llm, "course")
    assert not out.grounded and not out.citations


def test_search_tool_cannot_ground_answer_with_low_score():
    from src.services.agent import tools
    with patch.object(tools, "retrieve_tool", return_value=[{
        "text": "无关", "score": 0, "metadata": {"source_file": "a.md"},
    }]):
        result = tools.execute_tool("search_pdf", {"query": "卷积"}, vs=MagicMock(), course_id="course")
    assert result["results"] == result["citations"] == []


@pytest.mark.parametrize("error", ["api", "timeout"])
def test_tool_decision_failure_uses_same_scoped_rag_capability(error):
    from src.exceptions import LLMAPIException, LLMTimeoutException
    llm = MagicMock(configured=True)
    llm.chat_with_tools.side_effect = LLMAPIException() if error == "api" else LLMTimeoutException()
    llm.chat_stream.return_value = iter(["<NO_EVIDENCE>"])
    with patch("src.services.rag._retrieve", return_value=[]) as retrieve:
        out = ask("卷积定义", "concept", MagicMock(), llm, "course",
                  scenario="考试", as_of="2026-10-09")
    assert out.agent_used and not out.agentic
    assert out.tool_calls[0]["name"] == "rag_answer"
    assert not out.grounded
    assert retrieve.call_args.args[1] == "concept"
    assert retrieve.call_args.args[3] == "course"
    assert retrieve.call_args.kwargs["scenario"] == "考试"
    assert retrieve.call_args.kwargs["as_of"] == "2026-10-09"


def test_maximum_tool_rounds_do_not_hit_langgraph_default_recursion_limit():
    from src.services.agent import run_agent
    llm = MagicMock(configured=True)
    llm.chat_with_tools.return_value = {"content": "", "tool_calls": [
        {"id": "call", "name": "rag_answer", "arguments": '{"query":"卷积"}'},
    ]}
    with patch("src.services.agent.tools.rag_answer", return_value={
        "answer": "资料库中未找到相关内容", "grounded": False, "citations": [],
    }) as rag:
        result = run_agent("卷积", "course", MagicMock(), llm, max_steps=10)
    assert rag.call_count == 10
    assert llm.chat_with_tools.call_count == 11
    assert not result["grounded"]
