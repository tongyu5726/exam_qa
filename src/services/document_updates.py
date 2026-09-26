"""PDF 自动更新：内容指纹识别、影子入库与安全切换。"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from src.services.ingestion import ingest_file
from src.services.parsing import parse_file
from src.services.retrieval import invalidate_bm25_cache
from src.services.storage.doc_store import SQLiteDocStore
from src.services.storage.vector_store import ChromaVectorStore

logger = logging.getLogger(__name__)

_VERSION_SUFFIX = re.compile(
    r"(?:[\s_.-]*(?:v|ver|version|rev|revision|更新|修订|新版)?\s*\d+)?$",
    re.IGNORECASE,
)
_SPACE = re.compile(r"\s+")
_TEXT_SAMPLE = 80_000
_SHINGLE_SIZE = 5
_SIMILARITY_THRESHOLD = 0.20


@dataclass(frozen=True)
class UpdateResult:
    action: str
    doc_id: str | None = None
    previous_doc_id: str | None = None
    similarity: float | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def logical_document_name(filename: str) -> str:
    """将讲义_v2.pdf、讲义-修订3.pdf 归为同一个逻辑文档名。"""
    stem = Path(filename).stem.lower().strip()
    stem = _VERSION_SUFFIX.sub("", stem).strip(" _.-")
    return _SPACE.sub(" ", stem) or Path(filename).stem.lower()


def _shingles(text: str) -> set[str]:
    normalized = _SPACE.sub("", text).lower()[:_TEXT_SAMPLE]
    if len(normalized) <= _SHINGLE_SIZE:
        return {normalized} if normalized else set()
    return {
        normalized[index : index + _SHINGLE_SIZE]
        for index in range(0, len(normalized) - _SHINGLE_SIZE + 1, _SHINGLE_SIZE)
    }


def text_similarity(left: str, right: str) -> float:
    """用于版本识别的轻量内容相似度；不用于回答或检索排序。"""
    a, b = _shingles(left), _shingles(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _old_document_text(
    vs: ChromaVectorStore, course_id: str, doc_id: int
) -> str:
    chunks = vs.get_chunks(course_id=course_id, doc_id=str(doc_id), limit=10_000)
    return "\n".join(chunk.get("text", "") for chunk in chunks)


def _stage_copy(path: Path, content_hash: str, knowledge_dir: str) -> Path:
    """同路径覆盖时，将新文件复制为独立版本，避免复用旧 document 记录。"""
    versions_dir = Path(knowledge_dir) / ".versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    # 相同内容强制重建也必须是全新路径，不能复用历史 staging 文档记录。
    destination = versions_dir / f"{content_hash[:16]}_{uuid.uuid4().hex[:12]}_{path.name}"
    if not destination.exists():
        shutil.copy2(path, destination)
    return destination


def _active_candidates(
    ds: SQLiteDocStore, *, logical_name: str, course_id: str
) -> list[dict]:
    candidates = ds.find_active_by_logical_name(logical_name, course_id)
    if candidates:
        return candidates

    # 兼容升级前没有 logical_name 的历史记录，并在命中时补齐身份字段。
    fallback = [
        doc
        for doc in ds.list(course_id=course_id)
        if logical_document_name(doc["filename"]) == logical_name
    ]
    for doc in fallback:
        ds.update_identity(
            int(doc["id"]),
            content_hash=str(doc.get("content_hash") or ""),
            logical_name=logical_name,
        )
    return fallback


def ingest_or_update_pdf(
    *,
    path: str,
    vs: ChromaVectorStore,
    ds: SQLiteDocStore,
    course_id: str,
    course: str,
    college_id: str,
    knowledge_dir: str,
    display_name: str | None = None,
    force: bool = False,
) -> UpdateResult:
    """入库 PDF；识别到同一资料的新版本时采用影子入库后切换。"""
    source = Path(path)
    filename = display_name or source.name
    content_hash = file_sha256(source)
    logical_name = logical_document_name(filename)

    duplicate = ds.find_active_by_hash(content_hash, course_id)
    if duplicate and not force:
        return UpdateResult(
            action="unchanged",
            doc_id=str(duplicate["id"]),
            message="文件内容未变化，跳过重复入库",
        )

    same_path = ds.find_by_path(str(source.resolve()))
    if same_path and not int(same_path.get("is_active", 1)):
        same_path = None
    candidates = _active_candidates(ds, logical_name=logical_name, course_id=course_id)
    if same_path and all(int(doc["id"]) != int(same_path["id"]) for doc in candidates):
        candidates.insert(0, same_path)

    parsed = None
    chosen: dict | None = same_path
    similarity: float | None = 1.0 if same_path else None
    if candidates and chosen is None:
        parsed = parse_file(str(source))
        new_text = parsed.full_text
        scored = [
            (text_similarity(new_text, _old_document_text(vs, course_id, int(doc["id"]))), doc)
            for doc in candidates
        ]
        similarity, chosen = max(scored, key=lambda item: item[0])
        if similarity < _SIMILARITY_THRESHOLD:
            chosen = None

    if chosen is None:
        doc_id = ingest_file(
            path=str(source),
            vs=vs,
            ds=ds,
            course_id=course_id,
            course=course,
            college_id=college_id,
            display_name=filename,
            parsed_document=parsed,
        )
        ds.update_identity(
            int(doc_id), content_hash=content_hash, logical_name=logical_name
        )
        return UpdateResult(
            action="created",
            doc_id=doc_id,
            similarity=similarity,
            message="作为新资料入库",
        )

    # 输入路径与旧记录一致时，必须另存后再入库，避免 _acquire_doc_id 覆盖旧版本。
    stage_path = source
    if str(source.resolve()) == str(Path(chosen["file_path"]).resolve()):
        stage_path = _stage_copy(source, content_hash, knowledge_dir)

    new_doc_id = ingest_file(
        path=str(stage_path),
        vs=vs,
        ds=ds,
        course_id=course_id,
        course=course,
        college_id=college_id,
        display_name=filename,
        is_active=False,
        parsed_document=parsed,
    )
    ds.update_identity(
        int(new_doc_id), content_hash=content_hash, logical_name=logical_name
    )

    # 先激活新版再删除旧版，更新过程始终至少有一个版本可检索。
    superseded_ids = sorted(
        {
            int(doc["id"])
            for doc in candidates
            if int(doc["id"]) != int(new_doc_id)
        }
        | {int(chosen["id"])}
    )
    try:
        vs.set_active_by_doc_id(new_doc_id, True)
        for old_doc_id in superseded_ids:
            vs.delete_by_doc_id(str(old_doc_id))
    except Exception:
        vs.set_active_by_doc_id(new_doc_id, False)
        ds.update_status(int(new_doc_id), "failed", chunk_count=0)
        raise

    ds.promote_version(int(chosen["id"]), int(new_doc_id))
    extra_ids = [doc_id for doc_id in superseded_ids if doc_id != int(chosen["id"])]
    ds.supersede_many(extra_ids, int(new_doc_id))
    invalidate_bm25_cache(course_id)
    logger.info(
        "PDF 自动更新完成: %s v%s -> v%s (similarity=%s)",
        filename,
        chosen.get("version_number") or 1,
        int(chosen.get("version_number") or 1) + 1,
        "same-path" if similarity is None else f"{similarity:.3f}",
    )
    return UpdateResult(
        action="updated",
        doc_id=new_doc_id,
        previous_doc_id=str(chosen["id"]),
        similarity=similarity,
        message="新版本已完成入库并替换旧版本",
    )
