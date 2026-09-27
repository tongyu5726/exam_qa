"""Parse legacy Word files through a temporary DOCX conversion."""

from src.exceptions import BadRequestException
from src.services.parsing_adapters.models import ParsedDocument
from src.services.parsing_adapters.office.docx_adapter import _parse_docx
from src.services.parsing_adapters.office.legacy_doc_converter import _convert_doc_to_docx


def _parse_doc(path: str) -> ParsedDocument:
    docx_tmp = _convert_doc_to_docx(path)
    if docx_tmp:
        try:
            return _parse_docx(str(docx_tmp))
        finally:
            docx_tmp.unlink(missing_ok=True)
    raise BadRequestException(
        "无法解析 .doc 文件。请安装 LibreOffice 或 Microsoft Word，或将文件另存为 .docx 后重试。"
    )
