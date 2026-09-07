"""入库主链：分块、扫描、章节、acquire/fail、跨课拒绝。"""

import pytest

from src.exceptions import BadRequestException
from src.services.ingestion import (
    _acquire_doc_id,
    _chunk_structured,
    _fail_ingest,
    _needs_reindex,
    _split_text,
    assign_chapters,
    scan_knowledge_dir,
)
from src.services.parsing import ParsedBlock, ParsedDocument
from src.services.storage.catalog_store import DEFAULT_COURSE_ID, DEFAULT_COURSE_NAME

_COURSE = DEFAULT_COURSE_NAME
_CID = DEFAULT_COURSE_ID


def _vec_chunk(doc_id: int, text: str) -> dict:
    return {
        "doc_id": str(doc_id),
        "source_file": "x.md",
        "chunk_index": 0,
        "course": _COURSE,
        "course_id": _CID,
        "college_id": "college-default",
        "text": text,
    }


class TestSplitText:
    def test_short_and_empty(self):
        assert _split_text("短文本", chunk_size=500, chunk_overlap=50) == ["短文本"]
        assert _split_text("") == []
        assert _split_text("   \n\n  ") == []

    def test_split_and_overlap(self):
        text = "信号" * 200 + "\n\n" + "系统" * 200
        chunks = _split_text(text, chunk_size=100, chunk_overlap=20)
        assert len(chunks) >= 2
        assert chunks[0][-20:] in chunks[1]

    def test_latex_delimiters_are_not_split(self):
        text = r"说明。\[\lim_{n\to\infty} a_n = 1\]结论。"
        chunks = _split_text(text, chunk_size=8, chunk_overlap=0)
        assert "".join(chunks) == text
        assert sum(r"\[\lim_{n\to\infty} a_n = 1\]" in chunk for chunk in chunks) == 1


class TestChunkStructured:
    """MinerU 结构化分块：层级保留、表格独立、metadata 丰富。"""

    def _parsed(self):
        blocks = [
            ParsedBlock(block_type="header", text="信号与系统", page=1),
            ParsedBlock(block_type="title", text="第一章 绪论", page=1, level=1, bbox=(10, 10, 200, 30)),
            ParsedBlock(block_type="title", text="1.1 通信系统模型", page=1, level=2, bbox=(10, 40, 200, 60)),
            ParsedBlock(
                block_type="text",
                text="通信系统由信源、信道和信宿组成。信源产生消息。",
                page=1,
                bbox=(10, 70, 500, 150),
            ),
            ParsedBlock(
                block_type="table",
                text="名称 | 说明\n信源 | 信息发起点",
                page=2,
                html=(
                    "<table><tr><th>名称</th><th>说明</th></tr>"
                    "<tr><td>信源</td><td>信息发起点</td></tr></table>"
                ),
                bbox=(20, 30, 520, 200),
            ),
            ParsedBlock(
                block_type="image",
                text="",
                page=2,
                caption="图1-1 通信系统框图",
            ),
            ParsedBlock(block_type="formula", text="$$C = B\\log_2(1+SNR)$$", page=2),
        ]
        return ParsedDocument(pages=[], blocks=blocks)

    def test_section_path_and_context(self):
        chunks = _chunk_structured(
            self._parsed(), chunk_size=200, chunk_overlap=20
        )
        assert chunks, "应有切片输出"
        text_chunk = next(c for c in chunks if "信源、信道" in c["text"])
        assert text_chunk["section_path"] == "第一章 绪论 / 1.1 通信系统模型"
        assert text_chunk["chapter"] == "1.1 通信系统模型"
        assert "§ 1.1 通信系统模型" in text_chunk["text"]
        assert text_chunk["block_type"] == "text"
        assert "信源、信道和信宿" in text_chunk["text"]
        assert text_chunk["bbox"] == "10.00,40.00,500.00,150.00"

    def test_table_standalone_with_headers(self):
        chunks = _chunk_structured(
            self._parsed(), chunk_size=200, chunk_overlap=20
        )
        table_chunk = next(c for c in chunks if c["block_type"] == "table")
        assert table_chunk["table_headers"] == "名称 | 说明"
        assert "信源" in table_chunk["text"]
        assert table_chunk["page"] == 2
        assert table_chunk["chapter"] == "1.1 通信系统模型"
        assert table_chunk["bbox"] == "20.00,30.00,520.00,200.00"

    def test_image_without_summary_falls_back_to_caption(self):
        chunks = _chunk_structured(
            self._parsed(), chunk_size=200, chunk_overlap=20
        )
        image_chunk = next(c for c in chunks if c["block_type"] == "image")
        assert "图片说明：图1-1 通信系统框图" in image_chunk["text"]

    def test_header_footer_excluded_from_body(self):
        chunks = _chunk_structured(
            self._parsed(), chunk_size=200, chunk_overlap=20
        )
        assert all("信号与系统" not in c["text"] for c in chunks)

    def test_formula_is_standalone_with_section_and_nearby_chinese_context(self):
        parsed = ParsedDocument(
            pages=[],
            blocks=[
                ParsedBlock(block_type="title", text="5.1.3 折射率与载流子浓度的关系", page=5, level=2),
                ParsedBlock(
                    block_type="text",
                    text="折射率与介电常数的关系可表示为：",
                    page=5,
                ),
                ParsedBlock(
                    block_type="formula",
                    text=r"$$n\approx\sqrt{\varepsilon_r}$$",
                    latex=r"n\approx\sqrt{\varepsilon_r}",
                    page=5,
                ),
            ],
        )

        chunks = _chunk_structured(parsed, chunk_size=200, chunk_overlap=0)

        formula = next(c for c in chunks if c["block_type"] == "formula")
        body = next(c for c in chunks if c["block_type"] == "text")
        assert formula["section_path"] == "5.1.3 折射率与载流子浓度的关系"
        assert "折射率与介电常数的关系可表示为" in formula["context"]
        assert "公式上下文：" in formula["text"]
        assert r"$$n\approx\sqrt{\varepsilon_r}$$" in formula["text"]
        assert r"n\approx\sqrt{\varepsilon_r}" not in body["text"]

    def test_formula_infers_numbered_section_from_fallback_page_text(self):
        parsed = ParsedDocument(
            pages=[],
            blocks=[
                ParsedBlock(
                    block_type="text",
                    text="## 5.1.3 折射率与载流子浓度的关系\n折射率与介电常数的关系可表示为：",
                    page=5,
                ),
                ParsedBlock(block_type="formula", text=r"$$\varepsilon_r=n^2$$", page=5),
            ],
        )

        chunks = _chunk_structured(parsed, chunk_size=300, chunk_overlap=0)

        formula = next(c for c in chunks if c["block_type"] == "formula")
        assert formula["section_path"] == "5.1.3 折射率与载流子浓度的关系"
        assert "折射率与介电常数的关系可表示为" in formula["context"]

    def test_annotation_reference_kept_but_ui_noise_skipped(self):
        parsed = ParsedDocument(
            pages=[],
            blocks=[
                ParsedBlock(block_type="text", text="P28 Ex2", page=1, textbook_references=("P28 Ex2",), content_role="reference"),
                ParsedBlock(block_type="text", text="考点：夹逼准则", page=1, content_role="annotation"),
                ParsedBlock(block_type="text", text="10:29", page=1, content_role="noise"),
            ],
        )
        chunks = _chunk_structured(parsed, chunk_size=200, chunk_overlap=0)
        reference = next(c for c in chunks if c["content_role"] == "reference")
        annotation = next(c for c in chunks if c["content_role"] == "annotation")

        assert reference["textbook_references"] == "P28 Ex2"
        assert annotation["text"] == "考点：夹逼准则"
        assert all("10:29" not in c["text"] for c in chunks)


