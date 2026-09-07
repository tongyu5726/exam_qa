"""检索主链：course_id 隔离、阈值拒答、BM25/RRF、精排。"""

from unittest.mock import MagicMock, patch

from src.services.rerank import clear_reranker, rerank
from src.services.retrieval import (
    _BM25,
    _bm25_search,
    _select_evidence,
    clear_query_embed_cache,
    invalidate_bm25_cache,
    retrieve,
    rrf_fuse,
    tokenize,
)
from src.services.storage.catalog_store import (
    DEFAULT_COLLEGE_ID,
    DEFAULT_COURSE_ID,
    DEFAULT_COURSE_NAME,
)


def _chunk(doc_id: str, course_id: str, text: str, course: str = "课"):
    return {
        "doc_id": doc_id,
        "source_file": f"{doc_id}.md",
        "chunk_index": 0,
        "course": course,
        "course_id": course_id,
        "college_id": DEFAULT_COLLEGE_ID,
        "text": text,
    }


def test_vector_search_filters_by_course_id(vector_store):
    dim = 8
    vector_store.upsert(
        [_chunk("1", DEFAULT_COURSE_ID, "默认课：傅里叶变换", DEFAULT_COURSE_NAME)],
        [[1.0] + [0.0] * (dim - 1)],
    )
    vector_store.upsert(
        [_chunk("2", "course-ideology-2025", "思政课：考试题型", "思政原理")],
        [[0.0, 1.0] + [0.0] * (dim - 2)],
    )
    q = [1.0] + [0.0] * (dim - 1)
    hits = vector_store.search(q, top_k=5, course_id=DEFAULT_COURSE_ID)
    assert len(hits) == 1
    assert hits[0]["metadata"]["course_id"] == DEFAULT_COURSE_ID
    assert "傅里叶" in hits[0]["text"]
    hits_b = vector_store.search(q, top_k=5, course_id="course-ideology-2025")
    assert len(hits_b) == 1
    assert hits_b[0]["metadata"]["course_id"] == "course-ideology-2025"
    assert vector_store.search(q, top_k=5, course_id="course-nonexistent") == []


def test_vector_search_keeps_parse_provenance(vector_store):
    dim = 8
    chunk = _chunk("1", DEFAULT_COURSE_ID, "表格中的信源定义", DEFAULT_COURSE_NAME)
    chunk.update(
        {
            "bbox": "10.00,20.00,300.00,120.00",
            "parser_name": "mineru:high",
            "parse_quality": 0.91,
        }
    )
    vector_store.upsert([chunk], [[1.0] + [0.0] * (dim - 1)])

    hit = vector_store.search([1.0] + [0.0] * (dim - 1), top_k=1, course_id=DEFAULT_COURSE_ID)[0]
    assert hit["metadata"]["bbox"] == "10.00,20.00,300.00,120.00"
    assert hit["metadata"]["parser_name"] == "mineru:high"
    assert hit["metadata"]["parse_quality"] == 0.91


def test_search_requires_course_id(vector_store):
    import pytest

    with pytest.raises(ValueError, match="course_id"):
        vector_store.search([0.0] * 8, top_k=5)


def test_bm25_isolated_by_course_id(vector_store):
    dim = 8
    vector_store.upsert(
        [_chunk("1", DEFAULT_COURSE_ID, "默认课专有词：紫薇星傅里叶变换")],
        [[0.1] * dim],
    )
    vector_store.upsert(
        [_chunk("2", "course-ideology-2025", "思政课专有词：论述题")],
        [[0.2] * dim],
    )
    assert len(_bm25_search("紫薇星", vector_store, DEFAULT_COURSE_ID, 5)) == 1
    assert _bm25_search("紫薇星", vector_store, "course-ideology-2025", 5) == []
    assert len(_bm25_search("论述题", vector_store, "course-ideology-2025", 5)) == 1


def test_tokenize_and_bm25_rank():
    toks = tokenize("卷积定理 Convolution")
    assert "卷积" in toks and "convolution" in toks
    scores = _BM25(
        [["傅里叶", "变换"], ["卷积", "定理"], ["采样"]]
    ).scores(["卷积", "定理"])
    assert scores[1] > scores[0]


def test_rrf_prefers_agreement():
    a = [{"id": "x", "score": 0.9, "text": "x"}, {"id": "y", "score": 0.8, "text": "y"}]
    b = [{"id": "y", "score": 0.7, "text": "y"}, {"id": "z", "score": 0.6, "text": "z"}]
    fused = rrf_fuse(a, b, top_k=3)
    assert fused[0]["id"] == "y"
    assert {h["id"] for h in fused} == {"x", "y", "z"}


