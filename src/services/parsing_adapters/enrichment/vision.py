from __future__ import annotations

import logging
from pathlib import Path
from src.config import config

logger = logging.getLogger(__name__)




def _summarize_image(image_path: str, caption: str, page: int | None, context: str = "") -> str | None:
    """用配置的视觉模型生成图片/图表摘要（多模态视觉理解）。

    未配置 VISUAL_MODEL / 图片缺失 / 调用失败时返回 None，由调用方回退占位，
    不阻断入库主链路。
    """
    if not config.parsing.visual_model or not image_path:
        return None
    path = Path(image_path)
    if not path.exists():
        logger.warning("图片不存在，跳过视觉摘要: %s", image_path)
        return None
    try:
        # 延迟导入避免 dependencies -> service 的初始化环。
        from src.dependencies import get_vision_client
        from src.services.vision import DEFAULT_VISUAL_PROMPT

        prompt = DEFAULT_VISUAL_PROMPT
        if context:
            prompt += "\n邻近正文仅供理解上下文，不可冒充图中事实：\n" + context
        text = get_vision_client().analyze_file(
            path,
            prompt=prompt,
            caption=caption,
            page=page,
        )
        if text:
            logger.info(
                "视觉摘要完成: %s (第%s页)", Path(image_path).name, page or "?"
            )
            return text
    except Exception as exc:
        logger.warning("视觉摘要失败，图片按图注文本处理: %s", exc)
    return None
