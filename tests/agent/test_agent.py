"""Agent 循环：多步检索、改写上限、拒答、接口装配。"""

from unittest.mock import MagicMock, patch

import pytest

from fastapi.testclient import TestClient

from src.dependencies import get_catalog_store, get_llm_client, get_vector_store
from src.main import app
from src.services.agent import run_agent

client = TestClient(app)


def _fake_llm(configured: bool = True, chat_return: str = "改写后的查询"):
    llm = MagicMock()
    llm.configured = configured
    llm.chat.return_value = chat_return
    return llm


def _hit(score: float) -> dict:
    return {
        "text": "片段内容",
        "score": score,
        "metadata": {"source_file": "a.md", "page": 1},
    }


class TestAgentLoop:
    def test_agentic_calls_tool_then_returns_grounded_answer(self):
        vs = MagicMock()
        llm = _fake_llm(configured=True)
        llm.chat_with_tools.side_effect = [
            {
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "name": "search_pdf", "arguments": '{"query":"采样定理"}'},
                ],
            },
            {"content": "根据资料，采样频率需要满足奈奎斯特条件。", "tool_calls": []},
        ]
        with patch("src.services.agent.tools.retrieve_tool", return_value=[_hit(0.8)]) as ret:
            result = run_agent("采样定理要求", "course-default", vs, llm, agentic=True)
        assert result["agentic"] is True
        assert result["grounded"] is True
        assert result["answer"].startswith("根据资料")
        assert result["tool_calls"] == [
            {"id": "call_1", "name": "search_pdf", "arguments": {"query": "采样定理"}, "ok": True, "citation_count": 1, "error": ""}
        ]
        assert len(result["citations"]) == 1
        assert llm.chat_with_tools.call_count == 2
        ret.assert_called_once_with("采样定理", vs, "course-default", 5)

    def test_agentic_refuses_direct_answer_without_tool_evidence(self):
        vs = MagicMock()
        llm = _fake_llm(configured=True)
        llm.chat_with_tools.return_value = {"content": "我认为答案是……", "tool_calls": []}
        result = run_agent("问题", "course-default", vs, llm, agentic=True)
        assert result["agentic"] is True
        assert result["grounded"] is False
        assert result["answer"] == "资料库中未找到相关内容"

    def test_agentic_stops_at_tool_round_limit(self):
        vs = MagicMock()
        llm = _fake_llm(configured=True)
        llm.chat_with_tools.return_value = {
            "content": "", "tool_calls": [{"id": "call_1", "name": "search_pdf", "arguments": '{"query":"问题"}'}],
        }
        with patch("src.services.agent.tools.retrieve_tool", return_value=[_hit(0.8)]):
            result = run_agent("问题", "course-default", vs, llm, agentic=True, max_steps=1)
        assert result["grounded"] is False
        assert len(result["tool_calls"]) == 1
        assert llm.chat_with_tools.call_count == 2

    def test_agentic_unconfigured_falls_back_to_registered_rag(self):
        llm = _fake_llm(configured=False)
        with patch("src.services.rag._retrieve", return_value=[]):
            result = run_agent("问题", "course-default", MagicMock(), llm)
        assert result["agentic"] is False
        assert result["tool_calls"][0]["name"] == "rag_answer"
        assert result["grounded"] is False
        llm.chat_with_tools.assert_not_called()

    def test_generate_when_sufficient(self):
        vs = MagicMock()
        llm = _fake_llm(configured=True)
        with patch("src.services.agent.tools.retrieve_tool", return_value=[_hit(0.8)]) as ret:
            with patch(
                "src.services.generation.generate",
                return_value={"answer": "答案", "citations": [], "grounded": True},
            ) as gen:
                result = run_agent(
                    "问题", "course-default", vs, llm,
                    max_steps=3, top_k=5, score_threshold=0.25, agentic=False,
                )
        assert result["grounded"] is True
        assert result["answer"] == "答案"
        assert result["agent_used"] is True
        assert any(s.startswith("retrieve:") for s in result["steps"])
        assert any(s.startswith("grade:") for s in result["steps"])
        assert "generate" in result["steps"]
        assert ret.call_count == 1
        gen.assert_called_once()

    def test_rewrite_then_generate(self):
        vs = MagicMock()
        llm = _fake_llm(configured=True, chat_return="改写查询")
        with patch(
            "src.services.agent.tools.retrieve_tool",
            side_effect=[[_hit(0.1)], [_hit(0.8)]],
        ) as ret:
            with patch(
                "src.services.generation.generate",
                return_value={"answer": "答案", "citations": [], "grounded": True},
            ):
                result = run_agent(
                    "问题", "course-default", vs, llm,
                    max_steps=3, top_k=5, score_threshold=0.25, agentic=False,
                )
        assert result["grounded"] is True
        assert ret.call_count == 2
        assert any(s.startswith("rewrite:") for s in result["steps"])
        llm.chat.assert_called()

    def test_refuse_after_max_steps(self):
        vs = MagicMock()
        llm = _fake_llm(configured=True, chat_return="改写查询")
        with patch(
            "src.services.agent.tools.retrieve_tool",
            return_value=[_hit(0.1)],
        ) as ret:
            result = run_agent(
                "问题", "course-default", vs, llm,
                max_steps=2, top_k=5, score_threshold=0.25, agentic=False,
            )
        assert result["grounded"] is False
        assert result["answer"] == "资料库中未找到相关内容"
        assert ret.call_count == 2
        assert "refuse" in result["steps"]

    def test_refuse_when_llm_unconfigured(self):
        vs = MagicMock()
        llm = _fake_llm(configured=False)
        with patch(
            "src.services.agent.tools.retrieve_tool",
            return_value=[_hit(0.1)],
        ) as ret:
            result = run_agent(
                "问题", "course-default", vs, llm,
                max_steps=3, top_k=5, score_threshold=0.25, agentic=False,
            )
        assert result["grounded"] is False
        assert ret.call_count == 1
        assert "refuse" in result["steps"]


