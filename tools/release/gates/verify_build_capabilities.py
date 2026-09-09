#!/usr/bin/env python3
"""Fail-closed build/startup capability verification.

The pre-LTS contract promises two conversion engines and five retained
content encodings: identity, gzip, zlib-wrapped deflate, raw deflate, and
Brotli.  This gate accepts either a generated capability report or a source
tree and refuses to report full support when any promised capability is absent.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
from typing import Any

# Allow direct execution (tests invoke this script via subprocess without
# PYTHONPATH) while keeping the tools.lib import for the harness detector.
if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from tools.lib.path_validation import validate_write_path_within_root


EXPECTED_ENGINES = ("full_buffer", "streaming")
EXPECTED_ENCODINGS = (
    "identity",
    "gzip",
    "zlib_deflate",
    "raw_deflate",
    "brotli",
)


def _source_capabilities(root: pathlib.Path, features: str) -> dict[str, Any]:
    """Derive capabilities from the checked-out source, generated header, and
    the actual Rust feature set used for the build.

    The streaming engine is gated by the ``streaming`` Cargo feature
    (RUST_RELEASE_FEATURES); the full-buffer engine and the retained content
    encodings are unconditional parts of the Rust library, so their presence
    is verified against the source that the build compiles.

    The C module independently gates streaming through the
    ``NGX_MARKDOWN_RUST_FEATURES`` environment variable consumed by
    ``components/nginx-module/config`` (it adds
    ``-DMARKDOWN_STREAMING_ENABLED`` only when the feature list contains
    ``streaming``).  A gate that trusts only the requested ``--features``
    argument can certify a build whose C side was configured without
    streaming, so the environment variable is cross-checked here.
    """
    encoding = (root / "components/rust-converter/src/encoding.rs").read_text(
        encoding="utf-8"
    )
    decompress = (root / "components/rust-converter/src/decompress.rs").read_text(
        encoding="utf-8"
    )
    conversion = (
        root / "components/nginx-module/src/ngx_http_markdown_conversion_impl.h"
    ).read_text(encoding="utf-8")
    streaming = (
        root / "components/nginx-module/src/ngx_http_markdown_streaming_impl.h"
    ).read_text(encoding="utf-8")

    enabled_features = {f.strip() for f in features.split(",") if f.strip()}

    # Cross-check the C-side feature gate: components/nginx-module/config
    # maps NGX_MARKDOWN_RUST_FEATURES (default -> prune_noise_regions,
    # streaming; none -> empty) to -DMARKDOWN_STREAMING_ENABLED.  If the
    # environment pins a feature list that omits streaming while the Rust
    # build claims it, the compiled C module cannot actually stream.
    c_features_env = os.environ.get("NGX_MARKDOWN_RUST_FEATURES")
    c_streaming = True
    if c_features_env is not None:
        c_features = {
            f.strip() for f in c_features_env.split(",") if f.strip()
        } if c_features_env != "none" else set()
        c_streaming = "streaming" in c_features

    return {
        "engines": {
            "full_buffer": "ngx_http_markdown_fullbuffer_cleanup" in conversion,
            "streaming": "streaming" in enabled_features
            and "ngx_http_markdown_select_processing_path" in streaming
            and c_streaming,
        },
        "encodings": {
            "identity": "Identity" in encoding,
            "gzip": "Encoding::Gzip" in encoding and "Format::Gzip" in decompress,
            "zlib_deflate": "zlib-wrapped" in decompress,
            "raw_deflate": "raw RFC 1951" in decompress,
            "brotli": "Encoding::Br" in encoding and "Format::Brotli" in decompress,
        },
        "source_root": str(root),
        "features": features,
        "c_features_env": c_features_env,
    }


def _load_report(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read capability report {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("capability report must be a JSON object")
    return value


def _resolve_regular_file(path: pathlib.Path) -> pathlib.Path:
    """Resolve a CLI-supplied input path, rejecting symlinks and non-files.

    The gate runs in agentic CI contexts where CLI arguments may come from
    an untrusted caller; a resolved regular-file check prevents path
    injection through symlink or directory tricks.
    """
    if path.is_symlink():
        raise ValueError(f"refusing symlink path: {path}")
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"capability report is not a regular file: {path}")
    return resolved


def _allowed_write_roots() -> set[pathlib.Path]:
    """Return the resolved roots a write target may live under.

    The gate runs in agentic CI contexts where CLI arguments may come from
    an untrusted caller.  Write targets are confined to the repository root
    or the platform temporary locations (the E2E smoke scripts stage
    capability reports under the temp dir; on macOS /tmp is a symlink to
    /private/tmp, so the resolved aliases are included).
    """
    roots = {pathlib.Path.cwd().resolve()}
    roots.add(pathlib.Path(tempfile.gettempdir()).resolve())
    # The E2E smoke scripts stage capability reports under the platform
    # temp dir; on macOS /tmp is a symlink to /private/tmp, so include
    # the resolved aliases of the conventional temp locations.  These
    # roots are only allowlisted for the *containment check* below; the
    # actual write goes through validate_write_path_within_root after
    # symlink and regular-file rejection, so the public temp dirs are
    # never written directly.
    for alias in (os.environ.get("TMPDIR"), pathlib.Path(os.sep) / "tmp",  # NOSONAR S5443 (containment allowlist; write is re-validated)
                  pathlib.Path(os.sep) / "var" / "tmp"):
        if not alias:
            continue
        alias_root = pathlib.Path(alias).resolve()
        if alias_root.is_dir():
            roots.add(alias_root)
    return roots


def _write_root_for(path: pathlib.Path) -> pathlib.Path:
    """Return the allowed root that contains the write target.

    Rejects symlinks and non-regular-file targets before resolving the
    containing root, so validate_write_path_within_root only ever sees a
    concrete target inside an allowed tree.
    """
    if path.is_symlink():
        raise ValueError(f"refusing symlink path: {path}")
    target = path.resolve()
    if target.exists() and not target.is_file():
        raise ValueError(f"write target is not a regular file: {path}")
    for root in _allowed_write_roots():
        if target == root or target.is_relative_to(root):
            return root
    raise ValueError(f"write target escapes allowed roots: {path}")


def validate(report: dict[str, Any]) -> list[str]:
    """Return explicit missing-capability errors; an empty list means pass."""
    errors: list[str] = []
    for category, expected in (
        ("engines", EXPECTED_ENGINES),
        ("encodings", EXPECTED_ENCODINGS),
    ):
        values = report.get(category)
        if not isinstance(values, dict):
            errors.append(f"missing {category} capability map")
            continue
        for name in expected:
            if values.get(name) is not True:
                errors.append(f"missing promised {category[:-1]} capability: {name}")
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-root", type=pathlib.Path)
    source.add_argument("--capabilities", type=pathlib.Path)
    parser.add_argument(
        "--write",
        type=pathlib.Path,
        help="write the inspected capability report before validation",
    )
    parser.add_argument(
        "--features",
        default="streaming",
        help="comma-separated Rust feature set used for the build "
             "(default: streaming, matching RUST_RELEASE_FEATURES)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = (
            _source_capabilities(args.source_root.resolve(), args.features)
            if args.source_root is not None
            else _load_report(_resolve_regular_file(args.capabilities))
        )
        if args.write is not None:
            write_target = validate_write_path_within_root(
                args.write, _write_root_for(args.write))
            write_target.parent.mkdir(parents=True, exist_ok=True)
            with write_target.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(report, indent=2) + "\n")
        errors = validate(report)
    except (OSError, ValueError) as exc:
        print(f"CAPABILITY_CHECK_FAILED: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"CAPABILITY_CHECK_FAILED: {error}", file=sys.stderr)
        return 1
    print(
        "CAPABILITY_CHECK_PASSED: engines="
        + ",".join(EXPECTED_ENGINES)
        + " encodings="
        + ",".join(EXPECTED_ENCODINGS)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
