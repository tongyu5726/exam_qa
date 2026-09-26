"""PDF 图像页覆盖：不依赖图注，补充解析器可能遗漏的整页视觉证据。"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from src.config import config
from src.exceptions import BadRequestException
from src.services.parsing import ParsedBlock

logger = logging.getLogger(__name__)


def ensure_pdf_visual_coverage(document, source_path: str) -> int:
    """保守检查位图和矢量页。已有裁剪图不能证明同页其余图都已覆盖。

    扫描页、表格线和装饰也可能命中；不以尺寸阈值过滤小图，避免漏掉有用信息。
    不是语义检测器，特殊 PDF 对象及 VLM 读数仍需人工核验。
    """
    import fitz

    source = Path(source_path)
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    directory = Path(config.storage.parsed_assets_dir).resolve() / digest.hexdigest()[:24]
    if not document.blocks:
        document.blocks = [ParsedBlock("text", p.text, p.page) for p in document.pages]
    added = 0
    try:
        with fitz.open(source_path) as pdf:
            for index, page in enumerate(pdf):
                if not page.get_image_info() and not page.get_drawings():
                    continue
                caption = f"第{index + 1}页完整图表证据（逐一分析页面中的有用图表）"
                if any(b.page == index + 1 and b.caption == caption for b in document.blocks):
                    continue
                directory.mkdir(parents=True, exist_ok=True)
                image = directory / f"visual_page_{index + 1:04d}.png"
                if not image.is_file():
                    page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(image)
                block = ParsedBlock("image", "", index + 1,
                                    image_path=str(image), caption=caption,
                                    pdf_page_label=page.get_label())
                position = next((i for i, b in enumerate(document.blocks)
                                 if b.page is not None and b.page > index + 1), len(document.blocks))
                document.blocks.insert(position, block)
                added += 1
    except Exception as exc:
        raise BadRequestException("PDF 图像页检查或渲染失败，不能确认视觉覆盖", detail=str(exc)) from exc
    logger.info("PDF 视觉覆盖补充完成: %s 新增整页图片=%d", source.name, added)
    return added
