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


def parse_sha256sums(path: Path, errors: list[str]) -> dict[str, str]:
    """Parse SHA256SUMS as 'digest  filename' records."""
    entries: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        errors.append(f"Cannot read SHA256SUMS: {e}")
        return entries

    for line_no, line in enumerate(lines, start=1):
        if not line:
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2:
            errors.append(f"SHA256SUMS line {line_no} has invalid format")
            continue
        digest, filename = parts
        if not re.match(r"^[0-9a-f]{64}$", digest):
            errors.append(
                f"SHA256SUMS line {line_no} digest is not a 64-char hex string"
            )
            continue
        if not filename:
            errors.append(f"SHA256SUMS line {line_no} filename is empty")
            continue
        if filename in entries:
            errors.append(f"Duplicate file in SHA256SUMS: {filename}")
            continue
        entries[filename] = digest

    return entries


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
    is_tag_release = (
        manifest.get("workflow", {}).get("ref_type") == "tag"
        or (isinstance(git, dict) and bool(git.get("tag")))
    )
    if not isinstance(git, dict):
        errors.append("git must be an object")
    else:
        for key in ("repository", "commit"):
            if key not in git:
                errors.append(f"Missing git.{key}")
            elif not git[key]:
                errors.append(f"git.{key} is empty")
            else:
                check_no_placeholders(git[key], f"git.{key}", errors)
        if "commit" in git and not re.match(r"^[0-9a-f]{7,40}$", git["commit"]):
            errors.append(f"git.commit does not look like a SHA: {git['commit']}")

        # git.tag is required for tag releases, optional for workflow_dispatch
        if is_tag_release:
            if "tag" not in git:
                errors.append("Missing git.tag (required for tag releases)")
            elif not git["tag"]:
                errors.append("git.tag is empty (required for tag releases)")
            else:
                check_no_placeholders(git["tag"], "git.tag", errors)

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

                # Reject path traversal — filename must resolve inside artifact_dir
                resolved_artifact = artifact_dir.resolve()
                try:
                    fpath = (resolved_artifact / fname).resolve()
                    fpath.relative_to(resolved_artifact)
                except (ValueError, RuntimeError):
                    errors.append(
                        f"{prefix}: filename escapes artifact directory: {fname}"
                    )
                    continue

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

    # source — required for tag releases, optional for workflow_dispatch
    source = manifest.get("source")
    if is_tag_release:
        if not source or not isinstance(source, dict):
            errors.append("source is required for tag releases")
        elif not source.get("available", False):
            errors.append("source.available must be true for tag releases")
        else:
            if "archive_url" not in source or not source["archive_url"]:
                errors.append("source.archive_url is required for tag releases")
            else:
                check_no_placeholders(source["archive_url"], "source.archive_url", errors)
            if "sha256" not in source or not source["sha256"]:
                errors.append("source.sha256 is required for tag releases")
            else:
                check_no_placeholders(source["sha256"], "source.sha256", errors)
                if not re.match(r"^[0-9a-f]{64}$", source["sha256"]):
                    errors.append("source.sha256 is not a 64-char hex string")
    elif source and isinstance(source, dict):
        # Non-tag: source is optional; if present and available, validate fields
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
        if is_tag_release:
            if integrity.get("signature") != "SHA256SUMS.asc":
                errors.append(f"integrity.signature expected SHA256SUMS.asc, got: {integrity.get('signature')}")
            if integrity.get("signature_available") is not True:
                errors.append("integrity.signature_available must be true for tag releases")
            if integrity.get("signature_type") != "gpg-detached-ascii-armored":
                errors.append(
                    "integrity.signature_type expected gpg-detached-ascii-armored, "
                    f"got: {integrity.get('signature_type')}"
                )
            if integrity.get("signed_file") != "SHA256SUMS":
                errors.append(f"integrity.signed_file expected SHA256SUMS, got: {integrity.get('signed_file')}")
        else:
            if integrity.get("signature") is not None:
                errors.append(
                    f"integrity.signature expected null for non-tag runs, got: {integrity.get('signature')}"
                )
            if integrity.get("signature_available") is not False:
                errors.append("integrity.signature_available must be false for non-tag runs")
            if integrity.get("signature_type") is not None:
                errors.append(
                    f"integrity.signature_type expected null for non-tag runs, got: {integrity.get('signature_type')}"
                )
            if integrity.get("signed_file") is not None:
                errors.append(
                    f"integrity.signed_file expected null for non-tag runs, got: {integrity.get('signed_file')}"
                )

    # SHA256SUMS inclusion and digest consistency
    if sha256sums_path and sha256sums_path.exists():
        sha256_entries = parse_sha256sums(sha256sums_path, errors)
        if "release-manifest.json" not in sha256_entries:
            errors.append("release-manifest.json not found in SHA256SUMS")

        manifest_artifact = artifact_dir.resolve() / "release-manifest.json"
        if "release-manifest.json" in sha256_entries:
            if not manifest_artifact.exists():
                errors.append(
                    "release-manifest.json listed in SHA256SUMS but not found in artifacts"
                )
            else:
                actual_manifest_sha = sha256_file(manifest_artifact)
                if sha256_entries["release-manifest.json"] != actual_manifest_sha:
                    errors.append(
                        "SHA256SUMS digest mismatch for release-manifest.json: "
                        f"sha256sums={sha256_entries['release-manifest.json']}, "
                        f"actual={actual_manifest_sha}"
                    )

        allowed_sha256_names = set(manifest_filenames)
        allowed_sha256_names.add("release-manifest.json")

        for pkg in packages:
            if "filename" not in pkg:
                continue
            fname = pkg["filename"]
            sums_sha = sha256_entries.get(fname)
            if sums_sha is None:
                errors.append(f"Package {fname} not found in SHA256SUMS")
                continue
            if "sha256" in pkg and sums_sha != pkg["sha256"]:
                errors.append(
                    f"SHA256SUMS digest mismatch for {fname}: "
                    f"sha256sums={sums_sha}, manifest={pkg['sha256']}"
                )

        for fname in sorted(sha256_entries):
            if fname not in allowed_sha256_names:
                errors.append(f"Unexpected file in SHA256SUMS: {fname}")
    elif sha256sums_path:
        errors.append(f"SHA256SUMS file not found: {sha256sums_path}")

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
