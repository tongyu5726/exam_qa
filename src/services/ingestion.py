"""入库编排：解析 → 分块 → 向量化 → 写入 storage。"""

import logging
from pathlib import Path

from src.config import config
from src.exceptions import (
    AppException,
    BadRequestException,
    ServiceUnavailableException,
    UnsupportedFormatException,
)
from src.services.embedding import get_embedding_client
from src.services.evidence_metadata import extract_evidence_metadata
from src.services.ingestion_parts.chapters import (
    _enrich_chunks_with_context,
    assign_chapters,
)
from src.services.ingestion_parts.structured_chunks import _chunk_structured
from src.services.ingestion_parts.text_chunks import _chunk_document, _split_text
from src.services.parsing import SUPPORTED_EXTENSIONS, parse_file
from src.services.retrieval import invalidate_bm25_cache
from src.services.storage.catalog_store import (
    DEFAULT_COLLEGE_ID,
    DEFAULT_COURSE_ID,
    DEFAULT_COURSE_NAME,
)
from src.services.storage.doc_store import SQLiteDocStore
from src.services.storage.vector_store import ChromaVectorStore

logger = logging.getLogger(__name__)

# ponytail: Chroma 单次 upsert 上限经验值，超大文档分批写入
_UPSERT_BATCH = 128


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化，入库与检索共用。"""
    try:
        return get_embedding_client().embed(texts)
    except Exception as e:
        logger.error("向量化失败: %s", e)
        raise ServiceUnavailableException("向量化服务不可用", detail=str(e)) from e


def _acquire_doc_id(
    ds: SQLiteDocStore,
    vs: ChromaVectorStore,
    filepath: Path,
    filename: str,
    course: str,
    course_id: str,
    *,
    is_active: bool = True,
) -> int:
    """同路径且同课程复用记录；跨课程占用同路径则拒绝，避免串课。"""
    resolved = str(filepath.resolve())
    existing = ds.find_by_path(resolved)
    if existing:
        if existing.get("course_id") and existing["course_id"] != course_id:
            raise BadRequestException(
                f"文件已归属课程 {existing['course_id']}，不能再入库到 {course_id}"
            )
        doc_id = existing["id"]
        if existing["status"] in ("done", "failed", "processing"):
            vs.delete_by_doc_id(str(doc_id))
            ds.update_course(doc_id, course, course_id)
            ds.update_status(doc_id, "processing", chunk_count=0)
            logger.info("复用文档记录: doc_id=%s path=%s", doc_id, filename)
            return doc_id

    doc_id = ds.create(
        filename=filename,
        file_path=resolved,
        course=course,
        course_id=course_id,
        is_active=is_active,
    )
    ds.update_status(doc_id, "processing")
    return doc_id


def _fail_ingest(vs: ChromaVectorStore, ds: SQLiteDocStore, doc_id: int) -> None:
    """入库失败：清向量 + 标记 failed。"""
    try:
        vs.delete_by_doc_id(str(doc_id))
    except Exception as e:
        logger.warning("清理 doc_id=%s 向量失败: %s", doc_id, e)
    ds.update_status(doc_id, "failed", chunk_count=0)


def _upsert_chunks_batched(
    vs: ChromaVectorStore,
    chunk_dicts: list[dict],
    embeddings: list[list[float]],
) -> bool:
    """分批写入；任一批评因维度重建过集合则返回 True。"""
    wiped = False
    for i in range(0, len(chunk_dicts), _UPSERT_BATCH):
        sl = slice(i, i + _UPSERT_BATCH)
        wiped = vs.upsert(chunk_dicts[sl], embeddings[sl]) or wiped
    return wiped


def _mark_sibling_docs_stale(
    ds: SQLiteDocStore, keep_doc_id: int, course_id: str | None = None
) -> int:
    stale = 0
    for doc in ds.list(course_id=course_id):
        if doc["id"] != keep_doc_id and doc["status"] == "done":
            ds.update_status(doc["id"], "failed", chunk_count=0)
            stale += 1
    return stale


def _needs_reindex(existing: dict | None, mtime: float) -> bool:
    """判断文件是否需要（重新）入库。"""
    if existing is None:
        return True
    if existing["status"] != "done":
        return existing["status"] == "failed"
    stored = existing.get("file_mtime")
    if stored is None:
        return True
    return mtime > stored + 1e-3


def ingest_file(
    path: str,
    vs: ChromaVectorStore,
    ds: SQLiteDocStore,
    course_id: str = DEFAULT_COURSE_ID,
    course: str = DEFAULT_COURSE_NAME,
    college_id: str = DEFAULT_COLLEGE_ID,
    display_name: str | None = None,
    *,
    is_active: bool = True,
    parsed_document=None,
) -> str:
    """入库单文件，返回 doc_id。"""
    if not course_id or not course_id.strip():
        raise BadRequestException("course_id 不能为空")

    invalidate_bm25_cache(course_id)
    filepath = Path(path)
    filename = display_name or filepath.name
    logger.info("开始入库: %s course_id=%s", filename, course_id)

    ext = filepath.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatException(
            f"不支持的文件格式: {ext}，仅接受 PDF/TXT/MD/DOC/DOCX/PPTX"
        )

    # 先完成解析及视觉摘要；失败时不清除同路径旧文档的向量。
    existing = ds.find_by_path(str(filepath.resolve()))
    if existing and existing.get("course_id") and existing["course_id"] != course_id:
        raise BadRequestException("文件已归属其他课程，不能跨课程入库")
    parsed = parsed_document if parsed_document is not None else parse_file(path)
    if ext == ".pdf":
        from src.services.visual_coverage import ensure_pdf_visual_coverage
        ensure_pdf_visual_coverage(parsed, path)
    full_text = parsed.full_text
    if not full_text.strip() and not any(b.block_type == "image" for b in parsed.blocks):
        raise BadRequestException("解析后内容为空")
    structured = _chunk_structured(
        parsed, chunk_size=config.chunk.chunk_size, chunk_overlap=config.chunk.chunk_overlap,
    ) if parsed.has_blocks else None

    doc_id = _acquire_doc_id(
        ds, vs, filepath, filename, course, course_id, is_active=is_active
    )
    logger.info("文档记录就绪: doc_id=%s", doc_id)

    try:
        current_metadata = ds.get(doc_id) or {}
        if current_metadata.get("metadata_source") == "manual":
            evidence = {
                key: current_metadata[key]
                for key in (
                    "source_version",
                    "effective_from",
                    "effective_to",
                    "authority_level",
                    "authority_label",
                    "applicability_scope",
                    "metadata_confidence",
                    "metadata_source",
                )
            }
        else:
            evidence = extract_evidence_metadata(full_text, filename).to_dict()
            ds.update_evidence_metadata(doc_id, evidence)

        if parsed.has_blocks:
            # MinerU 结构化切片：语义分组 + 丰富 metadata
            chunk_texts = [c["text"] for c in structured]
            chunk_pages = [c["page"] for c in structured]
            chapters = [c["chapter"] for c in structured]
            block_types = [c["block_type"] for c in structured]
            section_paths = [c["section_path"] for c in structured]
            table_headers = [c["table_headers"] for c in structured]
            contexts = [c["context"] for c in structured]
            bboxes = [c["bbox"] for c in structured]
            pdf_page_labels = [c["pdf_page_label"] for c in structured]
            textbook_references = [c["textbook_references"] for c in structured]
            content_roles = [c["content_role"] for c in structured]
            image_paths = [c.get("image_path", "") for c in structured]
            image_captions = [c.get("image_caption", "") for c in structured]
        else:
            # 非结构化文档：保持原有按页分块 + 章节推断
            raw_chunks = _chunk_document(
                parsed,
                chunk_size=config.chunk.chunk_size,
                chunk_overlap=config.chunk.chunk_overlap,
            )
            if not raw_chunks:
                raise BadRequestException("分块结果为空")
            chunk_texts = [c[0] for c in raw_chunks]
            chunk_pages = [c[1] for c in raw_chunks]
            chapters = assign_chapters(full_text, chunk_texts, pages=chunk_pages)
            chunk_texts = _enrich_chunks_with_context(full_text, chunk_texts)
            block_types = ["text"] * len(chunk_texts)
            section_paths = [""] * len(chunk_texts)
            table_headers = [""] * len(chunk_texts)
            contexts = [""] * len(chunk_texts)
            bboxes = [""] * len(chunk_texts)
            pdf_page_labels = [""] * len(chunk_texts)
            textbook_references = [""] * len(chunk_texts)
            content_roles = ["content"] * len(chunk_texts)
            image_paths = [""] * len(chunk_texts)
            image_captions = [""] * len(chunk_texts)

        if not chunk_texts:
            raise BadRequestException("分块结果为空")
        logger.info("分块完成: %s, 共 %d 个 chunk", filename, len(chunk_texts))

        embeddings = embed_texts(chunk_texts)

        chunk_dicts = []
        for i, chunk_text in enumerate(chunk_texts):
            chunk_dicts.append({
                "doc_id": str(doc_id),
                "source_file": filename,
                "chunk_index": i,
                "course": course,
                "course_id": course_id,
                "college_id": college_id,
                "text": chunk_text,
                "page": chunk_pages[i],
                "chapter": chapters[i] if i < len(chapters) else "",
                "block_type": block_types[i] if i < len(block_types) else "",
                "section_path": section_paths[i] if i < len(section_paths) else "",
                "table_headers": table_headers[i] if i < len(table_headers) else "",
                "context": contexts[i] if i < len(contexts) else "",
                "bbox": bboxes[i] if i < len(bboxes) else "",
                "pdf_page_label": pdf_page_labels[i] if i < len(pdf_page_labels) else "",
                "textbook_references": textbook_references[i] if i < len(textbook_references) else "",
                "content_role": content_roles[i] if i < len(content_roles) else "content",
                "image_path": image_paths[i] if i < len(image_paths) else "",
                "image_caption": image_captions[i] if i < len(image_captions) else "",
                "parser_name": parsed.parser_name,
                "parse_quality": parsed.parse_quality,
                "is_active": is_active,
                **evidence,
            })

        wiped = _upsert_chunks_batched(vs, chunk_dicts, embeddings)
        if wiped:
            stale = _mark_sibling_docs_stale(ds, doc_id, course_id=course_id)
            if stale:
                logger.warning(
                    "Embedding 维度已变更，已将同课 %d 条其他资料标为 failed，请重新扫描/上传",
                    stale,
                )
        logger.info("向量写入完成: %s, %d chunks", filename, len(chunk_texts))

        ds.update_status(doc_id, "done", chunk_count=len(chunk_texts))
        ds.update_file_mtime(doc_id, filepath.stat().st_mtime)
        logger.info(
            "入库完成: %s -> doc_id=%s, %d chunks",
            filename,
            doc_id,
            len(chunk_texts),
        )
        return str(doc_id)

    except AppException:
        _fail_ingest(vs, ds, doc_id)
        raise
    except Exception as e:
        logger.exception("入库异常: %s", e)
        _fail_ingest(vs, ds, doc_id)
        raise AppException(f"入库失败: {e}", status_code=500)
    finally:
        invalidate_bm25_cache(course_id)


def scan_knowledge_dir(
    vs: ChromaVectorStore,
    ds: SQLiteDocStore,
    *,
    course_id: str = DEFAULT_COURSE_ID,
    course: str = DEFAULT_COURSE_NAME,
    college_id: str = DEFAULT_COLLEGE_ID,
    recover_stale: bool = False,
    force: bool = False,
) -> list[dict]:
    """扫描 knowledge；force=True 时对已 done 文件也重入库（补 chapter 等）。"""
    data_dir = Path(config.storage.knowledge_dir)
    if not data_dir.exists():
        return []

    if recover_stale:
        stale = ds.recover_stale_processing()
        if stale:
            logger.info("已将 %d 条 processing 记录恢复为 failed", stale)

    files = [
        f
        for f in data_dir.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    results: list[dict] = []
    # PDF 走内容指纹与版本切换；在函数内导入以避免与 ingest_file 的循环依赖。
    from src.services.document_updates import ingest_or_update_pdf

    non_pdf_files: list[Path] = []
    for f in files:
        if f.suffix.lower() == ".pdf":
            try:
                outcome = ingest_or_update_pdf(
                    path=str(f.resolve()),
                    vs=vs,
                    ds=ds,
                    course_id=course_id,
                    course=course,
                    college_id=college_id,
                    knowledge_dir=str(data_dir),
                    force=force,
                )
                results.append(outcome.to_dict())
            except Exception as e:
                logger.warning("PDF 自动更新失败 %s: %s", f.name, e)
                results.append({"action": "failed", "filename": f.name, "message": str(e)})
            continue
        non_pdf_files.append(f)

    to_ingest: list[Path] = []
    for f in non_pdf_files:
        resolved = str(f.resolve())
        mtime = f.stat().st_mtime
        existing = ds.find_by_path(resolved)
        if existing and existing.get("course_id") and existing["course_id"] != course_id:
            logger.info(
                "跳过跨课文件 %s（归属 %s，当前扫描 %s）",
                f.name,
                existing["course_id"],
                course_id,
            )
            continue
        if force and existing and existing.get("status") == "done":
            to_ingest.append(f)
            continue
        if _needs_reindex(existing, mtime):
            to_ingest.append(f)

    if not to_ingest:
        return results

    logger.info(
        "发现 %d 个待入库/更新文件%s，开始扫描入库…",
        len(to_ingest),
        "（强制重建）" if force else "",
    )
    for f in to_ingest:
        try:
            doc_id = ingest_file(
                path=str(f.resolve()),
                vs=vs,
                ds=ds,
                course_id=course_id,
                course=course,
                college_id=college_id,
            )
            logger.info("扫描入库: %s -> doc_id=%s", f.name, doc_id)
            results.append({"action": "created", "doc_id": doc_id, "filename": f.name})
        except Exception as e:
            logger.warning("扫描入库失败 %s: %s", f.name, e)
            results.append({"action": "failed", "filename": f.name, "message": str(e)})
    return results
