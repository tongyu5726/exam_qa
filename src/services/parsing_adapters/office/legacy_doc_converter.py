from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from src.services.parsing_adapters.commands import _run_capture
from src.services.parsing_adapters.office.soffice import _find_soffice

logger = logging.getLogger(__name__)


def _convert_doc_to_docx_soffice(path: str) -> Path | None:
    soffice = _find_soffice()
    if not soffice:
        return None

    out_dir = Path(tempfile.mkdtemp(prefix="exam_doc_"))
    dest: Path | None = None
    try:
        proc, _stdout, stderr = _run_capture(
            [soffice, "--headless", "--convert-to", "docx", "--outdir", str(out_dir), path],
            timeout=120,
        )
        if proc.returncode != 0:
            logger.warning(
                "LibreOffice 退出码 %s: %s", proc.returncode, stderr.strip()[:300]
            )
            return None
        converted = out_dir / f"{Path(path).stem}.docx"
        if not converted.is_file():
            return None
        fd, dest_name = tempfile.mkstemp(suffix=".docx", prefix="exam_doc_")
        os.close(fd)
        dest = Path(dest_name)
        shutil.copy2(converted, dest)
        return dest
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("LibreOffice 转换失败: %s", e)
        if dest is not None:
            dest.unlink(missing_ok=True)
        return None
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def _convert_doc_to_docx_word(path: str) -> Path | None:
    """Windows：无 LibreOffice 时用本机 Word COM 另存为 .docx。"""
    if os.name != "nt":
        return None
    src = str(Path(path).resolve())
    fd, dest_name = tempfile.mkstemp(suffix=".docx", prefix="exam_doc_")
    os.close(fd)
    dest = Path(dest_name)
    dest.unlink(missing_ok=True)
    src_ps = src.replace("'", "''")
    dest_ps = str(dest.resolve()).replace("'", "''")
    ps = (
        "$ErrorActionPreference='Stop'; "
        "$word=New-Object -ComObject Word.Application; "
        "$word.Visible=$false; "
        f"$doc=$word.Documents.Open('{src_ps}'); "
        f"$doc.SaveAs([ref]'{dest_ps}',[ref]16); "
        "$doc.Close(); $word.Quit(); "
        "[System.Runtime.InteropServices.Marshal]::ReleaseComObject($word)|Out-Null"
    )
    try:
        proc, _stdout, stderr = _run_capture(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            timeout=120,
        )
        if proc.returncode != 0:
            logger.warning(
                "Word COM 退出码 %s: %s", proc.returncode, stderr.strip()[:300]
            )
            return None
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        dest.unlink(missing_ok=True)
        return None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("Word COM 转换失败: %s", e)
        dest.unlink(missing_ok=True)
        return None


def _convert_doc_to_docx(path: str) -> Path | None:
    return _convert_doc_to_docx_soffice(path) or _convert_doc_to_docx_word(path)
