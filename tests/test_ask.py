"""对话主链：流式 / qa|concept|chapter / 拒答 / prompt。"""

import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fastapi.testclient import TestClient

from src.dependencies import get_llm_client
from src.main import app
from src.services import generation
from src.exceptions import LLMAPIException
from src.services.query import (
    _citations,
    ask,
    ask_stream,
)
from src.services.rag import CONCEPT_TOP_K, _retrieve, _verify_visual_evidence
from src.services.storage.catalog_store import (
    DEFAULT_COLLEGE_ID,
    DEFAULT_COURSE_ID,
    DEFAULT_COURSE_NAME,
)

client = TestClient(app)


@pytest.mark.parametrize("kind", ["answer", "refusal", "routing"])
def test_sync_stream_results_and_saved_turns_match(monkeypatch, kind, query_chain):
    from src.services import query

    save = MagicMock()
    monkeypatch.setattr(query, "_save_turn", save)
    if kind == "refusal":
        query_chain.retrieve.return_value = []
    question = "请生成练习题" if kind == "routing" else "卷积定义"
    args = dict(question=question, mode="qa", vs=query_chain.vs, llm=query_chain.llm,
                course_id="course", scenario="all", as_of="2026-10-04")
    sync = ask(**args)
    events = list(ask_stream(**args))
    stream_data, sync_data = events[-1]["data"], sync.model_dump()
    if kind == "routing":
        assert stream_data["response_kind"] == "clarification"
        stream_data["task"].pop("id")
        sync_data["task"].pop("id")
    assert stream_data == sync_data
    assert save.call_count == 2
    assert save.call_args_list[0] == save.call_args_list[1]


def test_stream_emits_retrieving_before_running_retrieval(monkeypatch):
    from src.services import rag

    retrieval = MagicMock(return_value=[])
    monkeypatch.setattr(rag, "_retrieve", retrieval)
    llm = MagicMock(configured=True)
    llm.chat_with_tools.side_effect = [
        {"content": "", "tool_calls": [{"id": "rag", "name": "rag_answer", "arguments": '{"query":"卷积"}'}]},
        {"content": "<NO_EVIDENCE>", "tool_calls": []},
    ]
    events = ask_stream("卷积", "qa", MagicMock(), llm, "course")
    assert next(events)["phase"] == "routing"
    retrieval.assert_not_called()
    assert next(events)["phase"] == "reasoning"
    assert next(events)["phase"] == "retrieving"
    retrieval.assert_not_called()
    assert list(events)[-1]["type"] == "done"
    retrieval.assert_called_once()


def test_citations_keep_nullable_legacy_metadata_compatibility():
    citation = _citations([{"doc_id": None, "source_file": "old.md", "snippet": "正文",
                            "score": .8, "authority_level": None, "parse_quality": None}])[0]
    assert citation.doc_id == ""
    assert citation.authority_level == 30
    assert citation.parse_quality == 0

_ASK = {
    "question": "测试",
    "mode": "qa",
    "course_id": "course-default",
    "stream": True,
}


def _hits():
    return [
        {
            "text": "傅里叶变换定义…",
            "score": 0.8,
            "metadata": {"source_file": "ch1.md", "page": 1},
        }
    ]


def test_citation_exposes_document_structure_provenance():
    raw = generation._build_citations(
        [
            {
                "text": "表格中的采样频率定义",
                "score": 0.8,
                "metadata": {
                    "doc_id": "42",
                    "source_file": "signals.pdf",
                    "page": 12,
                    "chapter": "第二章 采样",
                    "section_path": "第二章 采样 / 2.1 定理",
                    "block_type": "table",
                    "bbox": "10.00,20.00,300.00,120.00",
                    "parser_name": "mineru:high",
                    "parse_quality": 0.91,
                },
            }
        ]
    )
    citation = _citations(raw)[0]
    assert citation.doc_id == "42"
    assert citation.section_path == "第二章 采样 / 2.1 定理"
    assert citation.bbox == "10.00,20.00,300.00,120.00"
    assert citation.parser_name == "mineru:high"
    assert citation.parse_quality == 0.91


