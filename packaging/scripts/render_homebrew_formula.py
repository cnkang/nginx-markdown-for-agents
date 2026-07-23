#!/usr/bin/env python3
"""Render one Homebrew formula from a trusted source snapshot.

The rendered formula is written to stdout so the workflow owns the fixed
destination path. Only the class-level project ``url``, ``version``, and
``sha256`` stanzas are changed; nested resource identities are preserved.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from lib.path_validation import validate_read_path  # noqa: E402

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[\d]+\.[\d]+\.[\d]+$")


def render_formula(text: str, url: str, sha256: str, version: str) -> str:
    """Return formula text bound to one release archive identity.

    Raises:
        ValueError: If release values are malformed or the source does not
            contain exactly one class-level URL and SHA-256 stanza.
    """
    if not url.startswith("https://"):
        raise ValueError("formula URL must use HTTPS")
    if SHA256_RE.fullmatch(sha256) is None:
        raise ValueError("formula SHA-256 must be 64 lowercase hex characters")
    if VERSION_RE.fullmatch(version) is None:
        raise ValueError("formula version must use MAJOR.MINOR.PATCH")

    rendered: list[str] = []
    url_count = 0
    sha_count = 0
    version_count = 0

    for line in text.splitlines():
        if line.startswith("  url "):
            rendered.append(f'  url "{url}"')
            url_count += 1
        elif line.startswith("  version "):
            version_count += 1
        elif line.startswith("  sha256 "):
            rendered.extend((f'  version "{version}"', f'  sha256 "{sha256}"'))
            sha_count += 1
        else:
            rendered.append(line)

    if url_count != 1 or sha_count != 1:
        raise ValueError(
            "formula must contain exactly one class-level url and sha256 "
            f"stanza (url={url_count}, sha256={sha_count})"
        )
    if version_count > 1:
        raise ValueError(
            "formula must contain at most one class-level version stanza "
            f"(version={version_count})"
        )
    return "\n".join(rendered) + "\n"


def parse_args() -> argparse.Namespace:
    """Parse the renderer command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="trusted formula source")
    parser.add_argument("--url", required=True, help="exact archive URL")
    parser.add_argument("--sha256", required=True, help="archive SHA-256")
    parser.add_argument("--version", required=True, help="release version")
    return parser.parse_args()


def main() -> int:
    """Validate the source and emit the rendered formula to stdout."""
    args = parse_args()
    try:
        source = validate_read_path(args.source, purpose="Homebrew formula source")
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    try:
        rendered = render_formula(
            source.read_text(encoding="utf-8"),
            args.url,
            args.sha256,
            args.version,
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
