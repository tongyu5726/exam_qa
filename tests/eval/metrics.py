"""离线检索评估：Recall@K / MRR（BEIR 风格，按 source_file 判定相关）。"""

from __future__ import annotations

from typing import Iterable


def _ranked_sources(ranked_hits: list[dict]) -> list[str]:
    out: list[str] = []
    for h in ranked_hits:
        meta = h.get("metadata") or {}
        src = h.get("source_file") or meta.get("source_file") or ""
        out.append(str(src))
    return out


def recall_at_k(
    ranked_hits: list[dict],
    relevant_source_files: Iterable[str],
    k: int,
) -> float:
    """Recall@K：top-K 命中相关 source_file 的比例；相关集空 → 0。"""
    if k <= 0:
        return 0.0
    relevant = {s for s in relevant_source_files if s}
    if not relevant:
        return 0.0
    top = set(_ranked_sources(ranked_hits)[:k])
    return len(top & relevant) / len(relevant)


def mrr(
    ranked_hits: list[dict],
    relevant_source_files: Iterable[str],
) -> float:
    """MRR：首个相关文档 rank 的倒数；无命中 → 0。"""
    relevant = {s for s in relevant_source_files if s}
    if not relevant:
        return 0.0
    for i, src in enumerate(_ranked_sources(ranked_hits), start=1):
        if src in relevant:
            return 1.0 / i
    return 0.0


def aggregate_metrics(
    per_query: list[dict],
    *,
    k: int = 5,
) -> dict[str, float]:
    """对多条 {ranked_hits, relevant_source_files} 求宏平均。"""
    if not per_query:
        return {"recall_at_k": 0.0, "mrr": 0.0, "n": 0.0}
    recalls = [
        recall_at_k(q["ranked_hits"], q["relevant_source_files"], k) for q in per_query
    ]
    mrrs = [mrr(q["ranked_hits"], q["relevant_source_files"]) for q in per_query]
    n = len(per_query)
    return {
        "recall_at_k": sum(recalls) / n,
        "mrr": sum(mrrs) / n,
        "n": float(n),
    }
