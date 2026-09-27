"""Document parsing interface; implementations live in parsing_adapters."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from src.exceptions import BadRequestException, UnsupportedFormatException
from src.services.parsing_adapters.models import (
    PDFParseQuality,
    ParsedBlock,
    ParsedDocument,
    ParsedPage,
)
from src.services.parsing_adapters.office.doc_adapter import _parse_doc
from src.services.parsing_adapters.office.docx_adapter import _parse_docx
from src.services.parsing_adapters.office.pptx_adapter import _parse_pptx
from src.services.parsing_adapters.pdf.strategy import _parse_pdf
from src.services.parsing_adapters.text.markdown_adapter import parse_markdown
from src.services.parsing_adapters.text.txt_adapter import parse_txt

logger = logging.getLogger(__name__)
SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md", ".doc", ".docx", ".pptx"}
_PARSERS = {
    ".pdf": _parse_pdf,
    ".txt": parse_txt,
    ".md": parse_markdown,
    ".doc": _parse_doc,
    ".docx": _parse_docx,
    ".pptx": _parse_pptx,
}


def parse_file(path: str) -> ParsedDocument:
    ext = Path(path).suffix.lower()
    if ext not in _PARSERS:
        raise UnsupportedFormatException(
            f"不支持的文件格式: {ext}，仅接受 PDF/TXT/MD/DOC/DOCX/PPTX"
        )
    if not os.path.exists(path):
        raise BadRequestException(f"文件不存在: {path}")

    try:
        doc = _PARSERS[ext](path)
    except (UnsupportedFormatException, BadRequestException):
        raise
    except Exception as e:
        logger.error("文件解析失败 (%s): %s", path, e)
        raise BadRequestException(f"文件解析失败: {e}") from e

    if not doc.full_text.strip():
        raise BadRequestException("文件内容为空，无法入库")

    if not doc.parser_name:
        doc.parser_name = ext.lstrip(".")

    logger.info(
        "解析完成: %s parser=%s quality=%.2f pages=%d chars=%d",
        Path(path).name,
        doc.parser_name,
        doc.parse_quality,
        len(doc.pages),
        len(doc.full_text),
    )
    return doc
