#!/usr/bin/env python3
"""Fail closed when the declared public-surface contract drifts from source.

The inventory is intentionally more than a list of names.  The gate checks
the directive command contract, metric metadata, reason-code registry, FFI
signatures/ABI, and dynconf policy fields.  Source parsing is deliberately
small and deterministic because this gate also runs in a clean release
checkout without a compiler or an external schema package.
"""

from __future__ import print_function

import argparse
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from lib.path_validation import validate_read_path  # noqa: E402
from lib.reason_code import REASON_C_ACCESSOR_ALIASES  # noqa: E402

INVENTORY_PATH = os.path.join(ROOT, "docs", "harness", "public-surface-inventory.json")
DIRECTIVES_PATH = os.path.join(ROOT, "components", "nginx-module", "src", "ngx_http_markdown_config_directives_impl.h")
REASON_CODE_PATH = os.path.join(ROOT, "components", "rust-converter", "src", "decision", "reason_code.rs")
REASON_C_PATH = os.path.join(ROOT, "components", "nginx-module", "src", "ngx_http_markdown_reason.c")
DYNCONF_PATH = os.path.join(ROOT, "components", "nginx-module", "src", "ngx_http_markdown_dynconf_impl.h")
METRICS_PATH = os.path.join(ROOT, "components", "nginx-module", "src", "ngx_http_markdown_metrics_v1_renderer.h")
FFI_PATHS = (
    os.path.join(ROOT, "components", "rust-converter", "src", "ffi", "exports.rs"),
    os.path.join(ROOT, "components", "rust-converter", "src", "ffi", "incremental.rs"),
    os.path.join(ROOT, "components", "rust-converter", "src", "ffi", "streaming.rs"),
    os.path.join(ROOT, "components", "rust-converter", "src", "dynconf", "ffi.rs"),
    REASON_CODE_PATH,
)
FFI_HEADER_PATH = os.path.join(ROOT, "components", "rust-converter", "include", "markdown_converter.h")
COMMAND_REGISTRY_ERROR = "ngx_command_t registry is missing or unterminated"
MIGRATION_PREFIX = "Migration:"

DIRECTIVE_RE = re.compile(r'ngx_string\("(markdown_[^"\\]+)"\)')
REASON_CODE_RE = re.compile(r'^\s+(\w+)\s*=\s*(\d+)\s*,', re.MULTILINE)
DYNCONF_KEY_RE = re.compile(r'static\s+u_char\s+\w+_key\[\]\s*=\s*"([^"]+)"')
DYNCONF_KEYS = (
    "schema_version", "filter", "prune_noise", "log_verbosity",
    "error_policy", "streaming_buffer",
)
METRIC_NAME_RE = re.compile(r'\b(nginx_markdown_[a-z0-9_]+)\b')
FFI_FN_RE = re.compile(
    r'pub\s+(unsafe\s+)?extern\s+"C"\s+fn\s+(markdown_\w+)\s*'
    r'\(([^)]*)\)\s*(?:->\s*([^{}]*))?\s*\{',
)

REQUIRED_TOP_LEVEL = {
    "schema_version", "contract_version", "ffi_abi_version", "directives",
    "reject_only_directives", "otel", "dynconf_keys", "metrics",
    "reason_codes", "ffi_exports", "registry_count",
}
DIRECTIVE_FIELDS = {
    "name", "context", "default", "syntax", "status", "classification",
    "handler", "args", "conf_offset", "source_flags", "post",
    "otel_classification", "migration_target",
}
REJECT_FIELDS = {
    "name", "migration_target", "default", "syntax", "classification",
    "handler", "args", "context", "conf_offset", "source_flags", "post",
    "otel_classification", "status",
}
METRIC_FIELDS = {"name", "type", "labels", "order", "bounded_cardinality"}
REASON_FIELDS = {
    "discriminant", "name", "string", "metric_key", "c_accessor",
}
DYNCONF_FIELDS = {
    "name", "type", "allowed_values", "default", "inheritance", "dynamic",
    "unknown_key", "required", "duplicate",
}
FFI_FIELDS = {
    "name", "signature", "params", "return_type", "safety", "abi_version",
    "generated_header",
}


def _normalize_ffi_fragment(value):
    """Normalize Rust formatting without changing parameter semantics."""
    if not isinstance(value, str):
        return value
    return re.sub(r"\s+", " ", value).strip().rstrip(",").rstrip()


def _normalize_ffi_entry(entry):
    """Canonicalize FFI signature fields before comparing inventory data."""
    normalized = dict(entry)
    normalized["params"] = _normalize_ffi_fragment(normalized.get("params"))
    normalized["return_type"] = _normalize_ffi_fragment(
        normalized.get("return_type"))
    if isinstance(normalized.get("name"), str):
        normalized["signature"] = "{}({}) -> {}".format(
            normalized["name"], normalized.get("params", ""),
            normalized.get("return_type", ""))
    return normalized


def read_text(path):
    """Read a repository file only after validating its path."""
    validated = validate_read_path(path, purpose="public surface drift")
    return validated.read_text(encoding="utf-8")


def load_inventory(path=None):
    """Load the JSON inventory through the same path boundary as source files."""
    if path is None:
        path = INVENTORY_PATH
    return json.loads(read_text(os.path.realpath(path)))


def _names(values):
    """Return entry names while tolerating the inventory's scalar form."""
    if not isinstance(values, list):
        return []
    names = []
    for value in values:
        if isinstance(value, str):
            names.append(value)
        elif isinstance(value, dict) and isinstance(value.get("name"), str):
            names.append(value["name"])
    return names


def _duplicates(values):
    """Return duplicate values once, preserving their first duplicate order."""
    seen = set()
    duplicates = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return duplicates


def _validate_inventory_header(inventory):
    """Validate schema and contract metadata before inspecting source files."""
    errors = []
    missing = sorted(REQUIRED_TOP_LEVEL - set(inventory))
    if missing:
        errors.append("inventory missing top-level keys: {}".format(
            ", ".join(missing)))
    if inventory.get("schema_version") != "0.9.2":
        errors.append("inventory schema_version must be 0.9.2")
    if inventory.get("contract_version") != "1":
        errors.append("inventory contract_version must be 1")
    if not isinstance(inventory.get("ffi_abi_version"), int):
        errors.append("inventory ffi_abi_version must be an integer")
    if not isinstance(inventory.get("registry_count"), int):
        errors.append("inventory registry_count must be an integer")
    return errors


