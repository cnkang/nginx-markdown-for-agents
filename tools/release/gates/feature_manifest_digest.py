"""Validate and hash the canonical release feature manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tools.lib.path_validation import validate_read_path


CANONICAL_FEATURE_MANIFEST: dict[str, bool] = {
    "prune_noise_regions": True,
    "streaming": True,
}


def calculate_feature_manifest_digest(path: str | Path) -> str:
    """Validate one feature manifest and return its canonical SHA-256 digest."""
    try:
        manifest_path = validate_read_path(path, purpose="feature manifest")
        manifest: Any = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read feature manifest: {exc}") from exc

    if (
        not isinstance(manifest, dict)
        or set(manifest) != set(CANONICAL_FEATURE_MANIFEST)
        or any(
            type(manifest[key]) is not bool
            for key in CANONICAL_FEATURE_MANIFEST
        )
        or manifest != CANONICAL_FEATURE_MANIFEST
    ):
        raise ValueError(
            "official build feature manifest does not match the release "
            "feature contract"
        )

    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()
