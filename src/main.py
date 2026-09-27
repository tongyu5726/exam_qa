"""FastAPI 应用入口。注册路由与全局异常处理。"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.config import config
from src.dependencies import (
    get_catalog_store,
    get_doc_store,
    get_llm_client,
    get_vector_store,
    get_vision_client,
)
from src.exceptions import AppException
from src.services.embedding import get_embedding_client
from src.services.env_store import env_was_created
from src.services.storage.catalog_store import (
    DEFAULT_COLLEGE_ID,
    DEFAULT_COURSE_ID,
    DEFAULT_COURSE_NAME,
    LEGACY_COURSE_IDS,
)
from src.utils.banner import StartupCheck, log_startup_banner
from src.utils.logging import get_uvicorn_log_config, setup_logging
from src.ui_build import ensure_ui_build

logger = logging.getLogger(__name__)

_WEB_ROOT = Path(__file__).resolve().parent.parent
_VUE_DIST_DIR = _WEB_ROOT / "www-dist"


def _attach_file_handler() -> None:
    log_path = Path(config.storage.log_path).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setLevel(getattr(logging, config.log_level, logging.INFO))
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(handler)


def _embedding_check() -> StartupCheck:
    emb = get_embedding_client()
    status = emb.status()
    provider = config.embedding.provider.strip().lower()
    model = config.embedding.model

    if status == "ok":
        label = "本地模型" if provider == "local" else "远程 API"
        return StartupCheck("Embedding", "ok", f"{label} · {model}")

    if status == "unavailable":
        if provider == "openai":
            return StartupCheck("Embedding", "warn", "未配置 API Key")
        return StartupCheck("Embedding", "warn", "未配置")

    if provider == "local":
        return StartupCheck("Embedding", "warn", f"{model} · 设置页手动加载")
    return StartupCheck("Embedding", "warn", f"{model} · 首次使用时连接")


@asynccontextmanager
async def lifespan(app: FastAPI):
    started = time.perf_counter()
    setup_logging(level=config.log_level)
    _attach_file_handler()
    ensure_ui_build()

    config.validate()
    from src.services.llm_providers import ensure_seeded_from_env

    ensure_seeded_from_env()
    vs = get_vector_store()
    ds = get_doc_store()
    catalog = get_catalog_store()
    llm = get_llm_client()

    for old_id in LEGACY_COURSE_IDS:
        n = vs.rebind_course_id(
            old_id,
            DEFAULT_COURSE_ID,
            course=DEFAULT_COURSE_NAME,
            college_id=DEFAULT_COLLEGE_ID,
        )
        if n:
            logger.info(
                "向量 course_id 迁移: %s -> %s (%d chunks)",
                old_id,
                DEFAULT_COURSE_ID,
                n,
            )

    chroma_ok = vs.health_check()
    sqlite_ok = ds.health_check()
    if not chroma_ok or not sqlite_ok:
        raise RuntimeError("存储层连通性校验失败，阻止启动")

    app.state.llm_health = "ok" if llm.configured else "unavailable"
    app.state.embedding_health = get_embedding_client().status()

    catalog.require_course(DEFAULT_COURSE_ID)

    if env_was_created():
        logger.info("已从 .env.example 创建 .env，请按需填写 LLM_API_KEY")

    ui_mounted = (_VUE_DIST_DIR / "index.html").is_file()
    storage_root = Path(config.storage.chroma_path).expanduser().parent

    checks = [
        StartupCheck("存储", "ok", str(storage_root)),
        StartupCheck(
            "ChromaDB",
            "ok" if chroma_ok else "error",
            "本地向量库" if chroma_ok else "不可用",
        ),
        StartupCheck(
            "SQLite",
            "ok" if sqlite_ok else "error",
            "本地元数据" if sqlite_ok else "不可用",
        ),
        _embedding_check(),
        StartupCheck(
            "LLM",
            "ok" if llm.configured else "warn",
            config.llm.model if llm.configured else "未配置 LLM_API_KEY",
        ),
        StartupCheck(
            "VLM",
            "ok" if get_vision_client().configured else "warn",
            config.parsing.visual_model or "未配置（图表仅保留图注）",
        ),
        StartupCheck(
            "Web UI",
            "ok",
            "www-dist/" if ui_mounted else "未构建",
        ),
    ]

    log_startup_banner(
        app_name="溯知",
        version="0.1.0",
        port=config.port,
        web_ui=f"http://{config.host}:{config.port}/sz/",
        web_mounted=ui_mounted,
        api_prefix=config.api_v1_prefix,
        checks=checks,
        elapsed=time.perf_counter() - started,
    )
    yield
    logger.info("服务已停止")


app = FastAPI(
    title="溯知",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _validation_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "参数校验失败"
    err = errors[0]
    loc = [str(x) for x in err.get("loc", []) if x not in ("body", "query", "path")]
    field = ".".join(loc) if loc else "请求体"
    return f"{field}: {err.get('msg', '参数无效')}"


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    resp: dict = {"code": 422, "message": _validation_message(exc)}
    if config.debug:
        resp["detail"] = exc.errors()
    return JSONResponse(status_code=422, content=resp)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    resp = {"code": exc.status_code, "message": exc.message}
    if config.debug and exc.detail:
        resp["detail"] = exc.detail
    return JSONResponse(status_code=exc.status_code, content=resp)


@app.exception_handler(Exception)
async def unexpected_exception_handler(request: Request, exc: Exception):
    logger.exception("未预期错误")
    resp = {"code": 500, "message": "服务器内部错误"}
    if config.debug:
        resp["detail"] = str(exc)
    return JSONResponse(status_code=500, content=resp)


from src.apis.router import api_router  # noqa: E402

app.include_router(api_router, prefix=config.api_v1_prefix)


@app.get("/")
async def root():
    return RedirectResponse(url="/sz/")


@app.get("/sz-docs/")
@app.get("/sz-docs")
async def legacy_documents_redirect():
    return RedirectResponse(url="/sz/#/documents")


@app.get("/sz-bank/")
@app.get("/sz-bank")
async def legacy_bank_redirect(request: Request):
    topic = request.query_params.get("topic", "")
    suffix = f"?topic={quote(topic, safe='')}" if topic else ""
    return RedirectResponse(url=f"/sz/#/question-bank{suffix}")


@app.get("/sz-cfg/")
@app.get("/sz-cfg")
async def legacy_settings_redirect():
    return RedirectResponse(url="/sz/#/settings")


# Mount before lifespan so first startup can build the directory after app import.
app.mount("/sz", StaticFiles(directory=str(_VUE_DIST_DIR), html=True, check_dir=False), name="sz")


def _bind_check_host(host: str) -> str:
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def _is_port_in_use(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        sock.close()


def main() -> None:
    """CLI 入口：uv run exam"""
    import sys

    import uvicorn

    setup_logging(level=config.log_level)
    _attach_file_handler()

    host = config.host
    port = config.port
    check_host = _bind_check_host(host)
    if _is_port_in_use(check_host, port):
        logger.error("端口 %s 已被占用或系统保留，无法绑定", port)
        logger.info("可尝试: PORT=%s uv run exam", port + 1)
        raise SystemExit(1)

    # ponytail: Windows 上 reload 子进程常导致 Ctrl+C 无法退出
    use_reload = config.debug and sys.platform != "win32"
    if config.debug and sys.platform == "win32":
        logger.warning("Windows 下已禁用热重载，避免 Ctrl+C 无法退出")

    uvi_config = uvicorn.Config(
        "src.main:app",
        host=host,
        port=port,
        reload=use_reload,
        log_config=get_uvicorn_log_config(level=config.log_level),
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(uvi_config)

    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        logger.info("服务已停止")


if __name__ == "__main__":
    main()
