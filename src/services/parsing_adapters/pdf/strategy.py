from __future__ import annotations

import logging
from pathlib import Path
from src.config import config
from src.services.parsing_adapters.enrichment.formula import _enrich_document_with_formulas
from src.services.parsing_adapters.models import ParsedDocument
from src.services.parsing_adapters.pdf.markpdfdown_adapter import _parse_pdf_markpdfdown
from src.services.parsing_adapters.pdf.mineru_adapter import _mineru_available, _parse_pdf_mineru
from src.services.parsing_adapters.pdf.pymupdf_adapter import _parse_pdf_fitz, _parse_pdf_pymupdf4llm
from src.services.parsing_adapters.pdf.quality import _assess_pdf_quality, _log_pdf_quality, _pdf_kind, _pdf_page_count

logger = logging.getLogger(__name__)


def _parse_pdf(path: str) -> ParsedDocument:
    p = config.parsing
    pdf_kind = _pdf_kind(path)
    expected_pages = _pdf_page_count(path)
    logger.info("PDF 类型判断: %s -> %s", Path(path).name, pdf_kind)

    best_doc: ParsedDocument | None = None
    best_quality = _assess_pdf_quality(None, expected_pages=expected_pages)

    def consider(doc: ParsedDocument | None, source: str) -> bool:
        nonlocal best_doc, best_quality
        quality = _assess_pdf_quality(doc, expected_pages=expected_pages)
        _log_pdf_quality(source, quality)
        if doc is not None:
            doc.parser_name = source
            doc.parse_quality = quality.score
        if doc and doc.full_text.strip() and quality.score > best_quality.score:
            best_doc, best_quality = doc, quality
        return bool(doc and doc.full_text.strip() and quality.score >= p.pdf_quality_threshold)

    # 显式指定或 auto + 开关时，先调用可配置的外部增强解析器。
    want_markpdfdown = p.pdf_parser == "markpdfdown" or (
        p.pdf_parser == "auto" and p.markpdfdown_enabled
    )
    if want_markpdfdown:
        if consider(
            _parse_pdf_markpdfdown(
                path,
                cmd=p.markpdfdown_cmd,
                args_template=p.markpdfdown_args,
                timeout=p.markpdfdown_timeout,
            ),
            "markpdfdown",
        ):
            return best_doc  # type: ignore[return-value]

    # MarkPDFdown 是增强候选而不是单点依赖；显式选它失败时仍回退 MinerU。
    if p.pdf_parser in ("mineru", "auto", "markpdfdown"):
        want_mineru = p.pdf_parser in ("mineru", "markpdfdown") or pdf_kind in ("scanned", "mixed")
        if want_mineru:
            if _mineru_available(p.mineru_cmd):
                doc = _parse_pdf_mineru(
                    path,
                    cmd=p.mineru_cmd,
                    timeout=p.mineru_timeout,
                    backend=p.mineru_backend,
                    effort=p.mineru_effort,
                    lang=p.mineru_lang,
                    formula=p.mineru_formula,
                    table=p.mineru_table,
                    image_analysis=p.mineru_image_analysis,
                )
                if consider(doc, f"mineru:{p.mineru_effort}"):
                    return best_doc  # type: ignore[return-value]
                if p.mineru_retry_high and p.mineru_effort != "high":
                    high_doc = _parse_pdf_mineru(
                        path,
                        cmd=p.mineru_cmd,
                        timeout=p.mineru_timeout,
                        backend=p.mineru_backend,
                        effort="high",
                        lang=p.mineru_lang,
                        formula=p.mineru_formula,
                        table=p.mineru_table,
                        image_analysis=True,
                    )
                    if consider(high_doc, "mineru:high"):
                        return best_doc  # type: ignore[return-value]
            elif p.pdf_parser == "mineru":
                logger.warning(
                    "PDF_PARSER=mineru 但找不到命令 %s，回退现有链路", p.mineru_cmd
                )

    # 原生文本 PDF：直接提取
    try:
        doc = _parse_pdf_pymupdf4llm(path)
        doc = _enrich_document_with_formulas(doc, path)
        if consider(doc, "pymupdf4llm"):
            return best_doc  # type: ignore[return-value]
        if p.pdf_use_ocr and not p.pdf_force_ocr:
            logger.info("PDF 空文本，OCR 重试: %s", Path(path).name)
            doc = _parse_pdf_pymupdf4llm(path, force_ocr=True)
            doc = _enrich_document_with_formulas(doc, path)
            if consider(doc, "pymupdf4llm:ocr"):
                return best_doc  # type: ignore[return-value]
    except Exception as e:
        logger.warning("pymupdf4llm 失败，回退 fitz: %s", e)

    doc = _parse_pdf_fitz(path)
    if consider(doc, "fitz"):
        return best_doc  # type: ignore[return-value]

    if p.pdf_use_ocr and not p.pdf_force_ocr:
        try:
            ocr = _parse_pdf_pymupdf4llm(path, force_ocr=True)
            ocr = _enrich_document_with_formulas(ocr, path)
            if consider(ocr, "pymupdf4llm:ocr-final"):
                return best_doc  # type: ignore[return-value]
        except Exception as e:
            logger.warning("PDF OCR 失败: %s", e)
    if best_doc:
        logger.warning(
            "PDF 所有候选均未达到质量阈值 %.2f，保留最佳结果（%.2f）",
            p.pdf_quality_threshold,
            best_quality.score,
        )
        return best_doc
    return doc
