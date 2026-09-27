"""Build the Vue UI when its generated files are missing or stale."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DIST = ROOT / "www-dist"
SOURCE_FILES = ("package.json", "pnpm-lock.yaml", "vite.config.js", "index.html")


def _source_mtime() -> float:
    paths = [FRONTEND / name for name in SOURCE_FILES]
    for directory in (FRONTEND / "src", FRONTEND / "public"):
        if directory.is_dir():
            paths.extend(path for path in directory.rglob("*") if path.is_file())
    return max((path.stat().st_mtime for path in paths if path.is_file()), default=0)


def ui_build_needed() -> bool:
    index = DIST / "index.html"
    return not index.is_file() or _source_mtime() > index.stat().st_mtime


def ensure_ui_build() -> None:
    """Install locked JS dependencies if needed, then build before serving UI."""
    if not ui_build_needed():
        logger.info("Vue 前端构建产物已是最新")
        return

    pnpm = shutil.which("pnpm")
    if not pnpm:
        raise RuntimeError("Vue 前端需要构建，但未找到 pnpm；请安装 Node.js 20+ 和 pnpm")

    commands = []
    if not (FRONTEND / "node_modules" / ".bin" / "vite").exists() and not (
        FRONTEND / "node_modules" / ".bin" / "vite.cmd"
    ).exists():
        commands.append([pnpm, "install", "--frozen-lockfile"])
    commands.append([pnpm, "build"])

    for command in commands:
        logger.info("Vue 前端: %s", " ".join(command[1:]))
        try:
            subprocess.run(command, cwd=FRONTEND, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"Vue 前端构建失败：{' '.join(command[1:])}") from exc

    if not (DIST / "index.html").is_file():
        raise RuntimeError("Vue 前端构建完成，但缺少 www-dist/index.html")
