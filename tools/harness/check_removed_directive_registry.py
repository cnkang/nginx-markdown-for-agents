#!/usr/bin/env python3
"""Check removed-directive properties against the production command table.

The C property test intentionally has a small, compilable test harness.  This
companion check resolves the same ``ngx_string(...)`` entries from the real
``ngx_http_markdown_filter_commands`` definition, so a stale test inventory
cannot make a removed directive check pass while the production registry
changes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMMANDS = ROOT / "components/nginx-module/src/ngx_http_markdown_config_directives_impl.h"
NAMES = ROOT / "components/nginx-module/src/ngx_http_markdown_directive_names.h"
PROPERTY = ROOT / "components/nginx-module/tests/unit/removed_directive_rejection_property_test.c"


def _macro_values() -> dict[str, str]:
    text = NAMES.read_text(encoding="utf-8")
    return dict(re.findall(
        r"#define\s+(NGX_HTTP_MARKDOWN_DIRECTIVE_[A-Z0-9_]+)\s*\\?\s*\n?\s*\"([^\"]+)\"",
        text,
    ))


def _production_names() -> list[str]:
    text = COMMANDS.read_text(encoding="utf-8")
    table = re.search(
        r"static\s+ngx_command_t\s+ngx_http_markdown_filter_commands\[\]\s*=\s*\{(?P<body>.*?)^\};",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if table is None:
        raise AssertionError("production command table was not found")
    macros = _macro_values()
    entries = re.findall(
        r"ngx_string\((NGX_HTTP_MARKDOWN_DIRECTIVE_[A-Z0-9_]+)\)",
        table.group("body"),
    )
    if not entries:
        raise AssertionError("production command table has no directive entries")
    try:
        return [macros[name] for name in entries]
    except KeyError as exc:
        raise AssertionError(f"command table references undefined name {exc}") from exc


def main() -> int:
    production = _production_names()
    macro_values = _macro_values()
    inventory = [macro_values[name] for name in re.findall(
        r"X\((NGX_HTTP_MARKDOWN_DIRECTIVE_[A-Z0-9_]+)\)",
        NAMES.read_text(encoding="utf-8"),
    )]
    if production != inventory:
        raise AssertionError(
            "production command table differs from the canonical directive inventory:\n"
            f"production={production}\n inventory={inventory}"
        )
    if len(production) != 25 or len(set(production)) != len(production):
        raise AssertionError("production command table must contain 25 unique directives")
    removed = re.findall(
        r"\"(markdown_[a-z0-9_]+)\"",
        PROPERTY.read_text(encoding="utf-8"),
    )
    removed = [name for name in removed if name not in inventory]
    if any(name in production for name in removed):
        raise AssertionError("a removed directive is present in the production table")
    print(
        "removed-directive registry: production ngx_http_markdown_filter_commands "
        f"matches {len(production)} canonical entries; removed set is absent"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
