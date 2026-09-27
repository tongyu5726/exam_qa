from __future__ import annotations

import os
from src.config import config
from src.services.parsing_adapters.models import ParsedDocument, ParsedPage


def _page_num(meta: dict) -> int | None:
    raw = meta.get("page_number") or meta.get("page")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _pages_from_pymupdf4llm(raw) -> ParsedDocument | None:
    if isinstance(raw, str):
        text = raw.strip()
        return ParsedDocument([ParsedPage(page=None, text=text)]) if text else None
    pages = []
    for chunk in raw:
        text = (chunk.get("text") or "").strip()
        if text:
            pages.append(ParsedPage(page=_page_num(chunk.get("metadata") or {}), text=text))
    return ParsedDocument(pages) if pages else None


def _pymupdf4llm_kwargs(*, force_ocr: bool | None = None) -> dict:
    p = config.parsing
    return {
        "page_chunks": True,
        "use_ocr": p.pdf_use_ocr,
        "force_ocr": p.pdf_force_ocr if force_ocr is None else force_ocr,
        "ocr_language": p.pdf_ocr_language,
    }


def _parse_pdf_pymupdf4llm(path: str, *, force_ocr: bool | None = None) -> ParsedDocument | None:
    import pymupdf4llm

    # pymupdf4llm 调用 Tesseract 时从进程环境读取 TESSDATA_PREFIX。
    # 配置为空不覆盖用户/系统已有设置。
    tessdata_prefix = config.parsing.tessdata_prefix
    if tessdata_prefix:
        os.environ["TESSDATA_PREFIX"] = tessdata_prefix
    return _pages_from_pymupdf4llm(
        pymupdf4llm.to_markdown(path, **_pymupdf4llm_kwargs(force_ocr=force_ocr))
    )


def _parse_pdf_fitz(path: str) -> ParsedDocument:
    import fitz

    doc = fitz.open(path)
    try:
        pages = []
        for i, page in enumerate(doc, 1):
            text = page.get_text().strip()
            if not text:
                continue
            try:
                label = page.get_label() or ""
            except Exception:
                label = ""
            pages.append(ParsedPage(page=i, text=text, pdf_page_label=label))
        return ParsedDocument(pages)
    finally:
        doc.close()
