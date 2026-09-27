"""Internal ingestion chapters."""

from __future__ import annotations

import re


_CHAPTER_TITLE = re.compile(
    r"(第\s*[零一二三四五六七八九十百千0-9]+\s*章[^\n]{0,40}|Chapter\s+\d+[^\n]{0,40})",
    re.IGNORECASE,
)


_MD_HEADER = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


def _normalize_chapter(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip())


def _is_chapter_like(title: str) -> bool:
    return bool(_CHAPTER_TITLE.search(title))


def _collect_section_headers(text: str) -> list[tuple[int, str]]:
    """(char_pos, title)：MD 标题 + 行内「第N章」；章级标题优先保留。"""
    headers: list[tuple[int, str]] = []
    for m in _MD_HEADER.finditer(text):
        title = _normalize_chapter(m.group(2))
        if title:
            headers.append((m.start(), title))
    for m in _CHAPTER_TITLE.finditer(text):
        title = _normalize_chapter(m.group(1))
        if title and not any(abs(p - m.start()) < 4 and t == title for p, t in headers):
            headers.append((m.start(), title))
    headers.sort(key=lambda x: x[0])
    return headers


def assign_chapters(
    text: str,
    chunks: list[str],
    *,
    pages: list[int | None] | None = None,
) -> list[str]:
    """为每个 chunk 推断 chapter 名；无法推断则为空串（Chroma 不用 None）。"""
    if not chunks:
        return []
    headers = _collect_section_headers(text)
    out: list[str] = []
    for i, chunk in enumerate(chunks):
        chapter = ""
        m = _CHAPTER_TITLE.search(chunk)
        if m:
            chapter = _normalize_chapter(m.group(1))
        elif headers:
            try:
                pos = text.index(chunk[: min(80, len(chunk))])
            except ValueError:
                pos = -1
            if pos >= 0:
                chapter_hit = ""
                any_hit = ""
                for hpos, title in reversed(headers):
                    if hpos > pos:
                        continue
                    if not any_hit:
                        any_hit = title
                    if _is_chapter_like(title):
                        chapter_hit = title
                        break
                chapter = chapter_hit or any_hit
        if not chapter and pages is not None and i < len(pages) and pages[i] is not None:
            chapter = f"第{pages[i]}页"
        out.append(chapter)
    return out


def _enrich_chunks_with_context(
    text: str,
    chunks: list[str],
) -> list[str]:
    """给每个 chunk 前面加上它所属的小节标题，提升语义检索精度。"""
    if not chunks:
        return chunks

    headers: list[tuple[int, str, str]] = []
    for m in _MD_HEADER.finditer(text):
        headers.append((m.start(), m.group(2).strip(), m.group(1)))

    if not headers:
        return chunks

    enriched = []
    for chunk in chunks:
        try:
            pos = text.index(chunk[: min(80, len(chunk))])
        except ValueError:
            enriched.append(chunk)
            continue

        context = ""
        for hpos, title, level in reversed(headers):
            if hpos <= pos:
                prefix = "§ " if level == "#" else ("§§ " if level == "##" else "§§§ ")
                context = f"{prefix}{title}\n"
                break

        enriched.append(context + chunk if context and not chunk.startswith(context) else chunk)

    return enriched
