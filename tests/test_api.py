"""HTTP 契约：目录 / 资料上传删除扫描 / 问答校验 / 配置与模型。"""

import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.dependencies import get_catalog_store, get_doc_store
from src.main import app
from src.services import llm_providers as registry

client = TestClient(app)


class TestAskAPI:
    def test_missing_course_id_422(self):
        r = client.post("/api/v1/ask", json={"question": "卷积定理"})
        assert r.status_code == 422
        assert r.json()["code"] == 422

    def test_invalid_mode_422(self):
        r = client.post(
            "/api/v1/ask",
            json={
                "question": "测试",
                "mode": "bogus",
                "course_id": "course-default",
            },
        )
        assert r.status_code == 422


class TestCatalogAPI:
    def test_list_colleges_and_courses(self):
        r = client.get("/api/v1/colleges")
        assert r.status_code == 200
        assert any(c["id"] == "college-default" for c in r.json()["data"]["items"])
        r2 = client.get("/api/v1/courses")
        assert r2.status_code == 200
        assert any(c["id"] == "course-default" for c in r2.json()["data"]["items"])


class TestDocumentsAPI:
    def test_list_requires_course_id(self):
        assert client.get("/api/v1/documents").status_code == 422

    def test_list_ok(self):
        r = client.get("/api/v1/documents", params={"course_id": "course-default"})
        assert r.status_code == 200
        data = r.json()["data"]
        assert "items" in data and "embedding" in data

    def test_summary_by_type_default(self):
        r = client.get(
            "/api/v1/documents/summary",
            params={"course_id": "course-default"},
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["dimension"] == "type"
        assert "groups" in data and "total" in data

    def test_summary_by_chapter(self):
        r = client.get(
            "/api/v1/documents/summary",
            params={"course_id": "course-default", "by": "chapter"},
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["dimension"] == "chapter"
        assert "groups" in data and "total_chunks" in data
        for group in data["groups"]:
            assert "chapter" in group and "chunk_count" in group

    def test_summary_rejects_unknown_dimension(self):
        r = client.get(
            "/api/v1/documents/summary",
            params={"course_id": "course-default", "by": "bogus"},
        )
        assert r.status_code == 422


    def test_scan_and_unknown_course(self):
        ok = client.post(
            "/api/v1/documents/scan",
            data={"course_id": "course-default"},
        )
        assert ok.status_code == 200
        assert ok.json()["data"]["force"] is False

        bad = client.post(
            "/api/v1/documents/scan",
            data={"course_id": "course-nonexistent"},
        )
        assert bad.status_code == 404

    def test_scan_force_true(self, monkeypatch):
        called = {}

        def fake_scan(*args, **kwargs):
            called.update(kwargs)

        monkeypatch.setattr("src.apis.v1.documents.scan_knowledge_dir", fake_scan)
        r = client.post(
            "/api/v1/documents/scan",
            data={"course_id": "course-default", "force": "true"},
        )
        assert r.status_code == 200
        assert r.json()["data"]["force"] is True
        assert called.get("force") is True

    def test_upload_calls_ingest_with_course_id(self):
        def fake_ingest(**kwargs):
            assert kwargs["course_id"] == "course-default"
            return "42"

        with patch("src.apis.v1.documents.ingest_file", side_effect=fake_ingest):
            r = client.post(
                "/api/v1/documents",
                data={"course_id": "course-default"},
                files={"file": ("note.md", BytesIO(b"# hello\n"), "text/markdown")},
            )
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["doc_id"] == "42"
        assert body["course_id"] == "course-default"

    def test_upload_rejects_unsupported(self):
        r = client.post(
            "/api/v1/documents",
            data={"course_id": "course-default"},
            files={"file": ("x.xyz", BytesIO(b"nope"), "application/octet-stream")},
        )
        assert r.status_code == 400

    def test_delete_requires_course_and_404(self):
        r = client.delete("/api/v1/documents/99999?course_id=course-default")
        assert r.status_code == 404

    def test_open_source_serves_only_current_course(self, temp_dir):
        source = temp_dir / "source.md"
        source.write_text("# 原文内容", encoding="utf-8")
        ds, catalog = MagicMock(), MagicMock()
        ds.get.return_value = {
            "id": 7,
            "course_id": "course-default",
            "filename": "课程资料.md",
            "file_path": str(source),
        }
        app.dependency_overrides[get_doc_store] = lambda: ds
        app.dependency_overrides[get_catalog_store] = lambda: catalog
        try:
            response = client.get(
                "/api/v1/documents/7/source",
                params={"course_id": "course-default"},
            )
        finally:
            app.dependency_overrides.pop(get_doc_store, None)
            app.dependency_overrides.pop(get_catalog_store, None)
        assert response.status_code == 200
        assert response.text == "# 原文内容"
        assert "inline" in response.headers["content-disposition"]
        catalog.require_course.assert_called_once_with("course-default")

    def test_open_source_rejects_other_course(self, temp_dir):
        source = temp_dir / "source.md"
        source.write_text("# 原文内容", encoding="utf-8")
        ds, catalog = MagicMock(), MagicMock()
        ds.get.return_value = {
            "id": 7,
            "course_id": "course-other",
            "filename": "课程资料.md",
            "file_path": str(source),
        }
        app.dependency_overrides[get_doc_store] = lambda: ds
        app.dependency_overrides[get_catalog_store] = lambda: catalog
        try:
            response = client.get(
                "/api/v1/documents/7/source",
                params={"course_id": "course-default"},
            )
        finally:
            app.dependency_overrides.pop(get_doc_store, None)
            app.dependency_overrides.pop(get_catalog_store, None)
        assert response.status_code == 404


class TestConfigAndProviders:
    def test_config_includes_retrieval_rerank(self):
        r = client.get("/api/v1/config")
        assert r.status_code == 200
        ret = r.json()["data"]["retrieval"]
        for key in (
            "top_k",
            "score_threshold",
            "rerank_enabled",
            "rerank_model",
            "rerank_candidates",
            "rerank_top_n",
        ):
            assert key in ret

    def test_llm_providers_register_list(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm_providers.json"
            monkeypatch.setattr(registry, "STORE_PATH", path)
            monkeypatch.setattr(registry, "write_env_updates", lambda updates: None)
            monkeypatch.setattr(registry, "ensure_seeded_from_env", lambda: None)

            r = client.post(
                "/api/v1/llm-providers",
                json={
                    "name": "deepseek",
                    "format": "openai",
                    "model": "deepseek-chat",
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "sk-test",
                },
            )
            assert r.status_code == 200
            listed = client.get("/api/v1/llm-providers").json()["data"]
            assert any(p["name"] == "deepseek" for p in listed["items"])


class TestRootRedirect:
    def test_root_goes_to_sz(self):
        r = client.get("/", follow_redirects=False)
        assert r.status_code in (307, 302)
        assert r.headers["location"].endswith("/sz/")
