"""Local private storage with generated keys and a replaceable storage boundary."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.core.config import settings

MAX_BYTES = 20 * 1024 * 1024
ALLOWED = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".zip": {"application/zip", "application/x-zip-compressed"},
    ".txt": {"text/plain"},
    ".csv": {"text/csv", "application/csv", "text/plain"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
}


def safe_filename(value: str) -> str:
    name = unicodedata.normalize("NFKC", value).strip()
    if not name or len(name) > 255 or "/" in name or "\\" in name or name in {".", ".."} or ".." in Path(name).parts:
        raise HTTPException(status_code=422, detail="Invalid filename.")
    if not re.fullmatch(r"[\w .()\-]+", name, flags=re.UNICODE):
        raise HTTPException(status_code=422, detail="Filename contains unsupported characters.")
    return name


def _check_signature(extension: str, content: bytes) -> None:
    signatures = {
        ".pdf": content.startswith(b"%PDF-"),
        ".zip": content.startswith(b"PK\x03\x04"),
        ".docx": content.startswith(b"PK\x03\x04"),
        ".pptx": content.startswith(b"PK\x03\x04"),
        ".xlsx": content.startswith(b"PK\x03\x04"),
        ".png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": content.startswith(b"\xff\xd8\xff"),
        ".jpeg": content.startswith(b"\xff\xd8\xff"),
        ".txt": b"\x00" not in content and _is_utf8(content),
        ".csv": b"\x00" not in content and _is_utf8(content),
    }
    if not signatures.get(extension, False):
        raise HTTPException(status_code=415, detail="File contents do not match the supported file type.")


def _is_utf8(content: bytes) -> bool:
    try:
        content.decode("utf-8-sig")
        return True
    except UnicodeDecodeError:
        return False


def save_private_file(filename: str, content_type: str, content: bytes) -> tuple[str, str, int, str]:
    safe = safe_filename(filename)
    if not content or len(content) > min(settings.PRIVATE_FILE_MAX_BYTES, MAX_BYTES):
        raise HTTPException(status_code=413, detail="File must be between 1 byte and 20 MiB.")
    extension = Path(safe).suffix.lower()
    if extension not in ALLOWED or content_type.lower().split(";")[0].strip() not in ALLOWED[extension]:
        raise HTTPException(status_code=415, detail="Unsupported file type.")
    _check_signature(extension, content)
    key = uuid4().hex
    root = Path(settings.PRIVATE_FILE_STORAGE_DIR).resolve()
    target = (root / key[:2] / key).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(status_code=422, detail="Invalid storage path.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(content)
    temporary.replace(target)
    return key, safe, len(content), hashlib.sha256(content).hexdigest()


def private_file_path(storage_key: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", storage_key):
        raise HTTPException(status_code=404, detail="File not found.")
    root = Path(settings.PRIVATE_FILE_STORAGE_DIR).resolve()
    target = (root / storage_key[:2] / storage_key).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return target
