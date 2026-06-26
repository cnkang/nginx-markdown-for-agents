#!/usr/bin/env python3
"""validate-release-manifest.py — Validate release-manifest.json.

Checks schema, package integrity, SHA256SUMS inclusion, and absence of
placeholder values.

Usage:
    validate-release-manifest.py -m MANIFEST -d ARTIFACT_DIR [--sha256sums SHA256SUMS] [--version VERSION]

Exit codes:
    0  All validations passed
    1  Validation failure
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

PLACEHOLDER_PATTERNS = [
    re.compile(r"PLACEHOLDER", re.IGNORECASE),
    re.compile(r"<[^>]+>"),  # <...> angle-bracket placeholders
    re.compile(r"TODO", re.IGNORECASE),
    re.compile(r"FIXME", re.IGNORECASE),
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_no_placeholders(value: str, field_path: str, errors: list[str]) -> None:
    """Check that a string value contains no placeholder patterns."""
    for pat in PLACEHOLDER_PATTERNS:
        if pat.search(value):
            errors.append(f"{field_path} contains placeholder: {value!r}")
            return


def validate_manifest(
    manifest_path: Path,
    artifact_dir: Path,
    sha256sums_path: Path | None,
    expected_version: str | None,
) -> list[str]:
    errors: list[str] = []

    # Load manifest
    try:
        text = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(text)
    except json.JSONDecodeError as e:
        return [f"Invalid JSON: {e}"]
    except Exception as e:
        return [f"Cannot read manifest: {e}"]

    # Basic schema checks
    if not isinstance(manifest, dict):
        return ["Manifest must be a JSON object"]

    for key in ("schema_version", "project", "version", "git", "packages", "integrity", "workflow"):
        if key not in manifest:
            errors.append(f"Missing required top-level key: {key}")

    if errors:
        return errors  # Can't continue without basic keys

    # schema_version
    if manifest["schema_version"] != 1:
        errors.append(f"Unexpected schema_version: {manifest['schema_version']}")

    # project
    if manifest["project"] != "nginx-markdown-for-agents":
        errors.append(f"Unexpected project: {manifest['project']}")

    # version
    version = manifest["version"]
    check_no_placeholders(version, "version", errors)
    if expected_version and version != expected_version:
        errors.append(f"Version mismatch: manifest={version}, expected={expected_version}")

    # git
    git = manifest["git"]
    if not isinstance(git, dict):
        errors.append("git must be an object")
    else:
        for key in ("repository", "tag", "commit"):
            if key not in git:
                errors.append(f"Missing git.{key}")
            elif not git[key]:
                errors.append(f"git.{key} is empty")
            else:
                check_no_placeholders(git[key], f"git.{key}", errors)
        if "commit" in git and not re.match(r"^[0-9a-f]{7,40}$", git["commit"]):
            errors.append(f"git.commit does not look like a SHA: {git['commit']}")

    # packages
    packages = manifest.get("packages", [])
    if not isinstance(packages, list) or len(packages) == 0:
        errors.append("packages must be a non-empty list")
    else:
        manifest_filenames = set()
        for i, pkg in enumerate(packages):
            prefix = f"packages[{i}]"
            for key in ("filename", "format", "version", "sha256"):
                if key not in pkg:
                    errors.append(f"{prefix}: missing {key}")

            if "filename" in pkg:
                fname = pkg["filename"]
                check_no_placeholders(fname, f"{prefix}.filename", errors)
                manifest_filenames.add(fname)

                # Check file exists and SHA matches
                fpath = artifact_dir / fname
                if not fpath.exists():
                    errors.append(f"{prefix}: file not found in artifacts: {fname}")
                elif "sha256" in pkg:
                    actual_sha = sha256_file(fpath)
                    if actual_sha != pkg["sha256"]:
                        errors.append(
                            f"{prefix}: SHA256 mismatch for {fname}: "
                            f"manifest={pkg['sha256']}, actual={actual_sha}"
                        )

            if "format" in pkg and pkg["format"] not in ("deb", "rpm"):
                errors.append(f"{prefix}: unexpected format: {pkg['format']}")

            if "sha256" in pkg:
                check_no_placeholders(pkg["sha256"], f"{prefix}.sha256", errors)
                if not re.match(r"^[0-9a-f]{64}$", pkg["sha256"]):
                    errors.append(f"{prefix}: sha256 is not a 64-char hex string")

    # source (optional but recommended for tag releases)
    source = manifest.get("source")
    if source and isinstance(source, dict):
        if source.get("available", False):
            if "archive_url" in source:
                check_no_placeholders(source["archive_url"], "source.archive_url", errors)
            if "sha256" in source:
                check_no_placeholders(source["sha256"], "source.sha256", errors)
                if not re.match(r"^[0-9a-f]{64}$", source["sha256"]):
                    errors.append("source.sha256 is not a 64-char hex string")

    # integrity
    integrity = manifest.get("integrity", {})
    if isinstance(integrity, dict):
        if integrity.get("checksums") != "SHA256SUMS":
            errors.append(f"integrity.checksums expected SHA256SUMS, got: {integrity.get('checksums')}")
        if integrity.get("signature") != "SHA256SUMS.asc":
            errors.append(f"integrity.signature expected SHA256SUMS.asc, got: {integrity.get('signature')}")
        if integrity.get("signed_file") != "SHA256SUMS":
            errors.append(f"integrity.signed_file expected SHA256SUMS, got: {integrity.get('signed_file')}")

    # SHA256SUMS inclusion
    if sha256sums_path and sha256sums_path.exists():
        sha256_text = sha256sums_path.read_text(encoding="utf-8")
        if "release-manifest.json" not in sha256_text:
            errors.append("release-manifest.json not found in SHA256SUMS")
        # Verify each package in manifest is in SHA256SUMS
        for pkg in packages:
            if "filename" in pkg and pkg["filename"] not in sha256_text:
                errors.append(f"Package {pkg['filename']} not found in SHA256SUMS")
    elif sha256sums_path:
        errors.append(f"SHA256SUMS file not found: {sha256sums_path}")

    # Check no extra files in SHA256SUMS that aren't packages or manifest
    if sha256sums_path and sha256sums_path.exists():
        sha256_text = sha256sums_path.read_text(encoding="utf-8")
        for line in sha256_text.strip().splitlines():
            parts = line.split("  ", 1)
            if len(parts) == 2:
                fname = parts[1]
                if not (fname.endswith(".deb") or fname.endswith(".rpm") or fname == "release-manifest.json"):
                    errors.append(f"Unexpected file in SHA256SUMS: {fname}")

    # Check packages are sorted deterministically
    if packages:
        filenames = [p.get("filename", "") for p in packages]
        if filenames != sorted(filenames):
            errors.append("Packages are not sorted by filename")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate release-manifest.json")
    parser.add_argument("-m", "--manifest", required=True, help="Path to release-manifest.json")
    parser.add_argument("-d", "--artifact-dir", required=True, help="Artifact directory")
    parser.add_argument("--sha256sums", default=None, help="Path to SHA256SUMS file")
    parser.add_argument("--version", default=None, help="Expected version")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    artifact_dir = Path(args.artifact_dir)
    sha256sums_path = Path(args.sha256sums) if args.sha256sums else None

    errors = validate_manifest(manifest_path, artifact_dir, sha256sums_path, args.version)

    if errors:
        print("VALIDATION FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        raise SystemExit(1)

    print("release-manifest.json: all validations passed ✓")


if __name__ == "__main__":
    main()
