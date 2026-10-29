#!/usr/bin/env python3
"""Compose one nginx.org compatibility evidence record from a real build.

The composer does not trust the caller for anything the repository can
recompute.  It reads the module digest from the artifact, the source digest
from ``packaging/checksums.sha256``, the Rust version from
``rust-toolchain.toml``, and the source commit from ``git rev-parse HEAD``
unless the caller passes ``--source-sha`` (the release workflow passes
``github.sha``).

``build_args_digest`` binds the inputs that are not files in the checkout:
the builder Dockerfile, the container platform, the build kind, the static
Brotli mode, and the release identity values.  The digest is the SHA-256 of
their canonical JSON form, so it is reproducible on any host.

Usage:
    python3 tools/release/matrix/record_nginx_org_evidence.py \
        --build-dir dist/1.30.6-glibc-aarch64 \
        --ledger dist/1.30.6-glibc-aarch64/ledger.json \
        --nginx-version 1.30.6 --libc glibc --arch arm64 \
        --out docs/evidence/0.9.3/compat/nginx.org-1.30.6-glibc-arm64.json \
        [--source-sha SHA] [--verified-by local:maintainer] \
        [--workflow-run-url URL]

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
import subprocess
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

FEATURES = ["prune_noise_regions", "streaming"]
BUILD_KIND = "release"
BROTLI_MODE = "static-1.2.1"

_CHECKSUM_LINE_RE = re.compile(r"^([0-9a-f]{64})  (nginx-\d+\.\d+\.\d+)$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class ComposeError(RuntimeError):
    """Raised when a record cannot be composed from the build inputs."""


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_digest(repo_root: pathlib.Path, version: str) -> str:
    """Return the pinned SHA-256 of the nginx.org source tarball."""
    checksums = validate_read_path(
        repo_root / "packaging" / "checksums.sha256",
        purpose="nginx.org source checksums",
    )
    for line in checksums.read_text(encoding="utf-8").splitlines():
        match = _CHECKSUM_LINE_RE.match(line.strip())
        if match and match.group(2) == f"nginx-{version}":
            return f"sha256:{match.group(1)}"
    raise ComposeError(f"checksums.sha256 has no entry for nginx-{version}")


def _rust_version(repo_root: pathlib.Path) -> str:
    toolchain = validate_read_path(
        repo_root / "rust-toolchain.toml", purpose="pinned Rust toolchain"
    )
    match = re.search(
        r'^channel\s*=\s*"(\d+\.\d+\.\d+)"',
        toolchain.read_text(encoding="utf-8"),
        re.M,
    )
    if not match:
        raise ComposeError("rust-toolchain.toml has no pinned release channel")
    return match.group(1)


def _git_head(repo_root: pathlib.Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    sha = result.stdout.strip()
    if result.returncode != 0 or not _COMMIT_RE.match(sha):
        raise ComposeError("cannot resolve the source commit for the build")
    return sha


def _build_args_digest(
    repo_root: pathlib.Path,
    *,
    version: str,
    libc: str,
    arch: str,
    source_sha: str,
    rust_version: str,
) -> str:
    """Hash the build inputs that live outside the checkout."""
    dockerfile = validate_read_path(
        repo_root / "tools" / "build_release" / f"Dockerfile.{libc}",
        purpose="release builder Dockerfile",
    )
    manifest = validate_read_path(
        repo_root
        / "artifacts"
        / "release"
        / "0.9.3"
        / "official-build-feature-manifest.json",
        purpose="official feature manifest",
    )
    descriptor = {
        "arch": arch,
        "brotli_mode": BROTLI_MODE,
        "build_kind": BUILD_KIND,
        "dockerfile_sha256": _sha256_file(dockerfile),
        "feature_manifest_sha256": _sha256_file(manifest),
        "libc": libc,
        "nginx_version": version,
        "platform": f"linux/{arch}",
        "rust_version": rust_version,
        "source_sha": source_sha,
    }
    raw = json.dumps(descriptor, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compose(
    *,
    build_dir: pathlib.Path,
    ledger_path: pathlib.Path,
    version: str,
    libc: str,
    arch: str,
    verified_by: str,
    workflow_run_url: str,
    repo_root: pathlib.Path = REPO_ROOT,
    source_sha: str | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    """Return a contract-ready record for one nginx.org target."""
    module = validate_read_path(
        build_dir / "ngx_http_markdown_filter_module.so", purpose="release module"
    )
    ledger = json.loads(
        validate_read_path(ledger_path, purpose="verification ledger").read_text(
            encoding="utf-8"
        )
    )

    if ledger.get("nginx_version") != version:
        raise ComposeError("ledger was produced for a different NGINX version")
    if ledger.get("libc") != libc or ledger.get("arch") != arch:
        raise ComposeError("ledger was produced for a different libc or architecture")
    module_sha = _sha256_file(module)
    if ledger.get("module_sha256") != module_sha:
        raise ComposeError("ledger module digest does not match the built module")

    rust_version = _rust_version(repo_root)
    commit = source_sha if source_sha is not None else _git_head(repo_root)
    if not _COMMIT_RE.match(commit):
        raise ComposeError("source SHA must be 40 lowercase hex characters")

    return {
        "schema_version": 1,
        "target_identity": f"nginx.org:{version}:linux:{libc}:{arch}",
        "source_kind": "nginx.org",
        "nginx_version": version,
        "nginx_package_revision": version,
        "distro": "linux",
        "distro_version": "n/a",
        "arch": arch,
        "libc": libc,
        "abi_provide": "",
        "source_reference": f"https://nginx.org/download/nginx-{version}.tar.gz",
        "source_digest": _source_digest(repo_root, version),
        "build_args_digest": _build_args_digest(
            repo_root,
            version=version,
            libc=libc,
            arch=arch,
            source_sha=commit,
            rust_version=rust_version,
        ),
        "module_sha256": module_sha,
        "rust_version": rust_version,
        "features": list(FEATURES),
        "tests": ledger["tests"],
        "pass": ledger["pass"],
        "fail": ledger["fail"],
        "skip": ledger["skip"],
        "workflow_run_url": workflow_run_url,
        "commit_sha": commit,
        "date": date if date is not None else _today(),
        "verified_by": verified_by,
    }


def _today() -> str:
    import datetime

    return datetime.date.today().isoformat()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=pathlib.Path, required=True)
    parser.add_argument("--ledger", type=pathlib.Path, required=True)
    parser.add_argument("--nginx-version", required=True)
    parser.add_argument("--libc", choices=("glibc", "musl"), required=True)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--source-sha", default=None)
    parser.add_argument("--verified-by", default="local:release-agent")
    parser.add_argument("--workflow-run-url", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        record = compose(
            build_dir=args.build_dir,
            ledger_path=args.ledger,
            version=args.nginx_version,
            libc=args.libc,
            arch=args.arch,
            verified_by=args.verified_by,
            workflow_run_url=args.workflow_run_url,
            source_sha=args.source_sha,
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
    target.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