class TestScanAndChapter:
    def test_needs_reindex_rules(self):
        assert _needs_reindex(None, 100.0) is True
        assert _needs_reindex({"status": "done", "file_mtime": 100.0}, 100.0) is False
        assert _needs_reindex({"status": "done", "file_mtime": 100.0}, 200.0) is True
        assert _needs_reindex({"status": "failed", "file_mtime": 100.0}, 100.0) is True
        assert (
            _needs_reindex({"status": "processing", "file_mtime": None}, 100.0) is False
        )

    def test_scan_force_reindexes_done(
        self, temp_dir, vector_store, doc_store, monkeypatch
    ):
        knowledge = temp_dir / "knowledge"
        knowledge.mkdir()
        monkeypatch.setattr(
            "src.services.ingestion.config.storage.knowledge_dir", str(knowledge)
        )
        md = knowledge / "note.md"
        md.write_text("hello", encoding="utf-8")
        doc_id = doc_store.create(
            "note.md", str(md.resolve()), course=_COURSE, course_id=_CID
        )
        doc_store.update_status(doc_id, "done", chunk_count=1)
        doc_store.update_file_mtime(doc_id, md.stat().st_mtime)

        called: list[str] = []

        def fake_ingest(**kwargs):
            called.append(kwargs["path"])
            return str(doc_id)

        monkeypatch.setattr("src.services.ingestion.ingest_file", fake_ingest)

        scan_knowledge_dir(vector_store, doc_store, force=False)
        assert called == []
        scan_knowledge_dir(vector_store, doc_store, force=True)
        assert len(called) == 1 and called[0].endswith("note.md")

    def test_assign_chapters_header_and_page_fallback(self):
        text = "## 第3章 傅里叶\n\n定义。\n\n## 第4章 卷积\n\n卷积。\n"
        chapters = assign_chapters(text, ["定义。", "卷积。"])
        assert "第3章" in chapters[0] and "第4章" in chapters[1]
        assert assign_chapters("无标题", ["无标题"], pages=[2]) == ["第2页"]