@pytest.mark.parametrize("block_type", ["image_summary", "image"])
def test_visual_question_rechecks_the_original_chart(tmp_path, monkeypatch, block_type):
    image = tmp_path / "chart.png"
    image.write_bytes(b"image")

    class FakeVision:
        configured = True
        model = "test-vlm"

        def analyze_file(self, path, **kwargs):
            assert Path(path) == image
            assert "4000" in kwargs["prompt"]
            assert kwargs["caption"] == "图 5.3-1 反射率曲线"
            assert kwargs["page"] == 13
            return "4000 cm^-1 处约 28%，低于 40%。"

    monkeypatch.setattr("src.dependencies.get_vision_client", lambda: FakeVision())
    hits = [
        {
            "id": "50_10",
            "text": "图片说明：图 5.3-1 反射率曲线",
            "score": 0.8,
            "metadata": {
                "doc_id": "50",
                "block_type": block_type,
                "image_path": str(image),
                "image_caption": "图 5.3-1 反射率曲线",
                "page": 13,
            },
        }
    ]

    enriched = _verify_visual_evidence("波数为 4000 时反射率高于还是低于 40%？", hits)

    assert "针对当前问题的视觉复核" in enriched[0]["text"]
    assert "低于 40%" in enriched[0]["text"]
    assert hits[0]["text"] == "图片说明：图 5.3-1 反射率曲线"


def test_visual_question_appends_dedicated_chart_recall(monkeypatch):
    body = {
        "id": "50_9",
        "text": "5.3.3 检验测试结果",
        "score": 0.9,
        "metadata": {"doc_id": "50", "block_type": "text", "page": 13},
    }
    chart = {
        "id": "50_10",
        "text": "图片说明：图 5.3-1 反射率曲线",
        "score": 0.7,
        "metadata": {
            "doc_id": "50",
            "block_type": "image_summary",
            "page": 13,
        },
    }
    monkeypatch.setattr("src.services.rag.retrieve", lambda **_kwargs: [body])
    monkeypatch.setattr(
        "src.services.rag.retrieve_visual_evidence",
        lambda *_args, **_kwargs: [chart],
    )

    hits = _retrieve(
        "图 5.3-1 中 4000 cm^-1 的反射率高于还是低于 40%？",
        "qa",
        MagicMock(),
        "course-default",
    )

    assert [hit["id"] for hit in hits] == ["50_9", "50_10"]


class TestAskStream:
    @pytest.fixture(autouse=True)
    def isolated_vector_store(self, vector_store, tmp_path):
        # 不复用先前 API 测试可能已关闭或移除目录的存储客户端。
        from src.dependencies import get_vector_store, get_catalog_store, get_conversation_store
        from src.services.storage.catalog_store import CatalogStore
        from src.services.storage.conversation_store import ConversationStore
        catalog = CatalogStore(str(tmp_path / "catalog.db"))
        conversations = ConversationStore(str(tmp_path / "conversations.db"))
        overrides = {get_vector_store: lambda: vector_store,
                     get_catalog_store: lambda: catalog,
                     get_conversation_store: lambda: conversations}
        previous = {key: app.dependency_overrides.get(key) for key in overrides}
        app.dependency_overrides.update(overrides)
        try:
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    app.dependency_overrides.pop(key, None)
                else:
                    app.dependency_overrides[key] = value
            catalog.close()

    def test_refusal_emits_done(self):
        with patch("src.services.rag.retrieve", return_value=[]):
            with client.stream("POST", "/api/v1/ask", json=_ASK) as resp:
                assert resp.status_code == 200
                body = "".join(resp.iter_text())
                assert '"type": "done"' in body
                assert "未找到相关内容" in body

    def test_deltas_then_done(self, query_chain):
        app.dependency_overrides[get_llm_client] = lambda: query_chain.llm
        try:
            with client.stream("POST", "/api/v1/ask", json=_ASK) as resp:
                events = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]
            types = [event["type"] for event in events]
            assert "phase" in types and "delta" in types and "done" in types
        finally:
            app.dependency_overrides.pop(get_llm_client, None)

    def test_non_stream_json_refusal(self):
        with patch("src.services.rag.retrieve", return_value=[]):
            r = client.post("/api/v1/ask", json={**_ASK, "stream": False})
        assert r.status_code == 200
        assert r.json()["data"]["grounded"] is False

    def test_concept_mode_stream_accepted(self):
        with patch("src.services.rag.retrieve", return_value=[]):
            with client.stream(
                "POST",
                "/api/v1/ask",
                json={**_ASK, "mode": "concept", "question": "卷积定理"},
            ) as resp:
                assert resp.status_code == 200
                assert "未找到相关内容" in "".join(resp.iter_text())

    def test_question_generation_routes_without_retrieval_or_llm(self):
        with patch("src.services.rag.retrieve") as mock_retrieve:
            with client.stream(
                "POST",
                "/api/v1/ask",
                json={**_ASK, "question": "给我出几个期末的线代大题"},
            ) as resp:
                events = [
                    json.loads(line[6:])
                    for line in resp.iter_lines()
                    if line.startswith("data: ")
                ]
        mock_retrieve.assert_not_called()
        assert events[0]["phase"] == "routing"
        assert events[-1]["data"]["intent"]["task"] == "question_generate"
        assert "我的题库" in events[-1]["data"]["answer"]


