"""真实隔离存储回读测试；视觉 API 使用离线替身。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.config import config
from src.exceptions import BadRequestException, LLMAPIException
from src.services.ingestion import _chunk_structured, ingest_file
from src.services.parsing import ParsedBlock, ParsedDocument
from src.services.query import _needs_visual_verification, _verify_visual_evidence
from src.services.visual_coverage import ensure_pdf_visual_coverage
from src.services.vision import VisionClient


def test_summary_context_storage_and_query(vector_store, tmp_path, monkeypatch):
    image = tmp_path / "chart.png"
    image.write_bytes(b"offline")
    calls = []
    class Vision:
        configured = True
        model = "offline"
        def analyze_file(self, path, **kwargs):
            calls.append(str(path))
            return "4000 cm^-1处约28%，低于40%。"
    monkeypatch.setattr(config.parsing, "visual_model", "offline")
    monkeypatch.setattr(config.parsing, "visual_required", True)
    monkeypatch.setattr("src.dependencies.get_vision_client", lambda: Vision())
    document = ParsedDocument([], [
        ParsedBlock("title", "5.3 检验", 13, level=1),
        ParsedBlock("text", "入射角10度", 13),
        ParsedBlock("image", "", 13, image_path=str(image), caption="图5.3-1"),
        ParsedBlock("text", "反射率随波数变化", 13),
        ParsedBlock("title", "另一章节", 13, level=1),
        ParsedBlock("text", "不能混入", 13),
    ])
    chunks = _chunk_structured(document, chunk_size=800, chunk_overlap=20)
    visual = next(c for c in chunks if c["block_type"] == "image_summary")
    assert "入射角" in visual["context"] and "随波数" in visual["text"]
    assert "不能混入" not in visual["text"]
    for i, c in enumerate(chunks):
        c.update(doc_id="1", source_file="paper.pdf", course_id="test", course="test",
                 college_id="test", chunk_index=i)
    vector_store.upsert(chunks, [[1., 0., 0., 0.]] * len(chunks))
    hits = vector_store.get_chunks(course_id="test", block_type="image_summary")
    for rows in (hits, vector_store.search([1., 0., 0., 0.], course_id="test", block_type="image_summary")):
        assert rows[0]["metadata"]["image_path"] == str(image)
        assert rows[0]["metadata"]["image_caption"] == "图5.3-1"
        assert _needs_visual_verification("图5.3-1反射率", rows)
    assert "视觉复核" in _verify_visual_evidence("4000处反射率", hits)[0]["text"]
    assert len(calls) == 2
    assert vector_store.get_chunks(course_id="other") == []


def test_missing_summary_blocks_success(monkeypatch):
    monkeypatch.setattr(config.parsing, "visual_required", True)
    monkeypatch.setattr("src.services.parsing_adapters.enrichment.vision._summarize_image", lambda *a: None)
    with pytest.raises(BadRequestException, match="未完成 VLM"):
        _chunk_structured(ParsedDocument([], [ParsedBlock("image", "", 1)]), chunk_size=800, chunk_overlap=20)


def test_every_image_and_uncaptioned_image_gets_summary(monkeypatch):
    calls = []
    def summarize(path, caption, page, context):
        calls.append(path)
        return "视觉事实"
    monkeypatch.setattr("src.services.parsing_adapters.enrichment.vision._summarize_image", summarize)
    rows = _chunk_structured(ParsedDocument([], [ParsedBlock("image", "", 1, image_path=str(i), content_role="annotation") for i in range(3)]),
                             chunk_size=800, chunk_overlap=20)
    assert calls == ["0", "1", "2"]
    assert all(c["block_type"] == "image_summary" for c in rows)


def test_uncaptioned_vector_page_is_covered_once(tmp_path, monkeypatch):
    import fitz
    monkeypatch.setattr(config.storage, "parsed_assets_dir", str(tmp_path / "assets"))
    source = tmp_path / "chart.pdf"
    with fitz.open() as pdf:
        p = pdf.new_page()
        p.draw_line((10, 10), (200, 100))
        pdf.new_page()
        pdf.save(source)
    document = ParsedDocument([])
    assert ensure_pdf_visual_coverage(document, str(source)) == 1
    assert ensure_pdf_visual_coverage(document, str(source)) == 0
    assert document.blocks[0].page == 1
    assert Path(document.blocks[0].image_path).is_file()


def test_truncated_summary_rejected():
    vision = VisionClient(api_key="offline", base_url="http://unused", model="offline", timeout=1)
    vision._client = Mock()
    vision._client.chat.completions.create.return_value = SimpleNamespace(choices=[
        SimpleNamespace(finish_reason="length", message=SimpleNamespace(content="片段"))])
    with pytest.raises(LLMAPIException, match="截断"):
        vision.analyze_bytes(b"offline", mime="image/png")


def test_visual_failure_keeps_old_index(tmp_path, vector_store, doc_store, monkeypatch):
    path = tmp_path / "notes.md"
    path.write_text("original", encoding="utf-8")
    old = doc_store.create(filename=path.name, file_path=str(path.resolve()), course="test", course_id="test")
    doc_store.update_status(old, "done", chunk_count=1)
    vector_store.upsert([dict(doc_id=str(old), source_file=path.name, course_id="test", course="test",
                             chunk_index=0, text="old evidence")], [[1., 0., 0., 0.]])
    monkeypatch.setattr(config.parsing, "visual_required", True)
    monkeypatch.setattr("src.services.parsing_adapters.enrichment.vision._summarize_image", lambda *a: None)
    with pytest.raises(BadRequestException):
        ingest_file(str(path), vector_store, doc_store, course_id="test",
                    parsed_document=ParsedDocument([], [ParsedBlock("image", "", 1)]))
    assert doc_store.get(old)["status"] == "done"
    assert vector_store.get_chunks(course_id="test")[0]["text"] == "old evidence"
