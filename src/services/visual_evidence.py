"""问答与 Agent 共用的图表识别和面向问题的视觉复核。"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

VISUAL_QUERY_RE = re.compile(
    r"图(?:表|像|中|\s*\d)|曲线|坐标|横轴|纵轴|图例|趋势|峰值|谷值|"
    r"波数|反射率|透射率|高于|低于|以上还是以下|随.+变化",
    re.IGNORECASE,
)


def needs_visual_verification(question: str, hits: list[dict]) -> bool:
    if not VISUAL_QUERY_RE.search(question):
        return False
    return any(
        (hit.get("metadata") or {}).get("block_type") in {"image", "image_summary"}
        and (hit.get("metadata") or {}).get("image_path")
        for hit in hits
    )


def verify_visual_evidence(question: str, hits: list[dict]) -> list[dict]:
    """对最相关图表做一次面向当前问题的 VLM 复核；失败时保留入库摘要。"""
    if not needs_visual_verification(question, hits):
        return hits
    from src.dependencies import get_vision_client
    from src.services.vision import DEFAULT_VISUAL_PROMPT

    vision = get_vision_client()
    if not vision.configured:
        return hits

    for index, hit in enumerate(hits):
        metadata = hit.get("metadata") or {}
        if metadata.get("block_type") not in {"image", "image_summary"}:
            continue
        image_path = str(metadata.get("image_path") or "")
        if not image_path or not Path(image_path).is_file():
            continue
        prompt = (
            f"{DEFAULT_VISUAL_PROMPT}\n\n"
            f"当前用户问题：{question}\n"
            "请优先直接回答这个问题；涉及读图数值时说明是近似读数。"
        )
        try:
            analysis = vision.analyze_file(
                image_path,
                prompt=prompt,
                caption=str(metadata.get("image_caption") or ""),
                page=int(metadata["page"]) if int(metadata.get("page") or -1) > 0 else None,
            )
        except Exception as exc:
            logger.warning("查询时图表复核失败，继续使用入库摘要: %s", exc)
            return hits
        enriched = dict(hit)
        enriched["metadata"] = dict(metadata)
        enriched["text"] = (
            f"{hit.get('text', '').strip()}\n"
            f"针对当前问题的视觉复核：{analysis}"
        ).strip()
        output = list(hits)
        output[index] = enriched
        logger.info(
            "查询时图表复核完成: doc=%s page=%s model=%s",
            metadata.get("doc_id"),
            metadata.get("page"),
            vision.model,
        )
        return output
    return hits
