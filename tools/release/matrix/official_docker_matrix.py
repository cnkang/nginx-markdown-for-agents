#!/usr/bin/env python3
"""Resolve the release-blocking official NGINX Docker matrix."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from lib.path_validation import validate_read_path
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from lib.path_validation import validate_read_path

DOCKER_WORKFLOW = ".github/workflows/official-nginx-docker.yml"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SUPPORTED_DOCKER_OSES = {"alpine3.20", "alpine3.24", "debian12"}
SUPPORTED_DOCKER_ARCHES = {"amd64", "arm64"}
SUPPORTED_DOCKER_LIBC_BY_OS = {
    "alpine3.20": "musl",
    "alpine3.24": "musl",
    "debian12": "glibc",
}


def _canonical_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract the canonical release entries from a release matrix.
    
    Parameters:
    	data (dict[str, Any]): Release matrix data containing an `entries` list.
    
    Returns:
    	list[dict[str, Any]]: The validated release entries.
    
    Raises:
    	ValueError: If legacy aliases are present, `entries` is missing or empty, or any entry is not an object.
    """
    if "matrix" in data or "additional_artifacts" in data:
        raise ValueError(
            "release matrix must not contain legacy matrix aliases"
        )
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("release matrix entries must be a non-empty list")
    if not all(isinstance(entry, dict) for entry in entries):
        raise ValueError("release matrix entries must contain only objects")
    return entries


def _is_release_blocking_docker(entry: dict[str, Any]) -> bool:
    """
    Determine whether a matrix entry is a release-blocking official Docker image.
    
    Parameters:
        entry (dict[str, Any]): Matrix entry to evaluate.
    
    Returns:
        bool: `true` if the entry is an official release-blocking Docker image, `false` otherwise.
    
    Raises:
        ValueError: If an official release-blocking Docker entry does not use
            `support_tier="supported"`.
    """
    is_owned_release_blocking_docker = (
        entry.get("artifact_type") == "docker-image"
        and entry.get("release_blocking") is True
        and entry.get("owner_workflow") == DOCKER_WORKFLOW
    )
    if not is_owned_release_blocking_docker:
        return False
    if entry.get("support_tier") != "supported":
        raise ValueError(
            "release-blocking official Docker rows must use "
            "support_tier='supported': "
            f"{entry.get('nginx_version')}/{entry.get('os')}/"
            f"{entry.get('libc')}/{entry.get('arch')}"
        )
    return True


def _resolve_docker_entry(entry: dict[str, Any]) -> dict[str, str]:
    """
    Validate and normalize an official NGINX Docker matrix entry.
    
    Parameters:
        entry (dict[str, Any]): Docker matrix entry containing the NGINX version,
            operating system, libc, architecture, image reference, and image digest.
    
    Returns:
        dict[str, str]: Normalized entry with stable row identity and Docker tag.
    
    Raises:
        ValueError: If a required field, platform value, version, digest, or image
            reference is invalid or inconsistent.
    """
    version = entry.get("nginx_version")
    operating_system = entry.get("os")
    libc = entry.get("libc")
    arch = entry.get("arch")
    image_ref = entry.get("image_ref")
    image_digest = entry.get("image_digest")
    values = (version, operating_system, libc, arch, image_ref, image_digest)
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError(
            "every blocking Docker row must define version, os, libc, "
            "arch, image_ref, and image_digest"
        )
    if VERSION_RE.fullmatch(version) is None:
        raise ValueError(f"invalid Docker NGINX version: {version}")
    if operating_system not in SUPPORTED_DOCKER_OSES:
        raise ValueError(f"unsupported Docker operating system: {operating_system}")
    if libc not in {"glibc", "musl"}:
        raise ValueError(f"unsupported Docker libc: {libc}")
    if SUPPORTED_DOCKER_LIBC_BY_OS[operating_system] != libc:
        expected_libc = SUPPORTED_DOCKER_LIBC_BY_OS[operating_system]
        raise ValueError(
            f"unsupported Docker libc for {operating_system}: {libc}; "
            f"expected {expected_libc}"
        )
    if arch not in SUPPORTED_DOCKER_ARCHES:
        raise ValueError(f"unsupported Docker architecture: {arch}")
    if SHA256_RE.fullmatch(image_digest) is None:
        raise ValueError(f"invalid Docker image digest: {image_digest}")

    expected_suffix = f"-{operating_system}" if libc == "musl" else ""
    expected_ref = f"nginx:{version}{expected_suffix}"
    if image_ref != expected_ref:
        raise ValueError(
            f"Docker image_ref {image_ref!r} does not match row "
            f"{version}/{libc}; expected {expected_ref!r}"
        )

    return {
        "matrix_row_id": f"{version}/{operating_system}/{libc}/{arch}",
        "docker_tag": f"{version}-{operating_system}-{libc}-{arch}",
        "nginx_version": version,
        "os": operating_system,
        "libc": libc,
        "arch": arch,
        "image_ref": image_ref,
        "image_digest": image_digest,
    }


def _docker_sort_key(row: dict[str, str]) -> tuple[tuple[int, ...], str, str, str]:
    """Build a deterministic sort key for a normalized Docker matrix row."""
    return (
        tuple(int(part) for part in row["nginx_version"].split(".")),
        row["os"],
        row["libc"],
        row["arch"],
    )


def resolve_official_docker_entries(data: dict[str, Any]) -> list[dict[str, str]]:
    """
    Resolve supported, release-blocking official NGINX Docker entries from the release matrix.
    
    Parameters:
        data (dict[str, Any]): Release matrix data containing canonical Docker entries.
    
    Returns:
        list[dict[str, str]]: Normalized Docker entries sorted by NGINX version, OS, libc, and architecture.
    
    Raises:
        ValueError: If the matrix is invalid, contains no qualifying entries, or contains duplicate row identities.
    """
    entries = _canonical_entries(data)

    resolved: list[dict[str, str]] = []
    for entry in entries:
        if not _is_release_blocking_docker(entry):
            continue
        resolved.append(_resolve_docker_entry(entry))

    resolved.sort(key=_docker_sort_key)
    if not resolved:
        raise ValueError("no blocking official Docker rows were found")
    row_ids = [row["matrix_row_id"] for row in resolved]
    if len(set(row_ids)) != len(row_ids):
        raise ValueError("blocking Docker row identities must be unique")
    return resolved


def load_official_docker_entries(matrix_path: Path) -> list[dict[str, str]]:
    """
    Load and resolve the official Docker matrix from a JSON file.
    
    Parameters:
        matrix_path (Path): Path to the release matrix JSON file.
    
    Returns:
        list[dict[str, str]]: Resolved, validated official Docker matrix entries.
    
    Raises:
        ValueError: If the JSON root is not an object.
    """
    validated_path = validate_read_path(
        matrix_path, purpose="official Docker release matrix"
    )
    with validated_path.open(encoding="utf-8") as matrix_file:
        data = json.load(matrix_file)
    if not isinstance(data, dict):
        raise ValueError("release matrix root must be an object")
    return resolve_official_docker_entries(data)
