#!/usr/bin/env python3
"""Cross-document consistency validator for packaging documentation.

Validates that the README, installation guide, release matrix, and example
configs are consistent with each other.  Checks performed:

  1.  Every verification curl in README Quick Start appears in the installation guide
  2.  All markdown_* directives used in the installation guide are known from
      the reference config (examples/nginx-configs/01-minimal-reverse-proxy.conf)
  3.  All verification curls in the installation guide use the
      ``-sD - -o /dev/null`` pattern
  4.  All release artifact name references match the canonical naming pattern
  5.  Every "full" tier entry in tools/release-matrix.json has a matching row
      in the installation guide compatibility matrix table
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
INSTALL_GUIDE = ROOT / "docs" / "guides" / "INSTALLATION.md"
RELEASE_MATRIX = ROOT / "tools" / "release-matrix.json"
REFERENCE_CONFIG = ROOT / "examples" / "nginx-configs" / "01-minimal-reverse-proxy.conf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_quick_start(text: str) -> str:
    """Return the text between ``## Quick Start`` and the next ``## ``."""
    lines = text.splitlines(True)
    collecting = False
    parts: list[str] = []
    for line in lines:
        if line.startswith("## Quick Start"):
            collecting = True
        elif collecting and line.startswith("## "):
            break
        if collecting:
            parts.append(line)
    return "".join(parts)


def _extract_verification_curls(text: str) -> list[str]:
    """Return normalised verification curl commands from *text*.

    A verification curl is any line containing ``curl`` **and** an
    ``-H "Accept: text/markdown"`` or ``-H "Accept: text/html"`` header
    (i.e. the content-negotiation verification commands, not download curls
    or metrics endpoint requests).
    """
    curls: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if "curl" not in stripped:
            continue
        if '-H "Accept: text/markdown"' not in stripped and \
           '-H "Accept: text/html"' not in stripped:
            continue
        # Normalise whitespace for comparison
        normalised = " ".join(stripped.split())
        curls.append(normalised)
    return curls


def _normalise_curl_for_comparison(cmd: str) -> str:
    """Normalise a curl command for structural comparison.

    Replaces only the scheme + host[:port] portion of URLs, preserving the
    path.  README and installation guide verification commands must use the
    same URL path per Requirement 8.1.
    """
    return re.sub(r"https?://[^/\s]+", "HOST", cmd)


def _extract_nginx_code_blocks(text: str) -> str:
    """Return the concatenated content of all ```nginx fenced code blocks."""
    blocks: list[str] = []
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```nginx") and not in_block:
            in_block = True
            continue
        if stripped.startswith("```") and in_block:
            in_block = False
            continue
        if in_block:
            blocks.append(line)
    return "\n".join(blocks)


# ---------------------------------------------------------------------------
# Check 1 — README Quick Start curls appear in installation guide
# ---------------------------------------------------------------------------

def check_curl_consistency() -> list[str]:
    """Every verification curl in README Quick Start must appear identically
    in the installation guide (Requirement 8.1)."""
    readme_text = _read(README)
    install_text = _read(INSTALL_GUIDE)

    quick_start = _extract_quick_start(readme_text)
    if not quick_start:
        return ["Cannot locate '## Quick Start' section in README"]

    readme_curls = _extract_verification_curls(quick_start)
    install_curls = _extract_verification_curls(install_text)

    if not readme_curls:
        return ["No verification curls found in README Quick Start"]

    install_set = {_normalise_curl_for_comparison(c) for c in install_curls}
    errors: list[str] = [
        f"README Quick Start curl not found in installation guide: {cmd}"
        for cmd in readme_curls
        if _normalise_curl_for_comparison(cmd) not in install_set
    ]
    return errors


# ---------------------------------------------------------------------------
# Check 2 — directive consistency with reference config
# ---------------------------------------------------------------------------

