"""站点专用代理与公开下载镜像的配置读写。"""

import pytest

from src.apis.v1.config import _patch_to_env
from src.exceptions import BadRequestException
from src.config import ProxyConfig
from src.models import ConfigUpdateRequest, ProxyPatch
from src.services import http_client
from src.services.http_client import proxy_for_url


def test_site_proxy_precedence():
    proxy = ProxyConfig(
        url="http://127.0.0.1:7890",
        enabled=True,
        hf_url="http://127.0.0.1:7891",
        github_url="http://127.0.0.1:7892",
    )
    assert proxy_for_url("https://huggingface.co/model", proxy) == proxy.hf_url
    assert proxy_for_url("https://raw.githubusercontent.com/x/y", proxy) == proxy.github_url
    assert proxy_for_url("https://api.openai.com/v1", proxy) == proxy.url

    proxy.hf_url = ""
    proxy.github_url = ""
    assert proxy_for_url("https://huggingface.co/model", proxy) == proxy.url
    assert proxy_for_url("https://github.com/x/y", proxy) == proxy.url
    proxy.enabled = False
    assert proxy_for_url("https://github.com/x/y", proxy) == ""


def test_proxy_patch_writes_site_addresses():
    body = ConfigUpdateRequest(
        proxy=ProxyPatch(hf_url=" http://127.0.0.1:7891 ", github_url="http://127.0.0.1:7892")
    )
    updates, sections = _patch_to_env(body)
    assert updates["HF_PROXY_URL"] == "http://127.0.0.1:7891"
    assert updates["GITHUB_PROXY_URL"] == "http://127.0.0.1:7892"
    assert sections == ["proxy"]


def test_mirror_patch_keeps_download_mirrors_separate_from_http_proxy():
    body = ConfigUpdateRequest(proxy=ProxyPatch(
        hf_endpoint=" https://hf-mirror.com/ ",
        github_mirror_url="https://gh-proxy.com",
    ))
    updates, sections = _patch_to_env(body)
    assert updates == {
        "HF_ENDPOINT": "https://hf-mirror.com",
        "GITHUB_MIRROR_URL": "https://gh-proxy.com/",
    }
    assert sections == ["proxy"]


def test_mirror_patch_accepts_custom_and_direct():
    custom, _ = _patch_to_env(ConfigUpdateRequest(proxy=ProxyPatch(
        hf_endpoint="https://mirror.example/hub/",
        github_mirror_url="https://mirror.example/github/",
    )))
    assert custom["HF_ENDPOINT"] == "https://mirror.example/hub"
    assert custom["GITHUB_MIRROR_URL"] == "https://mirror.example/github/"
    direct, _ = _patch_to_env(ConfigUpdateRequest(proxy=ProxyPatch(
        hf_endpoint="", github_mirror_url="",
    )))
    assert direct["HF_ENDPOINT"] == ""
    assert direct["GITHUB_MIRROR_URL"] == ""


@pytest.mark.parametrize("value", ["javascript:alert(1)", "https://user:secret@example.com", "https://example.com/?token=secret"])
def test_mirror_patch_rejects_invalid_url(value):
    with pytest.raises(BadRequestException):
        _patch_to_env(ConfigUpdateRequest(proxy=ProxyPatch(hf_endpoint=value)))


def test_hf_download_client_uses_dedicated_proxy(monkeypatch):
    import huggingface_hub

    factories = []
    options = []
    monkeypatch.setattr(huggingface_hub, "set_client_factory", factories.append)
    monkeypatch.setattr(
        http_client.config,
        "proxy",
        ProxyConfig(url="http://127.0.0.1:7890", enabled=True, hf_url="http://127.0.0.1:7891"),
    )
    monkeypatch.setattr(http_client.httpx, "Client", lambda **kwargs: options.append(kwargs))

    http_client.configure_hf_http_client()
    factories[-1]()
    assert options[-1]["proxy"] == "http://127.0.0.1:7891"
    assert options[-1]["trust_env"] is False