def test_evidence_selection_prefers_authority_then_newer_effective_time():
    hits = [
        {"id": "course", "score": 0.99, "metadata": {"authority_level": 40, "effective_from": "2026-01-01"}},
        {"id": "rule-old", "score": 0.70, "metadata": {"authority_level": 80, "effective_from": "2025-01-01"}},
        {"id": "rule-new", "score": 0.50, "metadata": {"authority_level": 80, "effective_from": "2026-01-01"}},
    ]
    assert [hit["id"] for hit in _select_evidence(hits)] == ["rule-new", "rule-old", "course"]


def test_retrieve_filters_below_threshold():
    clear_query_embed_cache()
    vs = MagicMock()
    vs.search.return_value = [
        {"id": "a", "score": 0.9, "text": "a", "metadata": {}},
        {"id": "b", "score": 0.1, "text": "b", "metadata": {}},
    ]
    vs.get_by_course_id.return_value = []
    with patch(
        "src.services.retrieval._cached_query_vec", return_value=(0.1, 0.2)
    ):
        out = retrieve(
            "三重积分",
            vs,
            "course-default",
            top_k=5,
            score_threshold=0.25,
            rerank_enabled=False,
        )
    assert len(out) == 1
    assert out[0]["id"] == "a"


def test_retrieve_empty_query():
    vs = MagicMock()
    assert retrieve("   ", vs, "course-default") == []
    vs.search.assert_not_called()


def test_formula_query_expands_same_and_adjacent_page_formula_evidence():
    clear_query_embed_cache()
    vs = MagicMock()
    vs.search.return_value = [
        {
            "id": "body-1",
            "score": 0.9,
            "text": "折射率与介电常数的关系可表示为：",
            "metadata": {"doc_id": "44", "page": 5, "block_type": "text"},
        }
    ]
    vs.get_by_course_id.return_value = []
    vs.get_chunks.return_value = [
        {
            "id": "formula-4",
            "score": 0.0,
            "text": r"公式：$$\varepsilon_r=n^2$$",
            "metadata": {"doc_id": "44", "page": 4, "chunk_index": 10, "block_type": "formula"},
        },
        {
            "id": "formula-5",
            "score": 0.0,
            "text": r"公式上下文：折射率与载流子浓度的关系 公式：$$n=\sqrt{\varepsilon_r}$$",
            "metadata": {"doc_id": "44", "page": 5, "chunk_index": 11, "block_type": "formula"},
        },
        {
            "id": "formula-7",
            "score": 0.0,
            "text": r"公式：$$d=2n\lambda$$",
            "metadata": {"doc_id": "44", "page": 7, "chunk_index": 12, "block_type": "formula"},
        },
    ]

    with patch("src.services.retrieval._cached_query_vec", return_value=(0.1, 0.2)):
        out = retrieve(
            "折射率与载流子浓度的关系是什么？",
            vs,
            "course-default",
            top_k=5,
            score_threshold=0.25,
            rerank_enabled=False,
        )

    assert [hit["id"] for hit in out] == ["body-1", "formula-5", "formula-4"]
    assert out[1]["metadata"]["retrieval_reason"] == "formula_same_page"
    assert out[2]["metadata"]["retrieval_reason"] == "formula_adjacent_page"
    vs.get_chunks.assert_called_once_with(
        course_id="course-default", doc_id="44", block_type="formula", limit=500
    )


def test_non_formula_query_does_not_expand_formula_evidence():
    clear_query_embed_cache()
    vs = MagicMock()
    vs.search.return_value = [
        {"id": "body-1", "score": 0.9, "text": "概念定义", "metadata": {"doc_id": "1", "page": 2}}
    ]
    vs.get_by_course_id.return_value = []

    with patch("src.services.retrieval._cached_query_vec", return_value=(0.1, 0.2)):
        retrieve(
            "什么是折射率？",
            vs,
            "course-default",
            top_k=5,
            score_threshold=0.25,
            rerank_enabled=False,
        )

    vs.get_chunks.assert_not_called()


