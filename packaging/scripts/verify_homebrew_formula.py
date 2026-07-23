#!/usr/bin/env python3
"""Compare a release Formula with the trusted current-branch template.

Both Formula sources are rendered by the current trusted renderer using one
verified URL, SHA-256, and version. Only release identity fields may differ;
all executable Formula content must remain byte-for-byte equivalent.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from lib.path_validation import validate_read_path  # noqa: E402
from render_homebrew_formula import render_formula  # noqa: E402


def verify_formula_equivalence(
    trusted_text: str,
    candidate_text: str,
    *,
    url: str,
    sha256: str,
    version: str,
) -> None:
    """Raise ``ValueError`` unless sources differ only in release identity."""
    trusted = render_formula(trusted_text, url, sha256, version)
    candidate = render_formula(candidate_text, url, sha256, version)
    if candidate == trusted:
        return
    trusted_digest = hashlib.sha256(trusted.encode()).hexdigest()
    candidate_digest = hashlib.sha256(candidate.encode()).hexdigest()
    raise ValueError(
        "release Formula differs from the trusted current template outside "
        "url/version/sha256 fields "
        f"(trusted={trusted_digest}, candidate={candidate_digest})"
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trusted-template",
        required=True,
        help="current-branch canonical Formula",
    )
    parser.add_argument("--candidate", required=True, help="release Formula")
    parser.add_argument("--url", required=True, help="verified archive URL")
    parser.add_argument("--sha256", required=True, help="verified archive SHA-256")
    parser.add_argument("--version", required=True, help="verified release version")
    return parser.parse_args()


def main() -> int:
    """Read both Formulae and enforce normalized template equivalence."""
    args = parse_args()
    try:
        trusted = validate_read_path(
            args.trusted_template,
            purpose="trusted Homebrew Formula template",
        )
        candidate = validate_read_path(
            args.candidate,
            purpose="release Homebrew Formula",
        )
        verify_formula_equivalence(
            trusted.read_text(encoding="utf-8"),
            candidate.read_text(encoding="utf-8"),
            url=args.url,
            sha256=args.sha256,
            version=args.version,
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Homebrew Formula matches trusted current template", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
