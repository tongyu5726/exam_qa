"""Embedding 客户端：入库与检索共用，与 LLM 对话分离。"""

from __future__ import annotations

import logging
import threading
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

from src.config import EmbeddingConfig, LLMConfig, config
from src.services.inference_device import load_torch_model, run_torch_inference

logger = logging.getLogger(__name__)

_BATCH_SIZE = 64

# ponytail: 单槽进度；配置热更新用 generation 丢弃过期线程的写入
_progress_lock = threading.Lock()
_warmup_thread: threading.Thread | None = None
_warmup_gen = 0
_progress: dict[str, Any] = {
    "phase": "idle",
    "percent": None,
    "message": "",
    "error": None,
    "provider": "",
    "model": "",
}


def get_warmup_progress() -> dict[str, Any]:
    with _progress_lock:
        return dict(_progress)


def _set_progress(
    *,
    phase: str | None = None,
    percent: float | None | object = ...,
    message: str | None = None,
    error: str | None | object = ...,
    provider: str | None = None,
    model: str | None = None,
    gen: int | None = None,
) -> None:
    with _progress_lock:
        if gen is not None and gen != _warmup_gen:
            return
        if phase is not None:
            _progress["phase"] = phase
        if percent is not ...:
            _progress["percent"] = percent
        if message is not None:
            _progress["message"] = message
        if error is not ...:
            _progress["error"] = error
        if provider is not None:
            _progress["provider"] = provider
        if model is not None:
            _progress["model"] = model


def _mark_ready(client: EmbeddingClient, *, gen: int | None = None) -> None:
    _set_progress(
        phase="ok",
        percent=100,
        message="向量化已就绪",
        error=None,
        provider=client.provider,
        model=client.model,
        gen=gen,
    )


def _mark_error(client: EmbeddingClient, err: BaseException, *, gen: int | None = None) -> None:
    _set_progress(
        phase="error",
        percent=None,
        message="加载失败",
        error=str(err),
        provider=client.provider,
        model=client.model,
        gen=gen,
    )


def build_embedding_status(client: EmbeddingClient) -> dict[str, Any]:
    """对外状态：校正「模型已就绪但进度滞后」与「进度 ok 但客户端已重置」。"""
    warm = get_warmup_progress()
    ready = client.status()

    if ready == "ok":
        if warm.get("phase") != "ok":
            _mark_ready(client)
            warm = get_warmup_progress()
    elif warm.get("phase") == "ok":
        _set_progress(phase="idle", percent=None, message="", error=None)
        warm = get_warmup_progress()
        ready = "not_ready" if ready != "unavailable" else ready
    elif warm.get("phase") == "running":
        ready = "loading"
    elif warm.get("phase") == "error":
        ready = "not_ready" if ready != "unavailable" else ready

    return {
        "status": ready,
        "provider": client.provider,
        "model": client.model,
        "warmup": {
            "phase": warm.get("phase") or "idle",
            "percent": warm.get("percent"),
            "message": warm.get("message") or "",
            "error": warm.get("error"),
        },
    }


def _make_progress_tqdm(gen: int, base: type):
    class ProgressTqdm(base):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["disable"] = False
            super().__init__(*args, **kwargs)
            self._report()

        def update(self, n: float = 1):  # type: ignore[override]
            out = super().update(n)
            self._report()
            return out

        def _report(self) -> None:
            total = self.total or 0
            n = self.n or 0
            desc = (self.desc or "").strip() or "下载模型"
            if total > 0:
                pct = min(95.0, round(100.0 * n / total, 1))
                _set_progress(phase="running", percent=pct, message=desc, gen=gen)
            else:
                _set_progress(phase="running", percent=None, message=desc, gen=gen)

    return ProgressTqdm


