"""检索：向量 + BM25（按 course_id）→ RRF → 阈值过滤。"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from functools import lru_cache

from src.config import config
from src.exceptions import BadRequestException, ServiceUnavailableException
from src.services.embedding import get_embedding_client
from src.services.storage.vector_store import ChromaVectorStore

logger = logging.getLogger(__name__)

RRF_K = 60
_FORMULA_QUERY = re.compile(
    r"公式|表达式|关系式|方程|恒等式|推导|推导过程|数学关系|"
    r"(?:与|和).{1,30}(?:之间)?的?关系(?:是什么|如何|怎样|怎么|$)"
)
_LIST_QUERY = re.compile(
    r"有哪些|哪些|列出|列举|分别(?:是|为)|包括什么|"
    r"几(?:个|条|项|种|步|点|方面|类)|"
    r"必要条件|充分条件|条件|要点|步骤|流程|特点|特征|"
    r"原因|因素|类型|分类|组成|构成|注意事项|要求|原则|措施|方面"
)
_LIST_CONTEXT_CUE = re.compile(
    r"(?:综上|如下|分别|包括|包含|归纳|得出|共有|共计|分为)"
    r".{0,60}(?:条件|步骤|原因|要点|特点|原则|方法|类型|方面|项)"
)
_LIST_COUNT = re.compile(
    r"([0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*"
    r"(?:个|条|项|种|步|点|方面|类)\s*"
    r"(?:必要|充分|主要|基本|核心|关键|具体)?\s*"
    r"(?:条件|要求|步骤|原因|特点|要点|原则|方法|类型|方面|内容)?"
)
_ARABIC_LIST_ITEM = re.compile(
    r"(?m)(?:^|\n)\s*(?:[（(]\s*(\d{1,2})\s*[）)]|(\d{1,2})\s*[、.．])"
)
_CHINESE_LIST_ITEM = re.compile(
    r"(?m)(?:^|\n)\s*(?:[（(]\s*([一二三四五六七八九十两]{1,3})\s*[）)]|"
    r"([一二三四五六七八九十两]{1,3})\s*[、.])"
)

from src.services.tokenizer import tokenize  # noqa: E402


@lru_cache(maxsize=256)
def _cached_query_vec(cache_key: str, normalized_query: str) -> tuple[float, ...]:
    vec = get_embedding_client().embed([normalized_query])[0]
    return tuple(float(x) for x in vec)


def _embed_cache_key() -> str:
    emb = config.embedding
    return f"{emb.provider}|{emb.model}"


def clear_query_embed_cache() -> None:
    _cached_query_vec.cache_clear()


class _BM25:
    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_tokens = corpus_tokens
        self.n = len(corpus_tokens)
        self.doc_len = [len(t) for t in corpus_tokens]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        df: Counter[str] = Counter()
        for toks in corpus_tokens:
            df.update(set(toks))
        self.idf = {
            t: math.log(1 + (self.n - f + 0.5) / (f + 0.5)) for t, f in df.items()
        }

    def scores(self, query_tokens: list[str]) -> list[float]:
        if not self.n or not query_tokens:
            return [0.0] * self.n
        out: list[float] = []
        for i, toks in enumerate(self.corpus_tokens):
            tf = Counter(toks)
            dl = self.doc_len[i]
            s = 0.0
            for q in query_tokens:
                if q not in tf:
                    continue
                freq = tf[q]
                denom = freq + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1.0))
                s += self.idf.get(q, 0.0) * (freq * (self.k1 + 1)) / denom
            out.append(s)
        return out


def rrf_fuse(*ranked_lists: list[dict], k: int = RRF_K, top_k: int) -> list[dict]:
    scores: dict[str, float] = {}
    best: dict[str, dict] = {}
    for ranked in ranked_lists:
        for rank, hit in enumerate(ranked, start=1):
            cid = hit["id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            prev = best.get(cid)
            if prev is None or hit.get("score", 0) > prev.get("score", 0):
                best[cid] = hit
    return [
        best[cid]
        for cid, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    ]


def _vector_search(
    query: str,
    vs: ChromaVectorStore,
    course_id: str,
    top_k: int,
    *,
    scenario: str | None = None,
    as_of: str | None = None,
) -> list[dict]:
    try:
        query_vec = list(_cached_query_vec(_embed_cache_key(), query))
    except Exception as e:
        logger.error("检索向量化失败: %s", e)
        raise ServiceUnavailableException("向量化服务不可用", detail=str(e)) from e
    # 保持无证据约束时的调用形态，兼容现有工具实现；有约束才传给向量库。
    kwargs: dict[str, object] = {"top_k": top_k, "course_id": course_id}
    if scenario:
        kwargs["scenario"] = scenario
    if as_of:
        kwargs["as_of"] = as_of
    return vs.search(query_vec, **kwargs)


_FIGURE_QUERY_REF = re.compile(
    r"(?:图|figure|fig\.?)\s*([0-9]+(?:[.\-][0-9]+)*)",
    re.IGNORECASE,
)


def retrieve_visual_evidence(
    query: str,
    vs: ChromaVectorStore,
    course_id: str,
    *,
    top_k: int = 3,
    scenario: str | None = None,
    as_of: str | None = None,
) -> list[dict]:
    """为读图问题单独检索 image_summary，避免图表被普通正文挤出 Top-K。"""
    q = query.strip()
    if not q or top_k <= 0:
        return []
    try:
        query_vec = list(_cached_query_vec(_embed_cache_key(), q))
        hits: list[dict] = []
        seen_ids: set[str] = set()
        for block_type in ("image_summary", "image"):
            kwargs: dict[str, object] = {
                "top_k": top_k,
                "course_id": course_id,
                "block_type": block_type,
            }
            if scenario:
                kwargs["scenario"] = scenario
            if as_of:
                kwargs["as_of"] = as_of
            for hit in vs.search(query_vec, **kwargs):
                chunk_id = str(hit.get("id") or "")
                if chunk_id and chunk_id not in seen_ids:
                    seen_ids.add(chunk_id)
                    hits.append(hit)
        hits.sort(key=lambda hit: float(hit.get("score") or 0.0), reverse=True)
    except Exception as exc:
        # 视觉召回是增强链路，失败时仍保留普通正文回答。
        logger.warning("图表证据检索失败: %s", exc)
        return []

    requested = {
        match.group(1).replace(".", "-")
        for match in _FIGURE_QUERY_REF.finditer(q)
    }
    if requested:
        exact = []
        for hit in hits:
            meta = hit.get("metadata") or {}
            searchable = f"{hit.get('text', '')} {meta.get('image_caption', '')}"
            available = {
                match.group(1).replace(".", "-")
                for match in _FIGURE_QUERY_REF.finditer(searchable)
            }
            if requested.intersection(available):
                exact.append(hit)
        if exact:
            hits = exact

    output: list[dict] = []
    for hit in hits[:top_k]:
        item = dict(hit)
        meta = dict(hit.get("metadata") or {})
        meta["retrieval_reason"] = "visual_query"
        item["metadata"] = meta
        output.append(item)
    return output


_BM25_CACHE: dict[str, tuple[list[dict], _BM25]] = {}


def _bm25_for_course(vs: ChromaVectorStore, course_id: str) -> tuple[list[dict], _BM25]:
    """按 course_id 缓存语料与倒排索引，避免每次检索全量拉取并重建。"""
    cached = _BM25_CACHE.get(course_id)
    if cached is not None:
        return cached
    corpus = vs.get_by_course_id(course_id)
    entry = (corpus, _BM25([tokenize(c.get("text", "")) for c in corpus]))
    _BM25_CACHE[course_id] = entry
    return entry


def invalidate_bm25_cache(course_id: str | None = None) -> None:
    """使 BM25 语料缓存失效；course_id 为空时清空全部课程缓存。"""
    if course_id is None:
        _BM25_CACHE.clear()
    else:
        _BM25_CACHE.pop(course_id, None)


def _is_applicable(meta: dict, scenario: str | None, as_of: str | None) -> bool:
    scope = str(meta.get("applicability_scope") or "all")
    if scenario and scenario != "all" and scope not in ("all", scenario):
        return False
    if as_of:
        start = str(meta.get("effective_from") or "0001-01-01")
        end = str(meta.get("effective_to") or "9999-12-31")
        if not (start <= as_of <= end):
            return False
    return True


def _bm25_search(
    query: str,
    vs: ChromaVectorStore,
    course_id: str,
    top_k: int,
    *,
    scenario: str | None = None,
    as_of: str | None = None,
) -> list[dict]:
    corpus, bm25 = _bm25_for_course(vs, course_id)
    if not corpus:
        return []
    q_tokens = tokenize(query)
    if not q_tokens:
        return []
    raw = bm25.scores(q_tokens)
    max_s = max(raw) if raw else 0.0
    ranked = sorted(range(len(raw)), key=lambda i: raw[i], reverse=True)
    hits: list[dict] = []
    for i in ranked:
        if raw[i] <= 0 or len(hits) >= top_k:
            break
        hit = dict(corpus[i])
        if not _is_applicable(hit.get("metadata") or {}, scenario, as_of):
            continue
        hit["score"] = (raw[i] / max_s) if max_s > 0 else 0.0
        hits.append(hit)
    return hits


def _select_evidence(hits: list[dict]) -> list[dict]:
    """在已相关、已过滤的候选中按权威等级和生效时间选择更适用的证据。"""
    return sorted(
        hits,
        key=lambda hit: (
            int((hit.get("metadata") or {}).get("authority_level") or 0),
            str((hit.get("metadata") or {}).get("effective_from") or "0001-01-01"),
            float(hit.get("score") or 0.0),
        ),
        reverse=True,
    )


def _is_formula_query(query: str) -> bool:
    """规则层识别明确的公式型查询，避免为普通问答扩大证据集。"""
    return bool(_FORMULA_QUERY.search(query))


def _chinese_number(value: str) -> int | None:
    """解析列表中常见的一到九十九；超出范围时不做猜测。"""
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    return digits.get(value)


def _number_value(value: str) -> int | None:
    if value.isdigit():
        number = int(value)
        return number if 0 < number <= 99 else None
    return _chinese_number(value)


def _list_item_numbers(text: str) -> set[int]:
    numbers: set[int] = set()
    for match in _ARABIC_LIST_ITEM.finditer(text or ""):
        value = match.group(1) or match.group(2)
        if value:
            numbers.add(int(value))
    for match in _CHINESE_LIST_ITEM.finditer(text or ""):
        value = match.group(1) or match.group(2)
        number = _chinese_number(value) if value else None
        if number:
            numbers.add(number)
    return numbers


def _expected_list_count(text: str) -> int | None:
    counts = [
        number
        for match in _LIST_COUNT.finditer(text or "")
        if (number := _number_value(match.group(1))) is not None
    ]
    return max(counts) if counts else None


def _is_list_query(query: str) -> bool:
    """识别期望得到完整枚举结果的问题。"""
    return bool(_LIST_QUERY.search(query))


def _expand_list_evidence(
    query: str,
    anchors: list[dict],
    vs: ChromaVectorStore,
    course_id: str,
    top_k: int,
    *,
    scenario: str | None = None,
    as_of: str | None = None,
) -> list[dict]:
    """命中列表标题/编号项后，补齐附近的连续编号块。"""
    by_doc: dict[str, list[dict]] = {}
    for hit in anchors:
        meta = hit.get("metadata") or {}
        doc_id = str(meta.get("doc_id") or "")
        chunk_index = meta.get("chunk_index")
        if doc_id and isinstance(chunk_index, int):
            by_doc.setdefault(doc_id, []).append(hit)

    expanded: list[dict] = []
    seen_ids = {str(hit.get("id") or "") for hit in anchors}
    for doc_id, doc_anchors in by_doc.items():
        kwargs: dict[str, object] = {"course_id": course_id, "doc_id": doc_id, "limit": 2000}
        if scenario:
            kwargs["scenario"] = scenario
        if as_of:
            kwargs["as_of"] = as_of
        try:
            rows = vs.get_chunks(**kwargs)
        except Exception as exc:
            logger.warning("列表完整性证据读取失败 doc_id=%s: %s", doc_id, exc)
            continue

        rows = [
            row
            for row in rows
            if (row.get("metadata") or {}).get("block_type") != "formula"
            and isinstance((row.get("metadata") or {}).get("chunk_index"), int)
        ]
        rows.sort(key=lambda row: int((row.get("metadata") or {}).get("chunk_index") or 0))

        for anchor in sorted(doc_anchors, key=lambda hit: float(hit.get("score") or 0.0), reverse=True):
            anchor_meta = anchor.get("metadata") or {}
            anchor_index = int(anchor_meta.get("chunk_index") or 0)
            anchor_page = anchor_meta.get("page")
            window: list[dict] = []
            for row in rows:
                meta = row.get("metadata") or {}
                chunk_index = int(meta.get("chunk_index") or 0)
                if not (anchor_index - 2 <= chunk_index <= anchor_index + 12):
                    continue
                page = meta.get("page")
                if (
                    isinstance(anchor_page, int)
                    and anchor_page > 0
                    and isinstance(page, int)
                    and page > 0
                    and abs(page - anchor_page) > 1
                ):
                    continue
                window.append(row)

            local_text = "\n".join(str(row.get("text") or "") for row in window)
            expected = _expected_list_count(f"{query}\n{local_text}")
            item_rows = [row for row in window if _list_item_numbers(str(row.get("text") or ""))]
            all_numbers = (
                set().union(*(_list_item_numbers(str(row.get("text") or "")) for row in item_rows))
                if item_rows
                else set()
            )
            anchor_numbers = _list_item_numbers(str(anchor.get("text") or ""))
            has_cue = bool(_LIST_CONTEXT_CUE.search(local_text))
            if not item_rows or 1 not in all_numbers:
                continue
            if expected is None and not has_cue and not (anchor_numbers and len(all_numbers) >= 2):
                continue

            target = (
                set(range(1, expected + 1))
                if expected
                else set(range(1, min(max(all_numbers), 12) + 1))
            )
            nearby_anchors = [
                hit
                for hit in doc_anchors
                if abs(int((hit.get("metadata") or {}).get("chunk_index") or 0) - anchor_index) <= 12
            ]
            coverage = (
                set().union(*(_list_item_numbers(str(hit.get("text") or "")) for hit in nearby_anchors))
                if nearby_anchors
                else set()
            )
            anchor_score = max(float(hit.get("score") or 0.0) for hit in doc_anchors)
            expansion_limit = min(12, max(top_k * 2, expected or 0, 4))
            for row in item_rows:
                numbers = _list_item_numbers(str(row.get("text") or ""))
                if not ((numbers & target) - coverage):
                    continue
                coverage.update(numbers)
                chunk_id = str(row.get("id") or "")
                if chunk_id and chunk_id not in seen_ids:
                    seen_ids.add(chunk_id)
                    item = dict(row)
                    meta = dict(row.get("metadata") or {})
                    item["metadata"] = meta
                    distance = abs(int(meta.get("chunk_index") or 0) - anchor_index)
                    item["score"] = max(
                        float(item.get("score") or 0.0),
                        anchor_score - 0.005 * (distance + 1),
                    )
                    meta["retrieval_reason"] = "list_completion"
                    expanded.append(item)
                if target.issubset(coverage) or len(expanded) >= expansion_limit:
                    break
            if target.issubset(coverage):
                break
    return expanded


def _expand_formula_evidence(
    query: str,
    anchors: list[dict],
    vs: ChromaVectorStore,
    course_id: str,
    top_k: int,
    *,
    scenario: str | None = None,
    as_of: str | None = None,
) -> list[dict]:
    """按正文命中的文档和页码，补召回同页及相邻页的独立公式块。"""
    by_doc: dict[str, list[dict]] = {}
    for hit in anchors:
        meta = hit.get("metadata") or {}
        if meta.get("block_type") == "formula":
            continue
        doc_id = str(meta.get("doc_id") or "")
        page = meta.get("page")
        if not doc_id or not isinstance(page, int) or page < 1:
            continue
        by_doc.setdefault(doc_id, []).append(hit)

    candidates: list[tuple[int, int, int, dict]] = []
    query_tokens = set(tokenize(query))
    for doc_id, doc_anchors in by_doc.items():
        kwargs: dict[str, object] = {
            "course_id": course_id,
            "doc_id": doc_id,
            "block_type": "formula",
            "limit": 500,
        }
        if scenario:
            kwargs["scenario"] = scenario
        if as_of:
            kwargs["as_of"] = as_of
        try:
            formulas = vs.get_chunks(**kwargs)
        except Exception as exc:
            # 邻页扩展属于增益路径；读取失败时仍保留原始混合检索结果。
            logger.warning("公式邻页证据读取失败 doc_id=%s: %s", doc_id, exc)
            continue
        anchor_pages = [int((hit.get("metadata") or {})["page"]) for hit in doc_anchors]
        for formula in formulas:
            meta = formula.get("metadata") or {}
            page = meta.get("page")
            if not isinstance(page, int) or page < 1:
                continue
            distance = min(abs(page - anchor_page) for anchor_page in anchor_pages)
            if distance > 1:
                continue
            searchable = f"{formula.get('text', '')} {meta.get('context', '')} {meta.get('section_path', '')}"
            overlap = len(query_tokens.intersection(tokenize(searchable)))
            candidates.append(
                (-overlap, distance, int(meta.get("chunk_index") or 0), formula)
            )

    # 公式页通常包含一组连续推导；保留最多 top_k 条，不让长论文无限扩大 prompt。
    limit = max(3, min(top_k, 8))
    expanded: list[dict] = []
    # 已经由向量/BM25 命中的公式不占扩展名额，把预算留给尚未出现的关键公式。
    seen: set[str] = {str(hit.get("id") or "") for hit in anchors}
    for _, distance, _, formula in sorted(candidates, key=lambda item: item[:3]):
        chunk_id = str(formula.get("id") or "")
        if not chunk_id or chunk_id in seen:
            continue
        seen.add(chunk_id)
        item = dict(formula)
        meta = dict(formula.get("metadata") or {})
        item["metadata"] = meta
        same_doc_anchors = by_doc.get(str(meta.get("doc_id") or ""), [])
        anchor_score = max((float(hit.get("score") or 0.0) for hit in same_doc_anchors), default=0.0)
        item["score"] = max(float(item.get("score") or 0.0), anchor_score - 0.01 * (distance + 1))
        meta["retrieval_reason"] = "formula_same_page" if distance == 0 else "formula_adjacent_page"
        expanded.append(item)
        if len(expanded) >= limit:
            break
    return expanded


def retrieve(
    query: str,
    vs: ChromaVectorStore,
    course_id: str,
    top_k: int | None = None,
    *,
    score_threshold: float | None = None,
    rerank_enabled: bool | None = None,
    scenario: str | None = None,
    as_of: str | None = None,
) -> list[dict]:
    if top_k is None:
        top_k = config.retrieval.top_k
    if score_threshold is None:
        score_threshold = config.retrieval.score_threshold
    if rerank_enabled is None:
        rerank_enabled = config.retrieval.rerank_enabled
    if top_k <= 0:
        raise BadRequestException("top_k 必须大于 0")

    q = query.strip()
    if not q:
        return []
    if not course_id or not course_id.strip():
        raise BadRequestException("course_id 不能为空")

    # 精排开：宽池 → RRF → CrossEncoder → top_n
    if rerank_enabled:
        pool = max(config.retrieval.rerank_candidates, top_k)
        fuse_k = pool
    else:
        pool = top_k * 2
        fuse_k = top_k

    vec_hits = _vector_search(
        q, vs, course_id, pool, scenario=scenario, as_of=as_of
    )
    bm25_hits = _bm25_search(
        q, vs, course_id, pool, scenario=scenario, as_of=as_of
    )
    fused = (
        rrf_fuse(vec_hits, bm25_hits, top_k=fuse_k) if (vec_hits or bm25_hits) else []
    )

    if rerank_enabled and fused:
        from src.services.rerank import rerank as _rerank

        top_n = config.retrieval.rerank_top_n or top_k
        ranked = _rerank(
            q,
            fused,
            top_n,
            model_name=config.retrieval.rerank_model,
        )
        kept = [h for h in ranked if h.get("score", 0) >= score_threshold]
    else:
        kept = [h for h in fused if h.get("score", 0) >= score_threshold]

    formula_query = _is_formula_query(q)
    list_query = _is_list_query(q)
    # 列表问题中，单符号公式容易挤掉真正的编号项。
    # 除非问题同时明确要求公式，先将召回名额留给正文。
    if list_query and not formula_query:
        kept = [
            hit
            for hit in kept
            if (hit.get("metadata") or {}).get("block_type") != "formula"
        ]

    list_hits: list[dict] = []
    if list_query and kept:
        list_hits = _expand_list_evidence(
            q,
            kept,
            vs,
            course_id,
            top_k,
            scenario=scenario,
            as_of=as_of,
        )
        kept.extend(list_hits)

    formula_hits: list[dict] = []
    if formula_query and kept:
        formula_hits = _expand_formula_evidence(
            q,
            kept,
            vs,
            course_id,
            top_k,
            scenario=scenario,
            as_of=as_of,
        )
        # 正文先给出语义范围，再给公式；扩展命中的公式优先于初筛里常见的
        # 单符号碎片，并限制公式总量，避免公式型问题让 prompt 无上限膨胀。
        body_hits = [
            hit for hit in kept if (hit.get("metadata") or {}).get("block_type") != "formula"
        ]
        formula_candidates = formula_hits + [
            hit for hit in kept if (hit.get("metadata") or {}).get("block_type") == "formula"
        ]
        formula_limit = max(3, min(top_k, 8))
        selected_formulas: list[dict] = []
        seen_formula_ids: set[str] = set()
        for hit in formula_candidates:
            chunk_id = str(hit.get("id") or "")
            if not chunk_id or chunk_id in seen_formula_ids:
                continue
            seen_formula_ids.add(chunk_id)
            selected_formulas.append(hit)
            if len(selected_formulas) >= formula_limit:
                break
        kept = body_hits + selected_formulas

    if scenario or as_of:
        kept = _select_evidence(kept)

    logger.info(
        "混合检索: course=%s scenario=%s as_of=%s top_k=%d rerank=%s vec=%d bm25=%d list=%d formula=%d kept=%d",
        course_id, scenario or "all", as_of or "latest",
        top_k,
        rerank_enabled,
        len(vec_hits),
        len(bm25_hits),
        len(list_hits),
        len(formula_hits),
        len(kept),
    )
    return kept
