"""配置转换和失败请求不得提前持久化。"""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.apis.v1 import config as config_api
from src.main import app
from src.models import ConfigUpdateRequest
from src.services import llm_providers
from src.services.env_store import MASKED_SECRET


@pytest.mark.parametrize("invalid", [
    {"chunk": {"chunk_size": 1, "chunk_overlap": 1}},
    {"proxy": {"hf_endpoint": "javascript:bad"}},
    {"server": {"port": 8801}},
])
def test_invalid_mixed_patch_has_no_writes(monkeypatch, invalid):
    registry = Mock()
    environment = Mock()
    monkeypatch.setattr(llm_providers, "sync_active_fields_from_patch", registry)
    monkeypatch.setattr(llm_providers, "write_env_updates", environment)
    monkeypatch.setattr(config_api, "port_available", lambda *_: False)
    response = TestClient(app).patch("/api/v1/config", json={"llm": {"model": "new"}, **invalid})
    assert response.status_code == 400
    registry.assert_not_called()
    environment.assert_not_called()


def test_config_conversion_is_pure_and_keeps_serialization(monkeypatch):
    registry = Mock()
    monkeypatch.setattr(llm_providers, "sync_active_fields_from_patch", registry)
    updates, sections = config_api._patch_to_env(ConfigUpdateRequest.model_validate({
        "llm": {"api_key": MASKED_SECRET, "model": " model ", "timeout": 30},
        "retrieval": {"rerank_enabled": False, "rerank_top_n": 0},
        "parsing": {"visual_api_key": MASKED_SECRET, "mineru_timeout": 0,
                    "formula_recognition_device": " CPU "},
        "proxy": {"enabled": True, "no_proxy": " localhost "},
    }))
    assert updates == {"LLM_MODEL": "model", "LLM_TIMEOUT": "30",
                       "RERANK_ENABLED": "false", "RERANK_TOP_N": "0",
                       "MINERU_TIMEOUT": "0", "FORMULA_RECOGNITION_DEVICE": "cpu",
                       "PROXY_ENABLED": "true", "NO_PROXY": "localhost"}
    assert sections == ["llm", "retrieval", "parsing", "proxy"]
    registry.assert_not_called()


def test_environment_write_failure_does_not_change_registry(monkeypatch):
    registry = Mock()
    monkeypatch.setattr(llm_providers, "sync_active_fields_from_patch", registry)
    monkeypatch.setattr(llm_providers, "write_env_updates", Mock(side_effect=OSError("disk full")))
    response = TestClient(app).patch("/api/v1/config", json={"llm": {"model": "new"}})
    assert response.status_code == 500
    registry.assert_not_called()


def test_registry_partial_write_failure_restores_both_files(monkeypatch, tmp_path):
    from src.exceptions import AppException
    from src.services import env_store

    env = tmp_path / ".env"
    registry = tmp_path / "providers.json"
    env.write_bytes(b"LLM_MODEL=old\r\n# preserve comments\r\n")
    registry.write_bytes(b"[]\n")
    original = env.read_bytes(), registry.read_bytes()
    monkeypatch.setattr(env_store, "ENV_PATH", env)
    monkeypatch.setattr(llm_providers, "STORE_PATH", registry)
    monkeypatch.setattr("src.config.reload_config", lambda: None)
    def fail(**_kwargs):
        registry.write_bytes(b"partial")
        raise OSError("disk full")
    monkeypatch.setattr(llm_providers, "sync_active_fields_from_patch", fail)
    with pytest.raises(AppException, match="已恢复原配置"):
        llm_providers.persist_config_updates({"LLM_MODEL": "new"}, {"model": "new"})
    assert (env.read_bytes(), registry.read_bytes()) == original
