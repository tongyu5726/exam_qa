"""Locate the LibreOffice executable shared by Word and PowerPoint conversion."""

import os
import shutil
from pathlib import Path


def _find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        if found := shutil.which(name):
            return found
    if os.name == "nt":
        for exe in ("soffice.com", "soffice.exe"):
            for pattern in (
                rf"C:\Program Files\LibreOffice\program\{exe}",
                rf"C:\Program Files (x86)\LibreOffice\program\{exe}",
            ):
                if Path(pattern).is_file():
                    return pattern
    return None
