"""Startup UI build must detect stale output and use the locked frontend."""

import os
import pytest

from src import ui_build


def test_ui_build_runs_only_for_missing_or_stale_output(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    dist = tmp_path / "www-dist"
    (frontend / "src").mkdir(parents=True)
    (frontend / "node_modules" / ".bin").mkdir(parents=True)
    dist.mkdir()
    (frontend / "package.json").write_text("{}")
    (frontend / "pnpm-lock.yaml").write_text("lock")
    source = frontend / "src" / "App.vue"
    source.write_text("<template />")
    (frontend / "node_modules" / ".bin" / "vite").write_text("")
    index = dist / "index.html"
    monkeypatch.setattr(ui_build, "FRONTEND", frontend)
    monkeypatch.setattr(ui_build, "DIST", dist)
    monkeypatch.setattr(ui_build.shutil, "which", lambda name: "pnpm")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        index.write_text("built")

    monkeypatch.setattr(ui_build.subprocess, "run", run)
    ui_build.ensure_ui_build()
    assert [command[1] for command, _ in calls] == ["build"]
    assert calls[0][1]["cwd"] == frontend

    os.utime(index, (source.stat().st_mtime + 2, source.stat().st_mtime + 2))
    ui_build.ensure_ui_build()
    assert len(calls) == 1

    os.utime(source, (index.stat().st_mtime + 2, index.stat().st_mtime + 2))
    ui_build.ensure_ui_build()
    assert len(calls) == 2


def test_ui_build_reports_missing_pnpm(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_build, "DIST", tmp_path)
    monkeypatch.setattr(ui_build.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="pnpm"):
        ui_build.ensure_ui_build()
