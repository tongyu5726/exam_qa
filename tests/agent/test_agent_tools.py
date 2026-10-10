"""Agent 工具集：search_pdf / read_page / extract_table / analyze_chart / quote_source。"""

from unittest.mock import MagicMock, patch

import pytest

from src.exceptions import BadRequestException
from src.services.agent import tools


def _hit(text: str, *, page: int = 1, block_type: str = "text", score: float = 0.8, **meta_extra):
    meta = {
        "doc_id": "1",
        "source_file": "a.md",
        "chunk_index": 0,
        "course_id": "course-default",
        "page": page,
        "chapter": "第一章",
        "section_path": "第一章 / 1.1",
        "block_type": block_type,
        "table_headers": "",
        "context": "1.1",
    }
    meta.update(meta_extra)
    return {"id": f"1_0", "text": text, "score": score, "metadata": meta}


def _vs(*, chunks=None, search=None):
    vs = MagicMock()
    vs.get_chunks.return_value = chunks if chunks is not None else [_hit("第1页内容")]
    vs.search.return_value = search if search is not None else [_hit("表格内容", block_type="table")]
    return vs


class TestSearchPdf:
    def test_wraps_retrieve_with_citations(self):
        vs = _vs()
        with patch("src.services.agent.tools.retrieve_tool", return_value=[_hit("片段")]) as ret:
            out = tools.search_pdf("什么是采样定理", vs, "course-default")
        assert out["query"] == "什么是采样定理"
        assert len(out["results"]) == 1
        assert out["citations"][0]["source_file"] == "a.md"
        assert out["citations"][0]["page"] == 1
        ret.assert_called_once_with("什么是采样定理", vs, "course-default", 5)


class TestReadPage:
    def test_joins_page_chunks(self):
        vs = _vs(
            chunks=[
                _hit("第一段", page=2),
                _hit("第二段", page=2),
            ]
        )
        out = tools.read_page("1", 2, vs, "course-default")
        assert out["found"] is True
        assert "第一段" in out["text"] and "第二段" in out["text"]
        assert len(out["citations"]) == 2
        vs.get_chunks.assert_called_once_with(
            course_id="course-default", doc_id="1", source_file=None, page=2
        )

    def test_page_not_found(self):
        vs = _vs(chunks=[])
        out = tools.read_page("1", 99, vs, "course-default")
        assert out["found"] is False
        assert out["text"] == ""


class TestExtractTable:
    def test_by_query_uses_block_type_filter(self):
        vs = _vs()
        with patch("src.services.agent.tools._embed_query", return_value=[0.1, 0.2]):
            out = tools.extract_table(vs, "course-default", query="信道编码表")
        assert out["count"] == 1
        assert out["tables"][0]["text"] == "表格内容"
        vs.search.assert_called_once_with([0.1, 0.2], top_k=10, course_id="course-default", block_type="table")

    def test_by_doc_filters_tables(self):
        vs = _vs(
            chunks=[
                _hit("表1", block_type="table", page=3, table_headers="名称 | 说明")
            ]
        )
        out = tools.extract_table(vs, "course-default", doc_id="1", page=3)
        assert out["count"] == 1
        assert out["tables"][0]["table_headers"] == "名称 | 说明"
        assert out["tables"][0]["page"] == 3
        vs.get_chunks.assert_called_once_with(
            course_id="course-default", doc_id="1", source_file=None, page=3, block_type="table"
        )


class TestAnalyzeChart:
    def test_requested_figure_wins_over_higher_score_and_forwards_scope(self, monkeypatch):
        from src.services import retrieval

        monkeypatch.setattr(tools, "_embed_query", lambda _: [.1])
        monkeypatch.setattr(retrieval, "_cached_query_vec", lambda *_: (.1,))
        wrong = _hit("图 2.1 其他图", block_type="image_summary", score=.95)
        wrong["id"] = "wrong"
        wanted = _hit("图 5.3-1 频谱图", block_type="image_summary", score=.7)
        wanted["id"] = "wanted"
        vs = _vs(search=[wrong, wanted])
        out = tools.analyze_chart("图 5.3-1 说明什么", vs, "course-default",
                                  scenario="考试", as_of="2026-10-04")
        assert out["summaries"] == ["图 5.3-1 频谱图"]
        for call in vs.search.call_args_list:
            assert call.kwargs["course_id"] == "course-default"
            assert call.kwargs["scenario"] == "考试"
            assert call.kwargs["as_of"] == "2026-10-04"

    def test_no_summary_returns_note(self):
        vs = _vs(chunks=[], search=[])
        with patch("src.services.retrieval._cached_query_vec", return_value=[0.1]):
            out = tools.analyze_chart("这张图说明什么", vs, "course-default")
        assert out["found"] is False
        assert "未找到图表摘要" in out["note"]

    def test_with_llm_generates_analysis(self):
        llm = MagicMock()
        llm.configured = True
        llm.chat.return_value = "该图展示了奈奎斯特采样率。"
        vs = _vs(search=[_hit("奈奎斯特示意图", block_type="image_summary", page=5)])
        with patch("src.services.retrieval._cached_query_vec", return_value=[0.1]):
            out = tools.analyze_chart("这张图说明什么", vs, "course-default", llm)
        assert out["found"] is True
        assert out["analysis"] == "该图展示了奈奎斯特采样率。"
        assert out["citations"][0]["page"] == 5
        llm.chat.assert_called_once()

    def test_without_llm_returns_raw_summaries(self):
        vs = _vs(search=[_hit("频谱图摘要", block_type="image_summary")])
        with patch("src.services.retrieval._cached_query_vec", return_value=[0.1]):
            out = tools.analyze_chart("这张图说明什么", vs, "course-default")
        assert out["found"] is True
        assert out["summaries"] == ["频谱图摘要"]
        assert out["analysis"] == ""


class TestQuoteSource:
    def test_returns_citations(self):
        vs = _vs()
        with patch("src.services.agent.tools.retrieve_tool", return_value=[_hit("片段", page=7)]):
            out = tools.quote_source("引用来源", vs, "course-default")
        assert out["count"] == 1
        assert out["citations"][0]["page"] == 7
        assert out["citations"][0]["section_path"] == "第一章 / 1.1"


class TestToolRegistry:
    def test_schemas_complete(self):
        names = {t["function"]["name"] for t in tools.TOOL_SCHEMAS}
        assert names == tools.SUPPORTED_TOOLS
        for t in tools.TOOL_SCHEMAS:
            fn = t["function"]
            assert fn["name"] and fn["description"] and fn["parameters"]

    def test_execute_unknown_tool_rejected(self):
        vs = _vs()
        with pytest.raises(BadRequestException):
            tools.execute_tool("drop_table", {}, vs=vs, course_id="course-default")

    def test_execute_dispatches(self):
        vs = _vs()
        with patch("src.services.agent.tools.retrieve_tool", return_value=[_hit("片段")]):
            out = tools.execute_tool("search_pdf", {"query": "问题"}, vs=vs, course_id="course-default")
        assert out["query"] == "问题"

    def test_execute_forwards_evidence_filters(self):
        vs = _vs()
        with patch("src.services.agent.tools.retrieve_tool", return_value=[_hit("片段")]) as retrieve:
            tools.execute_tool(
                "search_pdf", {"query": "考试要求"}, vs=vs, course_id="course-default",
                scenario="考试", as_of="2026-09-01",
            )
        retrieve.assert_called_once_with(
            "考试要求", vs, "course-default", 5, scenario="考试", as_of="2026-09-01"
        )