def _validate_named_entries(inventory, key, fields):
    """Validate a named inventory array and return its errors and names."""
    values = inventory.get(key)
    if not isinstance(values, list):
        return ["{} must be an array".format(key)], []
    errors = []
    names = []
    for index, entry in enumerate(values):
        if not isinstance(entry, dict):
            errors.append("{}[{}] must be an object".format(key, index))
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            errors.append("{}[{}].name must be a string".format(key, index))
        else:
            names.append(name)
        missing = sorted(fields - set(entry))
        if missing:
            errors.append("{}[{}] missing fields: {}".format(
                key, index, ", ".join(missing)))
    for duplicate in _duplicates(names):
        errors.append("{} contains duplicate name: {}".format(key, duplicate))
    return errors, names


def _validate_otel_group(key, values):
    """Validate one OTel directive group without assuming entry shapes."""
    if not isinstance(values, list):
        return ["otel.{} must be an array".format(key)]
    errors = []
    names = []
    for index, entry in enumerate(values):
        entry_errors, name = _validate_otel_entry(key, index, entry)
        errors.extend(entry_errors)
        if name is not None:
            names.append(name)
    errors.extend(
        "otel.{} contains duplicate name: {}".format(key, duplicate)
        for duplicate in _duplicates(names))
    return errors


def _validate_otel_entry(key, index, entry):
    """Validate one OTel entry and return its optional string name."""
    if not isinstance(entry, dict):
        return ["otel.{}[{}] must be an object".format(key, index)], None
    errors = []
    name = entry.get("name")
    if not isinstance(name, str):
        errors.append("otel.{}[{}].name must be a string".format(key, index))
        name = None
    if key == "reject_only" and entry.get("status") != "reject_only":
        errors.append("otel.{}[{}].status must be reject_only".format(
            key, index))
    if key == "reject_only" and not isinstance(
            entry.get("migration_target"), str):
        errors.append("otel.{}[{}].migration_target must be a string".format(
            key, index))
    return errors, name


def _validate_otel_schema(inventory):
    """Validate the OTel directive groups and their status declaration."""
    otel = inventory.get("otel")
    if not isinstance(otel, dict):
        return ["otel must be an object"]
    errors = []
    for key in ("directives", "reject_only"):
        errors.extend(_validate_otel_group(key, otel.get(key)))
    if otel.get("status") not in ("experimental", "stable"):
        errors.append("otel.status must be experimental or stable")
    return errors


def _validate_dynconf_schema(inventory):
    """Validate the dynamic-configuration key entries."""
    errors, _ = _validate_named_entries(
        inventory, "dynconf_keys", DYNCONF_FIELDS)
    return errors


def _validate_metric_schema(inventory):
    """Validate metric fields, labels, ordering, and uniqueness."""
    values = inventory.get("metrics")
    if not isinstance(values, list):
        return ["metrics must be an array"]
    errors = []
    orders = []
    for index, entry in enumerate(values):
        entry_errors, order = _validate_metric_entry(index, entry)
        errors.extend(entry_errors)
        if order is not None:
            orders.append(order)
    if len(orders) != len(values) or len(orders) != len(set(orders)) \
            or sorted(orders) != list(range(len(values))):
        errors.append("metrics order must be a contiguous deterministic sequence")
    for duplicate in _duplicates(_names(values)):
        errors.append("metrics contains duplicate name: {}".format(duplicate))
    return errors


def _validate_metric_entry(index, entry):
    """Validate one metric entry and return its optional numeric order."""
    if not isinstance(entry, dict):
        return ["metrics[{}] must be an object".format(index)], None
    errors = []
    missing = sorted(METRIC_FIELDS - set(entry))
    if missing:
        errors.append("metrics[{}] missing fields: {}".format(
            index, ", ".join(missing)))
    labels = entry.get("labels")
    if not isinstance(labels, list):
        errors.append("metrics[{}].labels must be an array".format(index))
    elif any(not isinstance(label, str) for label in labels):
        errors.append("metrics[{}].labels must contain strings".format(index))
    elif labels != sorted(set(labels)):
        errors.append("metrics[{}].labels must be sorted and unique".format(
            index))
    order = entry.get("order")
    if not isinstance(order, int) or isinstance(order, bool):
        errors.append("metrics[{}].order must be an integer".format(index))
        order = None
    return errors, order


def _validate_reason_schema(inventory):
    """Validate contiguous reason discriminants and registry cardinality."""
    values = inventory.get("reason_codes")
    if not isinstance(values, list):
        return ["reason_codes must be an array"]
    errors = []
    discriminants = []
    if inventory.get("registry_count") != len(values):
        errors.append("inventory registry_count does not match array length")
    for index, entry in enumerate(values):
        if not isinstance(entry, dict):
            errors.append("reason_codes[{}] must be an object".format(index))
            continue
        discriminant = entry.get("discriminant")
        if isinstance(discriminant, int) and not isinstance(discriminant, bool):
            discriminants.append(discriminant)
        else:
            errors.append("reason_codes[{}].discriminant must be an integer".format(
                index))
        missing = sorted(REASON_FIELDS - set(entry))
        if missing:
            errors.append("reason_codes[{}] missing fields: {}".format(
                index, ", ".join(missing)))
    if discriminants != list(range(len(values))):
        errors.append("reason_codes discriminants must be contiguous from zero")
    for duplicate in _duplicates(_names(values)):
        errors.append("reason_codes contains duplicate name: {}".format(duplicate))
    return errors


