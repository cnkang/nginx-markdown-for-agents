#!/usr/bin/env python3
"""Keep per-path metrics fields synchronized across production and test stubs."""

from __future__ import print_function

import os
import sys


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from lib.path_validation import validate_read_path  # noqa: E402 - direct script import


PRODUCTION = os.path.join(
    ROOT, "components", "nginx-module", "src", "ngx_http_markdown_metrics_impl.h"
)
STUBS = (
    os.path.join(
        ROOT,
        "components",
        "nginx-module",
        "tests",
        "unit",
        "metrics_bounded_rendering_test.c",
    ),
    os.path.join(
        ROOT,
        "components",
        "nginx-module",
        "tests",
        "unit",
        "prometheus_per_path_test.c",
    ),
    os.path.join(
        ROOT,
        "components",
        "nginx-module",
        "tests",
        "unit",
        "prometheus_renderer_test.c",
    ),
)
FIELDS = (
    "path_entries",
    "path_conversions",
    "path_conversion_time_sum_ms",
    "overflow_count",
    "unretained_conversions",
    "unretained_conversion_time_sum_ms",
)


def read_text(path):
    # Validate every detector input before opening it, including fixed paths,
    # so the harness itself satisfies the repository path-safety contract.
    validated_path = validate_read_path(path, purpose="metrics stub drift")
    return validated_path.read_text(encoding="utf-8")


def main():
    production = read_text(PRODUCTION)
    missing = [field for field in FIELDS if field not in production]
    if missing:
        print("production per-path metrics missing: {}".format(", ".join(missing)), file=sys.stderr)
        return 1

    for stub in STUBS:
        text = read_text(stub)
        missing = [field for field in FIELDS if field not in text]
        if missing:
            print(
                "{} missing per-path metrics fields: {}".format(
                    os.path.relpath(stub, ROOT), ", ".join(missing)
                ),
                file=sys.stderr,
            )
            return 1

    print("metrics per-path production and test stubs are synchronized")
    return 0


if __name__ == "__main__":
    sys.exit(main())