def check_directive_consistency() -> list[str]:
    """All ``markdown_*`` directives in the installation guide code blocks
    must be known directives from the reference config."""
    config_text = _read(REFERENCE_CONFIG)
    install_text = _read(INSTALL_GUIDE)

    # Extract known directives from reference config
    known = set(re.findall(r"\b(markdown_\w+)\b", config_text))
    if not known:
        return ["No markdown_* directives found in reference config"]

    # Scan installation guide nginx code blocks for markdown_* directives
    nginx_content = _extract_nginx_code_blocks(install_text)
    used = set(re.findall(r"\b(markdown_\w+)\b", nginx_content))

    errors: list[str] = [
        f"Installation guide uses unknown directive '{directive}' (not in reference config; known: {sorted(known)})"
        for directive in sorted(used)
        if directive not in known
    ]
    return errors


# ---------------------------------------------------------------------------
# Check 3 — verification curls use -sD - -o /dev/null pattern
# ---------------------------------------------------------------------------

def check_curl_pattern() -> list[str]:
    """All verification curls in the installation guide must use the
    ``-sD - -o /dev/null`` pattern."""
    install_text = _read(INSTALL_GUIDE)
    curls = _extract_verification_curls(install_text)

    errors: list[str] = [
        f"Verification curl missing '-sD - -o /dev/null' pattern: {cmd}"
        for cmd in curls
        if "-sD - -o /dev/null" not in cmd
    ]
    return errors


# ---------------------------------------------------------------------------
# Check 4 — artifact name pattern
# ---------------------------------------------------------------------------

ARTIFACT_RE = re.compile(
    r"ngx_http_markdown_filter_module-"
    r"\d+\.\d+\.\d+-(glibc|musl)-(x86_64|aarch64)\.tar\.gz"
)

def check_artifact_names() -> list[str]:
    """All artifact name references in the installation guide must match the
    canonical naming pattern."""
    install_text = _read(INSTALL_GUIDE)

    # Find all strings that look like artifact references (tar.gz only)
    candidates = re.findall(
        r"ngx_http_markdown_filter_module-[^\s\"'`<>)]+\.tar\.gz(?!\.)",
        install_text,
    )

    errors: list[str] = [
        f"Artifact name does not match expected pattern: {candidate}"
        for candidate in candidates
        if not ARTIFACT_RE.fullmatch(candidate)
    ]
    return errors


# ---------------------------------------------------------------------------
# Check 5 — release matrix "full" entries in compatibility table
# ---------------------------------------------------------------------------

def check_matrix_consistency() -> list[str]:
    """Every ``"full"`` tier entry in release-matrix.json must have a matching
    row in the installation guide compatibility matrix table."""
    errors: list[str] = []
    matrix_data = json.loads(_read(RELEASE_MATRIX))
    install_text = _read(INSTALL_GUIDE)

    # Extract the auto-generated matrix table
    m = re.search(
        r"<!-- BEGIN AUTO-GENERATED MATRIX -->(.*?)<!-- END AUTO-GENERATED MATRIX -->",
        install_text,
        re.DOTALL,
    )
    if not m:
        return ["Cannot locate auto-generated matrix markers in installation guide"]

    table_text = m[1]

    # Parse table rows (skip header and separator)
    table_rows: list[tuple[str, str, str]] = []
    for line in table_text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        # cells[0] and cells[-1] are empty from leading/trailing pipes
        cells = [c for c in cells if c]
        if len(cells) < 3:
            continue
        # Skip header / separator rows
        if cells[0] == "NGINX Version" or cells[0].startswith("---"):
            continue
        table_rows.append((cells[0], cells[1], cells[2]))

    # Check each "full" entry
    for entry in matrix_data.get("matrix", []):
        if entry.get("support_tier") != "full":
            continue
        nginx = entry["nginx"]
        os_type = entry["os_type"]
        arch = entry["arch"]
        found = any(
            r[0] == nginx and r[1] == os_type and r[2] == arch
            for r in table_rows
        )
        if not found:
            errors.append(
                f"Full-tier entry missing from compatibility table: "
                f"nginx={nginx} os_type={os_type} arch={arch}"
            )
    return errors


# ---------------------------------------------------------------------------
# Check 6 — README Quick Start internal consistency (config ↔ curl paths)
# ---------------------------------------------------------------------------

