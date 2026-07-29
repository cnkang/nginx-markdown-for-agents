"""Shared file-hashing helpers for release/performance tooling."""

from __future__ import annotations

import hashlib
from pathlib import Path

from lib.path_validation import validate_read_path


def sha256_file(path: Path | str) -> str:
    """Return the lowercase hex SHA-256 digest of *path* in streaming fashion.

    Raises ``OSError`` when the file cannot be read.
    """
    validated_path = validate_read_path(path, purpose="artifact")
    digest = hashlib.sha256()
    with validated_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