def _validate_ffi_schema(inventory):
    """Validate FFI metadata, ABI versions, ordering, and uniqueness."""
    values = inventory.get("ffi_exports")
    if not isinstance(values, list):
        return ["ffi_exports must be an array"]
    errors = []
    names = []
    for index, entry in enumerate(values):
        if not isinstance(entry, dict):
            errors.append("ffi_exports[{}] must be an object".format(index))
            continue
        name = entry.get("name")
        if isinstance(name, str):
            names.append(name)
        else:
            errors.append("ffi_exports[{}].name must be a string".format(index))
        missing = sorted(FFI_FIELDS - set(entry))
        if missing:
            errors.append("ffi_exports[{}] missing fields: {}".format(
                index, ", ".join(missing)))
        if entry.get("abi_version") != inventory.get("ffi_abi_version"):
            errors.append("ffi_exports[{}] ABI version mismatch".format(index))
    if names != sorted(names):
        errors.append("ffi_exports must be sorted by name")
    for duplicate in _duplicates(names):
        errors.append("ffi_exports contains duplicate name: {}".format(duplicate))
    return errors


def validate_inventory_schema(inventory):
    """Return deterministic schema errors instead of raising KeyError."""
    if not isinstance(inventory, dict):
        return ["inventory must be a top-level object"]
    errors = _validate_inventory_header(inventory)
    active_errors, active_names = _validate_named_entries(
        inventory, "directives", DIRECTIVE_FIELDS)
    reject_errors, reject_names = _validate_named_entries(
        inventory, "reject_only_directives", REJECT_FIELDS)
    errors.extend(active_errors + reject_errors)
    if set(active_names) & set(reject_names):
        errors.append("directive appears in active and reject-only lists")
    errors.extend(_validate_otel_schema(inventory))
    errors.extend(_validate_dynconf_schema(inventory))
    errors.extend(_validate_metric_schema(inventory))
    errors.extend(_validate_reason_schema(inventory))
    errors.extend(_validate_ffi_schema(inventory))
    return errors


def _strip_c_comments(text):
    """Remove C comments before parsing the flat command registry."""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _directive_registry_block(text):
    """Extract the module's command registry, failing closed if it is incomplete."""
    marker = "static ngx_command_t ngx_http_markdown_filter_commands[] = {"
    if marker not in text:
        raise ValueError(COMMAND_REGISTRY_ERROR)
    block_start = text.index(marker)
    closing = text.find("\n};", block_start)
    if closing < 0:
        raise ValueError(COMMAND_REGISTRY_ERROR)
    return text[block_start:closing]


def _balanced_command_entries(block):
    """Split command rows while rejecting unbalanced braces."""
    entries = []
    depth = 0
    entry_start = None
    for index, char in enumerate(block):
        if char == "{":
            if depth == 1:
                entry_start = index + 1
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 1 and entry_start is not None:
                entries.append(block[entry_start:index])
                entry_start = None
    if depth != 1 or entry_start is not None:
        raise ValueError("malformed ngx_command_t registry")
    return entries


def _split_command_fields(entry):
    """Split one command row without treating commas inside casts as fields."""
    fields = []
    start = 0
    parens = 0
    for index, char in enumerate(entry):
        if char == "(":
            parens += 1
        elif char == ")":
            parens -= 1
        elif char == "," and parens == 0:
            fields.append(entry[start:index].strip())
            start = index + 1
    fields.append(entry[start:].strip())
    if len(fields) != 6:
        raise ValueError("malformed ngx_command_t row: {}".format(entry.strip()))
    return fields


def _command_args(flags):
    """Map NGINX command flags to the inventory's argument contract."""
    for token, value in (
        ("NGX_CONF_NOARGS", "no_args"),
        ("NGX_CONF_TAKE12", "1_or_2"),
        ("NGX_CONF_1MORE", "1_or_more"),
        ("NGX_CONF_ANY", "any"),
        ("NGX_CONF_FLAG", "flag"),
        ("NGX_CONF_TAKE1", "1"),
    ):
        if token in flags:
            return value
    return "unknown"


def _command_classification(name, handler):
    """Classify a directive as active, rejected, and/or OTel-specific."""
    if handler == "ngx_http_markdown_reject_otel_directive":
        return "reject_only", "reject_only"
    if handler in ("ngx_http_markdown_reject_removed_directive",
                   "ngx_http_markdown_reject_streaming_engine"):
        return "reject_only", "none"
    otel = "active" if name in ("markdown_otel", "markdown_otel_endpoint") else "none"
    return "active", otel


def _clean_c_comment(comment):
    """Return deterministic text lines from a C block comment."""
    lines = []
    for line in comment.splitlines():
        line = re.sub(r"^\s*\* ?", "", line).strip()
        lines.append(line)
    return lines


def _strip_parenthesized_suffix(syntax):
    """Remove a space-delimited, parenthesized syntax suffix."""
    if not syntax.endswith(")"):
        return syntax
    open_paren = syntax.rfind("(")
    if open_paren <= 0:
        return syntax
    prefix = syntax[:open_paren].rstrip(" \t")
    if len(prefix) == open_paren:
        return syntax
    suffix = syntax[open_paren + 1:-1]
    if any(char in "()\r\n" for char in suffix):
        return syntax
    return prefix


def _comment_syntax(lines, name):
    """Extract a directive syntax declaration from one adjacent comment."""
    name_line = next((index for index, line in enumerate(lines)
                      if line == name or line.startswith(name + " ")), None)
    if name_line is None:
        return None
    first = lines[name_line][len(name):].strip()
    parts = [first] if first else []
    index = name_line + 1
    stop_prefixes = ("Default:", "Context:", "Example:", MIGRATION_PREFIX,
                     "Security:", "Response formats:", "Public ")
    while index < len(lines) and lines[index] != "":
        candidate = lines[index]
        if candidate.startswith(stop_prefixes) or candidate.startswith(("-", "*")):
            break
        parts.append(candidate)
        index += 1
    syntax = _strip_parenthesized_suffix(" ".join(parts)).strip()
    return syntax or "(no args)"


def _comment_metadata_value(lines, prefixes):
    """Return the first non-empty value following one of the prefixes."""
    for line in lines:
        for prefix in prefixes:
            if not line.startswith(prefix):
                continue
            value = line[len(prefix):].lstrip(" \t")
            if value and "\r" not in value and "\n" not in value:
                return value.strip()
    return None


