"""ChromaDB 向量存储。检索按 course_id 过滤（docs/04 4.2）。"""

import logging
import re
from pathlib import Path

import chromadb
from chromadb.config import Settings

from src.exceptions import AppException, ServiceUnavailableException

logger = logging.getLogger(__name__)

_PAGE_PLACEHOLDER = re.compile(r"^第\s*\d+\s*页$")
_EVIDENCE_DEFAULTS = {
    "source_version": "",
    "effective_from": "0001-01-01",
    "effective_to": "9999-12-31",
    "authority_level": 30,
    "authority_label": "教学材料",
    "applicability_scope": "all",
    "metadata_confidence": 0.0,
    "metadata_source": "auto",
    "effective_from_sort": 10101,
    "effective_to_sort": 99991231,
}


def _date_sort_value(value: str | None, default: int) -> int:
    """Chroma 的范围比较只接受数值；日期字段仍以 ISO 字符串对外呈现。"""
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if len(digits) == 8:
        return int(digits)
    return default


class ChromaVectorStore:
    """向量存取：upsert、search、delete_by_doc_id。"""

    def __init__(self, persist_path: str, collection_name: str = "exam_rag"):
        self._persist_path = str(persist_path)
        self._collection_name = collection_name
        Path(self._persist_path).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=self._persist_path,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self._ensure_active_metadata()

    def _ensure_active_metadata(self) -> None:
        """为旧库补齐激活标记，升级后继续让既有资料可被检索。"""
        if self._collection.count() == 0:
            return
        try:
            rows = self._collection.get(include=["metadatas"])
            ids = rows.get("ids") or []
            metas = rows.get("metadatas") or []
            changed_ids: list[str] = []
            changed_metas: list[dict] = []
            for index, chunk_id in enumerate(ids):
                meta = dict(metas[index] or {}) if index < len(metas) else {}
                changed = False
                if "is_active" not in meta:
                    meta["is_active"] = True
                    changed = True
                for name, value in _EVIDENCE_DEFAULTS.items():
                    if name not in meta:
                        meta[name] = value
                        changed = True
                if changed:
                    changed_ids.append(chunk_id)
                    changed_metas.append(meta)
            if changed_ids:
                self._collection.update(ids=changed_ids, metadatas=changed_metas)
                logger.info("为 %d 个既有向量补齐 is_active 标记", len(changed_ids))
        except Exception as e:
            logger.warning("补齐向量激活标记失败，将在下次启动重试: %s", e)

    def stored_embedding_dim(self) -> int | None:
        """已有向量维度；空集合或无法读取时返回 None。"""
        if self._collection.count() == 0:
            return None
        peek = self._collection.peek(limit=1)
        embeddings = peek.get("embeddings")
        if embeddings is None:
            # 部分 Chroma 版本 peek 默认不含向量，再显式取一次
            try:
                got = self._collection.get(
                    ids=peek["ids"][:1], include=["embeddings"]
                )
                embeddings = got.get("embeddings")
            except Exception:
                return None
        if embeddings is None or len(embeddings) == 0:
            return None
        first = embeddings[0]
        if first is None:
            return None
        return int(len(first))

    def recreate_collection(self) -> None:
        name = self._collection_name
        try:
            self._client.delete_collection(name)
        except Exception as e:
            logger.warning("删除旧集合失败（可忽略）: %s", e)
        self._collection = self._client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    def ensure_embedding_dim(self, dim: int) -> bool:
        """维度可知且冲突时重建集合。空集合的维度锁由 upsert 重试处理。"""
        if dim <= 0:
            raise ValueError("embedding 维度必须为正")
        current = self.stored_embedding_dim()
        if current is None or current == dim:
            return False
        logger.warning(
            "Embedding 维度变更 %d → %d，重建空向量集合（旧向量已失效）",
            current,
            dim,
        )
        self.recreate_collection()
        return True

    def upsert(self, chunk_dicts: list[dict], embeddings: list[list[float]]) -> bool:
        """写入向量。返回是否因维度变更重建过集合。"""
        if not chunk_dicts:
            return False
        if len(chunk_dicts) != len(embeddings):
            raise ValueError("chunk_dicts 与 embeddings 长度不一致")

        dim = len(embeddings[0])
        if any(len(v) != dim for v in embeddings):
            raise ValueError("同一批 embeddings 维度不一致")
        wiped = self.ensure_embedding_dim(dim)

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict] = []

        for chunk in chunk_dicts:
            doc_id = str(chunk["doc_id"])
            idx = int(chunk["chunk_index"])
            course_id = str(chunk.get("course_id") or "")
            if not course_id:
                raise ValueError("chunk 缺少 course_id，禁止写入")
            ids.append(f"{doc_id}_{idx}")
            documents.append(chunk["text"])
            metadatas.append({
                "doc_id": doc_id,
                "source_file": chunk.get("source_file", ""),
                "chunk_index": idx,
                "course": chunk.get("course", ""),
                "course_id": course_id,
                "college_id": chunk.get("college_id", ""),
                "page": chunk.get("page") if chunk.get("page") is not None else -1,
                "chapter": str(chunk.get("chapter") or ""),
                "block_type": str(chunk.get("block_type") or ""),
                "section_path": str(chunk.get("section_path") or ""),
                "table_headers": str(chunk.get("table_headers") or ""),
                "context": str(chunk.get("context") or ""),
                "bbox": str(chunk.get("bbox") or ""),
                "pdf_page_label": str(chunk.get("pdf_page_label") or ""),
                "textbook_references": str(chunk.get("textbook_references") or ""),
                "content_role": str(chunk.get("content_role") or "content"),
                "parser_name": str(chunk.get("parser_name") or ""),
                "parse_quality": float(chunk.get("parse_quality") or 0.0),
                "is_active": bool(chunk.get("is_active", True)),
                "source_version": str(chunk.get("source_version") or ""),
                "effective_from": str(chunk.get("effective_from") or "0001-01-01"),
                "effective_to": str(chunk.get("effective_to") or "9999-12-31"),
                "authority_level": int(chunk.get("authority_level") or 30),
                "authority_label": str(chunk.get("authority_label") or "教学材料"),
                "applicability_scope": str(chunk.get("applicability_scope") or "all"),
                "metadata_confidence": float(chunk.get("metadata_confidence") or 0.0),
                "metadata_source": str(chunk.get("metadata_source") or "auto"),
                "effective_from_sort": _date_sort_value(
                    chunk.get("effective_from"), 10101
                ),
                "effective_to_sort": _date_sort_value(
                    chunk.get("effective_to"), 99991231
                ),
            })

        def _do_upsert() -> None:
            self._collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )

        try:
            _do_upsert()
        except Exception as e:
            if "dimension" not in str(e).lower():
                raise
            # Chroma 空集合仍可能锁旧维度
            logger.warning("upsert 维度冲突，强制重建集合后重试: %s", e)
            self.recreate_collection()
            wiped = True
            try:
                _do_upsert()
            except Exception as e2:
                raise AppException(
                    f"向量维度不匹配: {e2}。已尝试重建集合仍失败，请重启服务后再试。",
                    status_code=409,
                ) from e2
        return wiped

    def search(
        self,
        query_vec: list[float],
        top_k: int = 5,
        *,
        course_id: str | None = None,
        block_type: str | None = None,
        scenario: str | None = None,
        as_of: str | None = None,
    ) -> list[dict]:
        if not course_id:
            raise ValueError("search 必须提供 course_id，禁止全库检索")
        if top_k <= 0 or self._collection.count() == 0:
            return []

        try:
            where = self._evidence_where(
                course_id, block_type=block_type, scenario=scenario, as_of=as_of
            )
            results = self._collection.query(
                query_embeddings=[query_vec],
                n_results=min(top_k, self._collection.count()),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            if "dimension" in str(e).lower():
                raise AppException(
                    f"检索向量维度与资料库不一致: {e}。"
                    "请统一 Embedding 模型后重新扫描入库。",
                    status_code=409,
                ) from e
            logger.error("向量检索失败: %s", e)
            raise ServiceUnavailableException(
                "向量检索服务不可用", detail=str(e)
            ) from e

        hits: list[dict] = []
        if not results["ids"] or not results["ids"][0]:
            return hits

        for i, chunk_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i] or {}
            distance = results["distances"][0][i]
            score = max(0.0, 1.0 - distance)
            hits.append(
                self._normalize_hit(
                    chunk_id,
                    results["documents"][0][i],
                    meta,
                    score=score,
                )
            )

        hits.sort(key=lambda x: x["score"], reverse=True)
        return hits

    @staticmethod
    def _normalize_hit(
        chunk_id: str,
        text: str,
        meta: dict,
        score: float = 0.0,
    ) -> dict:
        """统一的 chunk 命中结构（含结构化解析 metadata）。"""
        page = meta.get("page")
        if page == -1:
            page = None
        return {
            "id": chunk_id,
            "text": text,
            "score": score,
            "metadata": {
                "doc_id": meta.get("doc_id", ""),
                "source_file": meta.get("source_file", ""),
                "chunk_index": meta.get("chunk_index", 0),
                "course": meta.get("course", ""),
                "course_id": meta.get("course_id", ""),
                "college_id": meta.get("college_id", ""),
                "page": page,
                "chapter": meta.get("chapter") or "",
                "block_type": meta.get("block_type") or "",
                "section_path": meta.get("section_path") or "",
                "table_headers": meta.get("table_headers") or "",
                "context": meta.get("context") or "",
                "bbox": meta.get("bbox") or "",
                "pdf_page_label": meta.get("pdf_page_label") or "",
                "textbook_references": meta.get("textbook_references") or "",
                "content_role": meta.get("content_role") or "content",
                "parser_name": meta.get("parser_name") or "",
                "parse_quality": float(meta.get("parse_quality") or 0.0),
                "source_version": meta.get("source_version") or "",
                "effective_from": meta.get("effective_from") or "0001-01-01",
                "effective_to": meta.get("effective_to") or "9999-12-31",
                "authority_level": int(meta.get("authority_level") or 30),
                "authority_label": meta.get("authority_label") or "教学材料",
                "applicability_scope": meta.get("applicability_scope") or "all",
                "metadata_confidence": float(meta.get("metadata_confidence") or 0.0),
                "metadata_source": meta.get("metadata_source") or "auto",
            },
        }

    @staticmethod
    def _evidence_where(
        course_id: str,
        *,
        block_type: str | None = None,
        scenario: str | None = None,
        as_of: str | None = None,
    ) -> dict:
        clauses: list[dict] = [{"course_id": course_id}, {"is_active": True}]
        if block_type:
            clauses.append({"block_type": block_type})
        if scenario and scenario != "all":
            clauses.append(
                {
                    "$or": [
                        {"applicability_scope": scenario},
                        {"applicability_scope": "all"},
                    ]
                }
            )
        if as_of:
            target = _date_sort_value(as_of, 0)
            clauses.append({"effective_from_sort": {"$lte": target}})
            clauses.append({"effective_to_sort": {"$gte": target}})
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    def get_chunks(
        self,
        *,
        course_id: str,
        doc_id: str | None = None,
        source_file: str | None = None,
        page: int | None = None,
        block_type: str | None = None,
        scenario: str | None = None,
        as_of: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """按条件取 chunk（course_id 强制；doc_id/source_file/page/block_type 可选过滤）。

        供 Agent 工具（read_page / extract_table / analyze_chart）读取结构化切片。
        """
        if not course_id:
            raise ValueError("get_chunks 必须提供 course_id，禁止全库读取")
        clauses: list[dict] = [{"course_id": course_id}, {"is_active": True}]
        if doc_id:
            clauses.append({"doc_id": str(doc_id)})
        if source_file:
            clauses.append({"source_file": source_file})
        if page is not None:
            clauses.append({"page": int(page)})
        if block_type:
            clauses.append({"block_type": block_type})
        if scenario and scenario != "all":
            clauses.append(
                {
                    "$or": [
                        {"applicability_scope": scenario},
                        {"applicability_scope": "all"},
                    ]
                }
            )
        if as_of:
            clauses.extend(
                [
                    {"effective_from_sort": {"$lte": _date_sort_value(as_of, 0)}},
                    {"effective_to_sort": {"$gte": _date_sort_value(as_of, 0)}},
                ]
            )
        where: dict = clauses[0] if len(clauses) == 1 else {"$and": clauses}
        if self._collection.count() == 0:
            return []
        try:
            results = self._collection.get(
                where=where,
                include=["documents", "metadatas"],
                limit=limit,
            )
        except Exception as e:
            logger.error("按条件读取向量失败: %s", e)
            raise ServiceUnavailableException(
                "向量读取失败", detail=str(e)
            ) from e

        ids = results.get("ids") or []
        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or []
        hits = [
            self._normalize_hit(
                ids[i],
                documents[i] if i < len(documents) else "",
                (metadatas[i] if i < len(metadatas) else None) or {},
            )
            for i in range(len(ids))
        ]
        hits.sort(key=lambda h: int((h.get("metadata") or {}).get("chunk_index") or 0))
        return hits

    def set_active_by_doc_id(self, doc_id: str, is_active: bool) -> None:
        """切换一个文档全部切片的检索可见性。"""
        rows = self._collection.get(
            where={"doc_id": str(doc_id)}, include=["metadatas"]
        )
        ids = rows.get("ids") or []
        metas = rows.get("metadatas") or []
        if not ids:
            return
        updated = []
        for index, _chunk_id in enumerate(ids):
            meta = dict(metas[index] or {}) if index < len(metas) else {}
            meta["is_active"] = bool(is_active)
            updated.append(meta)
        self._collection.update(ids=ids, metadatas=updated)

    def set_evidence_metadata_by_doc_id(self, doc_id: str, metadata: dict) -> None:
        """同步文档级证据元数据到全部向量切片，无需重新向量化。"""
        rows = self._collection.get(
            where={"doc_id": str(doc_id)}, include=["metadatas"]
        )
        ids = rows.get("ids") or []
        metas = rows.get("metadatas") or []
        if not ids:
            return
        updated = []
        for index, _chunk_id in enumerate(ids):
            meta = dict(metas[index] or {}) if index < len(metas) else {}
            meta.update({key: value for key, value in metadata.items() if value is not None})
            meta["effective_from_sort"] = _date_sort_value(
                meta.get("effective_from"), 10101
            )
            meta["effective_to_sort"] = _date_sort_value(
                meta.get("effective_to"), 99991231
            )
            updated.append(meta)
        self._collection.update(ids=ids, metadatas=updated)

    def rebind_course_id(
        self,
        from_course_id: str,
        to_course_id: str,
        *,
        course: str | None = None,
        college_id: str | None = None,
    ) -> int:
        """把旧 course_id 的 chunk metadata 改到新课（升级迁移用）。"""
        if not from_course_id or not to_course_id:
            raise ValueError("from/to course_id 不能为空")
        if from_course_id == to_course_id:
            return 0
        try:
            results = self._collection.get(
                where={"course_id": from_course_id},
                include=["metadatas"],
            )
        except Exception as e:
            logger.error("读取待迁移向量失败: %s", e)
            raise ServiceUnavailableException(
                "向量迁移失败", detail=str(e)
            ) from e

        ids = results.get("ids") or []
        if not ids:
            return 0
        metadatas = results.get("metadatas") or []
        new_metas: list[dict] = []
        for meta in metadatas:
            m = dict(meta or {})
            m["course_id"] = to_course_id
            if course is not None:
                m["course"] = course
            if college_id is not None:
                m["college_id"] = college_id
            new_metas.append(m)
        try:
            self._collection.update(ids=ids, metadatas=new_metas)
        except Exception as e:
            logger.error("写入迁移向量失败: %s", e)
            raise ServiceUnavailableException(
                "向量迁移失败", detail=str(e)
            ) from e
        return len(ids)

    def get_by_course_id(self, course_id: str) -> list[dict]:
        """按 course_id 取出全部 chunk（供 BM25 建索引）。禁止无 filter。"""
        if not course_id:
            raise ValueError("get_by_course_id 必须提供 course_id")
        if self._collection.count() == 0:
            return []
        try:
            results = self._collection.get(
                where={"$and": [{"course_id": course_id}, {"is_active": True}]},
                include=["documents", "metadatas"],
            )
        except Exception as e:
            logger.error("按 course_id 读取向量失败: %s", e)
            raise ServiceUnavailableException(
                "向量读取失败", detail=str(e)
            ) from e

        ids = results.get("ids") or []
        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or []
        hits: list[dict] = []
        for i, chunk_id in enumerate(ids):
            meta = (metadatas[i] if i < len(metadatas) else None) or {}
            hits.append(
                self._normalize_hit(
                    chunk_id,
                    documents[i] if i < len(documents) else "",
                    meta,
                )
            )
        return hits

    def list_chapters(self, course_id: str) -> list[str]:
        """本课已入库的非空 chapter 名（去重、排序）。"""
        corpus = self.get_by_course_id(course_id)
        return sorted(
            {
                (h.get("metadata") or {}).get("chapter") or ""
                for h in corpus
                if (h.get("metadata") or {}).get("chapter")
            }
        )

    def group_by_chapter(self, course_id: str) -> list[dict]:
        """按章节（内容）聚合本课 chunk，返回每个章节的来源文件与 chunk 数。"""
        corpus = self.get_by_course_id(course_id)
        groups: dict[str, dict] = {}
        for h in corpus:
            meta = h.get("metadata") or {}
            chapter = (meta.get("chapter") or "").strip()
            if not chapter or _PAGE_PLACEHOLDER.match(chapter):
                continue
            group = groups.setdefault(
                chapter,
                {"chapter": chapter, "source_files": set(), "chunk_count": 0},
            )
            group["source_files"].add(meta.get("source_file") or "未知")
            group["chunk_count"] += 1
        return [
            {
                "chapter": group["chapter"],
                "chunk_count": group["chunk_count"],
                "source_files": sorted(group["source_files"]),
            }
            for group in sorted(groups.values(), key=lambda g: g["chapter"])
        ]

    @staticmethod
    def _sort_chapter_hits(hits: list[dict]) -> list[dict]:
        def _key(h: dict):
            meta = h.get("metadata") or {}
            return (
                str(meta.get("source_file") or ""),
                int(meta.get("chunk_index") or 0),
            )

        return sorted(hits, key=_key)

    def get_by_chapter(
        self,
        course_id: str,
        chapter: str,
        *,
        scenario: str | None = None,
        as_of: str | None = None,
    ) -> list[dict]:
        """按课聚合章节；先精确，再 query⊂chapter 模糊（禁止 chapter⊂query，避免第9章误匹配第99章）。"""
        if not course_id or not course_id.strip():
            raise ValueError("get_by_chapter 必须提供 course_id")
        key = (chapter or "").strip()
        if not key:
            return []
        corpus = self.get_by_course_id(course_id)
        if scenario and scenario != "all":
            corpus = [
                hit
                for hit in corpus
                if (hit.get("metadata") or {}).get("applicability_scope")
                in ("all", scenario)
            ]
        if as_of:
            corpus = [
                hit
                for hit in corpus
                if str((hit.get("metadata") or {}).get("effective_from") or "0001-01-01")
                <= as_of
                <= str((hit.get("metadata") or {}).get("effective_to") or "9999-12-31")
            ]
        exact = [
            h
            for h in corpus
            if ((h.get("metadata") or {}).get("chapter") or "") == key
        ]
        if exact:
            return self._sort_chapter_hits(exact)
        key_l = key.lower()
        if len(key_l) < 2:
            return []
        fuzzy = [
            h
            for h in corpus
            if key_l in ((h.get("metadata") or {}).get("chapter") or "").lower()
        ]
        return self._sort_chapter_hits(fuzzy)

    def delete_by_doc_id(self, doc_id: str) -> None:
        try:
            self._collection.delete(where={"doc_id": doc_id})
            logger.info("已删除 doc_id=%s 的向量", doc_id)
        except Exception as e:
            logger.error("按 metadata 删除向量失败 doc_id=%s: %s", doc_id, e)
            raise ServiceUnavailableException("向量删除失败", detail=str(e)) from e

    def health_check(self) -> bool:
        try:
            self._collection.count()
            return True
        except Exception as e:
            logger.error("ChromaDB 健康检查失败: %s", e)
            return False

    def close(self) -> None:
        # ponytail: Chroma 无显式 close；释放引用以便 Windows 测试 teardown 解锁文件
        self._collection = None
        self._client = None
