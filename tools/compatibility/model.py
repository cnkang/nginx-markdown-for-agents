"""Deterministic contract manifest model: canonical JSON and digests.

The manifest is the machine-readable record of one source revision's public
contract.  Digest stability matters more than readability here: a manifest is
compared byte-for-byte across checkouts, so the serialization has no
timestamps, no absolute paths, and no environment-dependent ordering.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List

MANIFEST_SCHEMA = 1
INDEX_SCHEMA = 1

SURFACES = (
    "directives",
    "metrics",
    "reasons",
    "diagnostics",
    "http",
    "packaging",
    "ffi",
)

# Role labels recorded in the anchor index.  The role is documentation only;
# comparisons never branch on it, because a role change must not silently
# reclassify a breaking change.
ROLE_BREAKING_FREEZE = "breaking-freeze"
ROLE_MAINTENANCE_BASELINE = "maintenance-baseline"


def canonical_json(value: Any) -> str:
    """Return the single canonical serialization used for every digest."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest_of(value: Any) -> str:
    """Return ``sha256:<hex>`` over the canonical serialization of ``value``."""
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def digest_of_bytes(payload: bytes) -> str:
    """Return ``sha256:<hex>`` over raw bytes."""
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def surface_digests(surfaces: Dict[str, Any]) -> Dict[str, str]:
    """Digest each surface separately so a report can name the failing one."""
    return {name: digest_of(surfaces[name]) for name in SURFACES if name in surfaces}


def contract_digest(surfaces: Dict[str, Any]) -> str:
    """Digest the whole contract surface set."""
    return digest_of({name: surfaces[name] for name in SURFACES if name in surfaces})


def dump_manifest(manifest: Dict[str, Any]) -> str:
    """Serialize a manifest for the checked-in JSON file."""
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def sorted_by(items: Iterable[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    """Sort contract entries by a stable identity field."""
    return sorted(items, key=lambda item: item[key])


def require(condition: bool, message: str) -> None:
    """Raise ``ContractError`` when a contract invariant does not hold."""
    if not condition:
        raise ContractError(message)


class ContractError(RuntimeError):
    """A contract input is missing, malformed, or internally inconsistent."""