def _comment_public_metadata(lines):
    """Extract default, public syntax, and status annotations."""
    joined = "\n".join(lines)
    default_value = _comment_metadata_value(
        lines, ("Public Default:", "Default:"))
    public_default = _comment_metadata_value(lines, ("Public default:",))
    public_syntax = _comment_metadata_value(lines, ("Public syntax:",))
    if default_value is not None and public_default == default_value:
        public_default = None
    status_match = re.search(r"^Public status:\s*(\w+)$", joined, flags=re.M)
    return {
        "default": public_default or default_value,
        "syntax": public_syntax,
        "status": status_match.group(1) if status_match else None,
    }


def _comment_migration(lines):
    """Extract a migration target from one adjacent comment."""
    joined = "\n".join(lines)
    migration_text = _comment_metadata_value(lines, (MIGRATION_PREFIX,))
    if migration_text is None:
        for index, line in enumerate(lines):
            if line.startswith(MIGRATION_PREFIX):
                migration_text = _comment_metadata_value(
                    lines[index + 1:], ("",))
                break
    if migration_text:
        targets = re.findall(r"->[ \t]*(markdown_[a-z_]+)", migration_text)
        if targets:
            return targets[0]
    migrated_match = re.search(
        r"(?:Migrated to|replaced by)[ \t]+([^\.\r\n]+)(?:\.|$)",
        joined, flags=re.I)
    if migrated_match:
        return migrated_match.group(1).strip()
    if re.search(r"no direct (?:Config V2 )?equivalent|no replacement",
                 joined, flags=re.I):
        return "(removed, no replacement)"
    return None


def _directive_comment_metadata(text):
    """Extract public metadata from the command registry's adjacent comments."""
    block = _directive_registry_block(text)
    comments = [(match.end(), match.group(1)) for match in re.finditer(
        r"/\*(.*?)\*/", block, flags=re.S)]
    result = {}
    for name_match in re.finditer(r'ngx_string\("(markdown_[^"\\]+)"\)', block):
        row_start = block.rfind("{", 0, name_match.start())
        prior = [comment for end, comment in comments if end <= row_start]
        if not prior:
            continue
        lines = _clean_c_comment(prior[-1])
        metadata = _comment_public_metadata(lines)
        syntax = _comment_syntax(lines, name_match.group(1))
        result[name_match.group(1)] = {
            "default": metadata["default"],
            "syntax": metadata["syntax"] or syntax,
            "status": metadata["status"],
            "migration_target": _comment_migration(lines),
        }
    return result


def _hint_migration_target(text, hint_name):
    """Normalize a reject-only hint string to its migration target."""
    hint_match = next((match for match in re.finditer(
        r'static\s+u_char\s+([A-Za-z_]\w*)\[\]\s*=\s*'
        r'((?:"(?:\\.|[^"\\])*"\s*)+);', text, flags=re.S)
        if match.group(1) == hint_name), None)
    if hint_match is None:
        return None
    hint = "".join(re.findall(r'"((?:\\.|[^"\\])*)"', hint_match.group(2)))
    hint = hint.replace('\\"', '"')
    target = re.search(r'use\s+"([^"]+)"', hint)
    if target:
        return target.group(1).rstrip(";")
    if "no direct replacement" in hint:
        return "(removed, no replacement)"
    return "(not implemented)"


def _command_entry_metadata(name, flags, handler, fields, metadata, source_text):
    """Derive status, defaults, syntax, and migration from one command row."""
    classification, otel_classification = _command_classification(name, handler)
    source_metadata = (metadata or {}).get(name, {})
    status = source_metadata.get("status") or (
        "reject_only" if classification == "reject_only" else "active")
    if otel_classification == "active" and classification == "active":
        status = "experimental"
    migration_target = source_metadata.get("migration_target")
    if handler == "ngx_http_markdown_reject_streaming_engine":
        migration_target = "markdown_streaming"
    elif classification == "reject_only" and fields[5] != "NULL":
        migration_target = _hint_migration_target(source_text, fields[5]) \
            or migration_target
    elif classification == "reject_only" and migration_target is None:
        migration_target = "(not implemented)"
    default = source_metadata.get("default")
    if classification == "reject_only" and default is None:
        default = "(not applicable)"
    syntax = source_metadata.get("syntax") or {
        "flag": "on|off", "1": "<value>", "any": "<value>"}.get(
            _command_args(flags))
    return (classification, otel_classification, default, syntax, status,
            migration_target)


def _parse_command_entry(entry, metadata=None, source_text=""):
    """Parse one six-field ngx_command_t row into inventory-shaped metadata."""
    fields = _split_command_fields(entry)
    match = re.search(r'ngx_string\("([^"]+)"\)', fields[0])
    if not match:
        raise ValueError("directive row has no name")
    name = match.group(1)
    flags = "".join(fields[1].split())
    handler = fields[2]
    context = [label for token, label in (
        ("NGX_HTTP_MAIN_CONF", "http"),
        ("NGX_HTTP_SRV_CONF", "server"),
        ("NGX_HTTP_LOC_CONF", "location"),
    ) if token in flags]
    (classification, otel_classification, default, syntax, status,
     migration_target) = _command_entry_metadata(
         name, flags, handler, fields, metadata, source_text)
    return {
        "name": name, "context": context, "args": _command_args(flags),
        "handler": handler, "conf_offset": fields[3],
        "source_flags": flags,
        "post": None if fields[5] == "NULL" else fields[5],
        "classification": classification,
        "otel_classification": otel_classification,
        "default": default,
        "syntax": syntax or ("(no args)" if _command_args(flags) == "no_args" else None),
        "status": status,
        "migration_target": migration_target,
    }


def _command_contracts(text):
    """Parse the flat ngx_command_t registry without accepting malformed rows."""
    block = _directive_registry_block(_strip_c_comments(text))
    metadata = _directive_comment_metadata(text)
    contracts = {}
    names = []
    for entry in _balanced_command_entries(block):
        if "ngx_string(" not in entry:
            continue
        contract = _parse_command_entry(entry, metadata, text)
        names.append(contract["name"])
        contracts[contract["name"]] = contract
    if not contracts:
        raise ValueError("directive registry contains no commands")
    duplicates = _duplicates(names)
    if duplicates:
        raise ValueError("duplicate directive names: {}".format(", ".join(duplicates)))
    return contracts


