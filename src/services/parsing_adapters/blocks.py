from __future__ import annotations

import logging
import hashlib
import re
import shutil
from pathlib import Path
from src.config import config
from src.services.parsing_adapters.models import ParsedBlock, ParsedDocument

logger = logging.getLogger(__name__)


_TEXTBOOK_REFERENCE_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])(?:P|p)\s*\d{1,4}(?:\s*(?:例|Ex|T|习题)\s*\d+(?:\s*\([^)]{1,20}\))*)?"),
    re.compile(r"第\s*\d{1,4}\s*页"),
    re.compile(r"(?<![\u4e00-\u9fffA-Za-z0-9])(?:例|Ex|T)\s*\d+(?:\s*\([^)]{1,20}\))*", re.I),
)


_ANNOTATION_MARKER = re.compile(r"^(?:考点|注意|易错|方法|提示|结论|证明|思路)\s*[:：]", re.I)


_UI_NOISE = re.compile(
    r"^(?:\d{1,2}:\d{2}|[2-5]G|[0-9]{1,3}%|仅供参考|如有错误欢迎指正|"
    r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})$",
    re.I,
)


def _extract_textbook_references(text: str) -> tuple[str, ...]:
    """提取“教材内页码/题号”候选，不把它误作 PDF 物理页。"""
    found: list[str] = []
    for pattern in _TEXTBOOK_REFERENCE_PATTERNS:
        for match in pattern.finditer(text or ""):
            value = re.sub(r"\s+", " ", match.group(0)).strip()
            if value and value not in found:
                found.append(value)
    # P28 Ex2 同时会命中 P28 Ex2 与 Ex2；保留信息更完整的引用即可。
    return tuple(
        value
        for value in found
        if not any(value != candidate and value in candidate for candidate in found)
    )


def _content_role(block_type: str, text: str) -> str:
    """保守地标记版面角色；只滤明显噪声，红蓝批注等仍作为可检索证据保留。"""
    if block_type in {"header", "footer", "page_number", "footnote"}:
        return "noise"
    clean = " ".join((text or "").split())
    if clean and len(clean) <= 100 and _UI_NOISE.fullmatch(clean):
        return "noise"
    if _ANNOTATION_MARKER.match(clean):
        return "annotation"
    if clean and len(clean) <= 80 and _extract_textbook_references(clean):
        return "reference"
    return "content"


def _annotate_block(block: ParsedBlock) -> ParsedBlock:
    block.textbook_references = _extract_textbook_references(block.text)
    block.content_role = _content_role(block.block_type, block.text)
    return block


_CAPTION_TYPES = frozenset({"image_caption", "table_caption", "figure_caption"})


def _caption_text(block: dict) -> str:
    """图片/表格说明文本（兼容 text 与 lines 两种形态）。"""
    text = (block.get("text") or "").strip()
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
    return " ".join(parts)


def _as_bbox(raw) -> tuple | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        return tuple(float(v) for v in raw)
    except (TypeError, ValueError):
        return None


def _resolve_image_path(img_path: str | None, base_dir: Path | None) -> str:
    """MinerU 图片路径可能是相对 json 输出目录的，解析为绝对路径。"""
    if not img_path:
        return ""
    p = Path(img_path)
    if not p.is_absolute() and base_dir is not None:
        p = base_dir / p
    return str(p.resolve()) if p.exists() else ""


def _persist_parser_assets(
    document: ParsedDocument, *, work_dir: Path, source_path: str
) -> None:
    """在清理外部解析器临时目录前，持久化其中的图片资产。

    MinerU / MarkPDFdown 的 JSON 可能引用临时目录中的裁剪图。若不复制，入库阶段
    的视觉摘要必然读不到图片。仅复制位于本次 work_dir 下、且确实被 block 引用的文件。
    """
    asset_blocks = [b for b in document.blocks if b.image_path]
    if not asset_blocks:
        return
    try:
        source = Path(source_path)
        try:
            stamp = f"{source.resolve()}:{source.stat().st_size}:{source.stat().st_mtime_ns}"
        except OSError:
            stamp = str(source.resolve())
        target_dir = Path(config.storage.parsed_assets_dir).resolve() / hashlib.sha256(
            stamp.encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        target_dir.mkdir(parents=True, exist_ok=True)
        root = work_dir.resolve()
        copied: dict[Path, Path] = {}
        for index, block in enumerate(asset_blocks):
            original = Path(block.image_path)
            try:
                original = original.resolve()
                original.relative_to(root)
            except (OSError, ValueError):
                continue
            if not original.is_file():
                continue
            if original not in copied:
                destination = target_dir / f"{len(copied):03d}_{original.name}"
                shutil.copy2(original, destination)
                copied[original] = destination
            block.image_path = str(copied[original])
        if copied:
            logger.info("已持久化 %d 个解析图片资产: %s", len(copied), target_dir)
    except OSError as exc:
        # 图片资产失败不能让文本解析链路失效；后续按 caption 回退。
        logger.warning("解析图片资产持久化失败: %s", exc)
