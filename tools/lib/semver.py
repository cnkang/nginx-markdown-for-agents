"""One SemVer grammar for the release identity.

The release identity is SemVer 2.0.0 with an optional prerelease suffix, for
example ``1.0.0-rc.1``.  Build metadata is rejected: the package formats
cannot represent it, so a version that carries it would fail later in the
release chain instead of at the gate that reads it first.

Release-gate tools import this module so the grammar cannot drift between the
package pipeline, the schema-drift artifacts, the qualification records, and
the reason-code generator.
"""

from __future__ import annotations

import re

SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?$",
    re.ASCII,
)

# Schema artifacts follow the numeric release line, not the package identity.
# The line is shared by a prerelease candidate and its final release.
NUMERIC_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$", re.ASCII)


def is_release_version(value: object) -> bool:
    """Return whether ``value`` is a release identity this repository accepts."""
    return isinstance(value, str) and SEMVER_PATTERN.fullmatch(value) is not None


def is_numeric_line(value: object) -> bool:
    """Return whether ``value`` is a numeric release line (MAJOR.MINOR.PATCH)."""
    return isinstance(value, str) and NUMERIC_PATTERN.fullmatch(value) is not None


def release_core(value: str) -> str:
    """Return the MAJOR.MINOR.PATCH core of a release identity."""
    core = value.partition("-")[0]
    if not is_numeric_line(core):
        raise ValueError(f"release identity has no numeric core: {value!r}")
    return core
