#!/usr/bin/env python3
"""Check the knowledge-base contract tables against the frozen inventory.

The inventory remains the machine-readable source of truth.  This check keeps
the agent-facing Markdown tables synchronized so a hand-edited KB cannot
silently drift from the public surface.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INVENTORY_PATH = ROOT / "docs/harness/public-surface-inventory.json"
CONTRACT_PATH = ROOT / "docs/knowledge-base/config-contract.md"
README_PATH = ROOT / "docs/knowledge-base/README.md"
LIMIT_REGISTRY_PATH = ROOT / "tools/release/gates/validate_config_directives.py"
METRIC_KEY_FIELD = "metric key"
ALLOWED_VALUES_FIELD = "allowed values"
README_FROZEN_SECTION_RE = re.compile(r"^## Key Numbers\b", re.M)
README_FROZEN_ROW_RE = re.compile(
    r"^\|\s*(?:Active directives|Removed directives|Dynconf keys|"
    r"Metric families|Reason codes|FFI exports|MSRV / toolchain|OTel|Profiles)\s*\|",
    re.M,
)


def _split_row(line: str) -> list[str] | None:
    """Split a Markdown table row without splitting pipes in code spans."""
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None
    cells: list[str] = []
    current: list[str] = []
    in_code = False
    for char in stripped[1:]:
        if char == "`":
            in_code = not in_code
        if char == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current and current[-1] == "|":
        current.pop()
    cells.append("".join(current).strip())
    if cells and cells[-1] == "":
        cells.pop()
    return cells


def _find_table_header(
    lines: list[str], heading_pattern: str
) -> tuple[list[str], int]:
    heading = re.compile(heading_pattern)
    start = next((i for i, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        raise ValueError(f"missing table heading matching {heading_pattern!r}")

    header_index = next(
        (i for i in range(start + 1, len(lines)) if _split_row(lines[i]) is not None),
        None,
    )
    if header_index is None:
        raise ValueError(f"missing table after {heading_pattern!r}")
    headers = [cell.strip().lower() for cell in _split_row(lines[header_index]) or []]

    separator_index = header_index + 1
    while separator_index < len(lines) and not lines[separator_index].strip():
        separator_index += 1
    separator = _split_row(lines[separator_index]) if separator_index < len(lines) else None
    if separator is None or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
        raise ValueError(f"missing separator after table {heading_pattern!r}")
    return headers, separator_index


def _parse_table_rows(
    lines: list[str], separator_index: int, headers: list[str], heading_pattern: str
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in lines[separator_index + 1 :]:
        if line.lstrip().startswith("#"):
            break
        if not line.strip():
            continue
        cells = _split_row(line)
        if cells is None:
            if rows:
                break
            continue
        if len(cells) != len(headers):
            raise ValueError(
                f"table {heading_pattern!r} row has {len(cells)} cells, "
                f"expected {len(headers)}: {line}"
            )
        rows.append(dict(zip(headers, cells)))
    return rows


def _parse_table(text: str, heading_pattern: str) -> tuple[list[str], list[dict[str, str]]]:
    lines = text.splitlines()
    headers, separator_index = _find_table_header(lines, heading_pattern)
    return headers, _parse_table_rows(lines, separator_index, headers, heading_pattern)


def _clean(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return re.sub(r"\s+", " ", str(value).replace("`", "").strip())


def _compare_maps(
    errors: list[str],
    label: str,
    expected: dict[str, dict[str, str]],
    actual: dict[str, dict[str, str]],
    fields: tuple[str, ...],
) -> None:
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        errors.append(f"{label}: missing rows: {', '.join(missing)}")
    if extra:
        errors.append(f"{label}: unexpected rows: {', '.join(extra)}")
    for key in sorted(set(expected) & set(actual)):
        for field in fields:
            if expected[key].get(field) != actual[key].get(field):
                errors.append(
                    f"{label} {key}: {field} is {actual[key].get(field)!r}, "
                    f"inventory requires {expected[key].get(field)!r}"
                )


def _current_limit_keys() -> list[str]:
    """Read the existing release-gate limit registry without executing it."""
    source = LIMIT_REGISTRY_PATH.read_text(encoding="utf-8")
    match = re.search(r"CURRENT_LIMIT_KEYS\s*=\s*(\[[^]]*\])", source, re.S)
    if match is None:
        raise ValueError("missing CURRENT_LIMIT_KEYS registry")
    values = ast.literal_eval(match.group(1))
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("CURRENT_LIMIT_KEYS is not a list of strings")
    return values


def _parse_contract_tables(
    contract_text: str,
) -> tuple[list[dict[str, str]], ...]:
    patterns = (
        r"^## Active Directives \(",
        r"^## Dynconf Keys \(",
        r"^## Metric Families \(",
        r"^## Reason Codes \(",
        r"^## markdown_limits Keys$",
    )
    return tuple(_parse_table(contract_text, pattern)[1] for pattern in patterns)


def _validate_directives(
    errors: list[str], inventory: dict, directive_rows: list[dict[str, str]]
) -> None:
    expected_directives = {
        item["name"]: {
            "syntax": _clean(item["syntax"]),
            "default": _clean(item["default"]),
            "context": "/".join(str(value) for value in item["context"]),
        }
        for item in inventory["directives"]
    }
    actual_directives = {
        row["directive"].strip("`"): {
            "syntax": _clean(row["syntax"]),
            "default": _clean(row["default"]),
            "context": _clean(row["context"]),
        }
        for row in directive_rows
    }
    _compare_maps(
        errors,
        "directives",
        expected_directives,
        actual_directives,
        ("syntax", "default", "context"),
    )
    if len(directive_rows) != inventory["directive_count"]:
        errors.append(
            f"directives: table has {len(directive_rows)} rows, inventory has "
            f"{inventory['directive_count']}"
        )


def _validate_dynconf(
    errors: list[str],
    inventory: dict,
    dynconf_rows: list[dict[str, str]],
    contract_text: str,
) -> None:
    expected_dynconf = {
        item["name"]: {
            "type": _clean(item["type"]),
            ALLOWED_VALUES_FIELD: _clean(item["allowed_values"]),
            "default": _clean(item["default"]),
            "inheritance": _clean(item["inheritance"]),
        }
        for item in inventory["dynconf_keys"]
    }
    actual_dynconf = {
        row["key"].strip("`"): {
            "type": _clean(row["type"]),
            ALLOWED_VALUES_FIELD: _clean(row[ALLOWED_VALUES_FIELD]),
            "default": _clean(row["default"]),
            "inheritance": _clean(row["inheritance"]),
        }
        for row in dynconf_rows
    }
    for key, expected in expected_dynconf.items():
        actual = actual_dynconf.get(key)
        if actual is None:
            continue
        if not expected[ALLOWED_VALUES_FIELD] and "size" not in actual[ALLOWED_VALUES_FIELD]:
            errors.append(f"dynconf {key}: size range is not documented")
        elif not expected[ALLOWED_VALUES_FIELD]:
            expected[ALLOWED_VALUES_FIELD] = actual[ALLOWED_VALUES_FIELD]
    _compare_maps(
        errors,
        "dynconf",
        expected_dynconf,
        actual_dynconf,
        ("type", ALLOWED_VALUES_FIELD, "default", "inheritance"),
    )
    dynamic_count = sum(1 for item in inventory["dynconf_keys"] if item["dynamic"])
    if len(dynconf_rows) != len(inventory["dynconf_keys"]):
        errors.append(
            f"dynconf: table has {len(dynconf_rows)} rows, inventory has "
            f"{len(inventory['dynconf_keys'])}"
        )
    heading = (
        f"## Dynconf Keys ({dynamic_count} runtime-mutable + "
        "schema_version metadata)"
    )
    if heading not in contract_text:
        errors.append("dynconf: heading must distinguish runtime-mutable keys from schema metadata")
    if not re.search(
        r"five runtime-mutable keys .*?required `schema_version` metadata",
        contract_text,
        re.I | re.S,
    ):
        errors.append("dynconf: contract must describe five runtime-mutable keys plus schema metadata")


def _validate_readme(errors: list[str], readme_text: str) -> None:
    if README_FROZEN_SECTION_RE.search(readme_text) or README_FROZEN_ROW_RE.search(
        readme_text
    ):
        errors.append(
            "README must not duplicate the frozen numeric contract; link to config-contract.md"
        )
    for required_reference in ("config-contract.md", "public-surface-inventory.json"):
        if required_reference not in readme_text:
            errors.append(f"README must reference {required_reference}")


def _validate_metrics(
    errors: list[str], inventory: dict, metric_rows: list[dict[str, str]]
) -> None:
    expected_metrics = {
        item["name"]: {
            "type": _clean(item["type"]),
            "labels": _clean(item["labels"]) or "—",
            "cardinality": _clean(item["bounded_cardinality"]),
        }
        for item in inventory["metrics"]
    }
    actual_metrics = {
        row["metric"].strip("`"): {
            "type": _clean(row["type"]),
            "labels": _clean(row["labels"]) or "—",
            "cardinality": _clean(row["cardinality"]),
        }
        for row in metric_rows
    }
    _compare_maps(
        errors,
        "metrics",
        expected_metrics,
        actual_metrics,
        ("type", "labels", "cardinality"),
    )


def _validate_reasons_and_limits(
    errors: list[str],
    inventory: dict,
    reason_rows: list[dict[str, str]],
    limit_rows: list[dict[str, str]],
) -> None:
    expected_reasons = {
        str(item["discriminant"]): {
            "string": _clean(item["string"]),
            METRIC_KEY_FIELD: _clean(item["metric_key"]),
        }
        for item in inventory["reason_codes"]
    }
    actual_reasons = {
        row["#"]: {
            "string": _clean(row["string"]),
            METRIC_KEY_FIELD: _clean(row[METRIC_KEY_FIELD]),
        }
        for row in reason_rows
    }
    _compare_maps(
        errors,
        "reason codes",
        expected_reasons,
        actual_reasons,
        ("string", METRIC_KEY_FIELD),
    )

    expected_limits = {key: {} for key in _current_limit_keys()}
    actual_limits = {row["key"].strip("`"): {} for row in limit_rows}
    _compare_maps(errors, "markdown_limits", expected_limits, actual_limits, ())


def validate_contract(
    inventory: dict, contract_text: str, readme_text: str
) -> list[str]:
    errors: list[str] = []
    try:
        (
            directive_rows,
            dynconf_rows,
            metric_rows,
            reason_rows,
            limit_rows,
        ) = _parse_contract_tables(contract_text)
    except ValueError as exc:
        return [str(exc)]

    _validate_directives(errors, inventory, directive_rows)

    _validate_dynconf(errors, inventory, dynconf_rows, contract_text)
    _validate_readme(errors, readme_text)

    _validate_metrics(errors, inventory, metric_rows)

    _validate_reasons_and_limits(errors, inventory, reason_rows, limit_rows)

    ffi_count = len(inventory["ffi_exports"])
    ffi_heading = (
        f"## FFI Surface Summary ({ffi_count} exports, "
        f"ABI v{inventory['ffi_abi_version']})"
    )
    if ffi_heading not in contract_text:
        errors.append("FFI: summary export count or ABI heading does not match inventory")
    if f"**Classification:** all `{inventory['ffi_classification']}`" not in contract_text:
        errors.append("FFI: classification does not match inventory")
    header = inventory["ffi_exports"][0]["generated_header"]
    if f"**Generated header:** `{header}`" not in contract_text:
        errors.append("FFI: generated header does not match inventory")
    if (
        "Complete export names and signatures live in" not in contract_text
        or "public-surface-inventory.json" not in contract_text
        or "the generated header" not in contract_text
    ):
        errors.append("FFI: summary must point to complete inventory and generated-header surfaces")
    return errors


def main() -> int:
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    errors = validate_contract(
        inventory,
        CONTRACT_PATH.read_text(encoding="utf-8"),
        README_PATH.read_text(encoding="utf-8"),
    )
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("OK: knowledge-base contract matches public-surface inventory")
    return 0


if __name__ == "__main__":
    sys.exit(main())