def extract_directive_contract_from_c():
    """Extract directive metadata from the live C command registry."""
    return _command_contracts(read_text(DIRECTIVES_PATH))


def extract_directives_from_c():
    """Return live directive names in deterministic order."""
    return sorted(extract_directive_contract_from_c())


def extract_reason_codes_from_rust():
    """Extract Rust reason discriminants and variant names."""
    text = read_text(REASON_CODE_PATH)
    found = {}
    for match in REASON_CODE_RE.finditer(text):
        found[int(match.group(2))] = match.group(1)
    return found


def _reason_strings(text):
    """Extract the string representation used by the Rust reason registry."""
    as_str_body = text.split("pub fn as_str", 1)[1].split("pub fn metric_key", 1)[0]
    return dict(re.findall(r"ReasonCode::(\w+)\s*=>\s*\"([^\"]+)\"", as_str_body))


def _reason_metrics(text):
    """Extract metric keys while handling grouped Rust match arms."""
    metric_body = text.split("pub fn metric_key", 1)[1].split("pub fn log_callsite", 1)[0]
    metrics = {}
    pending_variants = []
    for line in metric_body.splitlines():
        lhs, separator, rhs = line.partition("=>")
        pending_variants.extend(re.findall(r"ReasonCode::(\w+)", lhs))
        if not separator:
            continue
        metric_match = re.match(r'\s*"([^\"]+)"', rhs)
        if metric_match is None:
            continue
        for variant in pending_variants:
            metrics[variant] = metric_match.group(1)
        pending_variants = []
    return metrics


def _reason_c_name(string):
    """Normalize Rust reason strings to the C accessor naming convention."""
    # C keeps the historical *_err storage names while Rust exposes the
    # clearer *_error strings; this mapping makes that intentional alias
    # explicit instead of treating it as drift.
    return REASON_C_ACCESSOR_ALIASES.get(string, string)


def extract_reason_contract_from_rust():
    """Reconcile Rust reasons with C storage and inventory-shaped metadata."""
    text = read_text(REASON_CODE_PATH)
    variants = extract_reason_codes_from_rust()
    strings = _reason_strings(text)
    metrics = _reason_metrics(text)
    c_text = read_text(REASON_C_PATH)
    c_accessors = set(re.findall(r"static\s+ngx_str_t\s+reason_str_(\w+)", c_text))
    result = {}
    for discriminant, variant in variants.items():
        string = strings.get(variant)
        if string is None:
            raise ValueError("reason variant missing as_str mapping: {}".format(variant))
        c_name = _reason_c_name(string)
        result[discriminant] = {
            "discriminant": discriminant, "name": variant, "string": string,
            "metric_key": metrics.get(variant), "c_accessor": "reason_str_" + c_name,
        }
        if c_name not in c_accessors:
            raise ValueError("C reason storage missing: reason_str_{}".format(c_name))
    return result


def extract_dynconf_keys_from_c():
    """Return the Rust-owned JSON schema keys exposed through the C bridge.

    The production C path deliberately does not duplicate the JSON key table:
    Rust owns parsing and validation, while C owns bounded file I/O and the
    atomic snapshot commit.  Keep this check tied to both sides of that
    boundary so a stale C-only key scan cannot mistake the legacy test parser
    for the production contract.
    """
    c_text = read_text(DYNCONF_PATH)
    rust_text = read_text(os.path.join(
        ROOT, "components", "rust-converter", "src", "dynconf", "schema.rs"))
    if "ngx_http_markdown_dynconf_apply_ffi_result" not in c_text:
        raise ValueError("C dynconf path does not apply the typed FFI result")
    if "KNOWN_KEYS" not in rust_text or "schema_version" not in rust_text:
        raise ValueError("Rust dynconf schema does not declare KNOWN_KEYS")
    return sorted(DYNCONF_KEYS)


def _dynconf_type_allowed(key):
    """Return the parser-backed type and allowed values for one key."""
    if key in ("filter", "prune_noise"):
        return "flag", ["on", "off"]
    if key == "log_verbosity":
        return "enum", ["error", "warn", "info", "debug"]
    if key == "error_policy":
        return "enum", ["pass", "fail_closed", "status 429", "status 503"]
    if key == "streaming_buffer":
        return "size", []
    return "version", ["1"]


def _dynconf_contract_entry(key, has_per_key_staging, required, duplicate):
    """Build one dynamic-configuration contract from parser evidence."""
    typ, allowed = _dynconf_type_allowed(key)
    return {
        "name": key, "type": typ, "allowed_values": allowed,
        "default": "required" if required else "inherited",
        "inheritance": "none" if key == "schema_version" else (
            "per-key" if has_per_key_staging else "unknown"),
        "required": required, "dynamic": key != "schema_version",
        "unknown_key": "reject", "duplicate": duplicate,
    }


def extract_dynconf_contract_from_c():
    """Build the Rust JSON parser/C atomic-apply contract."""
    text = read_text(DYNCONF_PATH)
    keys = extract_dynconf_keys_from_c()
    schema = read_text(os.path.join(
        ROOT, "components", "rust-converter", "src", "dynconf", "schema.rs"))
    parser = read_text(os.path.join(
        ROOT, "components", "rust-converter", "src", "dynconf", "parser.rs"))
    ffi = read_text(os.path.join(
        ROOT, "components", "rust-converter", "src", "dynconf", "ffi.rs"))
    if "unknown key" not in schema or "DYNCONF_ERR_UNKNOWN_KEY" not in ffi:
        raise ValueError("dynconf parser does not reject unknown keys")
    if "required field 'schema_version' is missing" not in schema:
        raise ValueError("dynconf schema_version is not required")
    if "DYNCONF_ERR_DUPLICATE_KEY" not in ffi or "DuplicateKey" not in parser:
        raise ValueError("dynconf parser does not reject duplicate keys")
    has_per_key_staging = (
        "watcher->staging_snapshot = watcher->active_snapshot" in text
        and "snapshot->" in text
        and "ngx_http_markdown_dynconf_apply" in text
    )
    required = True
    duplicate = "reject"
    return {
        key: _dynconf_contract_entry(
            key, has_per_key_staging, key == "schema_version" and required,
            duplicate)
        for key in keys
    }


