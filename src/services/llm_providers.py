"""LLM 模型注册表：JSON 持久化 + LLM_PROVIDER 选中。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.exceptions import AppException, BadRequestException, NotFoundException
from src.services.env_store import PROJECT_ROOT, write_env_updates

logger = logging.getLogger(__name__)

STORE_PATH = PROJECT_ROOT / "data" / "llm_providers.json"
# openai-compatible 写入时归一为 openai；对外只暴露规范值
CANONICAL_FORMATS = ("openai", "local")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _normalize(provider: dict[str, Any]) -> dict[str, str]:
    name = str(provider.get("name", "")).strip()
    fmt = str(provider.get("format", "openai")).strip().lower()
    if fmt == "openai-compatible":
        fmt = "openai"
    return {
        "name": name,
        "format": fmt,
        "api_key": str(provider.get("api_key", "")),
        "base_url": str(provider.get("base_url", "")).strip(),
        "model": str(provider.get("model", "")).strip(),
    }


def _validate(item: dict[str, str], *, require_key: bool = True) -> None:
    if not item["name"] or not _NAME_RE.match(item["name"]):
        raise BadRequestException(
            "name 须为字母数字开头，可含 ._- ，最长 64（如 deepseek、qwen-local）"
        )
    if item["format"] not in ("openai", "local"):
        raise BadRequestException("format 可选 openai / local（openai-compatible 等同 openai）")
    if not item["model"]:
        raise BadRequestException("model 不能为空")
    if item["format"] != "local" and require_key and not item["api_key"]:
        raise BadRequestException("openai 格式需填写 api_key")
    if item["format"] == "local" and not item["base_url"]:
        item["base_url"] = "http://127.0.0.1:11434/v1"


def load_providers() -> list[dict[str, str]]:
    if not STORE_PATH.is_file():
        return []
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [_normalize(p) for p in data if isinstance(p, dict) and p.get("name")]
    except (json.JSONDecodeError, OSError, KeyError) as e:
        logger.warning("读取 %s 失败: %s", STORE_PATH, e)
        return []


def save_providers(providers: list[dict[str, str]]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(
        json.dumps([_normalize(p) for p in providers], ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def public_info(p: dict[str, str], active: str) -> dict[str, Any]:
    return {
        "name": p["name"],
        "format": p["format"],
        "model": p["model"],
        "base_url": p["base_url"],
        "has_api_key": bool(p.get("api_key")),
        "active": p["name"] == active,
    }


def get_active_name() -> str:
    from src.config import config

    return (config.llm_provider or "").strip()


def ensure_seeded_from_env() -> None:
    """注册表为空且已有 LLM_API_KEY 时种子一条 default。"""
    from src.config import config

    if load_providers():
        return
    if not config.llm.api_key:
        return
    name = config.llm_provider.strip() or "default"
    item = _normalize(
        {
            "name": name,
            "format": "openai",
            "api_key": config.llm.api_key,
            "base_url": config.llm.base_url,
            "model": config.llm.model,
        }
    )
    save_providers([item])
    if not config.llm_provider:
        write_env_updates({"LLM_PROVIDER": name})
        from src.config import reload_config

        reload_config()
    logger.info("已从 .env 种子 LLM 模型: %s", name)


def list_public() -> dict[str, Any]:
    ensure_seeded_from_env()
    items = load_providers()
    active = get_active_name()
    if items and (not active or not any(p["name"] == active for p in items)):
        _sync_active_to_env(items[0])
        active = items[0]["name"]
    return {
        "items": [public_info(p, active) for p in items],
        "active": active,
        "formats": list(CANONICAL_FORMATS),
    }


def upsert_provider(payload: dict[str, Any], *, keep_key_if_blank: bool = True) -> dict[str, Any]:
    ensure_seeded_from_env()
    item = _normalize(payload)
    existing = {p["name"]: p for p in load_providers()}
    prev = existing.get(item["name"])
    if keep_key_if_blank and prev and not item["api_key"]:
        item["api_key"] = prev["api_key"]
    require_key = item["format"] != "local"
    _validate(item, require_key=require_key)
    existing[item["name"]] = item
    save_providers(list(existing.values()))
    active = get_active_name()
    if not active:
        set_active(item["name"])
        active = item["name"]
    elif active == item["name"]:
        _sync_active_to_env(item)
    return public_info(item, get_active_name())


def remove_provider(name: str) -> None:
    key = name.strip()
    providers = load_providers()
    kept = [p for p in providers if p["name"] != key]
    if len(kept) == len(providers):
        raise NotFoundException(f"模型未注册: {key}")
    save_providers(kept)
    if get_active_name() == key:
        if kept:
            set_active(kept[0]["name"])
        else:
            write_env_updates({"LLM_PROVIDER": ""})
            from src.config import reload_config
            from src.dependencies import reload_services

            reload_config()
            reload_services()


def set_active(name: str) -> dict[str, Any]:
    ensure_seeded_from_env()
    key = name.strip()
    providers = {p["name"]: p for p in load_providers()}
    if key not in providers:
        raise NotFoundException(f"模型未注册: {key}")
    item = providers[key]
    _sync_active_to_env(item)
    return list_public()


def _sync_active_to_env(item: dict[str, str]) -> None:
    from src.config import reload_config
    from src.dependencies import reload_services

    updates = {
        "LLM_PROVIDER": item["name"],
        "LLM_MODEL": item["model"],
        "LLM_BASE_URL": item["base_url"]
        or (
            "http://127.0.0.1:11434/v1"
            if item["format"] == "local"
            else "https://api.openai.com/v1"
        ),
    }
    if item["format"] == "local":
        updates["LLM_API_KEY"] = item["api_key"] or "local"
    elif item["api_key"]:
        updates["LLM_API_KEY"] = item["api_key"]
    write_env_updates(updates)
    reload_config()
    reload_services()
    logger.info("已切换 LLM: %s (%s)", item["name"], item["model"])


def sync_active_fields_from_patch(
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> None:
    """PATCH /config llm 时同步写回当前 active 注册项。"""
    ensure_seeded_from_env()
    active = get_active_name()
    providers = load_providers()
    if not active or not providers:
        return
    changed = False
    out: list[dict[str, str]] = []
    for p in providers:
        if p["name"] != active:
            out.append(p)
            continue
        if model is not None:
            p["model"] = model.strip()
            changed = True
        if base_url is not None:
            p["base_url"] = base_url.strip()
            changed = True
        if api_key is not None and api_key.strip():
            p["api_key"] = api_key.strip()
            changed = True
        out.append(p)
    if changed:
        save_providers(out)


def persist_config_updates(updates: dict[str, str], llm_patch: dict | None) -> None:
    """完成校验后的配置保存；普通写入异常时恢复两份文件的原内容。"""
    if llm_patch is None:
        write_env_updates(updates)
        return
    from src.services.env_store import ENV_PATH, MASKED_SECRET

    snapshots = {path: path.read_bytes() if path.exists() else None
                 for path in (ENV_PATH, STORE_PATH)}
    try:
        write_env_updates(updates)
        sync_active_fields_from_patch(
            model=llm_patch.get("model"), base_url=llm_patch.get("base_url"),
            api_key=llm_patch.get("api_key") if llm_patch.get("api_key") != MASKED_SECRET else None,
        )
    except Exception as exc:
        try:
            for path, content in snapshots.items():
                if content is None:
                    path.unlink(missing_ok=True)
                elif not path.exists() or path.read_bytes() != content:
                    path.write_bytes(content)
            from src.config import reload_config

            reload_config()
        except Exception as rollback_error:
            logger.error("配置保存后恢复失败", exc_info=True)
            raise AppException("配置保存及恢复失败，请检查文件权限与磁盘空间") from rollback_error
        if isinstance(exc, AppException):
            raise
        raise AppException("配置保存失败，已恢复原配置") from exc
