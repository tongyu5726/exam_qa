"""对话主链：流式 / qa|concept|chapter / 拒答 / prompt。"""

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fastapi.testclient import TestClient

from src.dependencies import get_llm_client
from src.main import app
from src.services import generation
from src.exceptions import LLMAPIException
from src.services.query import CONCEPT_TOP_K, _citations, ask, ask_stream
from src.services.storage.catalog_store import (
    DEFAULT_COLLEGE_ID,
    DEFAULT_COURSE_ID,
    DEFAULT_COURSE_NAME,
)

client = TestClient(app)

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


class TestAskStream:
    def test_refusal_emits_done(self):
        with patch("src.services.query.retrieve", return_value=[]):
            with client.stream("POST", "/api/v1/ask", json=_ASK) as resp:
                assert resp.status_code == 200
                body = "".join(resp.iter_text())
                assert '"type": "done"' in body
                assert "未找到相关内容" in body

    def test_deltas_then_done(self):
        def fake_stream(*_a, **_k):
            yield {"type": "delta", "text": "你好"}
            yield {
                "type": "done",
                "data": {
                    "answer": "你好",
                    "citations": [
                        {
                            "source_file": "ch1.md",
                            "page": 1,
                            "snippet": "片段",
                            "score": 0.8,
                        }
                    ],
                    "grounded": True,
                },
            }

        mock_llm = MagicMock()
        mock_llm.configured = True
        app.dependency_overrides[get_llm_client] = lambda: mock_llm
        try:
            with patch("src.services.query.retrieve", return_value=_hits()):
                with patch(
                    "src.services.query.stream_generate", side_effect=fake_stream
                ):
                    with client.stream("POST", "/api/v1/ask", json=_ASK) as resp:
                        events = [
                            json.loads(line[6:])
                            for line in resp.iter_lines()
                            if line.startswith("data: ")
                        ]
            types = [e["type"] for e in events]
            assert "phase" in types and "delta" in types and "done" in types
        finally:
            app.dependency_overrides.pop(get_llm_client, None)

    def test_non_stream_json_refusal(self):
        with patch("src.services.query.retrieve", return_value=[]):
            r = client.post("/api/v1/ask", json={**_ASK, "stream": False})
        assert r.status_code == 200
        assert r.json()["data"]["grounded"] is False

    def test_concept_mode_stream_accepted(self):
        with patch("src.services.query.retrieve", return_value=[]):
            with client.stream(
                "POST",
                "/api/v1/ask",
                json={**_ASK, "mode": "concept", "question": "卷积定理"},
            ) as resp:
                assert resp.status_code == 200
                assert "未找到相关内容" in "".join(resp.iter_text())

    def test_question_generation_routes_without_retrieval_or_llm(self):
        with patch("src.services.query.retrieve") as mock_retrieve:
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

    def test_concept_prompt_structure(self):
        msgs = generation._build_messages(
            [
                {
                    "text": "卷积定理：时域卷积对应频域相乘",
                    "metadata": {"source_file": "a.md"},
                }
            ],
            "卷积定理",
            mode="concept",
        )
        assert "定义" in msgs[0]["content"]
        assert "公式" in msgs[0]["content"]
        assert "例题" in msgs[0]["content"]

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

    def test_chapter_prompt_structure(self):
        msgs = generation._build_messages(
            [
                {
                    "text": "本章讲时移",
                    "metadata": {"source_file": "a.md", "chapter": "第3章"},
                }
            ],
            "第3章",
            mode="chapter",
        )
        assert "知识清单" in msgs[0]["content"]
        assert "重点" in msgs[0]["content"]
        assert "自测" in msgs[0]["content"]

    def test_concept_uses_larger_top_k(self):
        llm = MagicMock()
        llm.configured = True
        hits = [{"text": "定义…", "score": 0.9, "metadata": {"source_file": "a.md"}}]
        with patch("src.services.query.retrieve", return_value=hits) as mock_ret:
            with patch(
                "src.services.query.generate",
                return_value={
                    "answer": "定义…",
                    "citations": [
                        {
                            "source_file": "a.md",
                            "page": None,
                            "snippet": "定义",
                            "score": 0.9,
                        }
                    ],
                    "grounded": True,
                },
            ) as mock_gen:
                out = ask(
                    "卷积定理",
                    mode="concept",
                    vs=MagicMock(),
                    llm=llm,
                    course_id=DEFAULT_COURSE_ID,
                )
        assert mock_ret.call_args.kwargs["top_k"] == CONCEPT_TOP_K
        assert mock_gen.call_args.kwargs["mode"] == "concept"
        assert out.grounded is True

    def test_ask_stream_concept_passes_mode(self):
        hits = [{"text": "x", "score": 0.9, "metadata": {"source_file": "a.md"}}]
        llm = MagicMock()
        llm.configured = True

        def _fake_stream(**kwargs):
            assert kwargs["mode"] == "concept"
            yield {"type": "delta", "text": "定"}
            yield {
                "type": "done",
                "data": {
                    "answer": "定义",
                    "citations": [
                        {
                            "source_file": "a.md",
                            "page": None,
                            "snippet": "x",
                            "score": 0.9,
                        }
                    ],
                    "grounded": True,
                },
            }

        with patch("src.services.query.retrieve", return_value=hits) as mock_ret:
            with patch("src.services.query.stream_generate", side_effect=_fake_stream):
                events = list(
                    ask_stream(
                        "卷积",
                        mode="concept",
                        vs=MagicMock(),
                        llm=llm,
                        course_id=DEFAULT_COURSE_ID,
                    )
                )
        assert mock_ret.call_args.kwargs["top_k"] == CONCEPT_TOP_K
        assert events[0]["type"] == "phase"
        assert events[-1]["type"] == "done"

    def test_chapter_skips_retrieve(self):
        vs = MagicMock()
        vs.get_by_chapter.return_value = [
            {
                "id": "1",
                "text": "时移性质",
                "score": 0.0,
                "metadata": {"source_file": "a.md", "chapter": "第3章"},
            }
        ]
        llm = MagicMock()
        llm.configured = True
        with patch("src.services.query.retrieve") as mock_ret:
            with patch(
                "src.services.query.generate",
                return_value={
                    "answer": "清单…",
                    "citations": [
                        {
                            "source_file": "a.md",
                            "page": None,
                            "snippet": "时移",
                            "score": 1.0,
                        }
                    ],
                    "grounded": True,
                },
            ) as mock_gen:
                out = ask(
                    "第3章",
                    mode="chapter",
                    vs=vs,
                    llm=llm,
                    course_id=DEFAULT_COURSE_ID,
                )
        mock_ret.assert_not_called()
        vs.get_by_chapter.assert_called_once_with(DEFAULT_COURSE_ID, "第3章")
        assert mock_gen.call_args.kwargs["mode"] == "chapter"
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
        chunks = [
            {
                "doc_id": "1",
                "source_file": "a.md",
                "chunk_index": 0,
                "course": DEFAULT_COURSE_NAME,
                "course_id": DEFAULT_COURSE_ID,
                "college_id": DEFAULT_COLLEGE_ID,
                "text": "傅里叶定义",
                "chapter": "第3章 傅里叶变换",
            },
            {
                "doc_id": "1",
                "source_file": "a.md",
                "chunk_index": 1,
                "course": DEFAULT_COURSE_NAME,
                "course_id": DEFAULT_COURSE_ID,
                "college_id": DEFAULT_COLLEGE_ID,
                "text": "卷积",
                "chapter": "第4章 卷积",
            },
        ]
        vector_store.upsert(chunks, [[0.1] * 8, [0.2] * 8])
        exact = vector_store.get_by_chapter(DEFAULT_COURSE_ID, "第3章 傅里叶变换")
        assert len(exact) == 1 and "傅里叶" in exact[0]["text"]
        fuzzy = vector_store.get_by_chapter(DEFAULT_COURSE_ID, "第3章")
        assert len(fuzzy) == 1

        vector_store.upsert(
            [
                {
                    "doc_id": "2",
                    "source_file": "b.md",
                    "chunk_index": 0,
                    "course": DEFAULT_COURSE_NAME,
                    "course_id": DEFAULT_COURSE_ID,
                    "college_id": DEFAULT_COLLEGE_ID,
                    "text": "第九章正文",
                    "chapter": "第9章 采样",
                }
            ],
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
def conv_store():
    from src.services.storage.conversation_store import ConversationStore

    tmp = tempfile.mkdtemp()
    store = ConversationStore(str(Path(tmp) / "test_conv.db"))
    yield store
    store.close()
    shutil.rmtree(tmp, ignore_errors=True)


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

    def test_ask_returns_answer_when_save_fails(self):
        llm = MagicMock()
        llm.configured = True
        hits = [{"text": "定义…", "score": 0.9, "metadata": {"source_file": "a.md"}}]
        store = MagicMock()
        store.append_message.side_effect = RuntimeError("db down")
        with patch("src.services.query.retrieve", return_value=hits):
            with patch(
                "src.services.query.generate",
                return_value={
                    "answer": "定义…",
                    "citations": [
                        {
                            "source_file": "a.md",
                            "page": None,
                            "snippet": "x",
                            "score": 0.9,
                        }
                    ],
                    "grounded": True,
                },
            ):
                out = ask(
                    "卷积",
                    mode="qa",
                    vs=MagicMock(),
                    llm=llm,
                    course_id=DEFAULT_COURSE_ID,
                    conversation_store=store,
                    conversation_id="conv-1",
                )
        assert out.answer == "定义…"