def _metric_contract_from_text(text):
    """Extract metric names, types, labels, and source order from C text."""
    text = _strip_c_comments(text)
    # The C renderer splits long output lines into adjacent string literals;
    # join those literals before inspecting labels and TYPE declarations.
    text = re.sub(r'"\s*"', '', text)
    types = dict(re.findall(
        r"# TYPE\s+(nginx_markdown_[a-z0-9_]+)\s+(counter|gauge|histogram)",
        text))
    # Histogram _bucket/_sum/_count series are samples of one family; only
    # TYPE declarations identify the frozen family set.
    names = list(types)
    result = {}
    for order, name in enumerate(names):
        metric_type = types.get(name, "counter" if name.endswith("_total") else "gauge")
        labels = []
        series_name = re.escape(name)
        if metric_type == "histogram":
            series_name += r"(?:_(?:bucket|sum|count))?"
        for match in re.finditer(series_name + r"\{([^}]*)\}", text):
            labels.extend(re.findall(r"([a-z][a-z0-9_]*)\s*=", match.group(1)))
        labels = [label for label in labels if label != "le"]
        normalized_labels = sorted(set(labels))
        result[name] = {
            "name": name, "type": metric_type,
            "labels": normalized_labels, "order": order,
            "bounded_cardinality": "bounded" if normalized_labels else "fixed",
        }
    return result


def extract_metric_contract_from_c():
    """Extract the Prometheus metric contract from the C renderer."""
    return _metric_contract_from_text(read_text(METRICS_PATH))


def extract_metric_names_from_c():
    """Return live metric names in deterministic order."""
    return sorted(extract_metric_contract_from_c())


def _split_ffi_params(value):
    """Split a flat FFI parameter list without losing declaration order."""
    params = []
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char in "(<[":
            depth += 1
        elif char in ")>]":
            depth -= 1
        elif char == "," and depth == 0:
            params.append(value[start:index].strip())
            start = index + 1
    tail = value[start:].strip()
    if tail:
        params.append(tail)
    return params


def _rust_pointer_parts(value):
    """Return a Rust pointer qualifier and pointee, if present."""
    if value.startswith("*const "):
        return "const", value[len("*const "):]
    if value.startswith("*mut "):
        return "mut", value[len("*mut "):]
    return None


def _canonical_ffi_type(value, language):
    """Normalize Rust/C FFI types for ABI-relevant comparison."""
    value = _normalize_ffi_fragment(value)
    if language == "rust":
        if value == "()":
            return "void"
        pointer_parts = _rust_pointer_parts(value)
        if pointer_parts is not None:
            qualifier, pointee = pointer_parts
            return "ptr:{}:{}".format(
                qualifier, _canonical_ffi_type(pointee, language))
        value = {"u8": "u8", "u16": "u16", "u32": "u32", "u64": "u64",
                 "usize": "usize", "isize": "isize", "bool": "bool"}.get(
                     value, value)
        return value

    value = re.sub(r"\s+", " ", value).strip()
    pointer_depth = value.count("*")
    if pointer_depth:
        const_pointee = bool(re.search(r"\bconst\b", value))
        value = re.sub(r"\bconst\b", "", value)
        value = value.replace("*", "").strip()
    value = re.sub(r"^struct\s+", "", value)
    value = {
        "void": "void", "uint8_t": "u8", "uint16_t": "u16",
        "uint32_t": "u32", "uint64_t": "u64", "uintptr_t": "usize",
        "size_t": "usize", "int8_t": "i8", "int16_t": "i16",
        "int32_t": "i32", "int64_t": "i64", "intptr_t": "isize",
        "bool": "bool",
    }.get(value, value)
    if pointer_depth:
        qualifiers = ["mut"] * pointer_depth
        if const_pointee:
            qualifiers[-1] = "const"
        for qualifier in reversed(qualifiers):
            value = "ptr:{}:{}".format(qualifier, value)
    return value


def _rust_ffi_param_types(params):
    """Return ABI-normalized Rust parameter types in declaration order."""
    result = []
    for param in _split_ffi_params(params):
        if ":" not in param:
            raise ValueError("malformed Rust FFI parameter: {}".format(param))
        _, value = param.split(":", 1)
        result.append(_canonical_ffi_type(value, "rust"))
    return result


def _is_ascii_c_identifier_char(char):
    """Return whether a character can occur in a C identifier."""
    return (char == "_" or "A" <= char <= "Z" or "a" <= char <= "z"
            or "0" <= char <= "9")


def _strip_c_parameter_name(param):
    """Remove a trailing C parameter name without backtracking regexes."""
    name_start = len(param)
    while (name_start > 0
           and _is_ascii_c_identifier_char(param[name_start - 1])):
        name_start -= 1
    if (name_start == len(param) or name_start == 0
            or param[name_start] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ_"
            + "abcdefghijklmnopqrstuvwxyz"
            or param[name_start - 1] not in "* \t"):
        return param
    return param[:name_start].rstrip(" \t")


def _c_ffi_param_types(params):
    """Return ABI-normalized C parameter types in declaration order."""
    result = []
    for param in _split_ffi_params(params):
        param = _normalize_ffi_fragment(param)
        if param == "void":
            continue
        # C prototypes name the parameter last.  Removing only that token
        # preserves pointer const/mutability and integer-width typedefs.
        param_type = _strip_c_parameter_name(param)
        result.append(_canonical_ffi_type(param_type, "c"))
    return result


