#!/usr/bin/env python3
"""Compose one container-registry compatibility evidence record.

The official Docker workflow builds the module on an official ``nginx`` base
image that the release matrix pins by digest, and then runs
``packaging/scripts/verify-nginx-org-module.sh`` inside the built image.
This composer turns that ledger into a ``container-registry`` record.

The record names three immutable values:

- ``image_digest``: the pinned base image digest from the matrix row.
- ``built_image_id``: the content-addressed ID of the image that was
  verified.  The workflow loads the image locally and does not push it, so
  the image ID is its digest.
- ``module_sha256``: the digest of the module copied out of that image.

``image_reference`` keeps the matrix tag only as a label.  Coverage needs
both the tag and the digest to match the row, so a moved tag cannot stand in
for the pinned base.

Usage:
    python3 tools/release/matrix/record_container_evidence.py \
        --module-dir DIR \
        --ledger DIR/ledger.json \
        --nginx-version 1.26.3 --libc glibc --arch amd64 --distro debian12 \
        --image-reference nginx:1.26.3 --image-digest sha256:... \
        --built-image-id sha256:... \
        --out dist/compat-evidence/container-1.26.3-debian12-glibc-amd64.json \
        --source-sha SHA --verified-by ci:official-nginx-docker/build-and-verify \
        --workflow-run-url URL

Exit codes:
    0  Record written and contract-valid
    1  Missing input or contract violation
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(SCRIPT_DIR))
from lib.path_validation import (  # noqa: E402
    validate_read_path,
    validate_write_path_within_root,
)
from evidence_contract import validate_record  # noqa: E402
from record_nginx_org_evidence import (  # noqa: E402
    ComposeError,
    _read_ledger,
    _rust_version,
    _sha256_file,
    _test_ledger,
    _today,
)

# The source-build Dockerfile runs `make build`, whose release feature set is
# RUST_RELEASE_FEATURES (streaming).
FEATURES = ["streaming"]
DOCKERFILE = pathlib.Path("examples") / "docker" / "Dockerfile.official-nginx-source-build"
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DISTRO_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")


def _build_args_digest(
    repo_root: pathlib.Path,
    *,
    identity: tuple[str, str, str],
    distro: str,
    image: tuple[str, str],
    source_sha: str,
    rust_version: str,
) -> str:
    """Hash the image build inputs that are not files in the checkout."""
    version, libc, arch = identity
    reference, digest = image
    dockerfile = validate_read_path(
        repo_root / DOCKERFILE, purpose="official Docker source-build Dockerfile"
    )
    descriptor = {
        "arch": arch,
        "base_image": f"{reference}@{digest}",
        "builder": "official-nginx-docker/build-and-verify",
        "distro": distro,
        "dockerfile_sha256": _sha256_file(dockerfile),
        "features": list(FEATURES),
        "libc": libc,
        "nginx_version": version,
        "platform": f"linux/{arch}",
        "rust_version": rust_version,
        "source_sha": source_sha,
    }
    raw = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _check_inputs(
    distro: str, image: tuple[str, str], built_image_id: str, source_sha: str
) -> None:
    if not _DISTRO_RE.match(distro):
        raise ComposeError("distro must be a lowercase distro slug")
    if not _DIGEST_RE.match(image[1]):
        raise ComposeError("image digest must be sha256:<64 hex>")
    if not _DIGEST_RE.match(built_image_id):
        raise ComposeError("built image ID must be sha256:<64 hex>")
    if not _COMMIT_RE.match(source_sha):
        raise ComposeError("source SHA must be 40 lowercase hex characters")


def compose(
    *,
    module_dir: pathlib.Path,
    ledger_path: pathlib.Path,
    identity: tuple[str, str, str],
    distro: str,
    image: tuple[str, str],
    built_image_id: str,
    source_sha: str,
    verified_by: str,
    workflow_run_url: str,
    repo_root: pathlib.Path = REPO_ROOT,
) -> dict[str, Any]:
    """Return a contract-ready container record.

    ``identity`` is ``(nginx_version, libc, arch)`` and ``image`` is
    ``(image_reference, image_digest)``.
    """
    _check_inputs(distro, image, built_image_id, source_sha)
    version, libc, arch = identity
    reference, digest = image
    module_sha, ledger = _read_ledger(module_dir, ledger_path, identity)
    tests, counts = _test_ledger(ledger, module_sha)
    rust_version = _rust_version(repo_root)
    return {
        "schema_version": 1,
        "target_identity": (
            f"container-registry:{reference}@{digest}:{distro}:{libc}:{arch}"
        ),
        "source_kind": "container-registry",
        "nginx_version": version,
        "nginx_package_revision": version,
        "distro": distro,
        "distro_version": "n/a",
        "arch": arch,
        "libc": libc,
        "abi_provide": "",
        "source_reference": f"{reference}@{digest}",
        "source_digest": digest,
        "image_reference": reference,
        "image_digest": digest,
        "built_image_id": built_image_id,
        "build_args_digest": _build_args_digest(
            repo_root,
            identity=identity,
            distro=distro,
            image=image,
            source_sha=source_sha,
            rust_version=rust_version,
        ),
        "module_sha256": module_sha,
        "rust_version": rust_version,
        "features": list(FEATURES),
        "tests": tests,
        "pass": counts["PASS"],
        "fail": counts["FAIL"],
        "skip": counts["SKIP"],
        "workflow_run_url": workflow_run_url,
        "commit_sha": source_sha,
        "date": _today(),
        "verified_by": verified_by,
        "execution_environment": "native",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-dir", type=pathlib.Path, required=True)
    parser.add_argument("--ledger", type=pathlib.Path, required=True)
    parser.add_argument("--nginx-version", required=True)
    parser.add_argument("--libc", choices=("glibc", "musl"), required=True)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--distro", required=True)
    parser.add_argument("--image-reference", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--built-image-id", required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--verified-by", required=True)
    parser.add_argument("--workflow-run-url", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        record = compose(
            module_dir=args.module_dir,
            ledger_path=args.ledger,
            identity=(args.nginx_version, args.libc, args.arch),
            distro=args.distro,
            image=(args.image_reference, args.image_digest),
            built_image_id=args.built_image_id,
            source_sha=args.source_sha,
            verified_by=args.verified_by,
            workflow_run_url=args.workflow_run_url,
        )
    except (OSError, KeyError, ComposeError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    errors = validate_record(record)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    try:
        target = validate_write_path_within_root(args.out, root=REPO_ROOT)
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    # NOSONAR suppression for pythonsecurity:S2083: the path above passed
    # validate_write_path_within_root against the repository root, which
    # rejects a '..' component and any target that resolves outside the tree.
    target.write_text(  # NOSONAR
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
