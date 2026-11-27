"""Read and validate the release identity from the Cargo manifest."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from lib.semver import is_release_version  # noqa: E402

CARGO_MANIFEST = REPO_ROOT / "components/rust-converter/Cargo.toml"


def read_release_identity(manifest_path: Path = CARGO_MANIFEST) -> str:
    """Read a Cargo package version using the shared release identity rule."""
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot parse {manifest_path}: {exc}") from exc

    package = manifest.get("package")
    if not isinstance(package, dict):
        raise ValueError(f"missing [package] table in {manifest_path}")
    version = package.get("version")
    if not is_release_version(version):
        raise ValueError(f"invalid release identity in {manifest_path}")
    return version


def main() -> int:
    """Print the validated release identity for shell build callers."""
    try:
        print(read_release_identity())
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
