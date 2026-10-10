"""Embedding warmup / status API。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from fastapi.testclient import TestClient

from src.dependencies import get_embedding_client
from src.main import app
from src.services import embedding as emb_mod

client = TestClient(app)


def local_factory(monkeypatch, factory):
    import sys
    from types import SimpleNamespace

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=factory))
    monkeypatch.setattr(emb_mod, "_cached_model_path", lambda model: model)
    monkeypatch.setattr("src.services.http_client.configure_hf_http_client", MagicMock())
    monkeypatch.setattr("src.services.http_client.reset_hf_http_session", MagicMock())


def test_restart_loads_cache_without_network(monkeypatch, tmp_path):
    resolve_cache = emb_mod._cached_model_path
    encoder = MagicMock()
    encoder.encode.return_value = [MagicMock(tolist=lambda: [0.1, 0.2])]
    factory = MagicMock(return_value=encoder)
    local_factory(monkeypatch, factory)
    monkeypatch.setattr(emb_mod, "_cached_model_path", resolve_cache)
    monkeypatch.setenv("SENTENCE_TRANSFORMERS_HOME", str(tmp_path / "cache"))
    snapshot = MagicMock(return_value=str(tmp_path / "snapshot"))
    monkeypatch.setattr("huggingface_hub.snapshot_download", snapshot)
    # 新建客户端模拟重启；每次只离线读取缓存，首次 embed 也不重复装载。
    for _ in range(2):
        local = emb_mod.LocalEmbeddingClient("cached-model", device="cpu")
        local.warmup()
        assert local.embed(["问题"]) == [[0.1, 0.2]]
        assert local.status() == "ok"
    assert factory.call_count == 2
    assert all(call.kwargs["local_files_only"] for call in factory.call_args_list)
    assert all(call.args[0] == str(tmp_path / "snapshot") for call in factory.call_args_list)
    snapshot.assert_called_with(
        "sentence-transformers/cached-model", local_files_only=True, cache_dir=str(tmp_path / "cache"),
    )
    from src.services import http_client
    http_client.configure_hf_http_client.assert_not_called()
    http_client.reset_hf_http_session.assert_not_called()


def test_explicit_model_directory_never_uses_hub(monkeypatch, tmp_path):
    resolve_cache = emb_mod._cached_model_path
    factory = MagicMock()
    local_factory(monkeypatch, factory)
    monkeypatch.setattr(emb_mod, "_cached_model_path", resolve_cache)
    snapshot = MagicMock(side_effect=AssertionError("local path must not contact hub"))
    monkeypatch.setattr("huggingface_hub.snapshot_download", snapshot)
    emb_mod.LocalEmbeddingClient(str(tmp_path), device="cpu").warmup()
    factory.assert_called_once_with(str(tmp_path.resolve()), device="cpu", local_files_only=True)
    snapshot.assert_not_called()


def test_missing_cache_requires_explicit_download(monkeypatch):
    encoder = MagicMock()
    encoder.get_embedding_dimension.return_value = 2
    factory = MagicMock(side_effect=[OSError("missing weights"), OSError("missing weights"), encoder])
    local_factory(monkeypatch, factory)
    local = emb_mod.LocalEmbeddingClient("missing-model", device="cpu")
    emb_mod.clear_warmup_progress()
    # 服务启动只检查缓存，不能因缺少文件自动下载。
    progress = emb_mod.warmup_with_progress(local, local_only=True)
    assert progress["phase"] == "idle"
    assert progress["error"] is None
    assert local.status() == "not_ready"
    assert factory.call_count == 1
    # 用户手动预热时才允许补齐下载。
    emb_mod.warmup_with_progress(local)
    assert local.status() == "ok"
    assert [call.kwargs.get("local_files_only", False) for call in factory.call_args_list] == [True, True, False]


def test_invalid_cached_model_does_not_trigger_download(monkeypatch):
    factory = MagicMock(side_effect=ValueError("invalid configuration"))
    local_factory(monkeypatch, factory)
    local = emb_mod.LocalEmbeddingClient("invalid-model", device="cpu")
    with pytest.raises(ValueError, match="invalid configuration"):
        local.warmup()
    assert factory.call_count == 1


def test_startup_automatically_loads_local_cache():
    from src import main

    local = MagicMock(provider="local")
    local.status.return_value = "not_ready"
    store = MagicMock()
    store.rebind_course_id.return_value = 0
    with (
        patch.object(main, "get_embedding_client", return_value=local),
        patch.object(main, "start_warmup_background") as start,
        patch.object(main, "get_vector_store", return_value=store),
        patch.object(main, "get_doc_store", return_value=store),
        patch.object(main, "get_catalog_store", return_value=store),
        patch.object(main, "get_llm_client"),
        patch.object(main, "get_vision_client"),
        patch.object(main, "ensure_ui_build"),
        patch.object(main, "_attach_file_handler"),
        patch.object(main, "log_startup_banner"),
        patch("src.services.llm_providers.ensure_seeded_from_env"),
        TestClient(app),
    ):
        start.assert_called_once_with(local, local_only=True)


def test_get_warmup_progress_defaults():
    p = emb_mod.get_warmup_progress()
    assert "phase" in p
    assert "percent" in p


def test_start_warmup_background_already_ok():
    fake = MagicMock()
    fake.provider = "local"
    fake.model = "all-MiniLM-L6-v2"
    fake.status.return_value = "ok"
    emb_mod._warmup_thread = None
    out = emb_mod.start_warmup_background(fake)
    assert out["phase"] == "ok"
    assert out["percent"] == 100


def test_build_status_heals_when_model_ready():
    fake = MagicMock()
    fake.provider = "local"
    fake.model = "m"
    fake.status.return_value = "ok"
    emb_mod._set_progress(
        phase="running",
        percent=None,
        message="装载到内存…",
        error=None,
        provider="local",
        model="m",
    )
    data = emb_mod.build_embedding_status(fake)
    assert data["status"] == "ok"
    assert data["warmup"]["phase"] == "ok"
    assert data["warmup"]["percent"] == 100


def test_build_status_drops_stale_ok_after_reset():
    fake = MagicMock()
    fake.provider = "local"
    fake.model = "m"
    fake.status.return_value = "not_ready"
    emb_mod._set_progress(
        phase="ok",
        percent=100,
        message="向量化已就绪",
        error=None,
        provider="local",
        model="old",
    )
    data = emb_mod.build_embedding_status(fake)
    assert data["status"] == "not_ready"
    assert data["warmup"]["phase"] == "idle"


def test_embedding_status_and_warmup_api():
    fake = MagicMock()
    fake.provider = "local"
    fake.model = "all-MiniLM-L6-v2"
    fake.status.return_value = "not_ready"

    started = {}

    def fake_start(c):
        started["ok"] = True
        emb_mod._set_progress(
            phase="running",
            percent=12,
            message="downloading",
            error=None,
            provider="local",
            model="all-MiniLM-L6-v2",
        )
        return emb_mod.get_warmup_progress()

    app.dependency_overrides[get_embedding_client] = lambda: fake
    try:
        with patch(
            "src.apis.v1.embedding.start_warmup_background", side_effect=fake_start
        ):
            r = client.post("/api/v1/embedding/warmup")
            assert r.status_code == 200
            assert r.json()["data"]["warmup"]["phase"] == "running"
            assert started.get("ok")

            r2 = client.get("/api/v1/embedding/status")
            assert r2.status_code == 200
            assert r2.json()["data"]["warmup"]["percent"] == 12
            assert r2.json()["data"]["status"] == "loading"
    finally:
        app.dependency_overrides.pop(get_embedding_client, None)


def test_clear_warmup_progress_bumps_generation():
    g0 = emb_mod._warmup_gen
    emb_mod.clear_warmup_progress()
    assert emb_mod._warmup_gen == g0 + 1
    assert emb_mod.get_warmup_progress()["phase"] == "idle"


def test_patch_hub_tqdm_targets_module_not_class():
    """回归：import huggingface_hub.utils.tqdm 会拿到类，不能 .tqdm 取值。"""
    import huggingface_hub.utils as hub_utils

    class FakeBar:
        def __init__(self, *a, **k):
            self.total = 10
            self.n = 0
            self.desc = "x"

        def update(self, n=1):
            self.n += n

    Progress = emb_mod._make_progress_tqdm(emb_mod._warmup_gen, base=FakeBar)
    mod, utils, old_mod, old_utils = emb_mod._patch_hub_tqdm(Progress)
    try:
        assert utils.tqdm is Progress
        assert mod.tqdm is Progress
        bar = hub_utils.tqdm(total=10)
        bar.update(5)
        assert bar.n == 5
    finally:
        mod.tqdm = old_mod
        utils.tqdm = old_utils
