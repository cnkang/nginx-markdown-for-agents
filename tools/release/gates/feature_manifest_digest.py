"""Validate and hash the canonical release feature manifest."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# Imported as a module by the release workflow through ``python3 -`` heredocs,
# where sys.path[0] is the empty string and therefore the current directory.
# That happens to be the repository root today, but it is the caller's accident,
# not this module's contract: run the same import from a script, or from any
# directory but the root, and the repository root is absent from sys.path while
# Python has put this file's own directory there instead. Bootstrap it so the
# import resolves regardless of how the caller was launched.
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.lib.path_validation import validate_read_path  # noqa: E402


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
