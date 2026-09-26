"""入库编排：解析 → 分块 → 向量化 → 写入 storage。"""

import logging
import re
from pathlib import Path

from src.config import config
from src.exceptions import AppException, BadRequestException, ServiceUnavailableException, UnsupportedFormatException
from src.services.embedding import get_embedding_client
from src.services.evidence_metadata import extract_evidence_metadata
from src.services.parsing import SUPPORTED_EXTENSIONS, parse_file
from src.services.retrieval import invalidate_bm25_cache
from src.services.storage.catalog_store import (
    DEFAULT_COLLEGE_ID,
    DEFAULT_COURSE_ID,
    DEFAULT_COURSE_NAME,
)
from src.services.storage.vector_store import ChromaVectorStore
from src.services.storage.doc_store import SQLiteDocStore

logger = logging.getLogger(__name__)

# ponytail: Chroma 单次 upsert 上限经验值，超大文档分批写入
_UPSERT_BATCH = 128

_CHAPTER_TITLE = re.compile(
    r"(第\s*[零一二三四五六七八九十百千0-9]+\s*章[^\n]{0,40}|Chapter\s+\d+[^\n]{0,40})",
    re.IGNORECASE,
)
_MD_HEADER = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

# 结构化分块（MinerU blocks）用到的 block 类型
_TEXT_LIKE_BLOCKS = frozenset(
    {
        "text",
        "caption",
        "image_caption",
        "table_caption",
        "figure_caption",
    }
)
_FORMULA_BLOCKS = frozenset({"formula", "formula_inline"})
_MARGIN_BLOCKS = frozenset({"header", "footer"})

_CJK_TEXT = re.compile(r"[\u4e00-\u9fff]")
_FORMULA_CONTEXT_CUE = re.compile(
    r"公式|表达式|关系式|方程|恒等式|可表示为|可写为|可得|推导|其中|满足|定义为"
)
_NUMBERED_SECTION = re.compile(r"^(?:#{1,6}\s*)?((?:\d+\.)+\d+\s+[^\n]{1,80})$", re.MULTILINE)


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


def _section_label(section_path: str) -> str:
    """section_path 最后一级，作为该片段的上下文说明。"""
    return section_path.split(" / ")[-1] if section_path else ""


def _bbox_metadata(bboxes: list[tuple | None]) -> str:
    """将同页结构块坐标合并为可存入 Chroma 的稳定字符串。"""
    values: list[tuple[float, float, float, float]] = []
    for bbox in bboxes:
        if not isinstance(bbox, tuple) or len(bbox) != 4:
            continue
        try:
            x0, top, x1, bottom = (float(value) for value in bbox)
        except (TypeError, ValueError):
            continue
        values.append((x0, top, x1, bottom))
    if not values:
        return ""
    return ",".join(
        f"{value:.2f}"
        for value in (
            min(item[0] for item in values),
            min(item[1] for item in values),
            max(item[2] for item in values),
            max(item[3] for item in values),
        )
    )


def _join_references(values: list[str] | tuple[str, ...]) -> str:
    """Chroma metadata 只存标量，教材页码/题号候选用稳定分隔字符串保存。"""
    unique: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in unique:
            unique.append(clean)
    return "; ".join(unique)


def _formula_context_excerpt(blocks: list, index: int, max_chars: int = 420) -> str:
    """从公式同页结构块中提取章节线索和邻近中文正文，供检索公式语义。"""
    formula = blocks[index]
    candidates: list[tuple[int, int, str]] = []
    for other_index, other in enumerate(blocks):
        if other_index == index or other.page != formula.page:
            continue
        if other.block_type in _FORMULA_BLOCKS or other.block_type in _MARGIN_BLOCKS:
            continue
        if other.content_role == "noise":
            continue
        text = re.sub(r"[ \t]+", " ", str(other.text or ""))
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text or not _CJK_TEXT.search(text):
            continue
        cue_rank = 0 if _FORMULA_CONTEXT_CUE.search(text) else 1
        candidates.append((cue_rank, abs(other_index - index), text))

    selected: list[str] = []
    used = 0
    for _, _, text in sorted(candidates, key=lambda item: (item[0], item[1])):
        pieces = [
            piece.strip()
            for piece in re.split(r"(?<=[。！？；：])|\n+", text)
            if piece.strip() and _CJK_TEXT.search(piece)
        ]
        focused = [
            piece
            for piece in pieces
            if _FORMULA_CONTEXT_CUE.search(piece) or _NUMBERED_SECTION.match(piece)
        ]
        if not focused:
            focused = pieces[-2:]
        for piece in focused:
            if piece in selected:
                continue
            remaining = max_chars - used
            if remaining <= 0:
                return "\n".join(selected)
            selected.append(piece[:remaining])
            used += len(selected[-1]) + 1
    return "\n".join(selected)