class TestStreamGeneration:
    def test_reasoning_status_is_forwarded_before_content(self):
        llm = MagicMock()
        llm.chat_stream.return_value = iter(
            [
                {"type": "status", "status": "reasoning"},
                {"type": "content", "text": "答案"},
            ]
        )
        events = list(generation.stream_generate(_hits(), "测试", llm))
        assert events[0] == {"type": "phase", "phase": "reasoning"}
        assert events[1] == {"type": "delta", "text": "答案"}
        assert events[-1]["data"]["answer"] == "答案"

    def test_empty_stream_is_an_explicit_error(self):
        llm = MagicMock()
        llm.chat_stream.return_value = iter([])
        with pytest.raises(LLMAPIException, match="没有返回可显示"):
            list(generation.stream_generate(_hits(), "测试", llm))


@pytest.fixture
def query_chain(monkeypatch):
    """各用例共享准备过程，每次仍获得独立的返回数据和调用记录。"""
    from src.services import rag as query

    result = {"answer": "定义", "citations": [{"source_file": "a.md", "snippet": "证据", "score": .9}],
              "grounded": True}
    chain = SimpleNamespace(vs=MagicMock(), llm=MagicMock(configured=True), result=result,
                            retrieve=MagicMock(return_value=_hits()),
                            generate=MagicMock(return_value=result),
                            stream=MagicMock(side_effect=lambda **_: iter([
                                {"type": "delta", "text": result["answer"]}, {"type": "done", "data": result},
                            ])))
    chain.llm.chat_with_tools.side_effect = LLMAPIException("tools unsupported")
    for name, mock in (("retrieve", chain.retrieve), ("generate", chain.generate), ("stream_generate", chain.stream)):
        monkeypatch.setattr(query, name, mock)
    return chain


class TestAskModes:
    def test_auto_mode_routes_chapter_intent(self):
        vs = MagicMock()
        vs.get_by_chapter.return_value = []
        out = ask(
            "请概述第3章",
            mode="auto",
            vs=vs,
            llm=MagicMock(configured=True),
            course_id=DEFAULT_COURSE_ID,
        )
        assert out.intent is not None
        assert out.intent.mode == "chapter"
        assert out.intent.layer == "rule"
        vs.get_by_chapter.assert_called_once_with(DEFAULT_COURSE_ID, "请概述第3章")

    @pytest.mark.parametrize("mode,text,question,metadata,required", [
        ("concept", "卷积定理：时域卷积对应频域相乘", "卷积定理",
         {"source_file": "a.md"}, ["定义", "公式", "例题"]),
        ("chapter", "本章讲时移", "第3章",
         {"source_file": "a.md", "chapter": "第3章"}, ["知识清单", "重点", "自测"]),
    ])
    def test_mode_prompt_structure(self, mode, text, question, metadata, required):
        msgs = generation._build_messages([{"text": text, "metadata": metadata}], question, mode=mode)
        for term in required:
            assert term in msgs[0]["content"]

    def test_formula_evidence_is_explicitly_labeled_for_llm(self):
        msgs = generation._build_messages(
            [
                {
                    "text": r"公式：$$n=\sqrt{\varepsilon_r}$$",
                    "metadata": {"source_file": "paper.pdf", "block_type": "formula"},
                }
            ],
            "折射率与载流子浓度的关系是什么？",
            mode="qa",
        )

        assert "不得称其仍是图片" in msgs[0]["content"]
        assert "公式证据" in msgs[-1]["content"]
        assert r"n=\sqrt{\varepsilon_r}" in msgs[-1]["content"]

    def test_qa_prompt_requires_complete_numbered_lists(self):
        msgs = generation._build_messages(
            [
                {
                    "text": "共四个必要条件：(1)A。(2)B。(3)C。(4)D。",
                    "metadata": {"source_file": "paper.pdf", "block_type": "text"},
                }
            ],
            "有哪些必要条件？",
            mode="qa",
        )

        assert "共 N 项" in msgs[0]["content"]
        assert "去重后完整列出" in msgs[0]["content"]
        assert "不要在正文中输出【……】来源标签" in msgs[0]["content"]

    def test_concept_uses_larger_top_k(self, query_chain):
        out = ask("卷积定理", "concept", query_chain.vs, query_chain.llm, DEFAULT_COURSE_ID)
        assert query_chain.retrieve.call_args.kwargs["top_k"] == CONCEPT_TOP_K
        assert query_chain.stream.call_args.kwargs["mode"] == "concept"
        assert out.grounded is True

    def test_ask_stream_concept_passes_mode(self, query_chain):
        events = list(ask_stream("卷积", "concept", query_chain.vs, query_chain.llm, DEFAULT_COURSE_ID))
        assert query_chain.retrieve.call_args.kwargs["top_k"] == CONCEPT_TOP_K
        assert query_chain.stream.call_args.kwargs["mode"] == "concept"
        assert events[0]["type"] == "phase"
        assert events[-1]["type"] == "done"

    def test_chapter_skips_retrieve(self, query_chain):
        query_chain.vs.get_by_chapter.return_value = [
            {"id": "1", "text": "时移性质", "score": 0.0,
             "metadata": {"source_file": "a.md", "chapter": "第3章"}},
        ]
        out = ask("第3章", "chapter", query_chain.vs, query_chain.llm, DEFAULT_COURSE_ID)
        query_chain.retrieve.assert_not_called()
        query_chain.vs.get_by_chapter.assert_called_once_with(DEFAULT_COURSE_ID, "第3章")
        assert query_chain.stream.call_args.kwargs["mode"] == "chapter"
        assert out.grounded is True

    def test_chapter_refusal_when_missing(self):
        vs = MagicMock()
        vs.get_by_chapter.return_value = []
        out = ask(
            "不存在的章",
            mode="chapter",
            vs=vs,
            llm=MagicMock(configured=True),
            course_id="c",
        )
        assert out.grounded is False
        assert "未找到" in out.answer


