"""HTTP 契约：目录 / 资料上传删除扫描 / 问答校验 / 配置与模型。"""

import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.dependencies import get_catalog_store, get_doc_store, get_vision_client
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

    def test_config_exposes_llm_output_budget(self):
        response = client.get("/api/v1/config")
        assert response.status_code == 200
        assert response.json()["data"]["llm"]["max_tokens"] >= 256

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


class TestVLMAPI:
    class FakeVision:
        model = "test-vlm"

        def public_status(self):
            return {
                "configured": True,
                "model": self.model,
                "base_url": "https://vlm.example/v1",
                "timeout": 30,
                "protocol": "openai-compatible",
                "supported_mime_types": ["image/png"],
            }

        def analyze_bytes(self, image, **kwargs):
            assert image == b"fake-png"
            assert kwargs["mime"] == "image/png"
            assert kwargs["caption"] == "图 5.3-1"
            assert kwargs["page"] == 13
            return "4000 cm^-1 处反射率约为 28%，低于 40%。"

    def test_status_does_not_expose_api_key(self):
        app.dependency_overrides[get_vision_client] = self.FakeVision
        try:
            response = client.get("/api/v1/vlm/status")
        finally:
            app.dependency_overrides.pop(get_vision_client, None)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["configured"] is True
        assert "api_key" not in data

    def test_analyze_image(self):
        app.dependency_overrides[get_vision_client] = self.FakeVision
        try:
            response = client.post(
                "/api/v1/vlm/analyze",
                data={"caption": "图 5.3-1", "page": "13"},
                files={"file": ("chart.png", BytesIO(b"fake-png"), "image/png")},
            )
        finally:
            app.dependency_overrides.pop(get_vision_client, None)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["model"] == "test-vlm"
        assert "低于 40%" in data["analysis"]

    def test_rejects_non_image(self):
        app.dependency_overrides[get_vision_client] = self.FakeVision
        try:
            response = client.post(
                "/api/v1/vlm/analyze",
                files={"file": ("note.txt", BytesIO(b"text"), "text/plain")},
            )
        finally:
            app.dependency_overrides.pop(get_vision_client, None)
        assert response.status_code == 400


class TestRootRedirect:
    def test_root_goes_to_sz(self):
        r = client.get("/", follow_redirects=False)
        assert r.status_code in (307, 302)
        assert r.headers["location"].endswith("/sz/")

    def test_old_pages_are_removed(self):
        for page in ("sz", "sz-docs", "sz-bank", "sz-cfg"):
            response = client.get(f"/legacy/{page}/")
            assert response.status_code == 404
        assert client.get("/legacy/shared/css/tokens.css").status_code == 404

    def test_vue_build_preserves_legacy_entry_points(self):
        if not (Path(__file__).resolve().parent.parent / "www-dist" / "index.html").is_file():
            pytest.skip("静态构建由服务启动时生成")
        cases = {
            "/sz-docs/": "/sz/#/documents",
            "/sz-cfg/": "/sz/#/settings",
            "/sz-bank/?topic=采样定理": "/sz/#/question-bank?topic=%E9%87%87%E6%A0%B7%E5%AE%9A%E7%90%86",
        }
        for old, target in cases.items():
            response = client.get(old, follow_redirects=False)
            assert response.status_code == 307
            assert response.headers["location"] == target
        assert client.get("/sz/").status_code == 200
