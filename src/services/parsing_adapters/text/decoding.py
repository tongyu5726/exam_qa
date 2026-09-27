"""Decode user-supplied text files with the existing encoding fallback."""

from pathlib import Path


def read_text(path: str) -> str:
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")