class TestAgentApi:
    def test_missing_course_id_422(self):
        r = client.post("/api/v1/agent/run", json={"question": "问题"})
        assert r.status_code == 422

    def test_endpoint_ok(self):
        vs = MagicMock()
        llm = _fake_llm(configured=True)
        catalog = MagicMock()
        app.dependency_overrides[get_vector_store] = lambda: vs
        app.dependency_overrides[get_llm_client] = lambda: llm
        app.dependency_overrides[get_catalog_store] = lambda: catalog
        try:
            with patch("src.services.agent.tools.retrieve_tool", return_value=[_hit(0.8)]):
                with patch(
                    "src.services.generation.generate",
                    return_value={
                        "answer": "答案",
                        "citations": [
                            {"source_file": "a.md", "page": 1, "snippet": "片段", "score": 0.8}
                        ],
                        "grounded": True,
                    },
                ):
                    r = client.post(
                        "/api/v1/agent/run",
                        json={"question": "问题", "course_id": "course-default", "agentic": False},
                    )
        finally:
            app.dependency_overrides.pop(get_vector_store, None)
            app.dependency_overrides.pop(get_llm_client, None)
            app.dependency_overrides.pop(get_catalog_store, None)

        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 200
        assert body["data"]["agent_used"] is True
        assert body["data"]["grounded"] is True
        assert body["data"]["answer"] == "答案"
        catalog.require_course.assert_called_once_with("course-default")

    def test_endpoint_forwards_agentic_filters(self):
        vs = MagicMock()
        llm = _fake_llm(configured=True)
        catalog = MagicMock()
        app.dependency_overrides[get_vector_store] = lambda: vs
        app.dependency_overrides[get_llm_client] = lambda: llm
        app.dependency_overrides[get_catalog_store] = lambda: catalog
        try:
            with patch("src.services.query.run_agent", return_value={
                "answer": "答案", "citations": [], "grounded": True, "steps": [],
                "tool_calls": [], "agentic": True, "agent_used": True,
            }) as runner:
                r = client.post("/api/v1/agent/run", json={
                    "question": "考试要求", "course_id": "course-default", "agentic": True,
                    "scenario": "考试", "as_of": "2026-09-01",
                })
        finally:
            app.dependency_overrides.pop(get_vector_store, None)
            app.dependency_overrides.pop(get_llm_client, None)
            app.dependency_overrides.pop(get_catalog_store, None)
        assert r.status_code == 200
        assert runner.call_args.kwargs["agentic"] is True
        assert runner.call_args.kwargs["scenario"] == "考试"
        assert runner.call_args.kwargs["as_of"] == "2026-09-01"
