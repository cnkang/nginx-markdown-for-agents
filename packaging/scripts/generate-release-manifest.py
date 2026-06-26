#!/usr/bin/env python3
"""generate-release-manifest.py — Generate release-manifest.json for a release.

Scans an artifact directory for .deb and .rpm files, parses metadata from
filenames, computes SHA-256 hashes, and produces a deterministic JSON manifest.

Usage:
    generate-release-manifest.py -d ARTIFACT_DIR -o OUTPUT [options]

Options:
    -d DIR       Directory containing .deb and .rpm artifacts (required)
    -o OUTPUT    Output file path (default: artifacts/release-manifest.json)
    --version V  Package version (overrides auto-detection from filename)
    --tag T      Git tag (e.g. v0.8.3).  Implies --version from tag.
    --commit C   Git commit SHA (overrides git rev-parse)
    --run-id R   GitHub Actions run ID
    --run-number N  GitHub Actions run number
    --ref R      GitHub ref (e.g. refs/tags/v0.8.3)
    --ref-type T GitHub ref type (tag or branch)
    --repo REPO  GitHub repository (owner/repo format)
    --source-url URL   Source archive URL
    --source-sha S     Source archive SHA-256 (omit to leave unavailable)
    --no-source        Mark source as unavailable (dispatch without tag)

Exit codes:
    0  Manifest generated successfully
    1  Error (missing directory, no artifacts, parse failure)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

DEB_PATTERN = re.compile(
    r"^nginx-module-markdown-for-agents_(?P<version>[^_]+)_nginx-(?P<nginx_version>[0-9][^-]*)_(?P<arch>[a-z0-9]+)\.deb$"
)
RPM_PATTERN = re.compile(
    r"^nginx-module-markdown-for-agents-(?P<version>[0-9][^-]*)-nginx(?P<nginx_version>[0-9][^-]*)-[0-9]+\.(?P<rpm_arch>x86_64|aarch64)\.rpm$"
)

ARCH_NORMALIZE = {"x86_64": "amd64", "aarch64": "arm64"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_package(path: Path, expected_version: str | None = None) -> dict:
    """Parse package metadata from filename."""
    name = path.name
    m = DEB_PATTERN.match(name)
    if m:
        version = m.group("version")
        nginx_version = m.group("nginx_version")
        arch = m.group("arch")
        fmt = "deb"
        entry = {
            "filename": name,
            "format": fmt,
            "version": version,
            "nginx_version": nginx_version,
            "arch": arch,
            "sha256": sha256_file(path),
        }
        if expected_version and version != expected_version:
            print(
                f"WARNING: {name} version {version} != expected {expected_version}",
                file=sys.stderr,
            )
        return entry

    m = RPM_PATTERN.match(name)
    if m:
        version = m.group("version")
        nginx_version = m.group("nginx_version")
        rpm_arch = m.group("rpm_arch")
        arch = ARCH_NORMALIZE.get(rpm_arch, rpm_arch)
        fmt = "rpm"
        entry = {
            "filename": name,
            "format": fmt,
            "version": version,
            "nginx_version": nginx_version,
            "arch": arch,
            "rpm_arch": rpm_arch,
            "sha256": sha256_file(path),
        }
        if expected_version and version != expected_version:
            print(
                f"WARNING: {name} version {version} != expected {expected_version}",
                file=sys.stderr,
            )
        return entry

    print(f"ERROR: Cannot parse package filename: {name}", file=sys.stderr)
    raise SystemExit(1)


def git_info(commit_override: str | None = None) -> dict:
    """Get git repository info."""
    try:
        repo_raw = (
            subprocess.check_output(
                ["git", "remote", "get-url", "origin"], stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
        # Extract owner/repo from URL
        m = re.search(r"[:/]([^/]+/[^/]+?)(?:\.git)?$", repo_raw)
        repository = m.group(1) if m else "unknown/unknown"
    except Exception:
        repository = "unknown/unknown"

    if commit_override:
        commit = commit_override
    else:
        try:
            commit = (
                subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
                )
                .decode()
                .strip()
            )
        except Exception:
            commit = "unknown"

    return {"repository": repository, "commit": commit}


def tag_from_ref(ref: str | None) -> str | None:
    """Extract tag name from git ref."""
    if ref and ref.startswith("refs/tags/"):
        return ref[len("refs/tags/"):]
    return None


def version_from_tag(tag: str | None) -> str | None:
    """Extract version from tag (strip leading 'v')."""
    if tag and tag.startswith("v"):
        return tag[1:]
    return tag


def source_archive_info(
    repo: str, tag: str | None, source_url: str | None, source_sha: str | None
) -> dict:
    """Build source archive section."""
    if source_url:
        info: dict = {"archive_url": source_url, "available": True}
        if source_sha:
            info["sha256"] = source_sha
        return info

    if tag:
        url = f"https://github.com/{repo}/archive/refs/tags/{tag}.tar.gz"
        info = {"archive_url": url, "available": True}
        if source_sha:
            info["sha256"] = source_sha
        return info

    return {"available": False}


def build_manifest(
    artifact_dir: Path,
    version: str | None,
    tag: str | None,
    commit: str | None,
    run_id: str | None,
    run_number: str | None,
    ref: str | None,
    ref_type: str | None,
    repo: str | None,
    source_url: str | None,
    source_sha: str | None,
    no_source: bool,
) -> dict:
    """Build the complete manifest."""
    # Discover packages
    deb_files = sorted(artifact_dir.glob("*.deb"))
    rpm_files = sorted(artifact_dir.glob("*.rpm"))
    all_files = deb_files + rpm_files

    if not all_files:
        print(
            f"ERROR: No .deb or .rpm files found in {artifact_dir}", file=sys.stderr
        )
        raise SystemExit(1)

    packages = []
    detected_version = version
    for f in all_files:
        entry = parse_package(f, expected_version=version)
        packages.append(entry)
        if not detected_version:
            detected_version = entry["version"]

    if not detected_version:
        print("ERROR: Could not determine package version", file=sys.stderr)
        raise SystemExit(1)

    # Git info
    gi = git_info(commit)
    repository = repo or gi["repository"]
    commit_sha = gi["commit"]

    # Tag / version
    if tag is None and ref:
        tag = tag_from_ref(ref)
    if version is None and tag:
        version = version_from_tag(tag)
    if version is None:
        version = detected_version
    if tag is None:
        tag = f"v{version}"

    # Source archive
    if no_source:
        source: dict = {"available": False}
    else:
        source = source_archive_info(repository, tag, source_url, source_sha)

    # Workflow info
    workflow: dict = {
        "provider": "github-actions",
        "workflow": "release-packages.yml",
    }
    if run_id:
        workflow["run_id"] = run_id
    if run_number:
        workflow["run_number"] = run_number
    if ref:
        workflow["ref"] = ref
    if ref_type:
        workflow["ref_type"] = ref_type

    manifest = {
        "schema_version": 1,
        "project": "nginx-markdown-for-agents",
        "version": version,
        "git": {
            "repository": repository,
            "tag": tag,
            "commit": commit_sha,
        },
        "source": source,
        "packages": packages,
        "integrity": {
            "checksums": "SHA256SUMS",
            "signature": "SHA256SUMS.asc",
            "signature_type": "gpg-detached-ascii-armored",
            "signed_file": "SHA256SUMS",
        },
        "workflow": workflow,
    }

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate release-manifest.json"
    )
    parser.add_argument("-d", "--artifact-dir", required=True, help="Artifact directory")
    parser.add_argument("-o", "--output", default="artifacts/release-manifest.json", help="Output path")
    parser.add_argument("--version", default=None, help="Package version")
    parser.add_argument("--tag", default=None, help="Git tag (e.g. v0.8.3)")
    parser.add_argument("--commit", default=None, help="Git commit SHA")
    parser.add_argument("--run-id", default=None, help="GitHub Actions run ID")
    parser.add_argument("--run-number", default=None, help="GitHub Actions run number")
    parser.add_argument("--ref", default=None, help="GitHub ref")
    parser.add_argument("--ref-type", default=None, help="GitHub ref type")
    parser.add_argument("--repo", default=None, help="GitHub repository (owner/repo)")
    parser.add_argument("--source-url", default=None, help="Source archive URL")
    parser.add_argument("--source-sha", default=None, help="Source archive SHA-256")
    parser.add_argument("--no-source", action="store_true", help="Mark source unavailable")
    args = parser.parse_args()

    artifact_dir = Path(args.artifact_dir)
    if not artifact_dir.is_dir():
        print(f"ERROR: Artifact directory not found: {artifact_dir}", file=sys.stderr)
        raise SystemExit(1)

    manifest = build_manifest(
        artifact_dir=artifact_dir,
        version=args.version,
        tag=args.tag,
        commit=args.commit,
        run_id=args.run_id,
        run_number=args.run_number,
        ref=args.ref,
        ref_type=args.ref_type,
        repo=args.repo,
        source_url=args.source_url,
        source_sha=args.source_sha,
        no_source=args.no_source,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    text = json.dumps(manifest, indent=2, sort_keys=False) + "\n"
    output_path.write_text(text, encoding="utf-8")

    print(f"Release manifest written to: {output_path}", file=sys.stderr)
    print(f"  version: {manifest['version']}")
    print(f"  packages: {len(manifest['packages'])}")
    print(f"  source available: {manifest['source'].get('available', False)}")


if __name__ == "__main__":
    main()
