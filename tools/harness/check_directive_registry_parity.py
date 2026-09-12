#!/usr/bin/env python3
"""Check the production command table against the canonical directive inventory.

The module keeps one string per directive in
``ngx_http_markdown_directive_names.h`` and the production command table in
``ngx_http_markdown_config_directives_impl.h``.  The two must agree exactly:
a table entry without a canonical name, a canonical name without an entry, a
duplicate, or a stale name left behind by a removal all indicate that the
public directive surface drifted from its single source of truth.

The comparison is derived from those two files, so it keeps working when
directives are added or removed and never pins a count the sources already
determine.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMMANDS = ROOT / "components/nginx-module/src/ngx_http_markdown_config_directives_impl.h"
NAMES = ROOT / "components/nginx-module/src/ngx_http_markdown_directive_names.h"


def _parse_directive_macro(
    line: str, continuation: str | None
) -> tuple[str, str] | None:
    """Return the ``(macro, value)`` pair of a single directive definition."""
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
    if value is None:
        return None
    value = value.strip()
    if len(value) < 2:
        return None
    # A trailing C comment after the quoted value must be removed before
    # quote validation, so quoted definitions with comments are retained
    # while non-quoted values remain rejected.  A comment can never be a
    # valid directive value, so stripping it cannot mask a bad definition.
    value = re.split(r"/\*.*?\*/|//[^\n]*", value, maxsplit=1)[0].rstrip()
    if len(value) < 2:
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
    macro_names = re.findall(
        r"X\((NGX_HTTP_MARKDOWN_DIRECTIVE_[A-Z0-9_]+)\)",
        NAMES.read_text(encoding="utf-8"),
    )
    try:
        inventory = [macro_values[name] for name in macro_names]
    except KeyError as exc:
        raise AssertionError(f"inventory references undefined name {exc}") from exc

    if len(set(production)) != len(production):
        duplicates = sorted(
            name for name in set(production) if production.count(name) > 1
        )
        raise AssertionError(f"command table repeats directives: {duplicates}")
    if production != inventory:
        raise AssertionError(
            "production command table differs from the canonical directive inventory:\n"
            f"production={production}\n inventory={inventory}"
        )
    print(
        "directive registry parity: production ngx_http_markdown_filter_commands "
        f"matches all {len(production)} canonical entries"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
