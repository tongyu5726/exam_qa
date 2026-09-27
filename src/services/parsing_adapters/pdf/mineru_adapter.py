from __future__ import annotations

import logging
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from src.services.parsing_adapters.blocks import _CAPTION_TYPES, _annotate_block, _as_bbox, _caption_text, _persist_parser_assets, _resolve_image_path
from src.services.parsing_adapters.commands import _command_error_tail, _run_capture
from src.services.parsing_adapters.enrichment.formula import _formula_latex
from src.services.parsing_adapters.models import ParsedBlock, ParsedDocument, ParsedPage

logger = logging.getLogger(__name__)


def _mineru_available(cmd: str) -> bool:
    """MinerU CLI 是否可用（PATH 可找到）。"""
    return bool(shutil.which(cmd))


def _html_table_to_text(html: str) -> str:
    """把 MinerU 的表格 HTML 转成可检索的纯文本（单元格用 | 分隔）。"""
    if not html:
        return ""
    text = re.sub(r"<t[dh][^>]*>", " | ", html, flags=re.I)
    text = re.sub(r"</t[r]>", "\n", text, flags=re.I)
    text = re.sub(r"</t[dh]>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text, flags=re.I)
    lines = [re.sub(r"\s*[|]\s*$", "", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln.strip())


def _table_headers(html: str) -> str:
    """从表格 HTML 提取列头（<th>），写入结构化 metadata。"""
    if not html:
        return ""
    heads = re.findall(r"<t[h][^>]*>(.*?)</t[h]>", html, flags=re.I | re.S)
    cleaned = [" ".join(re.sub(r"<[^>]+>", " ", h).split()) for h in heads]
    return " | ".join(c for c in cleaned if c)


def _block_text(block: dict) -> str:
    """content_list 单个 block 的可检索文本（text/table/formula 按类型拼接）。"""
    btype = block.get("type") or ""
    if btype in ("formula", "formula_inline", "equation", "equation_interline", "inline_equation"):
        latex = _formula_latex(block)
        return f"$${latex}$$" if latex else ""
    if btype == "table":
        text = block.get("text") or ""
        html = block.get("html") or ""
        return text if text else _html_table_to_text(html)
    text = block.get("text") or ""
    if text:
        return text
    parts = []
    for ln in block.get("lines") or []:
        if isinstance(ln, dict):
            t = ln.get("text") or ln.get("content") or ""
        else:
            t = str(ln)
        if t.strip():
            parts.append(t.strip())
    return "\n".join(parts)


def _blocks_to_pages(blocks: list[ParsedBlock]) -> list[ParsedPage]:
    """按页聚合 block 文本，保证 pages 视图与 blocks 一致。"""
    by_page: dict[int, list[str]] = {}
    labels: dict[int, str] = {}
    for b in blocks:
        if not b.text:
            continue
        key = b.page or 0
        by_page.setdefault(key, []).append(b.text)
        if b.pdf_page_label and key not in labels:
            labels[key] = b.pdf_page_label
    pages = []
    for k in sorted(by_page):
        pages.append(
            ParsedPage(
                page=None if k == 0 else k,
                text="\n\n".join(by_page[k]),
                pdf_page_label=labels.get(k, ""),
            )
        )
    return pages


def _mineru_json_to_document(content: dict, *, base_dir: Path | None = None) -> ParsedDocument:
    """重组 MinerU content_list 为带 block 结构的 ParsedDocument（结构还原）。

    - 保留 title 层级、table HTML、formula、image 原图路径与说明；
    - 图片/表格说明（caption）就近挂载，无归属的保留为独立 caption block；
    - 页眉页脚保留在 blocks 中（结构还原），正文切片阶段再决定取舍。
    """
    items = content.get("content_list") or []
    raw: list[dict] = []
    for i, block in enumerate(items):
        if isinstance(block, dict):
            item = dict(block)
            item.setdefault("_seq", i)
            raw.append(item)

    # caption 挂载：优先挂到它前面的 image/table（MinerU 输出图在前、说明在后），
    # 无前驱时再找后面的，距离超过 3 个 block 视为无归属。
    targets = [
        (int(b.get("_seq", 0)), b.get("type"))
        for b in raw
        if b.get("type") in ("image", "table")
    ]
    caption_by_target: dict[int, str] = {}
    orphan_captions: list[tuple[int, int | None, str]] = []
    for block in raw:
        if block.get("type") not in _CAPTION_TYPES:
            continue
        text = _caption_text(block)
        if not text:
            continue
        seq = int(block.get("_seq", 0))
        candidates = [t for t in targets if t[0] < seq] or targets
        best_t, best_d = -1, 10**9
        for tseq, _ttype in candidates:
            d = abs(tseq - seq)
            if d < best_d:
                best_t, best_d = tseq, d
        if best_t >= 0 and best_d <= 3:
            caption_by_target[best_t] = text
        else:
            try:
                page = int(block.get("page_idx", 0)) + 1
            except (TypeError, ValueError):
                page = None
            orphan_captions.append((seq, page, text))

    blocks: list[ParsedBlock] = []
    for block in raw:
        try:
            page = int(block.get("page_idx", 0)) + 1
        except (TypeError, ValueError):
            page = None
        btype = str(block.get("type") or "text")
        if btype in {"equation", "equation_interline", "display_formula"}:
            btype = "formula"
        elif btype in {"inline_equation", "inline_formula"}:
            btype = "formula_inline"
        page_label = str(block.get("pdf_page_label") or block.get("page_label") or "").strip()
        seq = int(block.get("_seq", 0))
        order = block.get("order") if block.get("order") is not None else seq

        if btype in _CAPTION_TYPES:
            continue  # 已挂载或进入 orphan_captions
        if btype == "table":
            html = block.get("html") or ""
            text = (block.get("text") or "").strip() or _html_table_to_text(html)
            blocks.append(
                _annotate_block(ParsedBlock(
                    block_type="table",
                    text=text,
                    page=page,
                    html=html,
                    caption=caption_by_target.get(seq, ""),
                    order=order,
                    bbox=_as_bbox(block.get("bbox")),
                    pdf_page_label=page_label,
                ))
            )
        elif btype == "image":
            blocks.append(
                _annotate_block(ParsedBlock(
                    block_type="image",
                    text=(block.get("text") or "").strip(),
                    page=page,
                    image_path=_resolve_image_path(block.get("img_path"), base_dir),
                    caption=caption_by_target.get(seq, ""),
                    order=order,
                    bbox=_as_bbox(block.get("bbox")),
                    pdf_page_label=page_label,
                ))
            )
        elif btype in ("formula", "formula_inline"):
            latex = _formula_latex(block)
            blocks.append(
                _annotate_block(ParsedBlock(
                    block_type=btype,
                    text=f"$${latex}$$" if latex else "",
                    page=page,
                    latex=latex,
                    order=order,
                    bbox=_as_bbox(block.get("bbox")),
                    pdf_page_label=page_label,
                ))
            )
        elif btype == "title":
            try:
                level = int(block.get("level") or 1)
            except (TypeError, ValueError):
                level = 1
            blocks.append(
                _annotate_block(ParsedBlock(
                    block_type="title",
                    text=(block.get("text") or "").strip(),
                    page=page,
                    level=level,
                    order=order,
                    bbox=_as_bbox(block.get("bbox")),
                    pdf_page_label=page_label,
                ))
            )
        else:
            blocks.append(
                _annotate_block(ParsedBlock(
                    block_type=btype,
                    text=_block_text(block).strip(),
                    page=page,
                    order=order,
                    bbox=_as_bbox(block.get("bbox")),
                    pdf_page_label=page_label,
                ))
            )

    for seq, page, text in orphan_captions:
        blocks.append(_annotate_block(ParsedBlock(block_type="caption", text=text, page=page, order=seq)))

    blocks.sort(key=lambda b: (b.page or 0, b.order))
    return ParsedDocument(pages=_blocks_to_pages(blocks), blocks=blocks)


def _mineru_json_to_pages(content: dict) -> ParsedDocument:
    """兼容入口：仅按页重组文本（无结构信息）。"""
    return _mineru_json_to_document(content)


def _mineru_md_to_pages(
    md_text: str,
    *,
    base_dir: Path | None = None,
    source_path: str | None = None,
) -> ParsedDocument:
    """MinerU 无结构化 JSON 时，从 Markdown 恢复正文与图片 block。

    MinerU 常把裁剪图写为 ``![](images/xxx.jpg)``。旧实现将 Markdown 整体
    当作纯文本，导致图片既不入库也在临时目录清理时丢失。这里保留原始顺序，
    关联紧随图片的图注，并利用 PDF 文本层中的图号还原 1-based 物理页。
    """
    text = md_text.strip()
    if not text:
        return ParsedDocument([])

    matches = list(_MD_IMAGE_RE.finditer(text))
    if not matches:
        return ParsedDocument([ParsedPage(page=None, text=text)])

    blocks: list[ParsedBlock] = []
    page_texts = _pdf_normalized_pages(source_path)
    cursor = 0
    order = 0
    for match in matches:
        if match.start() < cursor:
            continue
        preceding = text[cursor : match.start()].strip()
        if preceding:
            blocks.append(
                _annotate_block(
                    ParsedBlock(
                        block_type="text",
                        text=preceding,
                        page=None,
                        order=order,
                    )
                )
            )
            order += 1

        caption, consumed_to = _caption_after_image(text, match.end())
        alt = _plain_markdown_line(match.group("alt"))
        if not caption and _FIGURE_CAPTION_RE.match(alt):
            caption = alt
        raw_path = match.group("path").strip("<>")
        page = _pdf_page_for_caption(page_texts, caption)
        blocks.append(
            _annotate_block(
                ParsedBlock(
                    block_type="image",
                    text=caption,
                    page=page,
                    image_path=_resolve_image_path(raw_path, base_dir),
                    caption=caption,
                    order=order,
                )
            )
        )
        order += 1
        cursor = consumed_to if consumed_to > match.end() else match.end()

    trailing = text[cursor:].strip()
    if trailing:
        blocks.append(
            _annotate_block(
                ParsedBlock(
                    block_type="text",
                    text=trailing,
                    page=None,
                    order=order,
                )
            )
        )
    return ParsedDocument(pages=_blocks_to_pages(blocks), blocks=blocks)


def _parse_pdf_mineru(
    path: str,
    *,
    cmd: str = "mineru",
    timeout: int = 0,
    backend: str = "hybrid-engine",
    effort: str = "medium",
    lang: str = "ch",
    formula: bool = True,
    table: bool = True,
    image_analysis: bool = True,
) -> ParsedDocument | None:
    """子进程调用 MinerU CLI，读取输出 Markdown/JSON 重组为 ParsedDocument。

    timeout<=0 表示不限时；任何失败返回 None，由调用方回退现有链路。
    """
    if not _mineru_available(cmd):
        return None
    tmp = Path(tempfile.mkdtemp(prefix="mineru_"))
    try:
        args = [
            cmd,
            "-p", str(path),
            "-o", str(tmp),
            "-m", "auto",
            "-b", backend,
            "-l", lang,
            "-f", str(formula).lower(),
            "-t", str(table).lower(),
            "--image-analysis", str(image_analysis).lower(),
        ]
        # --effort 仅 hybrid backend 支持；其他 backend 保持兼容。
        if backend in ("hybrid-engine", "hybrid-http-client"):
            args.extend(["--effort", effort])
        logger.info(
            "MinerU 解析开始: %s (backend=%s effort=%s timeout=%s)",
            Path(path).name,
            backend,
            effort,
            timeout or "无",
        )
        proc, stdout, stderr = _run_capture(
            args,
            timeout=None if timeout <= 0 else timeout,
        )
        if proc.returncode != 0:
            logger.warning(
                "MinerU 退出码 %s: %s",
                proc.returncode,
                _command_error_tail(stdout, stderr),
            )
            return None

        md_files = sorted(tmp.rglob("*.md"))
        json_files = sorted(tmp.rglob("*.json"))
        md_doc = None
        if md_files:
            # 输出目录可能同时含说明 Markdown 与正文 Markdown，优先正文体量最大的文件。
            md_file = max(md_files, key=lambda candidate: candidate.stat().st_size)
            md_doc = _mineru_md_to_pages(
                md_file.read_text(encoding="utf-8", errors="replace"),
                base_dir=md_file.parent,
                source_path=path,
            )
        json_candidates: list[ParsedDocument] = []
        for jf in json_files:
            try:
                content = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            payload = _mineru_content_payload(content)
            if payload is None:
                continue
            doc = _mineru_json_to_document(payload, base_dir=jf.parent)
            if doc.pages:
                json_candidates.append(doc)
        if json_candidates:
            # 多个 JSON 时优先 block 更多、正文更完整的结果，避免误取中间状态文件。
            doc = max(
                json_candidates,
                key=lambda candidate: (len(candidate.blocks), len(candidate.full_text)),
            )
            if image_analysis:
                _supplement_missing_figure_images(
                    doc,
                    source_path=path,
                    output_dir=tmp / "supplemental_figures",
                )
            _persist_parser_assets(doc, work_dir=tmp, source_path=path)
            logger.info(
                "MinerU 解析完成: %s, %d 页 / %d 块 (json)",
                Path(path).name,
                len(doc.pages),
                len(doc.blocks),
            )
            return doc
        if md_doc and md_doc.pages:
            _persist_parser_assets(md_doc, work_dir=tmp, source_path=path)
            image_count = sum(
                1 for block in md_doc.blocks if block.block_type == "image"
            )
            logger.info(
                "MinerU 解析完成: %s, 回退 Markdown (%d 个图片块)",
                Path(path).name,
                image_count,
            )
            return md_doc
        logger.warning("MinerU 未产出可用文本: %s", Path(path).name)
        return None
    except subprocess.TimeoutExpired:
        logger.warning("MinerU 解析超时（%s 秒）: %s", timeout, Path(path).name)
        return None
    except Exception as e:
        logger.warning("MinerU 解析异常: %s", e)
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_MD_IMAGE_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(\s*(?P<path><[^>]+>|[^\s)]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)"
)


_FIGURE_CAPTION_RE = re.compile(
    r"^(?:图\s*\d+(?:\.\d+)*(?:\s*[-－—]\s*\d+)*|figure\s*\d+|fig\.?\s*\d+)",
    re.IGNORECASE,
)


_FIGURE_LABEL_RE = re.compile(r"图\s*\d+(?:\.\d+)*(?:\s*[-－—]\s*\d+)*")


_ANY_FIGURE_LABEL_RE = re.compile(
    r"(?:图\s*\d+(?:\.\d+)*(?:\s*[-－—]\s*\d+)*|"
    r"figure\s*\d+(?:[.\-]\d+)*|fig\.?\s*\d+(?:[.\-]\d+)*)",
    re.IGNORECASE,
)


def _plain_markdown_line(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def _normalized_page_text(value: str) -> str:
    value = value.replace("－", "-").replace("—", "-")
    return re.sub(r"\s+", "", value).lower()


def _pdf_normalized_pages(source_path: str | None) -> list[str]:
    """一次读取 PDF 文本层，供同一文档的多个图注复用。"""
    if not source_path:
        return []
    try:
        import fitz

        pdf = fitz.open(source_path)
        try:
            return [_normalized_page_text(page.get_text("text")) for page in pdf]
        finally:
            pdf.close()
    except Exception as exc:
        logger.debug("无法读取 PDF 文本层以定位图片: %s", exc)
    return []


def _pdf_page_for_caption(page_texts: list[str], caption: str) -> int | None:
    """按图号/图注把 MinerU Markdown 图片映射回 PDF 物理页（1-based）。"""
    if not caption:
        return None
    label_match = _FIGURE_LABEL_RE.search(caption)
    needles = [_normalized_page_text(caption)]
    if label_match:
        needles.append(_normalized_page_text(label_match.group(0)))
    for needle in needles:
        if not needle:
            continue
        for index, page_text in enumerate(page_texts):
            if needle in page_text:
                return index + 1
    return None


def _caption_after_image(md_text: str, start: int) -> tuple[str, int]:
    """读取图片后首个非空 Markdown 行；若是图注，同时返回消费位置。"""
    tail = md_text[start:]
    match = re.match(
        r"(?P<prefix>[ \t]*(?:\r?\n)[ \t]*(?:\r?\n[ \t]*)*)"
        r"(?P<line>[^\r\n]+)",
        tail,
    )
    if not match:
        return "", start
    caption = _plain_markdown_line(match.group("line"))
    if not _FIGURE_CAPTION_RE.match(caption):
        return "", start
    return caption, start + match.end()


def _figure_caption(block: ParsedBlock) -> tuple[str, str] | None:
    """从独立图注 block 中提取图号和简短图注，排除“由图…可知”等正文引用。"""
    for raw_line in (block.caption or block.text).splitlines():
        line = _plain_markdown_line(raw_line)
        if not line or not _FIGURE_CAPTION_RE.match(line):
            continue
        label = _ANY_FIGURE_LABEL_RE.match(line)
        if label:
            return label.group(0), line[:240]
    return None


def _supplement_missing_figure_images(
    document: ParsedDocument,
    *,
    source_path: str,
    output_dir: Path,
) -> int:
    """为 MinerU 未导出的矢量图表渲染 PDF 区域，生成可供 VLM 使用的图片块。"""
    existing = {
        (
            block.page,
            _normalized_page_text(match.group(0)),
        )
        for block in document.blocks
        if block.block_type == "image"
        for match in [_ANY_FIGURE_LABEL_RE.search(block.caption or block.text)]
        if match
    }
    candidates: list[tuple[ParsedBlock, str, str]] = []
    seen = set(existing)
    for block in document.blocks:
        if not block.page:
            continue
        parsed = _figure_caption(block)
        if parsed is None:
            continue
        label, caption = parsed
        key = (block.page, _normalized_page_text(label))
        if key in seen:
            continue
        seen.add(key)
        candidates.append((block, label, caption))
    try:
        import fitz

        pdf = fitz.open(source_path)
    except Exception as exc:
        logger.warning("无法打开 PDF 以补偿图表图片: %s", exc)
        return 0

    # MinerU 可能把矢量图和图注一起漏掉，但 PDF 自身的文本层仍保留图注。
    # 不能只扫描 MinerU blocks；否则类似“图 5.3-1”虽能被 PyMuPDF 读到，
    # 却永远不会被渲染为可供 VLM 分析的图片。
    page_orders: dict[int, int] = {}
    for block in document.blocks:
        if block.page:
            page_orders[block.page] = max(page_orders.get(block.page, -1), block.order)
    for page_index, page in enumerate(pdf):
        physical_page = page_index + 1
        for raw_line in page.get_text("text").splitlines():
            caption = _plain_markdown_line(raw_line)
            if not caption or not _FIGURE_CAPTION_RE.match(caption):
                continue
            label_match = _ANY_FIGURE_LABEL_RE.match(caption)
            if not label_match:
                continue
            label = label_match.group(0)
            key = (physical_page, _normalized_page_text(label))
            if key in seen:
                continue
            seen.add(key)
            order = page_orders.get(physical_page, -1) + 1
            page_orders[physical_page] = order
            candidates.append(
                (
                    ParsedBlock(
                        block_type="caption",
                        text=caption,
                        page=physical_page,
                        order=order,
                    ),
                    label,
                    caption[:240],
                )
            )

    if not candidates:
        pdf.close()
        return 0

    created = 0
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        for source_block, label, caption in candidates:
            page_index = int(source_block.page or 0) - 1
            if page_index < 0 or page_index >= pdf.page_count:
                continue
            page = pdf[page_index]
            label_rects = page.search_for(label)
            if not label_rects:
                compact = re.sub(r"\s+", "", label)
                label_rects = page.search_for(compact)
            if label_rects:
                caption_rect = label_rects[-1]
                clip = fitz.Rect(
                    page.rect.x0,
                    max(page.rect.y0, caption_rect.y0 - page.rect.height * 0.62),
                    page.rect.x1,
                    min(page.rect.y1, caption_rect.y1 + 24),
                )
            else:
                # 找不到图注坐标时宁可渲染整页，避免再次丢失矢量图。
                clip = page.rect
            safe_label = re.sub(r"[^0-9A-Za-z]+", "_", label).strip("_") or str(created)
            image_file = output_dir / (
                f"page_{source_block.page:04d}_{safe_label}_{created:02d}.png"
            )
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False)
            pixmap.save(image_file)
            document.blocks.append(
                _annotate_block(
                    ParsedBlock(
                        block_type="image",
                        text=caption,
                        page=source_block.page,
                        image_path=str(image_file.resolve()),
                        caption=caption,
                        order=source_block.order,
                        bbox=(clip.x0, clip.y0, clip.x1, clip.y1),
                        pdf_page_label=source_block.pdf_page_label,
                    )
                )
            )
            created += 1
    except Exception as exc:
        logger.warning("PDF 图表补偿渲染失败: %s", exc)
    finally:
        pdf.close()

    if created:
        document.blocks.sort(key=lambda block: (block.page or 0, block.order))
        logger.info("MinerU 补偿渲染 %d 个矢量图表区域", created)
    return created


def _mineru_content_payload(content) -> dict | None:
    """兼容 MinerU 不同版本的 content_list JSON 外层结构。"""
    if isinstance(content, list) and any(isinstance(item, dict) for item in content):
        return {"content_list": content}
    if not isinstance(content, dict):
        return None
    if isinstance(content.get("content_list"), list):
        return content
    data = content.get("data")
    if isinstance(data, dict) and isinstance(data.get("content_list"), list):
        return data
    return None
