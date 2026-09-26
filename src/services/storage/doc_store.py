"""SQLite 文档元数据存储。"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.services.storage.catalog_store import DEFAULT_COURSE_ID, DEFAULT_COURSE_NAME

logger = logging.getLogger(__name__)


_TYPE_LABELS = {
    ".md": ("markdown", "Markdown 笔记"),
    ".txt": ("text", "纯文本"),
    ".pdf": ("pdf", "PDF 讲义"),
    ".doc": ("word", "Word 文档"),
    ".docx": ("word", "Word 文档"),
    ".pptx": ("ppt", "PPT 讲义"),
}


class SQLiteDocStore:
    """文档元数据 CRUD：create、update_status、get、list、delete。"""

    def __init__(self, db_path: str):
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.commit()

    def _init_schema(self) -> None:
        self._conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                chunk_count INTEGER NOT NULL DEFAULT 0,
                course TEXT NOT NULL DEFAULT '{DEFAULT_COURSE_NAME}',
                course_id TEXT NOT NULL DEFAULT '{DEFAULT_COURSE_ID}',
                created_at TEXT NOT NULL,
                file_mtime REAL,
                content_hash TEXT NOT NULL DEFAULT '',
                logical_name TEXT NOT NULL DEFAULT '',
                version_number INTEGER NOT NULL DEFAULT 1,
                previous_doc_id INTEGER,
                superseded_by INTEGER,
                is_active INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT,
                source_version TEXT NOT NULL DEFAULT '',
                effective_from TEXT NOT NULL DEFAULT '0001-01-01',
                effective_to TEXT NOT NULL DEFAULT '9999-12-31',
                authority_level INTEGER NOT NULL DEFAULT 30,
                authority_label TEXT NOT NULL DEFAULT '教学材料',
                applicability_scope TEXT NOT NULL DEFAULT 'all',
                metadata_confidence REAL NOT NULL DEFAULT 0,
                metadata_source TEXT NOT NULL DEFAULT 'auto'
            )
            """
        )
        self._conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(documents)")}
        if "file_mtime" not in cols:
            self._conn.execute("ALTER TABLE documents ADD COLUMN file_mtime REAL")
        if "course_id" not in cols:
            self._conn.execute(
                "ALTER TABLE documents ADD COLUMN course_id TEXT "
                f"NOT NULL DEFAULT '{DEFAULT_COURSE_ID}'"
            )
            self._conn.execute(
                "UPDATE documents SET course_id = ? "
                "WHERE course_id IS NULL OR course_id = ''",
                (DEFAULT_COURSE_ID,),
            )
            self._conn.execute(
                "UPDATE documents SET course = ? "
                "WHERE course IS NULL OR course = '' OR course = '信号与系统'",
                (DEFAULT_COURSE_NAME,),
            )
        additions = {
            "content_hash": "TEXT NOT NULL DEFAULT ''",
            "logical_name": "TEXT NOT NULL DEFAULT ''",
            "version_number": "INTEGER NOT NULL DEFAULT 1",
            "previous_doc_id": "INTEGER",
            "superseded_by": "INTEGER",
            "is_active": "INTEGER NOT NULL DEFAULT 1",
            "updated_at": "TEXT",
            "source_version": "TEXT NOT NULL DEFAULT ''",
            "effective_from": "TEXT NOT NULL DEFAULT '0001-01-01'",
            "effective_to": "TEXT NOT NULL DEFAULT '9999-12-31'",
            "authority_level": "INTEGER NOT NULL DEFAULT 30",
            "authority_label": "TEXT NOT NULL DEFAULT '教学材料'",
            "applicability_scope": "TEXT NOT NULL DEFAULT 'all'",
            "metadata_confidence": "REAL NOT NULL DEFAULT 0",
            "metadata_source": "TEXT NOT NULL DEFAULT 'auto'",
        }
        for name, definition in additions.items():
            if name not in cols:
                self._conn.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")
        self._conn.commit()

    def _cols(self) -> str:
        return (
            "id, filename, file_path, status, chunk_count, course, course_id, "
            "created_at, file_mtime, content_hash, logical_name, version_number, "
            "previous_doc_id, superseded_by, is_active, updated_at"
            ", source_version, effective_from, effective_to, authority_level, "
            "authority_label, applicability_scope, metadata_confidence, metadata_source"
        )

    def create(
        self,
        filename: str,
        file_path: str,
        course: str = DEFAULT_COURSE_NAME,
        course_id: str = DEFAULT_COURSE_ID,
        *,
        content_hash: str = "",
        logical_name: str = "",
        is_active: bool = True,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "INSERT INTO documents "
            "(filename, file_path, status, chunk_count, course, course_id, created_at, "
            "content_hash, logical_name, is_active, updated_at) "
            "VALUES (?, ?, 'pending', 0, ?, ?, ?, ?, ?, ?, ?)",
            (
                filename,
                file_path,
                course,
                course_id,
                now,
                content_hash,
                logical_name,
                int(is_active),
                now,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def get(self, doc_id: int) -> dict | None:
        row = self._conn.execute(
            f"SELECT {self._cols()} FROM documents WHERE id = ?",
            (doc_id,),
        ).fetchone()
        return dict(row) if row else None

    def update_status(
        self, doc_id: int, status: str, chunk_count: int | None = None
    ) -> None:
        if chunk_count is None:
            self._conn.execute(
                "UPDATE documents SET status = ?, updated_at = ? WHERE id = ?",
                (status, datetime.now(timezone.utc).isoformat(), doc_id),
            )
        else:
            self._conn.execute(
                "UPDATE documents SET status = ?, chunk_count = ?, updated_at = ? WHERE id = ?",
                (status, chunk_count, datetime.now(timezone.utc).isoformat(), doc_id),
            )
        self._conn.commit()

    def update_course(self, doc_id: int, course: str, course_id: str) -> None:
        self._conn.execute(
            "UPDATE documents SET course = ?, course_id = ? WHERE id = ?",
            (course, course_id, doc_id),
        )
        self._conn.commit()

    def list(
        self, course_id: str | None = None, *, include_history: bool = False
    ) -> list[dict]:
        active_clause = "" if include_history else " AND is_active = 1"
        if course_id:
            rows = self._conn.execute(
                f"SELECT {self._cols()} FROM documents "
                f"WHERE course_id = ?{active_clause} ORDER BY id DESC",
                (course_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT {self._cols()} FROM documents "
                f"WHERE 1 = 1{active_clause} ORDER BY id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def group_by_type(self, course_id: str | None = None) -> list[dict]:
        """按文件类型对文档大致分类，返回分组列表（每项含 type/label/documents）。"""
        groups: dict[str, dict] = {}
        for doc in self.list(course_id=course_id):
            ext = Path(doc["filename"]).suffix.lower()
            type_key, label = _TYPE_LABELS.get(ext, ("other", "其他"))
            group = groups.setdefault(
                type_key, {"type": type_key, "label": label, "documents": []}
            )
            group["documents"].append(doc)
        return sorted(groups.values(), key=lambda g: g["type"])

    def delete(self, doc_id: int) -> None:
        self._conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        self._conn.commit()

    def find_by_path(self, file_path: str) -> dict | None:
        row = self._conn.execute(
            f"SELECT {self._cols()} FROM documents "
            "WHERE file_path = ? ORDER BY id DESC LIMIT 1",
            (file_path,),
        ).fetchone()
        return dict(row) if row else None

    def find_active_by_hash(self, content_hash: str, course_id: str) -> dict | None:
        if not content_hash:
            return None
        row = self._conn.execute(
            f"SELECT {self._cols()} FROM documents "
            "WHERE content_hash = ? AND course_id = ? AND is_active = 1 "
            "ORDER BY id DESC LIMIT 1",
            (content_hash, course_id),
        ).fetchone()
        return dict(row) if row else None

    def find_active_by_logical_name(
        self, logical_name: str, course_id: str
    ) -> list[dict]:
        if not logical_name:
            return []
        rows = self._conn.execute(
            f"SELECT {self._cols()} FROM documents "
            "WHERE logical_name = ? AND course_id = ? AND is_active = 1 "
            "ORDER BY id DESC",
            (logical_name, course_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_identity(
        self, doc_id: int, *, content_hash: str, logical_name: str
    ) -> None:
        self._conn.execute(
            "UPDATE documents SET content_hash = ?, logical_name = ?, updated_at = ? "
            "WHERE id = ?",
            (content_hash, logical_name, datetime.now(timezone.utc).isoformat(), doc_id),
        )
        self._conn.commit()

    def promote_version(self, old_doc_id: int, new_doc_id: int) -> None:
        """将已成功入库的新记录设为当前版本，旧记录仅保留历史元数据。"""
        now = datetime.now(timezone.utc).isoformat()
        old = self.get(old_doc_id)
        if old is None:
            raise ValueError(f"旧文档不存在: {old_doc_id}")
        version = int(old.get("version_number") or 1) + 1
        self._conn.execute(
            "UPDATE documents SET is_active = 0, status = 'superseded', "
            "superseded_by = ?, updated_at = ? WHERE id = ?",
            (new_doc_id, now, old_doc_id),
        )
        self._conn.execute(
            "UPDATE documents SET is_active = 1, status = 'done', version_number = ?, "
            "previous_doc_id = ?, updated_at = ? WHERE id = ?",
            (version, old_doc_id, now, new_doc_id),
        )
        self._conn.commit()

    def supersede_many(self, doc_ids: list[int], superseded_by: int) -> None:
        """清理同一逻辑资料遗留的多个活跃旧版本。"""
        targets = sorted(
            {int(doc_id) for doc_id in doc_ids if int(doc_id) != superseded_by}
        )
        if not targets:
            return
        placeholders = ",".join("?" for _ in targets)
        self._conn.execute(
            f"UPDATE documents SET is_active = 0, status = 'superseded', "
            f"superseded_by = ?, updated_at = ? WHERE id IN ({placeholders})",
            (
                superseded_by,
                datetime.now(timezone.utc).isoformat(),
                *targets,
            ),
        )
        self._conn.commit()

    def update_evidence_metadata(self, doc_id: int, metadata: dict) -> None:
        """写入可过滤、可排序、可解释的证据元数据。"""
        fields = (
            "source_version",
            "effective_from",
            "effective_to",
            "authority_level",
            "authority_label",
            "applicability_scope",
            "metadata_confidence",
            "metadata_source",
        )
        values = [metadata.get(name) for name in fields]
        self._conn.execute(
            "UPDATE documents SET "
            + ", ".join(f"{name} = ?" for name in fields)
            + ", updated_at = ? WHERE id = ?",
            (*values, datetime.now(timezone.utc).isoformat(), doc_id),
        )
        self._conn.commit()

    def update_file_mtime(self, doc_id: int, mtime: float) -> None:
        self._conn.execute(
            "UPDATE documents SET file_mtime = ? WHERE id = ?",
            (mtime, doc_id),
        )
        self._conn.commit()

    def recover_stale_processing(self) -> int:
        cur = self._conn.execute(
            "UPDATE documents SET status = 'failed', chunk_count = 0 "
            "WHERE status = 'processing'"
        )
        self._conn.commit()
        return cur.rowcount

    def health_check(self) -> bool:
        try:
            self._conn.execute("SELECT 1").fetchone()
            return True
        except Exception as e:
            logger.error("SQLite 健康检查失败: %s", e)
            return False

    def close(self) -> None:
        self._conn.close()