def _patch_hub_tqdm(progress_cls: type):
    """huggingface_hub 把 tqdm 类挂在 utils 上，import utils.tqdm 易拿到类而非模块。"""
    import importlib

    import huggingface_hub.utils as hub_utils

    mod = importlib.import_module("huggingface_hub.utils.tqdm")
    old_mod = mod.tqdm
    old_utils = hub_utils.tqdm
    mod.tqdm = progress_cls
    hub_utils.tqdm = progress_cls
    return mod, hub_utils, old_mod, old_utils


def _load_local_with_progress(client: LocalEmbeddingClient, *, gen: int) -> None:
    import importlib

    mod = importlib.import_module("huggingface_hub.utils.tqdm")
    ProgressTqdm = _make_progress_tqdm(gen, base=mod.tqdm)
    mod, hub_utils, old_mod, old_utils = _patch_hub_tqdm(ProgressTqdm)
    try:
        _set_progress(
            phase="running",
            percent=None,
            message=f"拉取 {client.model}…",
            error=None,
            provider=client.provider,
            model=client.model,
            gen=gen,
        )
        client._load(gen=gen)
        _mark_ready(client, gen=gen)
    except Exception as e:
        logger.exception("本地 Embedding 加载失败")
        _mark_error(client, e, gen=gen)
        raise
    finally:
        mod.tqdm = old_mod
        hub_utils.tqdm = old_utils


def warmup_with_progress(client: EmbeddingClient, *, gen: int | None = None) -> dict[str, Any]:
    if gen is None:
        with _progress_lock:
            gen = _warmup_gen
    _set_progress(
        phase="running",
        percent=None,
        message="加载中…",
        error=None,
        provider=client.provider,
        model=client.model,
        gen=gen,
    )
    try:
        if isinstance(client, LocalEmbeddingClient):
            _load_local_with_progress(client, gen=gen)
        else:
            client.warmup()
            _mark_ready(client, gen=gen)
    except Exception as e:
        _mark_error(client, e, gen=gen)
        raise
    return get_warmup_progress()


def start_warmup_background(client: EmbeddingClient) -> dict[str, Any]:
    global _warmup_thread
    with _progress_lock:
        gen = _warmup_gen
        alive = _warmup_thread is not None and _warmup_thread.is_alive()
        same_target = (
            alive
            and _progress.get("provider") == client.provider
            and _progress.get("model") == client.model
        )
        if same_target:
            return dict(_progress)

        if client.status() == "ok":
            _progress.update(
                {
                    "phase": "ok",
                    "percent": 100,
                    "message": "向量化已就绪",
                    "error": None,
                    "provider": client.provider,
                    "model": client.model,
                }
            )
            return dict(_progress)

        def _run() -> None:
            try:
                warmup_with_progress(client, gen=gen)
            except Exception:
                pass

        _warmup_thread = threading.Thread(
            target=_run, name="embedding-warmup", daemon=True
        )
        _progress.update(
            {
                "phase": "running",
                "percent": None,
                "message": "开始拉取…" if client.provider == "local" else "探测中…",
                "error": None,
                "provider": client.provider,
                "model": client.model,
            }
        )
        _warmup_thread.start()
        return dict(_progress)


def clear_warmup_progress() -> None:
    """配置变更：作废进度，并提升 generation 使旧后台线程不再写进度。"""
    global _warmup_gen, _warmup_thread
    with _progress_lock:
        _warmup_gen += 1
        _warmup_thread = None
        _progress.update(
            {
                "phase": "idle",
                "percent": None,
                "message": "",
                "error": None,
                "provider": "",
                "model": "",
            }
        )


@runtime_checkable
class EmbeddingClient(Protocol):
    provider: str
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    def warmup(self) -> None:
        ...

    def status(self) -> str:
        """not_ready | ok | unavailable（不触发加载或网络请求）。"""
        ...