def _extract_c_ffi_prototypes(header):
    """Parse all C declarations for exported markdown_* functions."""
    text = _strip_c_comments(header)
    prototypes = {}
    for name_match in re.finditer(
            r"\b(markdown_\w*)[ \t]*\(", text, flags=re.ASCII):
        start = text.rfind(";", 0, name_match.start()) + 1
        end = text.find(");", name_match.end())
        if end < 0:
            raise ValueError("unterminated C FFI prototype")
        declaration = text[start:end + 1]
        declaration = re.sub(
            r"(?m)^[ \t]*#[^\r\n]*(?:\r?\n|$)", "", declaration)
        declaration_name = re.search(
            r"\b(markdown_\w*)[ \t]*\(", declaration, flags=re.ASCII)
        if declaration_name is None:
            continue
        name = declaration_name.group(1)
        return_type = declaration[:declaration_name.start()].strip()
        params = declaration[declaration_name.end():-1].strip()
        prototypes[name] = {
            "return_type": _canonical_ffi_type(return_type, "c"),
            "params": _c_ffi_param_types(params),
        }
    if not prototypes:
        raise ValueError("generated FFI header contains no markdown_* prototypes")
    return prototypes


def extract_ffi_contract_from_rust():
    """Extract Rust exports and verify their generated-header ABI surface."""
    result = {}
    for path in FFI_PATHS:
        text = read_text(path)
        for match in FFI_FN_RE.finditer(text):
            params = _normalize_ffi_fragment(match.group(3))
            return_type = _normalize_ffi_fragment(match.group(4) or "()")
            name = match.group(2)
            result[name] = {
                "name": name,
                "signature": "{}({}) -> {}".format(name, params, return_type),
                "params": params,
                "return_type": return_type,
                "safety": "unsafe" if match.group(1) else "safe",
                "abi_version": 1,
                "generated_header": "components/rust-converter/include/markdown_converter.h",
            }
    header = read_text(FFI_HEADER_PATH)
    # The generated header is the C-facing ABI boundary, so source-only
    # exports are incomplete even when Rust parsing succeeds.
    abi_match = re.search(r"#define\s+MARKDOWN_ABI_VERSION\s+(\d+)", header)
    if not abi_match:
        raise ValueError("generated FFI header has no MARKDOWN_ABI_VERSION")
    c_prototypes = _extract_c_ffi_prototypes(header)
    if set(result) != set(c_prototypes):
        missing = sorted(set(result) - set(c_prototypes))
        extra = sorted(set(c_prototypes) - set(result))
        raise ValueError(
            "Rust/C FFI export set mismatch: missing_header={}, extra_header={}".format(
                ",".join(missing), ",".join(extra)))
    for name, item in result.items():
        item["abi_version"] = int(abi_match.group(1))
        rust_params = _rust_ffi_param_types(item["params"])
        rust_return = _canonical_ffi_type(item["return_type"], "rust")
        c_params = c_prototypes[name]["params"]
        c_return = c_prototypes[name]["return_type"]
        if rust_params != c_params:
            raise ValueError(
                "generated FFI header parameter mismatch for {}: Rust={!r} C={!r}".format(
                    name, rust_params, c_params))
        if rust_return != c_return:
            raise ValueError(
                "generated FFI header return mismatch for {}: Rust={!r} C={!r}".format(
                    name, rust_return, c_return))
    return result


def extract_ffi_exports_from_rust():
    """Return live FFI export names in deterministic order."""
    return sorted(extract_ffi_contract_from_rust())


def _compare_maps(label, inventory_map, actual_map, fields):
    """Compare declared metadata, not only names, for one public-surface group."""
    drift = []
    inv_names = set(inventory_map)
    actual_names = set(actual_map)
    for name in sorted(actual_names - inv_names):
        drift.append("{} in source but not in inventory: {}".format(label, name))
    for name in sorted(inv_names - actual_names):
        drift.append("{} in inventory but not in source: {}".format(label, name))
    for name in sorted(inv_names & actual_names):
        for field in fields:
            if inventory_map[name].get(field) != actual_map[name].get(field):
                drift.append("{} {} mismatch for {}: inventory={!r} source={!r}".format(
                    label, field, name, inventory_map[name].get(field), actual_map[name].get(field)))
    return drift


def _inventory_directive_entries(inventory):
    """Merge active and reject-only directive entries by name."""
    entries = {}
    for entry in inventory.get("directives", []) + inventory.get("reject_only_directives", []):
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            entries[entry["name"]] = entry
    return entries


def _merge_otel_group(entries, values):
    """Merge OTel classifications while rejecting conflicting declarations."""
    for entry in values:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            continue
        name = entry["name"]
        if name in entries:
            if entry.get("otel_classification") != entries[name].get("otel_classification"):
                raise ValueError("directive OTel classification mismatch: {}".format(name))
        else:
            entries[name] = entry
    return entries


def _merge_otel_directive_entries(inventory, entries):
    """Add OTel directive metadata to the common directive map."""
    otel = inventory.get("otel", {})
    for group in ("directives", "reject_only"):
        _merge_otel_group(entries, otel.get(group, []))
    return entries


def check_directive_contract(inventory, actual_contract):
    """Compare directive behavior metadata against the source registry."""
    try:
        entries = _merge_otel_directive_entries(
            inventory, _inventory_directive_entries(inventory))
    except ValueError as exc:
        return [str(exc)]
    active_fields = ("classification", "context", "args", "handler", "conf_offset",
                     "source_flags", "post", "otel_classification", "default",
                     "syntax", "status", "migration_target")
    active_entries = {
        name: entry for name, entry in entries.items()
        if entry.get("classification") != "reject_only"
    }
    active_actual = {
        name: contract for name, contract in actual_contract.items()
        if contract.get("classification") != "reject_only"
    }
    reject_actual = {
        name: contract for name, contract in actual_contract.items()
        if contract.get("classification") == "reject_only"
    }
    drift = _compare_maps("directive", active_entries, active_actual, active_fields)
    if reject_actual:
        drift.append(
            "removed directives must be absent from the live registry: {}".format(
                ", ".join(sorted(reject_actual))))
    # Reject-only entries are a compatibility/documentation contract.  Their
    # expected runtime behavior is NGINX's standard unknown-directive error,
    # so they must not be represented by live command-table stubs.
    return drift


