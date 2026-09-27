"""Internal ingestion text chunks."""

from __future__ import annotations

import re


_LATEX_ENV = re.compile(r"\\begin\{(?:equation\*?|align\*?|aligned|cases|matrix)\}.*?\\end\{(?:equation\*?|align\*?|aligned|cases|matrix)\}", re.DOTALL)


_LATEX_BRACKET = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)


_LATEX_PAREN = re.compile(r"\\\((.+?)\\\)", re.DOTALL)


_LATEX_BLOCK = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)


_LATEX_INLINE = re.compile(r"\$([^$]+?)\$")


_LATEX_PLACEHOLDER = re.compile(r"__LATEX_\d+__")


def _protect_latex(text: str) -> tuple[str, dict[str, str]]:
    """保护常见 LaTeX 分隔符与环境，避免按句分块时切断公式。"""
    placeholders: dict[str, str] = {}
    counter = [0]

    def _replace_block(m):
        key = f"__LATEX_{counter[0]}__"
        placeholders[key] = m.group(0)
        counter[0] += 1
        return key

    for pattern in (_LATEX_ENV, _LATEX_BRACKET, _LATEX_PAREN, _LATEX_BLOCK, _LATEX_INLINE):
        text = pattern.sub(_replace_block, text)
    return text, placeholders


def _restore_latex(chunks: list[str], placeholders: dict[str, str]) -> list[str]:
    """将占位符还原为原始 LaTeX。"""
    result = []
    for chunk in chunks:
        for key, latex in placeholders.items():
            chunk = chunk.replace(key, latex)
        result.append(chunk)
    return result


def _split_long_protected(text: str, chunk_size: int) -> list[str]:
    """按长度切长句，但把单个 LaTeX 占位符视为不可分割单元。"""
    if not _LATEX_PLACEHOLDER.search(text):
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    out: list[str] = []
    current = ""
    pos = 0
    for match in _LATEX_PLACEHOLDER.finditer(text):
        for unit, protected in ((text[pos:match.start()], False), (match.group(0), True)):
            while unit:
                if protected:
                    if current and len(current) + len(unit) > chunk_size:
                        out.append(current)
                        current = ""
                    current += unit
                    unit = ""
                    continue
                room = chunk_size - len(current)
                if room <= 0:
                    out.append(current)
                    current = ""
                    room = chunk_size
                take = unit[:room]
                current += take
                unit = unit[room:]
                if len(current) >= chunk_size:
                    out.append(current)
                    current = ""
        pos = match.end()
    tail = text[pos:]
    while tail:
        room = chunk_size - len(current)
        if room <= 0:
            out.append(current)
            current = ""
            room = chunk_size
        current += tail[:room]
        tail = tail[room:]
        if len(current) >= chunk_size:
            out.append(current)
            current = ""
    if current:
        out.append(current)
    return [item for item in out if item]


def _split_text(
    text: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[str]:
    """将长文本按 chunk_size 切分，相邻块有 chunk_overlap 字符重叠。"""
    if not text or not text.strip():
        return []

    text, placeholders = _protect_latex(text)

    chunks: list[str] = []
    paragraphs = re.split(r"\n\s*\n", text)

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(para) <= chunk_size:
            chunks.append(para)
        else:
            sentences = re.split(r"(?<=[。！？；\n])", para)
            current = ""
            for sent in sentences:
                if not sent.strip():
                    continue
                if len(current) + len(sent) <= chunk_size:
                    current += sent
                else:
                    if current.strip():
                        chunks.append(current.strip())
                    if len(sent) > chunk_size:
                        for piece in _split_long_protected(sent, chunk_size):
                            if piece.strip():
                                chunks.append(piece.strip())
                        current = ""
                    else:
                        current = sent
            if current.strip():
                chunks.append(current.strip())

    if chunk_overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            overlap_text = prev[-chunk_overlap:] if len(prev) > chunk_overlap else prev
            overlapped.append(overlap_text + "\n\n" + chunks[i])
        chunks = overlapped

    return _restore_latex(chunks, placeholders)


def _chunk_document(
    parsed,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[tuple[str, int | None]]:
    """按页/段分块，保留 page 元数据。"""
    out: list[tuple[str, int | None]] = []
    for page in parsed.pages:
        for chunk in _split_text(page.text, chunk_size=chunk_size, chunk_overlap=chunk_overlap):
            out.append((chunk, page.page))
    return out
