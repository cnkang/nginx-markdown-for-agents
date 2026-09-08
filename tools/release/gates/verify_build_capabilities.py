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
from typing import Any


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
            else _load_report(args.capabilities)
        )
        if args.write is not None:
            args.write.parent.mkdir(parents=True, exist_ok=True)
            args.write.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
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
