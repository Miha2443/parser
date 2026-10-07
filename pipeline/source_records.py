"""Stable identity of a row in a specific immutable source file version."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_record_id(file_hash: str, sheet: str, row: int) -> str:
    if row < 1:
        raise ValueError("Excel source row must be one-based")
    value = json.dumps([file_hash, sheet, int(row)], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
