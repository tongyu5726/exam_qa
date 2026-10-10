"""GET/PATCH /api/v1/config — 设置页配置读写。"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request
from starlette import status

from src.config import config, reload_config
from src.dependencies import get_current_user, reload_services
from src.exceptions import BadRequestException
from src.models import ConfigUpdateRequest
from src.services.env_store import (
    ENV_PATH,
    MASKED_SECRET,
    PROJECT_ROOT,
    env_file_writable,
)
from src.services.settings_apply import build_settings_effects
from src.services.port_selection import port_available

router = APIRouter(prefix="/config", tags=["config"])


def _mirror_url(value: str, *, prefix: bool) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BadRequestException("镜像地址须为不含账号、查询参数的 HTTP(S) URL")
    return f"{value.rstrip('/')}/" if prefix else value.rstrip("/")


def _config_path_label() -> str:
    try:
        return str(ENV_PATH.relative_to(PROJECT_ROOT))
    except ValueError:
        return ".env"


def _public_fields(section: object, names: tuple[str, ...]) -> dict:
    """只读取显式公开字段，避免输出配置中的密钥或内部参数。"""
    return {name: getattr(section, name) for name in names}


def _build_config_data(request: Request) -> dict:
    from src.services import llm_providers as llm_reg

    emb = config.embedding
    llm = config.llm
    providers = llm_reg.list_public()
    return {
        "llm": {
            "active": providers.get("active") or config.llm_provider,
            "providers": providers.get("items") or [],
            "formats": providers.get("formats") or [],
            "model": llm.model,
            "base_url": llm.base_url,
            "max_tokens": llm.max_tokens,
            "timeout": llm.timeout,
            "configured": bool(llm.api_key),
        },
        "embedding": {
            "provider": emb.provider,
            "model": emb.model,
            "device": emb.device,
            "base_url": emb.resolve_base_url(llm),
            "timeout": emb.timeout,
            "configured": bool(emb.resolve_api_key(llm)),
            "uses_separate_credentials": bool(emb.api_key or emb.base_url),
        },
        "retrieval": _public_fields(config.retrieval, (
            "top_k", "score_threshold", "rerank_enabled",
            "rerank_model", "rerank_device", "rerank_candidates",
            "rerank_top_n",
        )),
        "chunk": _public_fields(config.chunk, (
            "chunk_size", "chunk_overlap",
        )),
        "storage": {
            "knowledge_dir": config.storage.knowledge_dir,
        },
        "parsing": {
            **_public_fields(config.parsing, (
                "pdf_use_ocr", "pdf_force_ocr", "pdf_ocr_language",
                "pdf_parser", "mineru_cmd", "mineru_timeout",
                "mineru_backend", "mineru_effort", "mineru_lang",
                "mineru_formula", "mineru_table", "mineru_image_analysis",
                "mineru_retry_high", "pdf_quality_threshold", "formula_recognition_enabled",
                "formula_recognition_device", "formula_recognition_model", "formula_recognition_enable_mkldnn",
                "markpdfdown_enabled", "markpdfdown_cmd", "markpdfdown_args",
                "markpdfdown_timeout", "visual_model", "visual_base_url",
                "visual_timeout",
            )),
            "visual_configured": bool(
                config.parsing.visual_model
                and (config.parsing.visual_api_key or config.llm.api_key)
            ),
        },
        "server": {
            "host": config.host,
            "port": config.port,
            "running_host": getattr(request.app.state, "bound_host", config.host),
            "running_port": getattr(request.app.state, "bound_port", config.port),
        },
        "app": _public_fields(config, (
            "max_upload_mb", "debug", "log_level",
        )),
        "proxy": _public_fields(config.proxy, (
            "url", "no_proxy", "enabled",
            "hf_url", "github_url", "hf_endpoint",
            "github_mirror_url",
        )),
        "meta": {
            "config_path": _config_path_label(),
            "env_writable": env_file_writable(),
        },
        "health": {
            "llm": "ok" if config.llm.api_key else "unavailable",
            "embedding": getattr(request.app.state, "embedding_health", "not_ready"),
        },
    }


# 普通字段的环境变量映射；特殊约束在转换阶段集中校验。
_ENV_FIELDS = {
    "llm": {"api_key": "LLM_API_KEY", "base_url": "LLM_BASE_URL", "model": "LLM_MODEL",
            "max_tokens": "LLM_MAX_TOKENS", "timeout": "LLM_TIMEOUT"},
    "embedding": {"provider": "EMBEDDING_PROVIDER", "api_key": "EMBEDDING_API_KEY",
                  "base_url": "EMBEDDING_BASE_URL", "model": "EMBEDDING_MODEL", "timeout": "EMBEDDING_TIMEOUT"},
    "retrieval": {"top_k": "RETRIEVAL_TOP_K", "score_threshold": "RETRIEVAL_SCORE_THRESHOLD",
                  "rerank_enabled": "RERANK_ENABLED", "rerank_model": "RERANK_MODEL",
                  "rerank_candidates": "RERANK_CANDIDATES", "rerank_top_n": "RERANK_TOP_N"},
    "chunk": {"chunk_size": "CHUNK_SIZE", "chunk_overlap": "CHUNK_OVERLAP"},
    "parsing": {field: field.upper() for field in (
        "pdf_use_ocr", "pdf_force_ocr", "pdf_ocr_language", "pdf_parser", "mineru_cmd",
        "mineru_timeout", "mineru_backend", "mineru_effort", "mineru_lang", "mineru_formula",
        "mineru_table", "mineru_image_analysis", "mineru_retry_high", "pdf_quality_threshold",
        "formula_recognition_enabled", "formula_recognition_device", "formula_recognition_model",
        "formula_recognition_enable_mkldnn", "markpdfdown_enabled", "markpdfdown_cmd",
        "markpdfdown_args", "markpdfdown_timeout", "visual_model", "visual_base_url",
        "visual_api_key", "visual_timeout",
    )},
    "app": {"max_upload_mb": "MAX_UPLOAD_MB", "log_level": "LOG_LEVEL"},
    "server": {"host": "HOST", "port": "PORT"},
    "proxy": {"url": "PROXY_URL", "no_proxy": "NO_PROXY", "enabled": "PROXY_ENABLED",
              "hf_url": "HF_PROXY_URL", "github_url": "GITHUB_PROXY_URL",
              "hf_endpoint": "HF_ENDPOINT", "github_mirror_url": "GITHUB_MIRROR_URL"},
}
_NONEMPTY_FIELDS = {"rerank_model", "mineru_cmd", "mineru_backend", "mineru_lang",
                    "formula_recognition_model", "host"}


def _patch_to_env(body: ConfigUpdateRequest) -> tuple[dict[str, str], list[str]]:
    """只校验和转换，不写文件或改动运行状态。"""
    updates: dict[str, str] = {}
    patched: list[str] = []
    for section, fields in _ENV_FIELDS.items():
        patch = getattr(body, section)
        if patch is None:
            continue
        patched.append(section)
        values = patch.model_dump(exclude_none=True)
        if section == "chunk":
            size = values.get("chunk_size", config.chunk.chunk_size)
            overlap = values.get("chunk_overlap", config.chunk.chunk_overlap)
            if overlap >= size:
                raise BadRequestException("chunk_overlap 必须小于 chunk_size")
        for field, value in values.items():
            if field.endswith("api_key") and value == MASKED_SECRET:
                continue
            serialized = str(value).strip() if not isinstance(value, bool) else str(value).lower()
            if field in _NONEMPTY_FIELDS and not serialized:
                label = "HOST" if field == "host" else field
                raise BadRequestException(f"{label} 不能为空")
            if field == "formula_recognition_device":
                serialized = serialized.lower()
                if serialized not in ("auto", "cpu", "gpu"):
                    raise BadRequestException("formula_recognition_device 仅支持 auto、cpu 或 gpu")
            elif field == "markpdfdown_args":
                if serialized and ("{input}" not in serialized or (
                    "{output}" not in serialized and "{output_file}" not in serialized
                )):
                    raise BadRequestException("markpdfdown_args 必须包含 {input} 与 {output}/{output_file} 占位符")
            elif field in ("hf_endpoint", "github_mirror_url"):
                serialized = _mirror_url(serialized, prefix=field == "github_mirror_url")
            elif field == "log_level":
                serialized = serialized.upper()
            updates[fields[field]] = serialized
    return updates, patched


@router.get("")
async def read_config(request: Request, _user=Depends(get_current_user)):
    return {"code": status.HTTP_200_OK, "data": _build_config_data(request)}


@router.get("/ports/check")
async def check_port(
    request: Request,
    port: int = Query(ge=1, le=65535),
    host: str | None = Query(default=None, max_length=255),
    _user=Depends(get_current_user),
):
    host = (host or config.host).strip()
    running_port = getattr(request.app.state, "bound_port", config.port)
    running_host = getattr(request.app.state, "bound_host", host)
    current = port == running_port and host == running_host
    available = current or port_available(host, port)
    return {"code": status.HTTP_200_OK, "data": {"port": port, "available": available, "current": current}}


@router.patch("")
async def patch_config(
    request: Request,
    body: ConfigUpdateRequest,
    _user=Depends(get_current_user),
):
    sections = [
        name
        for name in ("llm", "embedding", "retrieval", "chunk", "parsing", "app", "server", "proxy")
        if getattr(body, name) is not None
    ]
    if not sections:
        raise BadRequestException("请至少修改一项配置")

    updates, patched = _patch_to_env(body)
    if not updates:
        raise BadRequestException("没有可写入的配置项")

    if "PORT" in updates:
        port = int(updates["PORT"])
        running_port = getattr(request.app.state, "bound_port", config.port)
        running_host = getattr(request.app.state, "bound_host", config.host)
        target_host = updates.get("HOST", config.host)
        if (port != running_port or target_host != running_host) and not port_available(target_host, port):
            raise BadRequestException(f"端口 {port} 已被占用或系统保留")
    from src.services.llm_providers import persist_config_updates

    persist_config_updates(updates, body.llm.model_dump(exclude_none=True) if body.llm is not None else None)
    reload_config()
    reload_services()

    request.app.state.llm_health = "ok" if config.llm.api_key else "unavailable"
    from src.services.embedding import get_embedding_client

    request.app.state.embedding_health = get_embedding_client().status()

    data = _build_config_data(request)
    data["settings_effects"] = build_settings_effects(patched)
    if "HF_ENDPOINT" in updates:
        data["settings_effects"]["restart_required"].append("Hugging Face 下载镜像")
        data["settings_effects"]["notes"].append("重启服务后，新启动的 Hugging Face 模型下载会使用所选镜像。")
    return {"code": status.HTTP_200_OK, "data": data}