def _extract_curl_paths(text: str) -> set[str]:
    """Extract URL paths from verification curl commands in *text*."""
    paths: set[str] = set()
    for cmd in _extract_verification_curls(text):
        m = re.search(r"https?://[^/\s]+(\/\S*)", cmd)
        paths.add(m[1] if m else "/")
    return paths


def _extract_nginx_location_paths(text: str) -> set[str]:
    """Extract ``location`` paths from nginx code blocks in *text*."""
    nginx_content = _extract_nginx_code_blocks(text)
    return set(re.findall(r"\blocation\s+(\S+)\s*\{", nginx_content))


def check_readme_internal_consistency() -> list[str]:
    """Curl verification URLs in README Quick Start must target paths that
    are configured in the nginx blocks within the same section, or ``/``
    when no nginx config block is present (install-script auto-wired)."""
    readme_text = _read(README)
    quick_start = _extract_quick_start(readme_text)
    if not quick_start:
        return ["Cannot locate '## Quick Start' section in README"]

    curl_paths = _extract_curl_paths(quick_start)
    if not curl_paths:
        return []

    location_paths = _extract_nginx_location_paths(quick_start) or {"/"}

    errors: list[str] = []
    for path in sorted(curl_paths):
        # Normalise trailing slash for comparison
        normalised = path.rstrip("/") or "/"
        matched = any(
            (loc.rstrip("/") or "/") == normalised
            for loc in location_paths
        )
        if not matched:
            errors.append(
                f"README Quick Start curl targets '{path}' but no matching "
                f"location is configured (found: {sorted(location_paths)})"
            )
    return errors


# ---------------------------------------------------------------------------
# Check 7 — README/INSTALL verification curl host consistency
# ---------------------------------------------------------------------------

def _extract_curl_hosts(text: str) -> set[str]:
    """Extract host[:port] from verification curl URLs in *text*."""
    hosts: set[str] = set()
    for cmd in _extract_verification_curls(text):
        m = re.search(r"https?://([^/\s]+)", cmd)
        if m:
            hosts.add(m[1])
    return hosts


def check_curl_host_consistency() -> list[str]:
    """Verification curls in README Quick Start and installation guide must
    use the same host (e.g. both ``localhost``, not one ``127.0.0.1``)."""
    readme_text = _read(README)
    install_text = _read(INSTALL_GUIDE)

    quick_start = _extract_quick_start(readme_text)
    if not quick_start:
        return []

    readme_hosts = _extract_curl_hosts(quick_start)
    install_hosts = _extract_curl_hosts(install_text)

    if not readme_hosts or not install_hosts:
        return []

    extra = readme_hosts - install_hosts
    errors: list[str] = [
        f"README Quick Start uses host '{host}' not found in installation "
        f"guide verification curls (install guide uses: {sorted(install_hosts)})"
        for host in sorted(extra)
    ]
    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    missing: list[str] = []
    missing.extend(
        str(path.relative_to(ROOT))
        for path in (README, INSTALL_GUIDE, RELEASE_MATRIX, REFERENCE_CONFIG)
        if not path.exists()
    )
    if missing:
        print(f"ERROR: Required files not found: {', '.join(missing)}")
        return 1

    errors: list[str] = []

    checks = [
        ("Curl command consistency (README ↔ install guide)", check_curl_consistency()),
        ("Directive consistency (install guide ↔ reference config)", check_directive_consistency()),
        ("Verification curl pattern (-sD - -o /dev/null)", check_curl_pattern()),
        ("Artifact name pattern compliance", check_artifact_names()),
        ("Matrix consistency (release-matrix.json ↔ install guide)", check_matrix_consistency()),
        ("README Quick Start internal consistency (config ↔ curl paths)", check_readme_internal_consistency()),
        ("Curl host consistency (README ↔ install guide)", check_curl_host_consistency()),
    ]

    for _label, errs in checks:
        errors.extend(errs)

    if errors:
        print("Cross-document consistency checks FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("Cross-document consistency checks passed:")
    for label, _ in checks:
        print(f"  - {label}: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