class TestChapterStore:
    """chapter 聚合：精确 / 模糊匹配，防子串误伤。"""

    def test_get_by_chapter_exact_and_fuzzy(self, vector_store):
        base = {"doc_id": "1", "source_file": "a.md", "course": DEFAULT_COURSE_NAME,
                "course_id": DEFAULT_COURSE_ID, "college_id": DEFAULT_COLLEGE_ID}
        chunks = [
            {**base, "chunk_index": index, "text": text, "chapter": chapter}
            for index, text, chapter in [(0, "傅里叶定义", "第3章 傅里叶变换"), (1, "卷积", "第4章 卷积")]
        ]
        vector_store.upsert(chunks, [[0.1] * 8, [0.2] * 8])
        exact = vector_store.get_by_chapter(DEFAULT_COURSE_ID, "第3章 傅里叶变换")
        assert len(exact) == 1 and "傅里叶" in exact[0]["text"]
        fuzzy = vector_store.get_by_chapter(DEFAULT_COURSE_ID, "第3章")
        assert len(fuzzy) == 1

        vector_store.upsert(
            [{**base, "doc_id": "2", "source_file": "b.md", "chunk_index": 0,
              "text": "第九章正文", "chapter": "第9章 采样"}],
            [[0.3] * 8],
        )
        assert vector_store.get_by_chapter(DEFAULT_COURSE_ID, "第99章") == []
        assert len(vector_store.get_by_chapter(DEFAULT_COURSE_ID, "第9章")) == 1
        assert vector_store.list_chapters(DEFAULT_COURSE_ID) == [
            "第3章 傅里叶变换",
            "第4章 卷积",
            "第9章 采样",
        ]

@pytest.fixture
def conv_store(tmp_path):
    from src.services.storage.conversation_store import ConversationStore

    store = ConversationStore(str(tmp_path / "test_conv.db"))
    return store


class TestSaveTurn:
    def test_save_turn_persists_with_course_id(self, conv_store):
        from src.services.query import _save_turn

        ok = _save_turn(conv_store, "conv-1", "course-1", "问题", "回答", [], True, "qa")
        assert ok is True
        assert conv_store.get_conversation("conv-1")["course_id"] == "course-1"
        roles = [m["role"] for m in conv_store.get_history("conv-1")]
        assert roles == ["user", "assistant"]

    def test_save_turn_returns_false_on_failure(self):
        from src.services.query import _save_turn

        store = MagicMock()
        store.append_message.side_effect = RuntimeError("db down")
        assert (
            _save_turn(store, "conv-1", "course-1", "问题", "回答", [], True, "qa")
            is False
        )

    def test_ask_returns_answer_when_save_fails(self, query_chain):
        store = MagicMock()
        store.append_message.side_effect = RuntimeError("db down")
        out = ask("卷积", "qa", query_chain.vs, query_chain.llm, DEFAULT_COURSE_ID,
                  conversation_store=store, conversation_id="conv-1")
        assert out.answer == query_chain.result["answer"]
