"""证据元数据抽取、人工覆盖与场景/时效过滤。"""

from src.services.evidence_metadata import extract_evidence_metadata, normalize_scope
import pytest


def _chunk(doc_id: str, text: str, **evidence):
    return {
        "doc_id": doc_id,
        "source_file": f"{doc_id}.md",
        "chunk_index": 0,
        "course": "测试课程",
        "course_id": "course-default",
        "college_id": "college-default",
        "text": text,
        **evidence,
    }


def test_extract_version_effective_time_and_authority():
    metadata = extract_evidence_metadata(
        "教育部通知\n版本：2.1\n生效日期：2025年9月1日\n有效期至：2026年8月31日"
    )
    assert metadata.source_version == "2.1"
    assert metadata.effective_from == "2025-09-01"
    assert metadata.effective_to == "2026-08-31"
    assert metadata.authority_level == 80
    assert metadata.authority_label == "部委规范"


def test_scope_normalizes_to_stable_key():
    assert normalize_scope(" 本科生 / 考试  ") == "本科生_考试"
    assert normalize_scope("") == "all"


@pytest.mark.parametrize("day,expected", [
    ("2026-01-01", {"all", "exam"}), ("2026-12-31", {"all", "exam"}),
    ("2025-12-31", {"all"}), ("2027-01-01", {"all"}),
])
def test_vector_read_paths_share_scope_and_date_boundaries(vector_store, day, expected):
    chunks = [
        _chunk("all", "通用", chapter="第1章", applicability_scope="all"),
        _chunk("exam", "考试", chapter="第1章", applicability_scope="考试",
               effective_from="2026-01-01", effective_to="2026-12-31"),
        _chunk("other", "实验", chapter="第1章", applicability_scope="实验"),
        _chunk("foreign", "他课", chapter="第1章", course_id="other-course"),
        _chunk("inactive", "历史", chapter="第1章", is_active=False),
    ]
    vector = [1.0] + [0.0] * 7
    vector_store.upsert(chunks, [vector] * len(chunks))
    scope = dict(course_id="course-default", scenario="考试", as_of=day)
    reads = [vector_store.search(vector, top_k=10, **scope),
             vector_store.get_chunks(**scope), vector_store.get_by_course_id(**scope),
             vector_store.get_by_chapter(chapter="第1章", **scope)]
    for hits in reads:
        assert {h["metadata"]["doc_id"] for h in hits} == expected


def test_manual_metadata_sync_and_filtered_vector_search(doc_store, vector_store):
    doc_id = doc_store.create("rules.md", "rules.md", course_id="course-default")
    vector_store.upsert(
        [_chunk(str(doc_id), "考试时应遵守新版规则")], [[1.0] + [0.0] * 7]
    )
    metadata = {
        "source_version": "2026.1",
        "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
        "authority_level": 80,
        "authority_label": "部委规范",
        "applicability_scope": "考试",
        "metadata_confidence": 1.0,
        "metadata_source": "manual",
    }
    doc_store.update_evidence_metadata(doc_id, metadata)
    vector_store.set_evidence_metadata_by_doc_id(str(doc_id), metadata)

    saved = doc_store.get(doc_id)
    assert saved["metadata_source"] == "manual"
    assert saved["applicability_scope"] == "考试"
    hits = vector_store.search(
        [1.0] + [0.0] * 7,
        course_id="course-default",
        scenario="考试",
        as_of="2026-06-01",
    )
    assert len(hits) == 1
    assert hits[0]["metadata"]["source_version"] == "2026.1"
    assert vector_store.search(
        [1.0] + [0.0] * 7,
        course_id="course-default",
        scenario="实验",
        as_of="2026-06-01",
    ) == []
    assert vector_store.search(
        [1.0] + [0.0] * 7,
        course_id="course-default",
        scenario="考试",
        as_of="2027-01-01",
    ) == []
