"""HTTP 客户端与代理环境变量。"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlsplit

import httpx

from src.config import ProxyConfig, config

_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
)


def apply_proxy_env(proxy: ProxyConfig | None = None) -> None:
    """将代理写入进程环境，供 httpx / Hugging Face 等库使用。"""
    p = proxy if proxy is not None else config.proxy
    active = p.active_url
    if active:
        for key in _PROXY_ENV_KEYS:
            os.environ[key] = active
    else:
        for key in _PROXY_ENV_KEYS:
            os.environ.pop(key, None)
    if p.no_proxy:
        os.environ["NO_PROXY"] = p.no_proxy
        os.environ["no_proxy"] = p.no_proxy


def create_openai_http_client(timeout: float) -> httpx.Client:
    proxy = config.proxy.active_url or None
    return httpx.Client(proxy=proxy, timeout=timeout, trust_env=False)


def proxy_for_url(url: str, proxy: ProxyConfig | None = None) -> str:
    """按目标站点选代理，专用地址优先；空串表示直连。"""
    p = proxy if proxy is not None else config.proxy
    host = (urlsplit(url).hostname or "").lower()
    if host in {"huggingface.co", "hf.co"} or host.endswith(".huggingface.co"):
        return p.active_hf_url
    if host in {"github.com", "api.github.com", "raw.githubusercontent.com", "objects.githubusercontent.com"} or host.endswith(".githubusercontent.com"):
        return p.active_github_url
    return p.active_url


def configure_hf_http_client(*, use_proxy: bool = True) -> None:
    """为 huggingface_hub 设置下载代理；通用代理仍由环境变量处理。"""
    from huggingface_hub import set_client_factory
    from huggingface_hub.utils._http import default_client_factory, hf_request_event_hook

    proxy_url = config.proxy.active_hf_url if use_proxy else ""
    if not proxy_url:
        set_client_factory(default_client_factory)
        return

    def client_factory() -> httpx.Client:
        return httpx.Client(
            proxy=proxy_url,
            timeout=None,
            follow_redirects=True,
            trust_env=False,
            event_hooks={"request": [hf_request_event_hook]},
        )

    set_client_factory(client_factory)


def reset_hf_http_session() -> None:
    """丢弃 huggingface_hub 全局 httpx 客户端（避免 retry 时 Client closed）。"""
    try:
        from huggingface_hub.utils import _http

        _http.close_session()
    except Exception:
        pass


def is_proxy_or_conn_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    needles = (
        "10061",
        "积极拒绝",
        "connection refused",
        "actively refused",
        "client has been closed",
        "connecterror",
        "proxyerror",
        "failed to establish a new connection",
        "name or service not known",
        "nodename nor servname",
        "max retries exceeded",
    )
    return any(n in msg for n in needles)


def format_hf_download_error(exc: BaseException) -> str:
    """给设置页看的短错误；附带可操作提示。"""
    proxy = config.proxy.active_hf_url
    endpoint = (os.getenv("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")
    base = str(exc).strip() or exc.__class__.__name__
    if "10061" in base or "积极拒绝" in base or "connection refused" in base.lower():
        if proxy:
            return (
                f"无法连接下载源（当前代理 {proxy} 可能未启动）。"
                f"请检查代理软件，或在设置里修改相应代理地址后重试；"
                f"也可改 HF_ENDPOINT（现为 {endpoint}）。"
            )
        return (
            f"无法连接 {endpoint}。"
            "请检查网络，或设置可用的 HF_ENDPOINT / 系统代理后重试。"
        )
    if "client has been closed" in base.lower():
        return "下载会话已中断，请再点一次「拉取并加载模型」。"
    if len(base) > 240:
        base = base[:237] + "…"
    return base


@contextmanager
def without_process_proxy() -> Iterator[None]:
    """临时清掉代理环境变量，供 HF 直连镜像；退出后恢复项目代理设置。"""
    saved = {k: os.environ.get(k) for k in _PROXY_ENV_KEYS}
    try:
        for key in _PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        configure_hf_http_client(use_proxy=False)
        yield
    finally:
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        apply_proxy_env(config.proxy)
        configure_hf_http_client()
