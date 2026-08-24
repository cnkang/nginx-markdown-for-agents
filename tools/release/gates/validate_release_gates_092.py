#!/usr/bin/env python3
"""Release gate validator for 0.9.2.

Validates 0.9.2-specific deliverables:
  - Version consistency (all version sources = 0.9.2)
  - Reason code registry completeness (27 codes, including encoding_header_invalid)
  - Public surface inventory exists and is parseable

Adds 0.9.2-specific checks to the 0.9.1 Make gate chain; prior checks
remain delegated to that chain.

Exit codes:
  0 = all gates pass
  1 = at least one gate failed
"""

import json
import re
import tomllib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.lib.path_validation import validate_read_path  # noqa: E402
from tools.lib.reason_code import REASON_C_ACCESSOR_ALIASES  # noqa: E402

EXPECTED_VERSION = "0.9.2"
EXPECTED_REASON_CODE_COUNT = 27
CHANGELOG_FILENAME = "CHANGELOG.md"
REASON_CODE_RELATIVE_PATH = "components/rust-converter/src/decision/reason_code.rs"
REASON_C_RELATIVE_PATH = "components/nginx-module/src/ngx_http_markdown_reason.c"
REASON_INVENTORY_RELATIVE_PATH = "docs/harness/public-surface-inventory.json"
REASON_METADATA_FIELDS = ("discriminant", "name", "string", "metric_key",
                          "c_accessor")


def find_repo_root() -> Path:
    """Return the repository root resolved from this validator's location."""
    return REPO_ROOT


