"""documents API — 上传 / 列表 / 扫描 / 删除。"""

import os
import mimetypes
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from starlette import status

from src.config import config
from src.dependencies import (
    get_catalog_store,
    get_current_user,
    get_doc_store,
    get_vector_store,
)
from src.exceptions import (
    AppException,
    BadRequestException,
    NotFoundException,
    UnsupportedFormatException,
)
from src.services.ingestion import SUPPORTED_EXTENSIONS, ingest_file, scan_knowledge_dir
from src.services.document_updates import ingest_or_update_pdf
from src.services.evidence_metadata import normalize_scope
from src.services.retrieval import invalidate_bm25_cache
from src.services.storage.catalog_store import CatalogStore
from src.services.storage.doc_store import SQLiteDocStore
from src.services.storage.vector_store import ChromaVectorStore
from src.models import EvidenceMetadataPatch

router = APIRouter(prefix="/documents", tags=["documents"])

_CHUNK_SIZE = 1024 * 1024


def _knowledge_dir() -> Path:
    return Path(config.storage.knowledge_dir)


def _write_upload_limited(src: UploadFile, dest: Path, max_bytes: int) -> None:
    total = 0
    with open(dest, "wb") as out:
        while True:
            chunk = src.file.read(_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                limit_mb = max_bytes // (1024 * 1024)
                raise BadRequestException(f"文件超过大小限制（{limit_mb}MB）")
            out.write(chunk)


@router.post("")
async def upload_document(
    file: UploadFile = File(...),
    course_id: str = Form(...),
    vs: ChromaVectorStore = Depends(get_vector_store),
    ds: SQLiteDocStore = Depends(get_doc_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    course = catalog.require_course(course_id)

    if not file.filename:
        raise BadRequestException("文件名为空")

    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatException(
            f"不支持的文件格式: {ext}，仅接受 PDF/TXT/MD/DOC/DOCX/PPTX"
        )

    upload_dir = _knowledge_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{Path(file.filename).stem}_{os.urandom(4).hex()}{ext}"
    save_path = upload_dir / safe_name
    max_bytes = config.max_upload_mb * 1024 * 1024

    _write_upload_limited(file, save_path, max_bytes)

    try:
        if ext == ".pdf":
            outcome = ingest_or_update_pdf(
                path=str(save_path.resolve()),
                vs=vs,
                ds=ds,
                course_id=course_id,
                course=course["name"],
                college_id=course["college_id"],
                knowledge_dir=str(upload_dir),
                display_name=file.filename,
            )
            doc_id = outcome.doc_id
            if outcome.action == "unchanged":
                save_path.unlink(missing_ok=True)
        else:
            outcome = None
            doc_id = ingest_file(
                path=str(save_path.resolve()),
                vs=vs,
                ds=ds,
                course_id=course_id,
                course=course["name"],
                college_id=course["college_id"],
                display_name=file.filename,
            )
    except AppException:
        save_path.unlink(missing_ok=True)
        raise

    stored_path = str(save_path)
    if outcome and outcome.action == "unchanged" and doc_id is not None:
        existing = ds.get(int(doc_id))
        if existing:
            stored_path = existing["file_path"]

    return {
        "code": status.HTTP_200_OK,
        "data": {
            "doc_id": doc_id,
            "filename": file.filename,
            "status": "done",
            "stored_path": stored_path,
            "course_id": course_id,
            "update": outcome.to_dict() if outcome else None,
        },
    }


@router.get("")
async def list_documents(
    course_id: str,
    ds: SQLiteDocStore = Depends(get_doc_store),
    vs: ChromaVectorStore = Depends(get_vector_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(course_id)
    docs = ds.list(course_id=course_id)
    emb = config.embedding
    dim = vs.stored_embedding_dim()
    return {
        "code": status.HTTP_200_OK,
        "data": {
            "items": docs,
            "total": len(docs),
            "embedding": {
                "provider": emb.provider,
                "model": emb.model,
                "dim": int(dim) if dim is not None else None,
            },
        },
    }


@router.get("/{doc_id}/source")
async def open_document_source(
    doc_id: int,
    course_id: str = Query(...),
    ds: SQLiteDocStore = Depends(get_doc_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    """以当前课程为边界打开已入库文档的原始文件。"""
    catalog.require_course(course_id)
    doc = ds.get(doc_id)
    if doc is None or doc.get("course_id") != course_id:
        raise NotFoundException("文档不存在")
    source = Path(doc["file_path"])
    if not source.is_file():
        raise NotFoundException("源文件不存在或已被移动")
    media_type, _encoding = mimetypes.guess_type(doc.get("filename") or source.name)
    return FileResponse(
        source,
        media_type=media_type,
        filename=doc.get("filename") or source.name,
        content_disposition_type="inline",
    )


@router.patch("/{doc_id}/evidence")
async def update_document_evidence(
    doc_id: int,
    body: EvidenceMetadataPatch,
    course_id: str,
    vs: ChromaVectorStore = Depends(get_vector_store),
    ds: SQLiteDocStore = Depends(get_doc_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    """人工校正资料的版本、时效、权威性和受控适用场景，并同步所有向量块。"""
    catalog.require_course(course_id)
    doc = ds.get(doc_id)
    if doc is None or doc.get("course_id") != course_id:
        raise NotFoundException("文档不存在")

    supplied = body.model_dump(exclude_none=True)
    if not supplied:
        raise BadRequestException("至少提供一项证据元数据")

    metadata = {
        name: doc[name]
        for name in (
            "source_version",
            "effective_from",
            "effective_to",
            "authority_level",
            "authority_label",
            "applicability_scope",
        )
    }
    metadata.update(supplied)
    metadata["applicability_scope"] = normalize_scope(metadata["applicability_scope"])
    try:
        start = date.fromisoformat(metadata["effective_from"])
        end = date.fromisoformat(metadata["effective_to"])
    except ValueError as exc:
        raise BadRequestException("生效日期必须是有效的 YYYY-MM-DD") from exc
    if start > end:
        raise BadRequestException("effective_from 不能晚于 effective_to")

    metadata.update({"metadata_confidence": 1.0, "metadata_source": "manual"})
    ds.update_evidence_metadata(doc_id, metadata)
    vs.set_evidence_metadata_by_doc_id(str(doc_id), metadata)
    invalidate_bm25_cache(course_id)
    return {
        "code": status.HTTP_200_OK,
        "data": {"doc_id": doc_id, "evidence": metadata},
    }


@router.get("/summary")
async def document_summary(
    course_id: str,
    by: str = Query("type", pattern="^(type|chapter)$"),
    ds: SQLiteDocStore = Depends(get_doc_store),
    vs: ChromaVectorStore = Depends(get_vector_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    """课程资料大致分类。用户可选 `by=type`（按文件类型）或 `by=chapter`（按内容章节）。"""
    catalog.require_course(course_id)
    if by == "chapter":
        groups = vs.group_by_chapter(course_id=course_id)
        total = len(groups)
        total_chunks = sum(int(g["chunk_count"]) for g in groups)
        return {
            "code": status.HTTP_200_OK,
            "data": {
                "dimension": by,
                "groups": groups,
                "total": total,
                "total_chunks": total_chunks,
            },
        }

    groups = ds.group_by_type(course_id=course_id)
    total = sum(len(g["documents"]) for g in groups)
    return {
        "code": status.HTTP_200_OK,
        "data": {"dimension": by, "groups": groups, "total": total},
    }


@router.post("/scan")
async def scan_data_dir(
    course_id: str = Form(...),
    force: bool = Form(False),
    vs: ChromaVectorStore = Depends(get_vector_store),
    ds: SQLiteDocStore = Depends(get_doc_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    course = catalog.require_course(course_id)
    outcomes = scan_knowledge_dir(
        vs,
        ds,
        course_id=course_id,
        course=course["name"],
        college_id=course["college_id"],
        recover_stale=False,
        force=force,
    )
    return {
        "code": status.HTTP_200_OK,
        "data": {
            "message": "强制重建完成" if force else "扫描完成",
            "course_id": course_id,
            "force": force,
            "updates": outcomes,
        },
    }


@router.delete("/{doc_id}")
async def delete_document(
    doc_id: int,
    course_id: str,
    vs: ChromaVectorStore = Depends(get_vector_store),
    ds: SQLiteDocStore = Depends(get_doc_store),
    catalog: CatalogStore = Depends(get_catalog_store),
    _user=Depends(get_current_user),
):
    catalog.require_course(course_id)
    doc = ds.get(doc_id)
    if doc is None:
        raise NotFoundException(f"文档不存在: {doc_id}")
    if doc.get("course_id") != course_id:
        raise NotFoundException(f"文档不存在: {doc_id}")

    vs.delete_by_doc_id(str(doc_id))
    invalidate_bm25_cache(course_id)

    file_path = Path(doc["file_path"])
    if file_path.is_file():
        file_path.unlink(missing_ok=True)

    ds.delete(doc_id)

    return {
        "code": status.HTTP_200_OK,
        "data": {
            "message": f"文档 {doc['filename']} 已删除",
            "deleted_doc_id": str(doc_id),
        },
    }
