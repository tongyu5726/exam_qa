"""Internal ingestion structured chunks."""

from __future__ import annotations

import logging
import re
from src.config import config
from src.exceptions import BadRequestException
from src.services.ingestion_parts.text_chunks import _split_text

logger = logging.getLogger(__name__)


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
    from src.services.parsing_adapters.enrichment.vision import _summarize_image
    from src.services.parsing_adapters.pdf.mineru_adapter import _table_headers

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
