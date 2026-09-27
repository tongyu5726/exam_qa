"""Markdown parsing; retain headings for ingestion chapter inference."""

from src.services.parsing_adapters.models import ParsedDocument, ParsedPage
from src.services.parsing_adapters.text.decoding import read_text


def parse_markdown(path: str) -> ParsedDocument:
    text = read_text(path).strip()
    return ParsedDocument([ParsedPage(page=None, text=text)] if text else [])