def check_directives(inventory, actual_names):
    """Compare the declared directive name set with live source names."""
    inv = set(_names(inventory.get("directives", [])))
    inv.update(_names(inventory.get("otel", {}).get("directives", [])))
    actual = set(actual_names)
    drift = []
    if actual - inv:
        drift.append("directives in source but not in inventory: {}".format(", ".join(sorted(actual - inv))))
    reject_names = set(_names(inventory.get("reject_only_directives", [])))
    reject_names.update(_names(inventory.get("otel", {}).get("reject_only", [])))
    if actual & reject_names:
        drift.append("reject-only directives present in source: {}".format(
            ", ".join(sorted(actual & reject_names))))
    return drift


def check_reason_codes(inventory, actual_codes):
    """Compare reason discriminants and names with the Rust registry."""
    inv_by_disc = {rc["discriminant"]: rc["name"] for rc in inventory.get("reason_codes", [])}
    drift = []
    for disc in sorted(set(actual_codes) - set(inv_by_disc)):
        drift.append("reason code in source but not in inventory: {}={}".format(disc, actual_codes[disc]))
    for disc in sorted(set(inv_by_disc) - set(actual_codes)):
        drift.append("reason code in inventory but not in source: {}={}".format(disc, inv_by_disc[disc]))
    for disc in sorted(set(actual_codes) & set(inv_by_disc)):
        if actual_codes[disc] != inv_by_disc[disc]:
            drift.append("reason code name mismatch at discriminant {}: inventory={} source={}".format(
                disc, inv_by_disc[disc], actual_codes[disc]))
    return drift


def check_reason_contract(inventory, actual_contract):
    """Compare reason strings, metrics, C accessors, and registry size."""
    inv = {item.get("discriminant"): item for item in inventory.get("reason_codes", [])}
    drift = _compare_maps("reason code", inv, actual_contract,
                          ("name", "string", "metric_key", "c_accessor"))
    if inventory.get("registry_count") != len(actual_contract):
        drift.append("reason code registry_count mismatch: inventory={} source={}".format(
            inventory.get("registry_count"), len(actual_contract)))
    return drift


def check_dynconf_keys(inventory, actual_keys):
    """Compare declared dynamic-configuration names with live parser keys."""
    inv = set(_names(inventory.get("dynconf_keys", [])))
    actual = set(actual_keys)
    drift = []
    if actual - inv:
        drift.append("dynconf keys in source but not in inventory: {}".format(", ".join(sorted(actual - inv))))
    if inv - actual:
        drift.append("dynconf keys in inventory but not in source: {}".format(", ".join(sorted(inv - actual))))
    return drift


def check_dynconf_contract(inventory, actual_contract):
    """Compare dynamic-configuration behavior metadata with the C parser."""
    inv = {item.get("name"): item for item in inventory.get("dynconf_keys", [])}
    return _compare_maps("dynconf key", inv, actual_contract,
                         ("type", "allowed_values", "default", "inheritance",
                          "required", "dynamic", "unknown_key", "duplicate"))


def check_metrics(inventory, actual_names):
    """Compare the declared metric name set with live renderer names."""
    inv = set(_names(inventory.get("metrics", [])))
    actual = set(actual_names)
    drift = []
    if actual - inv:
        drift.append("metrics in source but not in inventory: {}".format(", ".join(sorted(actual - inv))))
    if inv - actual:
        drift.append("metrics in inventory but not in source: {}".format(", ".join(sorted(inv - actual))))
    return drift


def check_metric_contract(inventory, actual_contract):
    """Compare metric type, labels, and deterministic render order."""
    inv = {item.get("name"): item for item in inventory.get("metrics", [])}
    return _compare_maps("metric", inv, actual_contract,
                         ("type", "labels", "order", "bounded_cardinality"))


def check_ffi_exports(inventory, actual_exports):
    """Compare the declared FFI export set with Rust source exports."""
    inv = set(_names(inventory.get("ffi_exports", [])))
    actual = set(actual_exports)
    drift = []
    if actual - inv:
        drift.append("FFI exports in source but not in inventory: {}".format(", ".join(sorted(actual - inv))))
    if inv - actual:
        drift.append("FFI exports in inventory but not in source: {}".format(", ".join(sorted(inv - actual))))
    return drift


def check_ffi_contract(inventory, actual_contract):
    """Compare FFI signatures and ABI metadata with Rust and its header."""
    inv = {item.get("name"): _normalize_ffi_entry(item)
           for item in inventory.get("ffi_exports", [])}
    actual = {name: _normalize_ffi_entry(item)
              for name, item in actual_contract.items()}
    return _compare_maps("FFI export", inv, actual,
                         ("signature", "params", "return_type", "safety", "abi_version", "generated_header"))


def main():
    """Validate inventory schema first, then compare it with live source contracts."""
    parser = argparse.ArgumentParser(description="Detect public surface contract drift")
    parser.add_argument("--inventory", default=INVENTORY_PATH, help="Path to public-surface-inventory.json")
    args = parser.parse_args()
    all_drift = []
    try:
        inventory = load_inventory(args.inventory)
        all_drift.extend(validate_inventory_schema(inventory))
        # Do not inspect source contracts until the declaration itself is
        # structurally valid; malformed inventory data must fail closed.
        if not all_drift:
            all_drift.extend(check_directive_contract(inventory, extract_directive_contract_from_c()))
            all_drift.extend(check_reason_contract(inventory, extract_reason_contract_from_rust()))
            all_drift.extend(check_dynconf_contract(inventory, extract_dynconf_contract_from_c()))
            all_drift.extend(check_metric_contract(inventory, extract_metric_contract_from_c()))
            all_drift.extend(check_ffi_contract(inventory, extract_ffi_contract_from_rust()))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        all_drift.append("public surface source/schema parse error: {}".format(exc))
    if all_drift:
        for message in sorted(set(all_drift)):
            print("DRIFT: {}".format(message), file=sys.stderr)
        print("public surface contract drift detected ({} issue(s))".format(len(set(all_drift))), file=sys.stderr)
        return 1
    print("public surface inventory contract is synchronized with source code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