class LocalEmbeddingClient:
    provider = "local"

    def __init__(self, model_name: str, device: str = "auto"):
        self.model = model_name
        self.device = device
        self._model = None

    def _load(self, *, gen: int | None = None):
        if self._model is not None:
            return

        from sentence_transformers import SentenceTransformer

        from src.services.http_client import (
            format_hf_download_error,
            is_proxy_or_conn_error,
            reset_hf_http_session,
            without_process_proxy,
        )
        from src.config import config as app_config

        logger.info("加载本地 Embedding 模型: %s", self.model)
        _set_progress(
            phase="running",
            percent=None,
            message="装载到内存…",
            provider=self.provider,
            model=self.model,
            gen=gen,
        )

        from src.services.http_client import configure_hf_http_client

        configure_hf_http_client()

        def _fit() -> None:
            reset_hf_http_session()
            self._model = load_torch_model(SentenceTransformer, self.model, self.device)

        try:
            _fit()
        except Exception as first:
            # 代理未开 / 会话被关：直连再试一次（镜像站通常不需要本地 7890）
            if app_config.proxy.active_hf_url and is_proxy_or_conn_error(first):
                logger.warning(
                    "经代理拉取失败（%s），改为直连重试…",
                    first.__class__.__name__,
                )
                _set_progress(
                    phase="running",
                    percent=None,
                    message="代理失败，直连重试…",
                    provider=self.provider,
                    model=self.model,
                    gen=gen,
                )
                try:
                    with without_process_proxy():
                        _fit()
                except Exception as second:
                    raise RuntimeError(format_hf_download_error(second)) from second
            else:
                raise RuntimeError(format_hf_download_error(first)) from first

        logger.info("Embedding 维度: %d", self._model.get_embedding_dimension())

    def warmup(self) -> None:
        self._load()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._load()
        vectors = run_torch_inference(
            self._model,
            lambda: self._model.encode(texts, show_progress_bar=False, normalize_embeddings=True),
        )
        return [v.tolist() for v in vectors]

    def status(self) -> str:
        return "ok" if self._model is not None else "not_ready"

    def health_check(self) -> bool:
        return self.status() == "ok"


class OpenAIEmbeddingClient:
    provider = "openai"

    def __init__(self, api_key: str, base_url: str, model: str, timeout: int = 60):
        from openai import OpenAI

        self.model = model
        self._client = None
        self._warmed = False
        if api_key:
            from src.services.http_client import create_openai_http_client

            self._client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                max_retries=1,
                http_client=create_openai_http_client(timeout),
            )
            logger.info("Embedding API 已配置: %s @ %s", model, base_url)

    def warmup(self) -> None:
        if self._client:
            self.embed(["ping"])
            self._warmed = True

    def status(self) -> str:
        if self._client is None:
            return "unavailable"
        return "ok"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._client is None:
            raise RuntimeError("Embedding API 未配置：请设置 EMBEDDING_API_KEY 或 LLM_API_KEY")

        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            resp = self._client.embeddings.create(model=self.model, input=batch)
            ordered = sorted(resp.data, key=lambda item: item.index)
            out.extend(item.embedding for item in ordered)
        self._warmed = True
        return out

    def health_check(self) -> bool:
        return self.status() == "ok"


def create_embedding_client(
    emb_cfg: EmbeddingConfig,
    llm_cfg: LLMConfig,
) -> EmbeddingClient:
    provider = emb_cfg.provider.strip().lower()
    if provider == "openai":
        return OpenAIEmbeddingClient(
            api_key=emb_cfg.resolve_api_key(llm_cfg),
            base_url=emb_cfg.resolve_base_url(llm_cfg),
            model=emb_cfg.model,
            timeout=emb_cfg.timeout,
        )
    if provider == "local":
        return LocalEmbeddingClient(model_name=emb_cfg.model, device=emb_cfg.device)
    raise ValueError(f"未知 EMBEDDING_PROVIDER: {emb_cfg.provider!r}，可选 local / openai")


@lru_cache
def get_embedding_client() -> EmbeddingClient:
    return create_embedding_client(config.embedding, config.llm)


def reset_embedding_client() -> None:
    from src.services.retrieval import clear_query_embed_cache

    get_embedding_client.cache_clear()
    clear_query_embed_cache()
    clear_warmup_progress()