class TestIngestHelpers:
    def test_acquire_reuses_failed(self, doc_store, vector_store, temp_dir):
        path = temp_dir / "retry.md"
        path.write_text("# t\nbody", encoding="utf-8")
        resolved = str(path.resolve())
        doc_id = doc_store.create("retry.md", resolved, course=_COURSE, course_id=_CID)
        doc_store.update_status(doc_id, "failed")
        reused = _acquire_doc_id(
            doc_store, vector_store, path, "retry.md", _COURSE, _CID
        )
        assert reused == doc_id
        assert doc_store.get(doc_id)["status"] == "processing"

    def test_acquire_reuses_done_clears_vectors(self, doc_store, vector_store, temp_dir):
        path = temp_dir / "done.md"
        path.write_text("x", encoding="utf-8")
        resolved = str(path.resolve())
        doc_id = doc_store.create("done.md", resolved, course=_COURSE, course_id=_CID)
        doc_store.update_status(doc_id, "done", chunk_count=3)
        vector_store.upsert([_vec_chunk(doc_id, "old")], [[0.2] * 8])
        reused = _acquire_doc_id(
            doc_store, vector_store, path, "done.md", _COURSE, _CID
        )
        assert reused == doc_id
        assert vector_store.search([0.2] * 8, top_k=5, course_id=_CID) == []

    def test_fail_ingest_cleans_vectors(self, doc_store, vector_store, temp_dir):
        path = temp_dir / "fail.md"
        path.write_text("content", encoding="utf-8")
        doc_id = doc_store.create(
            "fail.md", str(path.resolve()), course=_COURSE, course_id=_CID
        )
        doc_store.update_status(doc_id, "processing")
        vector_store.upsert([_vec_chunk(doc_id, "content")], [[0.1] * 8])
        _fail_ingest(vector_store, doc_store, doc_id)
        assert doc_store.get(doc_id)["status"] == "failed"
        assert vector_store.search([0.1] * 8, top_k=5, course_id=_CID) == []

    def test_acquire_rejects_cross_course(self, doc_store, vector_store, temp_dir):
        path = temp_dir / "move.md"
        path.write_text("x", encoding="utf-8")
        resolved = str(path.resolve())
        doc_id = doc_store.create("move.md", resolved, course=_COURSE, course_id=_CID)
        doc_store.update_status(doc_id, "done", chunk_count=1)
        with pytest.raises(BadRequestException):
            _acquire_doc_id(
                doc_store,
                vector_store,
                path,
                "move.md",
                "思政原理",
                "course-ideology-2025",
            )
        assert doc_store.get(doc_id)["course_id"] == _CID


class TestDocStoreIsolation:
    def test_list_by_course_id(self, doc_store):
        doc_store.create("a.pdf", "/tmp/a.pdf", course_id=_CID)
        doc_store.create(
            "b.pdf",
            "/tmp/b.pdf",
            course="思政原理",
            course_id="course-ideology-2025",
        )
        docs = doc_store.list(course_id=_CID)
        assert len(docs) == 1
        assert docs[0]["filename"] == "a.pdf"

    def test_create_update_delete(self, doc_store):
        doc_id = doc_store.create(
            "test.md", "/tmp/test.md", course=_COURSE, course_id=_CID
        )
        doc_store.update_status(doc_id, "done", chunk_count=3)
        assert doc_store.get(doc_id)["chunk_count"] == 3
        doc_store.delete(doc_id)
        assert doc_store.get(doc_id) is None


class TestVectorStoreGuard:
    def test_upsert_requires_course_id(self, vector_store):
        with pytest.raises(ValueError, match="course_id"):
            vector_store.upsert(
                [
                    {
                        "doc_id": "1",
                        "source_file": "a.md",
                        "chunk_index": 0,
                        "course": _COURSE,
                        "text": "no course_id",
                    }
                ],
                [[0.1] * 8],
            )

    def test_group_by_chapter_filters_pages_and_groups(self, vector_store, monkeypatch):
        monkeypatch.setattr(
            vector_store,
            "get_by_course_id",
            lambda course_id: [
                {"metadata": {"chapter": "第2章 熵", "source_file": "a.md"}},
                {"metadata": {"chapter": "第2章 熵", "source_file": "b.pdf"}},
                {"metadata": {"chapter": "第5页", "source_file": "a.md"}},
                {"metadata": {"chapter": "", "source_file": "a.md"}},
                {"metadata": {"chapter": "第1章 信号", "source_file": "a.md"}},
            ],
        )
        groups = vector_store.group_by_chapter(course_id=_CID)
        assert [g["chapter"] for g in groups] == ["第1章 信号", "第2章 熵"]
        assert groups[1]["chunk_count"] == 2
        assert groups[1]["source_files"] == ["a.md", "b.pdf"]


    def test_delete_by_doc_id(self, vector_store):
        vector_store.upsert([_vec_chunk(42, "chunk a")], [[0.1] * 8])
        vector_store.delete_by_doc_id("42")
        assert vector_store.search([0.1] * 8, top_k=5, course_id=_CID) == []
