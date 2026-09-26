"""PDF 自动更新：内容指纹、版本识别与安全切换。"""

from pathlib import Path

from src.services import document_updates
from src.services.storage.catalog_store import DEFAULT_COURSE_ID, DEFAULT_COURSE_NAME


def _chunk(doc_id: int, text: str) -> dict:
    return {
        "doc_id": str(doc_id),
        "source_file": "讲义.pdf",
        "chunk_index": 0,
        "course": DEFAULT_COURSE_NAME,
        "course_id": DEFAULT_COURSE_ID,
        "college_id": "college-default",
        "text": text,
        "is_active": True,
    }


def test_logical_name_and_similarity():
    assert document_updates.logical_document_name("信号与系统_v2.pdf") == "信号与系统"
    assert document_updates.logical_document_name("信号与系统-修订3.pdf") == "信号与系统"
    assert document_updates.text_similarity("卷积定理的定义", "卷积定理的定义与性质") > 0
    assert document_updates.text_similarity("卷积定理", "完全不同的内容") == 0


def test_same_hash_skips_duplicate(temp_dir, doc_store, vector_store):
    path = Path(temp_dir) / "讲义.pdf"
    path.write_bytes(b"same-pdf-content")
    digest = document_updates.file_sha256(path)
    doc_id = doc_store.create(
        "讲义.pdf",
        str(path.resolve()),
        course=DEFAULT_COURSE_NAME,
        course_id=DEFAULT_COURSE_ID,
        content_hash=digest,
        logical_name="讲义",
    )
    doc_store.update_status(doc_id, "done", chunk_count=1)

    result = document_updates.ingest_or_update_pdf(
        path=str(path),
        vs=vector_store,
        ds=doc_store,
        course_id=DEFAULT_COURSE_ID,
        course=DEFAULT_COURSE_NAME,
        college_id="college-default",
        knowledge_dir=str(temp_dir),
    )

    assert result.action == "unchanged"
    assert result.doc_id == str(doc_id)


def test_same_path_update_stages_then_promotes(
    temp_dir, doc_store, vector_store, monkeypatch
):
    path = Path(temp_dir) / "讲义.pdf"
    path.write_bytes(b"old-pdf-content")
    old_id = doc_store.create(
        "讲义.pdf",
        str(path.resolve()),
        course=DEFAULT_COURSE_NAME,
        course_id=DEFAULT_COURSE_ID,
        content_hash=document_updates.file_sha256(path),
        logical_name="讲义",
    )
    doc_store.update_status(old_id, "done", chunk_count=1)
    vector_store.upsert([_chunk(old_id, "旧版内容")], [[0.1] * 8])

    # 模拟旧实现遗留的同名活跃副本；成功切换后也必须一并退出检索范围。
    duplicate_path = Path(temp_dir) / "讲义_副本.pdf"
    duplicate_path.write_bytes(b"older-pdf-content")
    duplicate_id = doc_store.create(
        "讲义.pdf",
        str(duplicate_path.resolve()),
        course=DEFAULT_COURSE_NAME,
        course_id=DEFAULT_COURSE_ID,
        logical_name="讲义",
    )
    doc_store.update_status(duplicate_id, "done", chunk_count=1)
    vector_store.upsert([_chunk(duplicate_id, "更旧内容")], [[0.15] * 8])

    path.write_bytes(b"new-pdf-content")

    def fake_ingest(**kwargs):
        staged = Path(kwargs["path"])
        assert staged.parent.name == ".versions"
        new_id = doc_store.create(
            "讲义.pdf",
            str(staged.resolve()),
            course=DEFAULT_COURSE_NAME,
            course_id=DEFAULT_COURSE_ID,
            is_active=kwargs["is_active"],
        )
        doc_store.update_status(new_id, "done", chunk_count=1)
        vector_store.upsert(
            [{**_chunk(new_id, "新版内容"), "is_active": kwargs["is_active"]}],
            [[0.2] * 8],
        )
        return str(new_id)

    monkeypatch.setattr(document_updates, "ingest_file", fake_ingest)
    result = document_updates.ingest_or_update_pdf(
        path=str(path),
        vs=vector_store,
        ds=doc_store,
        course_id=DEFAULT_COURSE_ID,
        course=DEFAULT_COURSE_NAME,
        college_id="college-default",
        knowledge_dir=str(temp_dir),
    )

    assert result.action == "updated"
    assert result.previous_doc_id == str(old_id)
    old = doc_store.get(old_id)
    duplicate = doc_store.get(duplicate_id)
    new = doc_store.get(int(result.doc_id))
    assert old["status"] == "superseded" and old["is_active"] == 0
    assert duplicate["status"] == "superseded" and duplicate["is_active"] == 0
    assert new["version_number"] == 2 and new["is_active"] == 1
    hits = vector_store.search([0.2] * 8, course_id=DEFAULT_COURSE_ID)
    assert [hit["metadata"]["doc_id"] for hit in hits] == [str(new["id"])]