def _formula_section_hint(context: str) -> str:
    """结构化标题缺失时，从邻近正文中的编号标题补出章节 metadata。"""
    matches = _NUMBERED_SECTION.findall(context)
    return matches[-1].strip() if matches else ""


def _image_context(blocks: list, index: int) -> str:
    """同页前后最近正文，遇标题立即停止，避免跨章节拼接。"""
    selected = []
    for direction in (-1, 1):
        for distance in range(1, 7):
            position = index + distance * direction
            if not 0 <= position < len(blocks):
                break
            other = blocks[position]
            if other.page != blocks[index].page or other.block_type == "title":
                break
            if other.block_type == "text" and other.content_role == "content" and other.text.strip():
                selected.append((position, other.text.strip()[:600]))
                break
    return "\n".join(text for _, text in sorted(selected))


def _chunk_structured(
    parsed,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[dict]:
    """按 MinerU block 语义切片，输出带丰富 metadata 的 chunk。

    - 标题层级 → section_path（如「第一章 绪论 / 1.1 通信系统模型」）；
    - 标题与段落合并为文本组，超长按句切分，保持层级关系；
    - 独立表格结构化提取（列头进 metadata，长表格行列转文本）；
    - 图片/图表独立成组：配置视觉模型则生成多模态摘要，否则用说明占位；
    - 每个切片带上下文说明（所属小节标题）与 block 元数据。
    """
    from src.services.parsing import _summarize_image, _table_headers

    out: list[dict] = []
    section_stack: list[str] = []
    group: dict | None = None

    def _section_path() -> str:
        return " / ".join(section_stack)

    def _new_group(page: int | None) -> dict:
        return {
            "parts": [],
            "page": page,
            "block_type": "text",
            "table_headers": "",
            "section_path": _section_path(),
            "bboxes": [],
            "pdf_page_label": "",
            "textbook_references": [],
        }

    def _flush() -> None:
        nonlocal group
        if group is None:
            return
        body = "\n\n".join(p for p in group["parts"] if p and p.strip()).strip()
        page = group["page"]
        block_type = group["block_type"]
        table_headers = group["table_headers"]
        section_path = group["section_path"]
        bbox = _bbox_metadata(group["bboxes"])
        pdf_page_label = group["pdf_page_label"]
        textbook_references = _join_references(group["textbook_references"])
        group = None
        if not body:
            return
        prefix = _section_label(section_path)
        context = f"§ {prefix}\n" if prefix else ""
        chapter = prefix or (f"第{page}页" if page else "")
        for piece in _split_text(body, chunk_size=chunk_size, chunk_overlap=chunk_overlap):
            if context and not piece.startswith(context):
                piece = context + piece
            out.append(
                {
                    "text": piece,
                    "page": page,
                    "chapter": chapter,
                    "block_type": block_type,
                    "section_path": section_path,
                    "table_headers": table_headers,
                    "context": context.strip(),
                    "bbox": bbox,
                    "pdf_page_label": pdf_page_label,
                    "textbook_references": textbook_references,
                    "content_role": "content",
                    "image_path": "",
                    "image_caption": "",
                }
            )

    def _standalone(
        block,
        text: str,
        block_type: str,
        headers: str = "",
        *,
        extra_context: str = "",
        section_hint: str = "",
    ) -> None:
        section_path = _section_path() or section_hint
        prefix = _section_label(section_path)
        context = "\n".join(part for part in (prefix, extra_context) if part).strip()
        out.append(
            {
                "text": text,
                "page": block.page,
                "chapter": prefix or (f"第{block.page}页" if block.page else ""),
                "block_type": block_type,
                "section_path": section_path,
                "table_headers": headers,
                "context": context,
                "bbox": _bbox_metadata([block.bbox]),
                "pdf_page_label": block.pdf_page_label,
                "textbook_references": _join_references(block.textbook_references),
                "content_role": block.content_role,
                "image_path": block.image_path if block_type in {"image", "image_summary"} else "",
                "image_caption": block.caption if block_type in {"image", "image_summary"} else "",
            }
        )

    blocks = list(parsed.blocks)
    for block_index, block in enumerate(blocks):
        btype = block.block_type
        if btype in _MARGIN_BLOCKS or block.content_role == "noise":
            # 页眉页脚和移动端状态栏、页脚声明等保留在结构中，但不污染检索正文。
            logger.info("跳过噪声 block: %s 第%s页", btype, block.page or "?")
            continue

        if block.content_role in {"annotation", "reference"} and btype != "image":
            _flush()
            text = block.text.strip()
            if text:
                _standalone(block, text, block.content_role)
            continue

        if btype == "title":
            _flush()
            title = block.text.strip()
            level = max(1, block.level or 1)
            while section_stack and len(section_stack) >= level:
                section_stack.pop()
            section_stack.append(title)
            group = _new_group(block.page)
            group["parts"].append(title)
            group["bboxes"].append(block.bbox)
            group["pdf_page_label"] = block.pdf_page_label
            group["textbook_references"].extend(block.textbook_references)
            continue

        if btype == "table":
            _flush()
            text = block.text.strip()
            if block.caption:
                text = f"{text}\n\n表格说明：{block.caption}" if text else f"表格说明：{block.caption}"
            if text:
                _standalone(block, text, "table", headers=_table_headers(block.html))
            continue

        if btype == "image":
            _flush()
            nearby = _image_context(blocks, block_index)
            summary = _summarize_image(block.image_path, block.caption, block.page, nearby)
            if summary:
                text = f"视觉摘要：{summary}"
                if block.caption:
                    text = f"图片说明：{block.caption}\n{text}"
                if _section_path():
                    text = f"§ {_section_path()}\n{text}"
                if nearby:
                    text += f"\n邻近正文（非图中直接读数）：{nearby}"
                _standalone(block, text, "image_summary", extra_context=nearby)
            elif config.parsing.visual_required:
                raise BadRequestException(
                    f"第{block.page or '?'}页图片未完成 VLM 摘要：{block.caption or '无图注图片'}。"
                    "请检查视觉模型配置、图片文件和调用日志后重试。"
                )
            else:
                _standalone(
                    block, f"[视觉识别未完成] 图片说明：{block.caption or '无图注图片'}",
                    "image", extra_context=nearby,
                )
            continue

        if btype in _FORMULA_BLOCKS:
            # 公式必须作为独立证据保存，不能再并入 block_type=text 的正文组。
            _flush()
            formula_text = block.text.strip()
            if not formula_text and block.latex:
                formula_text = f"$${block.latex.strip()}$$"
            if formula_text:
                nearby = _formula_context_excerpt(blocks, block_index)
                section_hint = _formula_section_hint(nearby)
                prefix = _section_label(_section_path() or section_hint)
                parts = []
                if prefix:
                    parts.append(f"§ {prefix}")
                if nearby:
                    parts.append(f"公式上下文：{nearby}")
                parts.append(f"公式：{formula_text}")
                _standalone(
                    block,
                    "\n".join(parts),
                    "formula",
                    extra_context=nearby,
                    section_hint=section_hint,
                )
            continue

        # 文本类：段落/独立说明合并进当前文本组；公式由上面的独立分支处理。
        if btype in _TEXT_LIKE_BLOCKS or btype.startswith("text"):
            if group is None:
                group = _new_group(block.page)
            elif group["page"] != block.page:
                _flush()
                group = _new_group(block.page)
            if block.text and block.text.strip():
                group["parts"].append(block.text.strip())
                group["bboxes"].append(block.bbox)
                if not group["pdf_page_label"]:
                    group["pdf_page_label"] = block.pdf_page_label
                group["textbook_references"].extend(block.textbook_references)
            continue

        # 未知类型 block：按文本兜底，避免丢失内容
        if group is None:
            group = _new_group(block.page)
        elif group["page"] != block.page:
            _flush()
            group = _new_group(block.page)
        if block.text and block.text.strip():
            group["parts"].append(block.text.strip())
            group["bboxes"].append(block.bbox)
            if not group["pdf_page_label"]:
                group["pdf_page_label"] = block.pdf_page_label
            group["textbook_references"].extend(block.textbook_references)

    _flush()
    return out


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化，入库与检索共用。"""
    try:
        return get_embedding_client().embed(texts)
    except Exception as e:
        logger.error("向量化失败: %s", e)
        raise ServiceUnavailableException("向量化服务不可用", detail=str(e)) from e


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


def _acquire_doc_id(
    ds: SQLiteDocStore,
    vs: ChromaVectorStore,
    filepath: Path,
    filename: str,
    course: str,
    course_id: str,
    *,
    is_active: bool = True,
) -> int:
    """同路径且同课程复用记录；跨课程占用同路径则拒绝，避免串课。"""
    resolved = str(filepath.resolve())
    existing = ds.find_by_path(resolved)
    if existing:
        if existing.get("course_id") and existing["course_id"] != course_id:
            raise BadRequestException(
                f"文件已归属课程 {existing['course_id']}，不能再入库到 {course_id}"
            )
        doc_id = existing["id"]
        if existing["status"] in ("done", "failed", "processing"):
            vs.delete_by_doc_id(str(doc_id))
            ds.update_course(doc_id, course, course_id)
            ds.update_status(doc_id, "processing", chunk_count=0)
            logger.info("复用文档记录: doc_id=%s path=%s", doc_id, filename)
            return doc_id

    doc_id = ds.create(
        filename=filename,
        file_path=resolved,
        course=course,
        course_id=course_id,
        is_active=is_active,
    )
    ds.update_status(doc_id, "processing")
    return doc_id


def _fail_ingest(vs: ChromaVectorStore, ds: SQLiteDocStore, doc_id: int) -> None:
    """入库失败：清向量 + 标记 failed。"""
    try:
        vs.delete_by_doc_id(str(doc_id))
    except Exception as e:
        logger.warning("清理 doc_id=%s 向量失败: %s", doc_id, e)
    ds.update_status(doc_id, "failed", chunk_count=0)


def _upsert_chunks_batched(
    vs: ChromaVectorStore,
    chunk_dicts: list[dict],
    embeddings: list[list[float]],
) -> bool:
    """分批写入；任一批评因维度重建过集合则返回 True。"""
    wiped = False
    for i in range(0, len(chunk_dicts), _UPSERT_BATCH):
        sl = slice(i, i + _UPSERT_BATCH)
        wiped = vs.upsert(chunk_dicts[sl], embeddings[sl]) or wiped
    return wiped


def _mark_sibling_docs_stale(
    ds: SQLiteDocStore, keep_doc_id: int, course_id: str | None = None
) -> int:
    stale = 0
    for doc in ds.list(course_id=course_id):
        if doc["id"] != keep_doc_id and doc["status"] == "done":
            ds.update_status(doc["id"], "failed", chunk_count=0)
            stale += 1
    return stale


def _needs_reindex(existing: dict | None, mtime: float) -> bool:
    """判断文件是否需要（重新）入库。"""
    if existing is None:
        return True
    if existing["status"] != "done":
        return existing["status"] == "failed"
    stored = existing.get("file_mtime")
    if stored is None:
        return True
    return mtime > stored + 1e-3


def ingest_file(
    path: str,
    vs: ChromaVectorStore,
    ds: SQLiteDocStore,
    course_id: str = DEFAULT_COURSE_ID,
    course: str = DEFAULT_COURSE_NAME,
    college_id: str = DEFAULT_COLLEGE_ID,
    display_name: str | None = None,
    *,
    is_active: bool = True,
    parsed_document=None,
) -> str:
    """入库单文件，返回 doc_id。"""
    if not course_id or not course_id.strip():
        raise BadRequestException("course_id 不能为空")

    invalidate_bm25_cache(course_id)
    filepath = Path(path)
    filename = display_name or filepath.name
    logger.info("开始入库: %s course_id=%s", filename, course_id)

    ext = filepath.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatException(
            f"不支持的文件格式: {ext}，仅接受 PDF/TXT/MD/DOC/DOCX/PPTX"
        )

    # 先完成解析及视觉摘要；失败时不清除同路径旧文档的向量。
    existing = ds.find_by_path(str(filepath.resolve()))
    if existing and existing.get("course_id") and existing["course_id"] != course_id:
        raise BadRequestException("文件已归属其他课程，不能跨课程入库")
    parsed = parsed_document if parsed_document is not None else parse_file(path)
    if ext == ".pdf":
        from src.services.visual_coverage import ensure_pdf_visual_coverage
        ensure_pdf_visual_coverage(parsed, path)
    full_text = parsed.full_text
    if not full_text.strip() and not any(b.block_type == "image" for b in parsed.blocks):
        raise BadRequestException("解析后内容为空")
    structured = _chunk_structured(
        parsed, chunk_size=config.chunk.chunk_size, chunk_overlap=config.chunk.chunk_overlap,
    ) if parsed.has_blocks else None

    doc_id = _acquire_doc_id(
        ds, vs, filepath, filename, course, course_id, is_active=is_active
    )
    logger.info("文档记录就绪: doc_id=%s", doc_id)

    try:
        current_metadata = ds.get(doc_id) or {}
        if current_metadata.get("metadata_source") == "manual":
            evidence = {
                key: current_metadata[key]
                for key in (
                    "source_version",
                    "effective_from",
                    "effective_to",
                    "authority_level",
                    "authority_label",
                    "applicability_scope",
                    "metadata_confidence",
                    "metadata_source",
                )
            }
        else:
            evidence = extract_evidence_metadata(full_text, filename).to_dict()
            ds.update_evidence_metadata(doc_id, evidence)

        if parsed.has_blocks:
            # MinerU 结构化切片：语义分组 + 丰富 metadata
            chunk_texts = [c["text"] for c in structured]
            chunk_pages = [c["page"] for c in structured]
            chapters = [c["chapter"] for c in structured]
            block_types = [c["block_type"] for c in structured]
            section_paths = [c["section_path"] for c in structured]
            table_headers = [c["table_headers"] for c in structured]
            contexts = [c["context"] for c in structured]
            bboxes = [c["bbox"] for c in structured]
            pdf_page_labels = [c["pdf_page_label"] for c in structured]
            textbook_references = [c["textbook_references"] for c in structured]
            content_roles = [c["content_role"] for c in structured]
            image_paths = [c.get("image_path", "") for c in structured]
            image_captions = [c.get("image_caption", "") for c in structured]
        else:
            # 非结构化文档：保持原有按页分块 + 章节推断
            raw_chunks = _chunk_document(
                parsed,
                chunk_size=config.chunk.chunk_size,
                chunk_overlap=config.chunk.chunk_overlap,
            )
            if not raw_chunks:
                raise BadRequestException("分块结果为空")
            chunk_texts = [c[0] for c in raw_chunks]
            chunk_pages = [c[1] for c in raw_chunks]
            chapters = assign_chapters(full_text, chunk_texts, pages=chunk_pages)
            chunk_texts = _enrich_chunks_with_context(full_text, chunk_texts)
            block_types = ["text"] * len(chunk_texts)
            section_paths = [""] * len(chunk_texts)
            table_headers = [""] * len(chunk_texts)
            contexts = [""] * len(chunk_texts)
            bboxes = [""] * len(chunk_texts)
            pdf_page_labels = [""] * len(chunk_texts)
            textbook_references = [""] * len(chunk_texts)
            content_roles = ["content"] * len(chunk_texts)
            image_paths = [""] * len(chunk_texts)
            image_captions = [""] * len(chunk_texts)

        if not chunk_texts:
            raise BadRequestException("分块结果为空")
        logger.info("分块完成: %s, 共 %d 个 chunk", filename, len(chunk_texts))

        embeddings = embed_texts(chunk_texts)

        chunk_dicts = []
        for i, chunk_text in enumerate(chunk_texts):
            chunk_dicts.append({
                "doc_id": str(doc_id),
                "source_file": filename,
                "chunk_index": i,
                "course": course,
                "course_id": course_id,
                "college_id": college_id,
                "text": chunk_text,
                "page": chunk_pages[i],
                "chapter": chapters[i] if i < len(chapters) else "",
                "block_type": block_types[i] if i < len(block_types) else "",
                "section_path": section_paths[i] if i < len(section_paths) else "",
                "table_headers": table_headers[i] if i < len(table_headers) else "",
                "context": contexts[i] if i < len(contexts) else "",
                "bbox": bboxes[i] if i < len(bboxes) else "",
                "pdf_page_label": pdf_page_labels[i] if i < len(pdf_page_labels) else "",
                "textbook_references": textbook_references[i] if i < len(textbook_references) else "",
                "content_role": content_roles[i] if i < len(content_roles) else "content",
                "image_path": image_paths[i] if i < len(image_paths) else "",
                "image_caption": image_captions[i] if i < len(image_captions) else "",
                "parser_name": parsed.parser_name,
                "parse_quality": parsed.parse_quality,
                "is_active": is_active,
                **evidence,
            })

        wiped = _upsert_chunks_batched(vs, chunk_dicts, embeddings)
        if wiped:
            stale = _mark_sibling_docs_stale(ds, doc_id, course_id=course_id)
            if stale:
                logger.warning(
                    "Embedding 维度已变更，已将同课 %d 条其他资料标为 failed，请重新扫描/上传",
                    stale,
                )
        logger.info("向量写入完成: %s, %d chunks", filename, len(chunk_texts))

        ds.update_status(doc_id, "done", chunk_count=len(chunk_texts))
        ds.update_file_mtime(doc_id, filepath.stat().st_mtime)
        logger.info(
            "入库完成: %s -> doc_id=%s, %d chunks",
            filename,
            doc_id,
            len(chunk_texts),
        )
        return str(doc_id)

    except AppException:
        _fail_ingest(vs, ds, doc_id)
        raise
    except Exception as e:
        logger.exception("入库异常: %s", e)
        _fail_ingest(vs, ds, doc_id)
        raise AppException(f"入库失败: {e}", status_code=500)
    finally:
        invalidate_bm25_cache(course_id)


def scan_knowledge_dir(
    vs: ChromaVectorStore,
    ds: SQLiteDocStore,
    *,
    course_id: str = DEFAULT_COURSE_ID,
    course: str = DEFAULT_COURSE_NAME,
    college_id: str = DEFAULT_COLLEGE_ID,
    recover_stale: bool = False,
    force: bool = False,
) -> list[dict]:
    """扫描 knowledge；force=True 时对已 done 文件也重入库（补 chapter 等）。"""
    data_dir = Path(config.storage.knowledge_dir)
    if not data_dir.exists():
        return []

    if recover_stale:
        stale = ds.recover_stale_processing()
        if stale:
            logger.info("已将 %d 条 processing 记录恢复为 failed", stale)

    files = [
        f
        for f in data_dir.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    results: list[dict] = []
    # PDF 走内容指纹与版本切换；在函数内导入以避免与 ingest_file 的循环依赖。
    from src.services.document_updates import ingest_or_update_pdf

    non_pdf_files: list[Path] = []
    for f in files:
        if f.suffix.lower() == ".pdf":
            try:
                outcome = ingest_or_update_pdf(
                    path=str(f.resolve()),
                    vs=vs,
                    ds=ds,
                    course_id=course_id,
                    course=course,
                    college_id=college_id,
                    knowledge_dir=str(data_dir),
                    force=force,
                )
                results.append(outcome.to_dict())
            except Exception as e:
                logger.warning("PDF 自动更新失败 %s: %s", f.name, e)
                results.append({"action": "failed", "filename": f.name, "message": str(e)})
            continue
        non_pdf_files.append(f)

    to_ingest: list[Path] = []
    for f in non_pdf_files:
        resolved = str(f.resolve())
        mtime = f.stat().st_mtime
        existing = ds.find_by_path(resolved)
        if existing and existing.get("course_id") and existing["course_id"] != course_id:
            logger.info(
                "跳过跨课文件 %s（归属 %s，当前扫描 %s）",
                f.name,
                existing["course_id"],
                course_id,
            )
            continue
        if force and existing and existing.get("status") == "done":
            to_ingest.append(f)
            continue
        if _needs_reindex(existing, mtime):
            to_ingest.append(f)

    if not to_ingest:
        return results

    logger.info(
        "发现 %d 个待入库/更新文件%s，开始扫描入库…",
        len(to_ingest),
        "（强制重建）" if force else "",
    )
    for f in to_ingest:
        try:
            doc_id = ingest_file(
                path=str(f.resolve()),
                vs=vs,
                ds=ds,
                course_id=course_id,
                course=course,
                college_id=college_id,
            )
            logger.info("扫描入库: %s -> doc_id=%s", f.name, doc_id)
            results.append({"action": "created", "doc_id": doc_id, "filename": f.name})
        except Exception as e:
            logger.warning("扫描入库失败 %s: %s", f.name, e)
            results.append({"action": "failed", "filename": f.name, "message": str(e)})
    return results
