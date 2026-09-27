from __future__ import annotations

import logging
import re
from src.services.parsing_adapters.models import PDFParseQuality, ParsedDocument

logger = logging.getLogger(__name__)


def _pdf_page_count(path: str) -> int:
    """读取物理页数；失败时返回 0，质量评估仍可继续。"""
    try:
        import fitz

        doc = fitz.open(path)
        try:
            return doc.page_count
        finally:
            doc.close()
    except Exception:
        return 0


def _assess_pdf_quality(
    doc: ParsedDocument | None, *, expected_pages: int = 0
) -> PDFParseQuality:
    """以文本密度、页覆盖与结构还原评估候选结果，范围固定为 0～1。"""
    if doc is None:
        return PDFParseQuality(0.0, 0.0, 0.0, 0.0, expected_pages, 0)

    pages_with_text = [page for page in doc.pages if page.text.strip()]
    chars = len(re.sub(r"\s", "", doc.full_text))
    parsed_pages = len(pages_with_text)
    # Markdown 回退只有一页 None，不能把它误当作仅覆盖原 PDF 的第一页。
    if expected_pages and any(page.page is None for page in pages_with_text) and chars >= 80:
        page_coverage = 1.0
    else:
        denominator = expected_pages or max(len(doc.pages), 1)
        page_coverage = min(parsed_pages / denominator, 1.0)
    chars_per_page = chars / max(expected_pages or parsed_pages, 1)
    text_density = min(chars_per_page / 160, 1.0)
    structure_ratio = 1.0 if doc.blocks else (0.65 if chars else 0.0)
    score = round(0.45 * text_density + 0.40 * page_coverage + 0.15 * structure_ratio, 4)
    return PDFParseQuality(
        score=score,
        page_coverage=round(page_coverage, 4),
        chars_per_page=round(chars_per_page, 2),
        structure_ratio=structure_ratio,
        expected_pages=expected_pages,
        parsed_pages=parsed_pages,
    )


def _log_pdf_quality(source: str, quality: PDFParseQuality) -> None:
    logger.info(
        "PDF 候选质量: %s score=%.2f coverage=%.0f%% chars/page=%.0f structure=%.0f%%",
        source,
        quality.score,
        quality.page_coverage * 100,
        quality.chars_per_page,
        quality.structure_ratio * 100,
    )


def _pdf_kind(path: str) -> str:
    """抽样判断 PDF 类型：text（原生文本）| scanned（扫描件/图片型）| mixed。

    原生文本 PDF 直接提取文本；扫描件/混合型交给 OCR / MinerU。
    采样首页、次页、中间页、末页，避免只按首字符数误判。
    """
    try:
        import fitz

        doc = fitz.open(path)
        try:
            n = doc.page_count
            if n == 0:
                return "scanned"
            total = 0
            sampled = 0
            for i in sorted({0, 1, n // 2, n - 1}):
                if i >= n:
                    continue
                total += len(re.sub(r"\s", "", doc[i].get_text() or ""))
                sampled += 1
            if sampled == 0 or total < 30:
                return "scanned"
            avg = total / sampled
            if avg < 30:
                return "scanned"
            return "mixed" if avg < 200 else "text"
        finally:
            doc.close()
    except Exception:
        return "scanned"