def test_list_query_completes_numbered_neighbors_and_drops_formula_noise():
    clear_query_embed_cache()
    vs = MagicMock()
    vs.search.return_value = [
        {
            "id": "46_137",
            "score": 0.9,
            "text": "综上，我们得出了多光束干涉的四个必要条件：\n\n(1)干涉光的频率需要相同。",
            "metadata": {
                "doc_id": "46",
                "page": 12,
                "chunk_index": 137,
                "block_type": "text",
            },
        },
        {
            "id": "46_152",
            "score": 0.8,
            "text": r"公式：$$n=2.52$$",
            "metadata": {
                "doc_id": "46",
                "page": 12,
                "chunk_index": 152,
                "block_type": "formula",
            },
        },
    ]
    vs.get_by_course_id.return_value = []
    vs.get_chunks.return_value = [
        vs.search.return_value[0],
        {
            "id": "46_138",
            "score": 0.0,
            "text": "(1)干涉光的频率需要相同。\n\n(2)干涉光的振动方向不可垂直。",
            "metadata": {"doc_id": "46", "page": 12, "chunk_index": 138, "block_type": "text"},
        },
        {
            "id": "46_139",
            "score": 0.0,
            "text": "(2)干涉光的振动方向不可垂直。\n\n(3)干涉光需要有恒定的相位差。",
            "metadata": {"doc_id": "46", "page": 12, "chunk_index": 139, "block_type": "text"},
        },
        {
            "id": "46_140",
            "score": 0.0,
            "text": "(3)干涉光需要有恒定的相位差。\n\n(4)外延层材料的反射率足够高。",
            "metadata": {"doc_id": "46", "page": 12, "chunk_index": 140, "block_type": "text"},
        },
    ]

    with patch("src.services.retrieval._cached_query_vec", return_value=(0.1, 0.2)):
        out = retrieve(
            "多光束干涉的必要条件",
            vs,
            "course-default",
            top_k=5,
            score_threshold=0.25,
            rerank_enabled=False,
        )

    assert [hit["id"] for hit in out] == ["46_137", "46_138", "46_139", "46_140"]
    assert all((hit.get("metadata") or {}).get("block_type") != "formula" for hit in out)
    assert all(
        hit["metadata"].get("retrieval_reason") == "list_completion"
        for hit in out[1:]
    )
    vs.get_chunks.assert_called_once_with(course_id="course-default", doc_id="46", limit=2000)


def test_rerank_orders_by_scores():
    hits = [
        {"id": "a", "text": "noise", "score": 0.9},
        {"id": "b", "text": "gold", "score": 0.8},
        {"id": "c", "text": "mid", "score": 0.7},
    ]
    out = rerank(
        "卷积",
        hits,
        top_n=2,
        model_name="dummy",
        score_fn=lambda q, t: [0.0, 5.0, 1.0],
    )
    assert [h["id"] for h in out] == ["b", "c"]


def test_retrieve_rerank_path_uses_wide_pool(monkeypatch):
    clear_query_embed_cache()
    clear_reranker()
    monkeypatch.setattr("src.config.config.retrieval.rerank_enabled", True)
    monkeypatch.setattr("src.config.config.retrieval.rerank_candidates", 4)
    monkeypatch.setattr("src.config.config.retrieval.rerank_top_n", 2)
    monkeypatch.setattr("src.config.config.retrieval.rerank_model", "dummy")

    fused_pool = [
        {"id": "a", "text": "a", "score": 0.99, "metadata": {}},
        {"id": "b", "text": "b", "score": 0.98, "metadata": {}},
        {"id": "c", "text": "c", "score": 0.97, "metadata": {}},
        {"id": "d", "text": "d", "score": 0.96, "metadata": {}},
    ]
    vs = MagicMock()
    vs.search.return_value = fused_pool
    vs.get_by_course_id.return_value = []

    def fake_rerank(query, hits, top_n, *, model_name):
        ordered = [
            {**hits[3], "score": 0.9},
            {**hits[0], "score": 0.8},
            {**hits[1], "score": 0.1},
        ]
        return ordered[:top_n]

    with (
        patch("src.services.retrieval._cached_query_vec", return_value=(0.1, 0.2)),
        patch("src.services.rerank.rerank", side_effect=fake_rerank),
    ):
        out = retrieve(
            "q",
            vs,
            "course-default",
            top_k=2,
            score_threshold=0.5,
            rerank_enabled=True,
        )

    assert [h["id"] for h in out] == ["d", "a"]
    assert vs.search.call_args.kwargs["top_k"] == 4

def test_bm25_cache_refreshes_after_invalidate(vector_store):
    dim = 8
    vector_store.upsert(
        [_chunk("1", DEFAULT_COURSE_ID, "默认课：傅里叶变换", DEFAULT_COURSE_NAME)],
        [[0.1] * dim],
    )
    assert len(_bm25_search("傅里叶", vector_store, DEFAULT_COURSE_ID, 5)) == 1
    vector_store.upsert(
        [_chunk("2", DEFAULT_COURSE_ID, "默认课：卷积定理", DEFAULT_COURSE_NAME)],
        [[0.2] * dim],
    )
    assert _bm25_search("卷积", vector_store, DEFAULT_COURSE_ID, 5) == []
    invalidate_bm25_cache(DEFAULT_COURSE_ID)
    assert len(_bm25_search("卷积", vector_store, DEFAULT_COURSE_ID, 5)) == 1
    invalidate_bm25_cache()
    assert len(_bm25_search("卷积", vector_store, DEFAULT_COURSE_ID, 5)) == 1
