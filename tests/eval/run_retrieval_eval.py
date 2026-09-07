"""离线检索评估入口：打印按 course_id 的 Recall@K / MRR。

  uv run --no-sync python -m tests.eval.run_retrieval_eval --chroma ./storage/chroma
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import defaultdict
from pathlib import Path

from src.services.eval_metrics import aggregate_metrics
from src.services.retrieval import retrieve
from src.services.storage.catalog_store import DEFAULT_COURSE_ID
from src.services.storage.vector_store import ChromaVectorStore

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "fixtures" / "retrieval_eval.jsonl"


def load_cases(path: Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def evaluate(
    vs: ChromaVectorStore,
    cases: list[dict],
    *,
    k: int = 5,
    score_threshold: float = 0.0,
) -> dict[str, dict[str, float]]:
    """按 course_id 宏平均；expect_empty 且相关集空时统计 empty_ok_rate。"""
    by_course: dict[str, list[dict]] = defaultdict(list)
    empty_ok = 0
    empty_total = 0

    for c in cases:
        course_id = c["course_id"]
        hits = retrieve(
            c["question"],
            vs,
            course_id,
            top_k=k,
            score_threshold=score_threshold,
        )
        relevant = c.get("relevant_source_files") or []
        if c.get("expect_empty") and not relevant:
            empty_total += 1
            if not hits:
                empty_ok += 1
            continue
        by_course[course_id].append(
            {"ranked_hits": hits, "relevant_source_files": relevant}
        )

    report: dict[str, dict[str, float]] = {}
    for course_id, rows in by_course.items():
        report[course_id] = aggregate_metrics(rows, k=k)

    if empty_total:
        report["_empty_refusal"] = {
            "empty_ok_rate": empty_ok / empty_total,
            "n": float(empty_total),
            "recall_at_k": 0.0,
            "mrr": 0.0,
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieval Recall@K / MRR")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--chroma", type=Path, default=None, help="Chroma 路径；默认临时空库")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument(
        "--score-threshold",
        type=float,
        default=0.0,
        help="评估时阈值；默认 0 以测召回上限",
    )
    args = parser.parse_args()

    cases = load_cases(args.fixture)
    if args.chroma:
        vs = ChromaVectorStore(str(args.chroma))
        try:
            report = evaluate(
                vs, cases, k=args.k, score_threshold=args.score_threshold
            )
        finally:
            vs.close()
    else:
        with tempfile.TemporaryDirectory() as td:
            vs = ChromaVectorStore(td, collection_name="eval_empty")
            try:
                report = evaluate(
                    vs, cases, k=args.k, score_threshold=args.score_threshold
                )
            finally:
                vs.close()
            print(
                "(空库) 正例 Recall/MRR 应为 0；_empty_refusal 在负例上应接近 1。"
                f" DEFAULT_COURSE={DEFAULT_COURSE_ID}"
            )

    for course_id, metrics in sorted(report.items()):
        print(
            f"{course_id}: Recall@{args.k}={metrics.get('recall_at_k', 0):.4f} "
            f"MRR={metrics.get('mrr', metrics.get('empty_ok_rate', 0)):.4f} "
            f"n={int(metrics.get('n', 0))}"
        )


if __name__ == "__main__":
    main()
