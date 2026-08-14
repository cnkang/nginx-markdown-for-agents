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


def _parse_directive_macro(
    line: str, continuation: str | None
) -> tuple[str, str] | None:
    prefix = "#define "
    name_prefix = "NGX_HTTP_MARKDOWN_DIRECTIVE_"
    definition = line.strip()
    if not definition.startswith(prefix):
        return None

    parts = definition[len(prefix):].split(None, 1)
    if len(parts) != 2 or not parts[0].startswith(name_prefix):
        return None

    name, value = parts
    if value == "\\":
        value = continuation
    if value is None or len(value) < 2:
        return None
    if value[0] != '"' or value[-1] != '"':
        return None
    return name, value[1:-1]


def _macro_values() -> dict[str, str]:
    lines = NAMES.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}

    for index, line in enumerate(lines):
        continuation = lines[index + 1].strip() if index + 1 < len(lines) else None
        parsed = _parse_directive_macro(line, continuation)
        if parsed is not None:
            values[parsed[0]] = parsed[1]

    return values


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
    # The production-table invariant checks the RAW matches first: an
    # active name listed among the removed candidates must fail loudly
    # even before filtering.
    if any(name in production for name in removed):
        raise AssertionError("a removed directive is present in the production table")
    removed = [name for name in removed if name not in production]
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
