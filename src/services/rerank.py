"""BGE CrossEncoder 精排（sentence-transformers，懒加载）。"""

from __future__ import annotations

import logging
import math
from typing import Any

from src.config import config
from src.services.inference_device import load_torch_model, run_torch_inference

logger = logging.getLogger(__name__)

_model: Any = None
_model_name: str | None = None
_model_device: str | None = None


def clear_reranker() -> None:
    """配置变更后丢弃已加载模型。"""
    global _model, _model_name, _model_device
    _model = None
    _model_name = None
    _model_device = None


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _load(model_name: str):
    global _model, _model_name, _model_device
    device = config.retrieval.rerank_device
    if _model is not None and _model_name == model_name and _model_device == device:
        return _model
    from sentence_transformers import CrossEncoder

    logger.info("加载精排模型: %s", model_name)
    _model = load_torch_model(CrossEncoder, model_name, device)
    _model_name = model_name
    _model_device = device
    return _model


def rerank(
    query: str,
    hits: list[dict],
    top_n: int,
    *,
    model_name: str,
    score_fn=None,
) -> list[dict]:
    """对候选 hit 精排，写回 score=sigmoid(logit)，返回 top_n。

    score_fn 可注入（单测）：(query, texts) -> list[float] raw logits。
    """
    if top_n <= 0 or not hits:
        return []
    q = query.strip()
    if not q:
        return hits[:top_n]

    texts = [h.get("text") or "" for h in hits]
    if score_fn is not None:
        raw_scores = list(score_fn(q, texts))
    else:
        model = _load(model_name)
        pred = run_torch_inference(model, lambda: model.predict([[q, t] for t in texts]))
        raw_scores = pred.tolist() if hasattr(pred, "tolist") else list(pred)

    if len(raw_scores) != len(hits):
        raise ValueError(
            f"精排分数数量与候选不一致: scores={len(raw_scores)} hits={len(hits)}"
        )

    ranked: list[dict] = []
    for hit, raw in zip(hits, raw_scores):
        item = dict(hit)
        item["score"] = float(sigmoid(float(raw)))
        item["rerank_logit"] = float(raw)
        ranked.append(item)
    ranked.sort(key=lambda h: h["score"], reverse=True)
    return ranked[:top_n]
