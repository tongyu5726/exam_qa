"""GET/PATCH /api/v1/config — 设置页配置读写。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
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
    write_env_updates,
)
from src.services.settings_apply import build_settings_effects

router = APIRouter(prefix="/config", tags=["config"])


def _config_path_label() -> str:
    try:
        return str(ENV_PATH.relative_to(PROJECT_ROOT))
    except ValueError:
        return ".env"


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
        "retrieval": {
            "top_k": config.retrieval.top_k,
            "score_threshold": config.retrieval.score_threshold,
            "rerank_enabled": config.retrieval.rerank_enabled,
            "rerank_model": config.retrieval.rerank_model,
            "rerank_device": config.retrieval.rerank_device,
            "rerank_candidates": config.retrieval.rerank_candidates,
            "rerank_top_n": config.retrieval.rerank_top_n,
        },
        "chunk": {
            "chunk_size": config.chunk.chunk_size,
            "chunk_overlap": config.chunk.chunk_overlap,
        },
        "storage": {
            "knowledge_dir": config.storage.knowledge_dir,
        },
        "parsing": {
            "pdf_use_ocr": config.parsing.pdf_use_ocr,
            "pdf_force_ocr": config.parsing.pdf_force_ocr,
            "pdf_ocr_language": config.parsing.pdf_ocr_language,
            "pdf_parser": config.parsing.pdf_parser,
            "mineru_cmd": config.parsing.mineru_cmd,
            "mineru_timeout": config.parsing.mineru_timeout,
            "mineru_backend": config.parsing.mineru_backend,
            "mineru_effort": config.parsing.mineru_effort,
            "mineru_lang": config.parsing.mineru_lang,
            "mineru_formula": config.parsing.mineru_formula,
            "mineru_table": config.parsing.mineru_table,
            "mineru_image_analysis": config.parsing.mineru_image_analysis,
            "mineru_retry_high": config.parsing.mineru_retry_high,
            "pdf_quality_threshold": config.parsing.pdf_quality_threshold,
            "formula_recognition_enabled": config.parsing.formula_recognition_enabled,
            "formula_recognition_device": config.parsing.formula_recognition_device,
            "formula_recognition_model": config.parsing.formula_recognition_model,
            "formula_recognition_enable_mkldnn": (
                config.parsing.formula_recognition_enable_mkldnn
            ),
            "markpdfdown_enabled": config.parsing.markpdfdown_enabled,
            "markpdfdown_cmd": config.parsing.markpdfdown_cmd,
            "markpdfdown_args": config.parsing.markpdfdown_args,
            "markpdfdown_timeout": config.parsing.markpdfdown_timeout,
            "visual_model": config.parsing.visual_model,
            "visual_base_url": config.parsing.visual_base_url,
            "visual_timeout": config.parsing.visual_timeout,
            "visual_configured": bool(
                config.parsing.visual_model
                and (config.parsing.visual_api_key or config.llm.api_key)
            ),
        },
        "server": {
            "host": config.host,
            "port": config.port,
        },
        "app": {
            "max_upload_mb": config.max_upload_mb,
            "debug": config.debug,
            "log_level": config.log_level,
        },
        "proxy": {
            "url": config.proxy.url,
            "no_proxy": config.proxy.no_proxy,
            "enabled": config.proxy.enabled,
        },
        "meta": {
            "config_path": _config_path_label(),
            "env_writable": env_file_writable(),
        },
        "health": {
            "llm": "ok" if config.llm.api_key else "unavailable",
            "embedding": getattr(request.app.state, "embedding_health", "not_ready"),
        },
    }


def _patch_to_env(body: ConfigUpdateRequest) -> tuple[dict[str, str], list[str]]:
    updates: dict[str, str] = {}
    patched: list[str] = []

    if body.llm is not None:
        patched.append("llm")
        p = body.llm.model_dump(exclude_none=True)
        if "api_key" in p and p["api_key"] != MASKED_SECRET:
            updates["LLM_API_KEY"] = p["api_key"].strip()
        if "base_url" in p:
            updates["LLM_BASE_URL"] = p["base_url"].strip()
        if "model" in p:
            updates["LLM_MODEL"] = p["model"].strip()
        if "max_tokens" in p:
            updates["LLM_MAX_TOKENS"] = str(p["max_tokens"])
        if "timeout" in p:
            updates["LLM_TIMEOUT"] = str(p["timeout"])
        from src.services import llm_providers as llm_reg

        llm_reg.sync_active_fields_from_patch(
            model=p.get("model"),
            base_url=p.get("base_url"),
            api_key=p.get("api_key") if p.get("api_key") != MASKED_SECRET else None,
        )

    if body.embedding is not None:
        patched.append("embedding")
        p = body.embedding.model_dump(exclude_none=True)
        if "provider" in p:
            updates["EMBEDDING_PROVIDER"] = p["provider"]
        if "api_key" in p and p["api_key"] != MASKED_SECRET:
            updates["EMBEDDING_API_KEY"] = p["api_key"].strip()
        if "base_url" in p:
            updates["EMBEDDING_BASE_URL"] = p["base_url"].strip()
        if "model" in p:
            updates["EMBEDDING_MODEL"] = p["model"].strip()
        if "timeout" in p:
            updates["EMBEDDING_TIMEOUT"] = str(p["timeout"])

    if body.retrieval is not None:
        patched.append("retrieval")
        p = body.retrieval.model_dump(exclude_none=True)
        if "top_k" in p:
            updates["RETRIEVAL_TOP_K"] = str(p["top_k"])
        if "score_threshold" in p:
            updates["RETRIEVAL_SCORE_THRESHOLD"] = str(p["score_threshold"])
        if "rerank_enabled" in p:
            updates["RERANK_ENABLED"] = "true" if p["rerank_enabled"] else "false"
        if "rerank_model" in p:
            name = str(p["rerank_model"]).strip()
            if not name:
                raise BadRequestException("rerank_model 不能为空")
            updates["RERANK_MODEL"] = name
        if "rerank_candidates" in p:
            updates["RERANK_CANDIDATES"] = str(p["rerank_candidates"])
        if "rerank_top_n" in p:
            updates["RERANK_TOP_N"] = str(p["rerank_top_n"])

    if body.chunk is not None:
        patched.append("chunk")
        p = body.chunk.model_dump(exclude_none=True)
        size = p.get("chunk_size", config.chunk.chunk_size)
        overlap = p.get("chunk_overlap", config.chunk.chunk_overlap)
        if "chunk_size" in p:
            updates["CHUNK_SIZE"] = str(p["chunk_size"])
            size = p["chunk_size"]
        if "chunk_overlap" in p:
            overlap = p["chunk_overlap"]
            updates["CHUNK_OVERLAP"] = str(p["chunk_overlap"])
        if overlap >= size:
            raise BadRequestException("chunk_overlap 必须小于 chunk_size")

    if body.parsing is not None:
        patched.append("parsing")
        p = body.parsing.model_dump(exclude_none=True)
        if "pdf_use_ocr" in p:
            updates["PDF_USE_OCR"] = "true" if p["pdf_use_ocr"] else "false"
        if "pdf_force_ocr" in p:
            updates["PDF_FORCE_OCR"] = "true" if p["pdf_force_ocr"] else "false"
        if "pdf_ocr_language" in p:
            updates["PDF_OCR_LANGUAGE"] = p["pdf_ocr_language"].strip()
        if "pdf_parser" in p:
            parser = str(p["pdf_parser"]).strip().lower()
            if parser not in ("auto", "pymupdf", "mineru", "markpdfdown"):
                raise BadRequestException("pdf_parser 可选 auto / pymupdf / mineru / markpdfdown")
            updates["PDF_PARSER"] = parser
        if "mineru_cmd" in p:
            cmd = str(p["mineru_cmd"]).strip()
            if not cmd:
                raise BadRequestException("mineru_cmd 不能为空")
            updates["MINERU_CMD"] = cmd
        if "mineru_timeout" in p:
            if p["mineru_timeout"] < 0:
                raise BadRequestException("mineru_timeout 不能为负")
            updates["MINERU_TIMEOUT"] = str(p["mineru_timeout"])
        if "mineru_backend" in p:
            backend = str(p["mineru_backend"]).strip()
            if not backend:
                raise BadRequestException("mineru_backend 不能为空")
            updates["MINERU_BACKEND"] = backend
        if "mineru_effort" in p:
            updates["MINERU_EFFORT"] = p["mineru_effort"]
        if "mineru_lang" in p:
            lang = str(p["mineru_lang"]).strip()
            if not lang:
                raise BadRequestException("mineru_lang 不能为空")
            updates["MINERU_LANG"] = lang
        for field, env_name in (
            ("mineru_formula", "MINERU_FORMULA"),
            ("mineru_table", "MINERU_TABLE"),
            ("mineru_image_analysis", "MINERU_IMAGE_ANALYSIS"),
            ("mineru_retry_high", "MINERU_RETRY_HIGH"),
            ("markpdfdown_enabled", "MARKPDFDOWN_ENABLED"),
        ):
            if field in p:
                updates[env_name] = "true" if p[field] else "false"
        if "pdf_quality_threshold" in p:
            updates["PDF_QUALITY_THRESHOLD"] = str(p["pdf_quality_threshold"])
        if "formula_recognition_enabled" in p:
            updates["FORMULA_RECOGNITION_ENABLED"] = (
                "true" if p["formula_recognition_enabled"] else "false"
            )
        if "formula_recognition_device" in p:
            device = str(p["formula_recognition_device"]).strip().lower()
            if device not in ("auto", "cpu", "gpu"):
                raise BadRequestException("formula_recognition_device 仅支持 auto、cpu 或 gpu")
            updates["FORMULA_RECOGNITION_DEVICE"] = device
        if "formula_recognition_model" in p:
            model = str(p["formula_recognition_model"]).strip()
            if not model:
                raise BadRequestException("formula_recognition_model 不能为空")
            updates["FORMULA_RECOGNITION_MODEL"] = model
        if "formula_recognition_enable_mkldnn" in p:
            updates["FORMULA_RECOGNITION_ENABLE_MKLDNN"] = (
                "true" if p["formula_recognition_enable_mkldnn"] else "false"
            )
        if "markpdfdown_cmd" in p:
            updates["MARKPDFDOWN_CMD"] = str(p["markpdfdown_cmd"]).strip()
        if "markpdfdown_args" in p:
            args = str(p["markpdfdown_args"]).strip()
            if args and (
                "{input}" not in args
                or ("{output}" not in args and "{output_file}" not in args)
            ):
                raise BadRequestException(
                    "markpdfdown_args 必须包含 {input} 与 {output}/{output_file} 占位符"
                )
            updates["MARKPDFDOWN_ARGS"] = args
        if "markpdfdown_timeout" in p:
            updates["MARKPDFDOWN_TIMEOUT"] = str(p["markpdfdown_timeout"])
        if "visual_model" in p:
            updates["VISUAL_MODEL"] = p["visual_model"].strip()
        if "visual_base_url" in p:
            updates["VISUAL_BASE_URL"] = p["visual_base_url"].strip()
        if "visual_api_key" in p and p["visual_api_key"] != MASKED_SECRET:
            updates["VISUAL_API_KEY"] = p["visual_api_key"].strip()
        if "visual_timeout" in p:
            if p["visual_timeout"] < 0:
                raise BadRequestException("visual_timeout 不能为负")
            updates["VISUAL_TIMEOUT"] = str(p["visual_timeout"])

    if body.app is not None:
        patched.append("app")
        p = body.app.model_dump(exclude_none=True)
        if "max_upload_mb" in p:
            updates["MAX_UPLOAD_MB"] = str(p["max_upload_mb"])
        if "log_level" in p:
            updates["LOG_LEVEL"] = str(p["log_level"]).strip().upper()

    if body.server is not None:
        patched.append("server")
        p = body.server.model_dump(exclude_none=True)
        if "host" in p:
            host = p["host"].strip()
            if not host:
                raise BadRequestException("HOST 不能为空")
            updates["HOST"] = host
        if "port" in p:
            updates["PORT"] = str(p["port"])

    if body.proxy is not None:
        patched.append("proxy")
        p = body.proxy.model_dump(exclude_none=True)
        if "url" in p:
            updates["PROXY_URL"] = p["url"].strip()
        if "no_proxy" in p:
            updates["NO_PROXY"] = p["no_proxy"].strip()
        if "enabled" in p:
            updates["PROXY_ENABLED"] = "true" if p["enabled"] else "false"

    return updates, patched


@router.get("")
async def read_config(request: Request, _user=Depends(get_current_user)):
    return {"code": status.HTTP_200_OK, "data": _build_config_data(request)}


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

    write_env_updates(updates)
    reload_config()
    reload_services()

    request.app.state.llm_health = "ok" if config.llm.api_key else "unavailable"
    from src.services.embedding import get_embedding_client

    request.app.state.embedding_health = get_embedding_client().status()

    data = _build_config_data(request)
    data["settings_effects"] = build_settings_effects(patched)
    return {"code": status.HTTP_200_OK, "data": data}