def _cargo_version_mismatch(path: Path) -> str | None:
    if not path.exists():
        return "Cargo.toml: file not found"
    try:
        validated = validate_read_path(path, purpose="Cargo.toml")
        cargo_data = tomllib.loads(validated.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"Cargo.toml: invalid TOML ({exc})"
    package = cargo_data.get("package")
    version = package.get("version") if isinstance(package, dict) else None
    if version != EXPECTED_VERSION:
        return f"Cargo.toml: {version if version is not None else 'version not found'}"
    return None


def _changelog_version_mismatch(path: Path) -> str | None:
    if not path.exists():
        return f"{CHANGELOG_FILENAME}: file not found"
    try:
        validated = validate_read_path(path, purpose="CHANGELOG")
        content = validated.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return f"{CHANGELOG_FILENAME}: cannot read ({exc})"
    if (
        f"## [{EXPECTED_VERSION}]" not in content
        and f"## v{EXPECTED_VERSION}" not in content
    ):
        return f"{CHANGELOG_FILENAME}: {EXPECTED_VERSION} header not found"
    return None


def check_version_consistency(repo: Path) -> dict:
    """Verify all version sources agree on 0.9.2."""
    mismatches = []
    for mismatch in (
        _cargo_version_mismatch(
            repo / "components/rust-converter/Cargo.toml"
        ),
        _changelog_version_mismatch(repo / CHANGELOG_FILENAME),
    ):
        if mismatch is not None:
            mismatches.append(mismatch)

    if mismatches:
        return {"name": "version_consistency", "status": "fail",
                "message": "; ".join(mismatches)}
    return {"name": "version_consistency", "status": "pass",
            "details": {"expected": EXPECTED_VERSION}}


def _duplicates(values):
    """Return duplicate values in first-seen order."""
    seen = set()
    duplicates = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates


def _validate_reason_entry(entry, index, label):
    """Validate one reason entry and return its identity fields."""
    if not isinstance(entry, dict):
        return [f"{label}[{index}] must be an object"], None, None
    missing = [field for field in REASON_METADATA_FIELDS if field not in entry]
    if missing:
        return [f"{label}[{index}] missing fields: {', '.join(missing)}"], None, None

    errors = []
    discriminant = entry["discriminant"]
    if not isinstance(discriminant, int) or isinstance(discriminant, bool):
        errors.append(f"{label}[{index}].discriminant must be an integer")
        discriminant = None
    name = entry["name"]
    if not isinstance(name, str) or not name:
        errors.append(f"{label}[{index}].name must be a non-empty string")
        name = None
    for field in ("string", "metric_key", "c_accessor"):
        if not isinstance(entry[field], str) or not entry[field]:
            errors.append(f"{label}[{index}].{field} must be a non-empty string")
    return errors, discriminant, name


def _validate_reason_entry_shapes(entries, label):
    """Validate reason entry fields, uniqueness, and discriminant ordering."""
    checked = [_validate_reason_entry(entry, index, label)
               for index, entry in enumerate(entries)]
    errors = [error for entry_errors, _, _ in checked for error in entry_errors]
    discriminants = [discriminant for _, discriminant, _ in checked
                     if discriminant is not None]
    names = [name for _, _, name in checked if name is not None]
    errors.extend(f"{label} contains duplicate discriminant: {duplicate}"
                  for duplicate in _duplicates(discriminants))
    errors.extend(f"{label} contains duplicate name: {duplicate}"
                  for duplicate in _duplicates(names))
    if discriminants != list(range(len(entries))):
        errors.append(f"{label} discriminants are not contiguous and ordered from zero")
    return errors


def _parse_reason_arm_map(body, registry_name):
    """Parse a ReasonCode match body and reject duplicate arms."""
    matches = re.findall(
        r"ReasonCode::([A-Za-z_]\w*)[ \t]*=>[ \t]*\"([^\"]*)\"",
        body, flags=re.ASCII)
    values = {}
    duplicates = []
    for name, value in matches:
        if name in values:
            duplicates.append(name)
        values[name] = value
    if duplicates:
        return None, f"Rust {registry_name} contains duplicate entry: {duplicates[0]}"
    return values, None


def _parse_reason_metric_map(body):
    """Parse metric match arms, including grouped variants."""
    values = {}
    pending = []
    for line in body.splitlines():
        lhs, separator, rhs = line.partition("=>")
        pending.extend(re.findall(
            r"ReasonCode::([A-Za-z_]\w*)", lhs, flags=re.ASCII))
        if not separator:
            continue
        match = re.match(r"[ \t]*\"([^\"]*)\"", rhs)
        if match is None:
            return None, "Rust metric registry contains a malformed match arm"

        for name in pending:
            if name in values:
                return None, f"Rust metric registry contains duplicate entry: {name}"
            values[name] = match.group(1)
        pending = []
    if pending:
        return None, "Rust metric registry contains an unterminated match arm"
    return values, None


def _parse_reason_enum(content):
    """Parse the Rust count and enum variants."""
    count_match = re.search(
        r"pub const REASON_CODE_COUNT:[ \t]*usize[ \t]*=[ \t]*(\d+)",
        content)
    if count_match is None:
        return None, "REASON_CODE_COUNT not found"
    count = int(count_match.group(1))

    enum_marker = "pub enum ReasonCode"
    enum_start = content.find(enum_marker)
    body_start = content.find("{", enum_start)
    body_end = content.find("\n}", body_start)
    if enum_start < 0 or body_start < 0 or body_end < 0:
        return None, "ReasonCode enum is missing or unterminated"
    enum_body = content[body_start + 1:body_end]
    variants = [
        {"discriminant": int(discriminant), "name": name}
        for name, discriminant in re.findall(
            r"(?m)^[ \t]*([A-Za-z_]\w*)[ \t]*=[ \t]*(\d+),[ \t]*$",
            enum_body, flags=re.ASCII)
    ]
    source_shape_errors = _validate_reason_entry_shapes(
        [dict(entry, string="source", metric_key="source", c_accessor="source")
         for entry in variants], "Rust enum")
    if source_shape_errors:
        return None, "; ".join(source_shape_errors)
    if count != len(variants):
        return None, f"REASON_CODE_COUNT is {count}, but Rust enum has {len(variants)} entries"
    return {"count": count, "variants": variants}, None


def _parse_reason_all(content, variant_names):
    """Validate the exhaustive Rust reason-code array."""
    all_marker = "pub const ALL"
    all_start = content.find(all_marker)
    all_body_start = content.find("[", all_start)
    all_body_end = content.find("];", all_body_start)
    if all_start < 0 or all_body_start < 0 or all_body_end < 0:
        return "Rust reason code ALL registry is missing or unterminated"
    all_names = re.findall(
        r"ReasonCode::([A-Za-z_]\w*)",
        content[all_body_start:all_body_end], flags=re.ASCII)
    duplicates = _duplicates(all_names)
    if duplicates:
        return f"Rust reason code ALL registry contains duplicate: {duplicates[0]}"
    if all_names != variant_names:
        return "Rust reason code ALL registry order or membership does not match enum"
    return None


def _parse_reason_registries(content):
    """Parse the Rust string and metric registries."""
    as_str_marker = "pub fn as_str"
    metric_marker = "pub fn metric_key"
    as_str_start = content.find(as_str_marker)
    metric_start = content.find(metric_marker, as_str_start)
    if as_str_start < 0 or metric_start < 0:
        return None, None, "Rust reason string registries are missing or malformed"
    strings, error = _parse_reason_arm_map(
        content[as_str_start:metric_start], "string registry")
    if error:
        return None, None, error
    log_marker = "pub fn log_callsite"
    log_start = content.find(log_marker, metric_start)
    if log_start < 0:
        return None, None, "Rust reason metric registry is missing or malformed"
    metric_match = re.search(
        r"match\s+self\s*\{(?P<body>.*?)\n[ \t]*}\s*\n[ \t]*}",
        content[metric_start:log_start], flags=re.S)
    if metric_match is None:
        return None, None, "Rust reason metric match is missing or unterminated"
    metrics, error = _parse_reason_metric_map(
        metric_match.group("body"))
    return strings, metrics, error


def _validate_reason_registry_names(variants, strings, metrics):
    """Ensure metadata registries contain exactly the enum variants."""
    variant_names = [entry["name"] for entry in variants]
    for registry_name, registry in (("string", strings), ("metric", metrics)):
        missing = sorted(set(variant_names) - set(registry))
        extra = sorted(set(registry) - set(variant_names))
        if missing or extra:
            detail = []
            if missing:
                detail.append(f"missing {', '.join(missing)}")
            if extra:
                detail.append(f"unknown {', '.join(extra)}")
            return f"Rust {registry_name} registry mismatch: {'; '.join(detail)}"
    return None


def _parse_reason_source(content):
    """Parse the complete Rust reason enum and its metadata registries."""
    parsed, error = _parse_reason_enum(content)
    if error:
        return None, error
    variants = parsed["variants"]
    variant_names = [entry["name"] for entry in variants]
    error = _parse_reason_all(content, variant_names)
    if error:
        return None, error
    strings, metrics, error = _parse_reason_registries(content)
    if error:
        return None, error
    error = _validate_reason_registry_names(variants, strings, metrics)
    if error:
        return None, error

    return [
        {
            "discriminant": entry["discriminant"],
            "name": entry["name"],
            "string": strings[entry["name"]],
            "metric_key": metrics[entry["name"]],
        }
        for entry in variants
    ], None


def _canonical_reason_codes(inventory):
    """Return canonical inventory reason entries or a clear schema error."""
    if not isinstance(inventory, dict):
        return None, "public surface inventory must be an object"
    entries = inventory.get("reason_codes")
    if not isinstance(entries, list):
        return None, "inventory reason_codes must be an array"
    if inventory.get("registry_count") != len(entries):
        return None, "inventory registry_count does not match reason_codes length"
    errors = _validate_reason_entry_shapes(entries, "inventory reason_codes")
    if errors:
        return None, "; ".join(errors)
    return entries, None


def _load_reason_registry_files(repo):
    """Load the Rust source and canonical inventory for validation."""
    rc_file = repo / REASON_CODE_RELATIVE_PATH
    if not rc_file.exists():
        return None, None, "reason_code.rs not found"
    inventory_file = repo / REASON_INVENTORY_RELATIVE_PATH
    if not inventory_file.exists():
        return None, None, "public-surface-inventory.json not found"
    try:
        validated_rc = validate_read_path(rc_file, purpose="reason code registry")
        source_content = validated_rc.read_text(encoding="utf-8")
        validated_inv = validate_read_path(
            inventory_file, purpose="public surface inventory")
        inventory = json.loads(validated_inv.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, None, f"unable to read canonical reason registry: {exc}"
    return source_content, inventory, None


def _compare_reason_registry(canonical, actual):
    """Return metadata mismatches between inventory and Rust source."""
    mismatches = []
    for expected, observed in zip(canonical, actual):
        for field in ("discriminant", "name", "string", "metric_key"):
            if expected[field] != observed[field]:
                mismatches.append(
                    f"{observed['name']} {field}: inventory={expected[field]} source={observed[field]}")
    if len(canonical) != len(actual):
        mismatches.append(
            f"registry length: inventory={len(canonical)} source={len(actual)}")
    return mismatches


def _validate_c_reason_registry(repo, canonical):
    """Validate C reason storage and accessor metadata against inventory."""
    c_file = repo / REASON_C_RELATIVE_PATH
    if not c_file.exists():
        return "ngx_http_markdown_reason.c not found"
    try:
        validated = validate_read_path(c_file, purpose="C reason registry")
        c_content = validated.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return f"unable to read C reason registry: {exc}"
    c_accessors = set(re.findall(
        r"static[ \t]+ngx_str_t[ \t]+(reason_str_\w+)",
        c_content, flags=re.ASCII))
    for entry in canonical:
        c_name = REASON_C_ACCESSOR_ALIASES.get(
            entry["string"], entry["string"])
        expected_accessor = "reason_str_" + c_name
        if entry["c_accessor"] != expected_accessor:
            return (f"{entry['name']} c_accessor metadata mismatch: "
                    f"inventory={entry['c_accessor']} expected={expected_accessor}")
        if expected_accessor not in c_accessors:
            return f"C reason storage missing: {expected_accessor}"
    return None


def check_reason_code_registry(repo: Path) -> dict:
    """Verify the complete Rust reason registry against canonical inventory."""
    source_content, inventory, error = _load_reason_registry_files(repo)
    if error:
        return {"name": "reason_code_registry", "status": "fail",
                "message": error}

    canonical, error = _canonical_reason_codes(inventory)
    if error:
        return {"name": "reason_code_registry", "status": "fail",
                "message": error}
    if len(canonical) != EXPECTED_REASON_CODE_COUNT:
        return {"name": "reason_code_registry", "status": "fail",
                "message": f"Expected {EXPECTED_REASON_CODE_COUNT} canonical entries, got {len(canonical)}"}

    actual, error = _parse_reason_source(source_content)
    if error:
        return {"name": "reason_code_registry", "status": "fail",
                "message": error}
    mismatches = _compare_reason_registry(canonical, actual)
    if mismatches:
        return {"name": "reason_code_registry", "status": "fail",
                "message": "; ".join(mismatches)}

    error = _validate_c_reason_registry(repo, canonical)
    if error:
        return {"name": "reason_code_registry", "status": "fail",
                "message": error}
    return {"name": "reason_code_registry", "status": "pass",
            "details": {"count": len(actual)}}


def check_public_surface_inventory(repo: Path) -> dict:
    """Verify public surface inventory exists and is parseable JSON."""
    inventory = repo / REASON_INVENTORY_RELATIVE_PATH
    if not inventory.exists():
        return {"name": "public_surface_inventory", "status": "fail",
                "message": "public-surface-inventory.json not found"}
    try:
        validated = validate_read_path(
            inventory, purpose="public surface inventory")
        data = json.loads(validated.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"name": "public_surface_inventory", "status": "fail",
                "message": f"invalid JSON: {exc}"}
    if not isinstance(data, dict):
        return {"name": "public_surface_inventory", "status": "fail",
                "message": "expected top-level JSON object"}
    return {"name": "public_surface_inventory", "status": "pass",
            "details": {"keys": list(data.keys())[:10]}}


def main():
    """Run the 0.9.2-specific checks and exit non-zero on any failure."""
    repo = find_repo_root()
    checks = [
        check_version_consistency(repo),
        check_reason_code_registry(repo),
        check_public_surface_inventory(repo),
    ]

    failed = [c for c in checks if c["status"] == "fail"]
    for c in checks:
        status_tag = "PASS" if c["status"] == "pass" else "FAIL"
        msg = f"  [{status_tag}] {c['name']}"
        if "message" in c:
            msg += f": {c['message']}"
        print(msg)

    if failed:
        print(f"\n0.9.2 gates: {len(failed)} FAILED")
        sys.exit(1)
    print("\n0.9.2 gates: ALL PASS")


if __name__ == "__main__":
    main()
