"""Extract one revision's public contract from tracked repository sources.

Every surface is read from the same source revision.  Nothing here reads
``docs/`` prose as evidence of behaviour: prose records what the release
promised, and the implementation files record what it did.  The behavioural
``http`` surface is bound to the contract test files that actually execute the
request path, and the manifest records the binding rather than a claim.
"""


from __future__ import annotations

import json
import re
import tomllib
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .model import ContractError, digest_of, require, sorted_by
from .sources import SourceReader

DIRECTIVES_IMPL = "components/nginx-module/src/ngx_http_markdown_config_directives_impl.h"
DIRECTIVE_NAMES = "components/nginx-module/src/ngx_http_markdown_directive_names.h"
CONFIG_MERGE_SOURCES = (
    "components/nginx-module/src/ngx_http_markdown_config_merge_impl.h",
    "components/nginx-module/src/ngx_http_markdown_filter_module.h",
)
METRICS_REGISTRY = "schemas/metrics-v1.registry.json"
METRICS_RENDERER = "components/nginx-module/src/ngx_http_markdown_metrics_v1_renderer.h"
REASON_REGISTRY = "components/rust-converter/reason_registry.toml"
DIAGNOSTICS_SCHEMA = "schemas/diagnostics.schema.json"
FFI_HEADER = "components/rust-converter/include/markdown_converter.h"
RELEASE_MATRIX = "tools/release-matrix.json"
NFPM_CONFIG = "packaging/nfpm/nfpm.yaml"
DOC_FFI_INVENTORY = "docs/harness/public-surface-inventory.json"

# Behavioural contract for the HTTP surface.  Each entry names the observable
# behaviour and the tracked tests that prove it.  The gate digests these files,
# so an edit that changes an assertion cannot pass silently; the tests
# themselves remain the proof that the behaviour holds.
HTTP_CONTRACT_TESTS = {
    "accept_negotiation": (
        "components/nginx-module/tests/unit/accept_negotiation_test.c",
        "components/nginx-module/tests/unit/accept_production_test.c",
        "tools/e2e/verify_accept_negotiation_e2e.sh",
    ),
    "conditional_304": (
        "components/nginx-module/tests/unit/conditional_production_test.c",
        "tools/e2e/verify_conditional_requests_e2e.sh",
    ),
    "head_request": (
        "components/nginx-module/tests/unit/head_request_test.c",
    ),
    "header_plan_vary_content_type": (
        "components/nginx-module/tests/unit/header_plan_test.c",
        "components/nginx-module/tests/unit/header_plan_apply_test.c",
        "components/nginx-module/tests/unit/header_ordering_test.c",
    ),
    "pre_post_commit": (
        "components/nginx-module/tests/unit/header_plan_prepare_commit_test.c",
        "components/nginx-module/tests/unit/stream_commit_test.c",
    ),
    "status_and_range": (
        "tools/e2e/verify_status_codes_e2e.sh",
    ),
    "auth_and_cache": (
        "tools/e2e/verify_auth_cache_e2e.sh",
    ),
}

UNIT_SUITE_TARGET = "make test-nginx-unit"
# Executor for each HTTP behaviour group.  These targets run in CI, not inside
# this gate: the gate proves the contract text did not move, the targets prove
# the behaviour still holds.
HTTP_EXECUTION_TARGETS = {
    "accept_negotiation": f"{UNIT_SUITE_TARGET} / bash tools/e2e/verify_accept_negotiation_e2e.sh",
    "conditional_304": f"{UNIT_SUITE_TARGET} / bash tools/e2e/verify_conditional_requests_e2e.sh",
    "head_request": UNIT_SUITE_TARGET,
    "header_plan_vary_content_type": UNIT_SUITE_TARGET,
    "pre_post_commit": UNIT_SUITE_TARGET,
    "status_and_range": "bash tools/e2e/verify_status_codes_e2e.sh",
    "auth_and_cache": "bash tools/e2e/verify_auth_cache_e2e.sh",
}

_CONTEXT_FLAGS = (
    ("NGX_HTTP_MAIN_CONF", "http"),
    ("NGX_HTTP_SRV_CONF", "server"),
    ("NGX_HTTP_LOC_CONF", "location"),
)

_MACRO_DEFINITION_RE = re.compile(
    r'^#define\s+(NGX_HTTP_MARKDOWN_DIRECTIVE_[A-Z0-9_]+)\s+\\\s*"([^"]+)"',
    re.MULTILINE,
)
_MACRO_USE_RE = re.compile(r'ngx_string\((NGX_HTTP_MARKDOWN_DIRECTIVE_[A-Z0-9_]+)\)')
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_NAME_RE = re.compile(r'ngx_string\("([^"]+)"\)')


def _strip_comments(text: str) -> str:
    """Remove C comments before splitting a command row into fields."""
    return re.sub(r"//[^\n]*", "", _COMMENT_RE.sub("", text))


def _mask_comments(text: str) -> str:
    """Blank out comments while preserving character offsets."""
    return _COMMENT_RE.sub(lambda match: " " * len(match.group(0)), text)


def _expand_macros(text: str, names_text: str) -> str:
    """Substitute directive-name macros with their literal spellings."""
    definitions = dict(_MACRO_DEFINITION_RE.findall(names_text))
    require(
        len(definitions) >= 20,
        "directive name inventory defines fewer than 20 names",
    )
    return _MACRO_USE_RE.sub(
        lambda match: f'ngx_string("{definitions.get(match.group(1), match.group(1))}")',
        text,
    )


def _registry_block(text: str) -> str:
    """Return the command-table body, failing closed when it is incomplete."""
    marker = "static ngx_command_t ngx_http_markdown_filter_commands[] = {"
    index = text.find(marker)
    require(index >= 0, "ngx_command_t registry is missing")
    closing = text.find("\n};", index)
    require(closing >= 0, "ngx_command_t registry is unterminated")
    return text[index:closing]


def _entry_spans(block: str) -> List[tuple]:
    """Return ``(open_brace, end)`` spans for every command row."""
    masked = _mask_comments(block)
    spans: List[tuple] = []
    depth = 0
    start: Optional[int] = None
    for index, char in enumerate(masked):
        if char == "{":
            if depth == 1:
                start = index
            depth += 1
        elif char == "}":
            depth -= 1
            require(depth >= 0, "malformed ngx_command_t registry")
            if depth == 1 and start is not None:
                spans.append((start, index))
                start = None
    require(depth == 1 and start is None, "malformed ngx_command_t registry")
    require(len(spans) > 0, "ngx_command_t registry has no entries")
    return spans


def _split_fields(entry: str) -> List[str]:
    """Split one command row on top-level commas."""
    fields: List[str] = []
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
    require(len(fields) == 6, f"malformed ngx_command_t row: {entry.strip()}")
    return fields


def _flag_tokens(flags: str) -> set:
    """Split a command-flag expression into exact tokens."""
    return {token.strip() for token in flags.split("|") if token.strip()}


def _args_contract(flags: str) -> str:
    """Map NGINX command flags to the inventory argument contract."""
    tokens = _flag_tokens(flags)
    return next(
        (
            value
            for token, value in (
                ("NGX_CONF_NOARGS", "no_args"),
                ("NGX_CONF_TAKE12", "1_or_2"),
                ("NGX_CONF_1MORE", "1_or_more"),
                ("NGX_CONF_ANY", "any"),
                ("NGX_CONF_FLAG", "flag"),
                ("NGX_CONF_TAKE1", "1"),
            )
            if token in tokens
        ),
        "unknown",
    )


def _context_contract(flags: str) -> List[str]:
    """Return the directive contexts declared by the command flags."""
    tokens = _flag_tokens(flags)
    return [name for token, name in _CONTEXT_FLAGS if token in tokens]


def _preceding_comment(block: str, entry_brace: int) -> str:
    """Return the nearest comment that ends before a command row."""
    candidate = ""
    for match in _COMMENT_RE.finditer(block, 0, entry_brace):
        if block[match.end():entry_brace].strip() == "":
            candidate = match.group(0)
    return candidate


def _comment_lines(comment: str) -> List[str]:
    """Return normalized text lines of a C block comment."""
    body = comment.strip()
    body = body.removeprefix("/*")
    body = body.removesuffix("*/")
    lines = []
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("*"):
            line = line[1:].strip()
        lines.append(line)
    return lines


_SYNTAX_NOTE_MARKERS = ("Config V2", "0.9.")


def _strip_syntax_note(syntax: str) -> str:
    """Remove a trailing version annotation without an ambiguous pattern."""
    if not syntax.endswith(")"):
        return syntax
    start = syntax.rfind("(")
    if start <= 0:
        return syntax
    note = syntax[start:]
    if any(marker in note for marker in _SYNTAX_NOTE_MARKERS):
        return syntax[:start].rstrip()
    return syntax


def _comment_syntax(lines: List[str], name: str) -> str:
    """Return the documented value syntax for a directive."""
    return next(
        (
            _strip_syntax_note(line[len(name) :].strip())
            for line in lines
            if line and line.startswith(name) is not False
        ),
        "",
    )


def _comment_value(lines: List[str], label: str) -> Optional[str]:
    """Return the documented value of one ``Label: value`` comment line."""
    prefix = label.lower()
    for line in lines:
        lowered = line.lower()
        if lowered.startswith(prefix):
            return line[len(label):].strip()
    return None


def _documented_default(lines: List[str]) -> str:
    """Return the operator-facing default, preferring ``Public default``."""
    for label in ("Public default:", "Default:"):
        if value := _comment_value(lines, label):
            return value
    return ""


def _documented_context(lines: List[str]) -> List[str]:
    """Return the documented contexts when the comment records them."""
    value = _comment_value(lines, "Context:")
    if not value:
        return []
    head = value.split("(", 1)[0]
    return [part.strip() for part in head.split(",") if part.strip()]


def _directive_entry(name: str, fields: List[str], comment: str) -> Dict[str, Any]:
    """Build one directive contract entry."""
    flags = fields[1]
    lines = _comment_lines(comment)
    syntax = _comment_syntax(lines, name)
    if not syntax and "NGX_CONF_NOARGS" in flags:
        syntax = "(no args)"
    return {
        "name": name,
        "context": _context_contract(flags),
        "documented_context": _documented_context(lines),
        "args": _args_contract(flags),
        "handler": fields[2],
        "conf_offset": fields[3],
        "source_flags": flags,
        "syntax": syntax,
        "default": _documented_default(lines),
        "documentation": " ".join(line for line in lines if line),
    }


def extract_directives(reader: SourceReader) -> Dict[str, Any]:
    """Extract the command-table contract for every registered directive."""
    impl = reader.read_text(DIRECTIVES_IMPL)
    names = reader.read_text(DIRECTIVE_NAMES)
    block = _registry_block(_expand_macros(impl, names))
    entries: List[Dict[str, Any]] = []
    for brace, end in _entry_spans(block):
        fields = _split_fields(_strip_comments(block[brace + 1:end]))
        match = _NAME_RE.search(fields[0])
        if match is None:
            raise ContractError("command row has no directive name")
        name = match.group(1)
        require(
            name.startswith("markdown_"),
            f"unexpected directive name outside the markdown_ namespace: {name}",
        )
        entries.append(_directive_entry(name, fields, _preceding_comment(block, brace)))
    names_seen = [entry["name"] for entry in entries]
    require(
        len(names_seen) == len(set(names_seen)),
        "duplicate directive names in the command table",
    )
    declared = sorted(set(re.findall(r'"([^"]+)"', names)))
    require(
        set(names_seen) <= set(declared),
        "command table registers a name absent from the directive name inventory",
    )
    return {
        "count": len(entries),
        "entries": sorted_by(entries, "name"),
        "declared_names": sorted(declared),
        "runtime_default_sources": {
            path: digest_of(reader.read_text(path))
            for path in CONFIG_MERGE_SOURCES
        },
        "reject_only_count": sum(bool(entry["handler"].endswith("removed_directive"))
                             for entry in entries),
    }


def _metric_family(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one declared metric family."""
    labels = []
    labels.extend(
        {
            "name": label["name"],
            "values": sorted(label.get("values") or []),
            "dynamic": bool(label.get("dynamic", False)),
        }
        for label in entry.get("labels") or []
    )
    family: Dict[str, Any] = {
        "name": entry["name"],
        "type": entry["type"],
        "labels": sorted_by(labels, "name"),
    }
    if "bucket_boundaries" in entry:
        family["bucket_boundaries"] = entry["bucket_boundaries"]
    if "value" in entry:
        family["value"] = entry["value"]
    return family


def _join_c_string_literals(text: str) -> str:
    """Join adjacent C string literals so emitted series are parseable."""
    return re.sub(r'"\s*"', "", text)


def _parse_label_pairs(body: str) -> List[tuple]:
    """Parse ``name=\\"value\\"`` pairs from one emitted series body."""
    pairs: List[tuple] = []
    for part in body.split(","):
        name, separator, rest = part.partition('=\\"')
        if not separator or not rest.endswith('\\"'):
            continue
        name = name.strip()
        if not name or not name.replace("_", "").isalnum():
            continue
        pairs.append((name, rest[: -len('\\"')]))
    pairs.sort()
    return pairs


def _type_families(joined: str) -> Dict[str, str]:
    """Return the family-to-type map the renderer declares."""
    families: Dict[str, str] = {
        match.group(1): match.group(2)
        for match in re.finditer(r"# TYPE (\S+) (\S+)\\n", joined)
    }
    require(len(families) > 0, "metrics renderer declares no # TYPE families")
    return families


def _sample_family_map(families: Dict[str, str]) -> Dict[str, tuple]:
    """Map emitted sample names to their declared family and component."""
    samples = {name: (name, None) for name in families}
    for name, metric_type in families.items():
        if metric_type != "histogram":
            continue
        for component in ("bucket", "sum", "count"):
            sample = f"{name}_{component}"
            if sample in samples:
                raise ContractError(f"ambiguous histogram sample family: {sample}")
            samples[sample] = (name, component)
    return samples


def _collect_series(
    joined: str, families: Dict[str, str]
) -> Tuple[Dict[str, set], Dict[str, List[List[str]]]]:
    """Collect emitted series and label sets per family and component.

    Label sets are recorded per histogram component and sorted, so reordering
    the renderer's ``_bucket`` and ``_count`` lines leaves the contract
    unchanged while an added or removed label set still differs.
    """
    raw_series: Dict[str, set] = {name: set() for name in families}
    label_sets: Dict[str, set] = {name: set() for name in families}
    sample_families = _sample_family_map(families)
    for match in re.finditer(r"(nginx_markdown_[a-z0-9_]+)\{([^{}]*)\}", joined):
        resolved = sample_families.get(match.group(1))
        if resolved is None:
            continue
        family, component = resolved
        pairs = _parse_label_pairs(match.group(2))
        if not pairs:
            continue
        marker = [f"@component={component}"] if component else []
        label_sets[family].add(
            tuple(marker + sorted(name for name, _ in pairs))
        )
        raw_series[family].add(
            (component, tuple(f"{name}={value}" for name, value in pairs))
        )
    labels = {
        name: [list(label_set) for label_set in sorted(label_sets[name])]
        for name in families
    }
    return raw_series, labels


# A sample name ends at a space or a tab.  The renderer source spells a tab as
# the two-character C escape, and a literal tab is also accepted.
_SAMPLE_NAME_END_RE = re.compile(r"[ \t]|\\t")


def _unlabelled_samples(joined: str, families: Dict[str, str]) -> List[str]:
    """Return the families the renderer emits without any label block."""
    present = set()
    sample_families = _sample_family_map(families)
    for chunk in joined.split("\\n"):
        text = chunk.lstrip('" \t')
        separator = _SAMPLE_NAME_END_RE.search(text)
        if separator is None:
            continue
        resolved = sample_families.get(text[: separator.start()])
        if resolved is None:
            continue
        family, component = resolved
        present.add(family if component is None else f"{family}:{component}")
    return sorted(present)


def _emitted_series(renderer: str) -> Dict[str, Any]:
    """Extract emitted families, types, label names, literal series, samples."""
    joined = _join_c_string_literals(renderer)
    families = _type_families(joined)
    raw_series, labels = _collect_series(joined, families)
    series = {
        name: [
            ([f"@component={component}"] if component else [])
            + list(label_values)
            for component, label_values in sorted(
                raw_series[name], key=lambda item: (item[0] or "", item[1])
            )
        ]
        for name in sorted(raw_series)
    }
    return {
        "families": {name: families[name] for name in sorted(families)},
        "label_sets": {name: labels[name] for name in sorted(labels)},
        "series": series,
        "unlabelled_samples": _unlabelled_samples(joined, families),
    }


def extract_metrics(reader: SourceReader) -> Dict[str, Any]:
    """Extract the declared registry plus the renderer's emitted wire set."""
    registry = json.loads(reader.read_text(METRICS_REGISTRY))
    constraints = registry.get("constraints") or {}
    families = sorted_by(
        [_metric_family(entry) for entry in registry.get("families") or []], "name"
    )
    require(len(families) > 0, "metrics registry declares no families")
    renderer = reader.read_text(METRICS_RENDERER)
    return {
        "registry_schema_version": registry.get("schema_version"),
        "format": registry.get("format"),
        "family_prefix": constraints.get("family_prefix"),
        "forbidden_labels": sorted(constraints.get("forbidden_labels") or []),
        "max_histogram_buckets": constraints.get("max_histogram_buckets"),
        "families": families,
        "emitted": _emitted_series(renderer),
    }


def extract_reasons(reader: SourceReader) -> Dict[str, Any]:
    """Extract the declarative reason registry."""
    registry = tomllib.loads(reader.read_text(REASON_REGISTRY))
    reasons = registry.get("reasons") or []
    require(len(reasons) > 0, "reason registry declares no reasons")
    entries = []
    entries.extend(
        {
            "discriminant": reason["discriminant"],
            "key": reason["key"],
            "stage": reason.get("default_stage"),
            "outcome": reason.get("outcome"),
            "default_origin": reason.get("default_origin"),
            "allowed_origins": sorted(reason.get("allowed_origins") or []),
            "operator_visible": bool(reason.get("operator_visible")),
        }
        for reason in reasons
    )
    ordered = sorted_by(entries, "discriminant")
    discriminants = [entry["discriminant"] for entry in ordered]
    require(
        discriminants == list(range(len(discriminants))),
        "reason discriminants are not a contiguous 0..N-1 set",
    )
    keys = [entry["key"] for entry in ordered]
    require(
        len(keys) == len(set(keys)),
        "reason registry declares a duplicate key",
    )
    return {
        "registry_schema_version": (registry.get("metadata") or {}).get("schema_version"),
        "count": len(ordered),
        "entries": ordered,
    }


_REF_PREFIX = "#/$defs/"
_REF_DEPTH_LIMIT = 8
_SCHEMA_NESTING_LIMIT = 16
# Keywords that never change which instances validate.  Only these may sit
# beside a ``$ref``: a validation keyword there applies alongside the target,
# and its ``additionalProperties`` scope excludes the target's ``properties``,
# which a flattened projection cannot represent.
_SCHEMA_ANNOTATION_KEYWORDS = frozenset(
    {
        "$comment",
        "default",
        "deprecated",
        "description",
        "examples",
        "readOnly",
        "title",
        "writeOnly",
    }
)


_SCHEMA_CONSTRAINT_KEYS = (
    "const",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "multipleOf",
    "oneOf",
    "pattern",
    "uniqueItems",
)
_UNSUPPORTED_SCHEMA_KEYWORDS = (
    "allOf",
    "anyOf",
    "contains",
    "dependentRequired",
    "dependentSchemas",
    "else",
    "if",
    "maxContains",
    "minContains",
    "not",
    "patternProperties",
    "prefixItems",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
)


def _contains_ref(value: Any) -> bool:
    """Return True when a schema fragment contains a ``$ref`` anywhere."""
    if isinstance(value, dict):
        return "$ref" in value or any(_contains_ref(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_ref(item) for item in value)
    return False


def _validate_one_of(alternatives: Any) -> None:
    """Require inline, supported ``oneOf`` alternatives."""
    if not isinstance(alternatives, list) or not alternatives:
        raise ContractError("diagnostics oneOf must be a non-empty schema list")
    for alternative in alternatives:
        if not isinstance(alternative, dict):
            raise ContractError("boolean or non-object JSON schemas are unsupported")
        if _contains_ref(alternative):
            # The oneOf list is recorded verbatim, so a reference would pin
            # the pointer text and miss every change to its target.
            raise ContractError(
                "diagnostics oneOf alternatives must inline their schema "
                "instead of using $ref"
            )
        _schema_constraints(alternative)


def _schema_constraints(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Project supported JSON Schema value constraints."""
    if unsupported := sorted(
        key for key in _UNSUPPORTED_SCHEMA_KEYWORDS if key in schema
    ):
        raise ContractError(
            f'unsupported diagnostics schema keywords: {", ".join(unsupported)}'
        )
    if schema.get("oneOf") is not None:
        _validate_one_of(schema["oneOf"])
    return {
        key: schema[key]
        for key in _SCHEMA_CONSTRAINT_KEYS
        if key in schema
    }


def _resolve_ref(schema: Any, defs: Dict[str, Any], depth: int = 0) -> Dict[str, Any]:
    """Resolve a local ``$ref`` chain, failing closed when it cannot."""
    if not isinstance(schema, dict):
        raise ContractError("boolean or non-object JSON schemas are unsupported")
    reference = schema.get("$ref")
    if reference is None:
        return schema
    if depth > _REF_DEPTH_LIMIT:
        raise ContractError(f"schema $ref chain is too deep: {reference}")
    if not isinstance(reference, str) or not reference.startswith(_REF_PREFIX):
        raise ContractError("unsupported schema $ref: {!r}".format(reference))
    target = defs.get(reference[len(_REF_PREFIX):])
    if not isinstance(target, dict):
        raise ContractError("unresolved schema $ref: {!r}".format(reference))
    siblings = {key: value for key, value in schema.items() if key != "$ref"}
    if validating := sorted(set(siblings) - _SCHEMA_ANNOTATION_KEYWORDS):
        raise ContractError(
            "schema $ref {!r} has validation siblings the projection cannot "
            "represent: {}".format(reference, ", ".join(validating))
        )
    resolved = _resolve_ref(target, defs, depth + 1)
    return {**resolved, **siblings}


def _schema_additional_properties(schema: Dict[str, Any]) -> Optional[bool]:
    """Return a boolean additionalProperties contract or fail closed."""
    value = schema.get("additionalProperties")
    if value is not None and not isinstance(value, bool):
        raise ContractError(
            "schema-valued additionalProperties is unsupported"
        )
    return value


def _sorted_enum(values: List[Any]) -> List[Any]:
    """Sort an enum deterministically, tolerating a null entry."""
    return sorted(values, key=lambda value: (value is None, str(value)))


def _schema_types(schema: Dict[str, Any]) -> set:
    """Return the declared JSON types of a resolved schema as a set.

    A type list such as ``["array", "null"]`` still describes an array, so
    the projection must not skip its structure.
    """
    declared = schema.get("type")
    if isinstance(declared, list):
        return {item for item in declared if isinstance(item, str)}
    return {declared} if isinstance(declared, str) else set()


# A structure keyword constrains matching instances even when ``type`` is
# omitted, so its presence alone selects the object or array projection.
_OBJECT_SCHEMA_KEYWORDS = ("properties", "required", "additionalProperties")


def _is_object_schema(schema: Dict[str, Any]) -> bool:
    """Return True when a resolved schema constrains object instances."""
    return "object" in _schema_types(schema) or any(
        keyword in schema for keyword in _OBJECT_SCHEMA_KEYWORDS
    )


def _is_array_schema(schema: Dict[str, Any]) -> bool:
    """Return True when a resolved schema constrains array instances."""
    return "array" in _schema_types(schema) or "items" in schema


def _schema_nested(
    schema: Dict[str, Any], defs: Dict[str, Any], depth: int
) -> Dict[str, Any]:
    """Project the object and array structure below one resolved schema."""
    if depth > _SCHEMA_NESTING_LIMIT:
        raise ContractError("diagnostics schema nesting is too deep")
    nested: Dict[str, Any] = {}
    if _is_object_schema(schema):
        nested["additional_properties"] = _schema_additional_properties(schema)
        nested["required_children"] = sorted(schema.get("required") or [])
        nested["properties"] = _schema_properties(schema, defs, depth + 1)
    if _is_array_schema(schema):
        nested |= _schema_items(schema, defs, depth + 1)
    return nested


def _schema_properties(
    schema: Dict[str, Any], defs: Dict[str, Any], depth: int = 0
) -> List[Dict[str, Any]]:
    """Project the child fields of a resolved object schema, recursively."""
    children = []
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        raise ContractError("diagnostics schema properties must be a mapping")
    for child, child_schema in properties.items():
        child_schema = _resolve_ref(child_schema, defs)
        field: Dict[str, Any] = {
            "name": child,
            "type": child_schema.get("type"),
        }
        if "enum" in child_schema:
            field["enum"] = _sorted_enum(child_schema["enum"])
        if constraints := _schema_constraints(child_schema):
            field["constraints"] = constraints
        field |= _schema_nested(child_schema, defs, depth)
        children.append(field)
    return sorted(children, key=lambda item: item["name"])


def _schema_items(
    schema: Dict[str, Any], defs: Dict[str, Any], depth: int = 0
) -> Dict[str, Any]:
    """Project the item schema of an array field, resolving a ``$ref``."""
    item_schema = schema.get("items")
    if item_schema is None:
        raise ContractError("array field declares no item schema")
    item = _resolve_ref(item_schema, defs)
    if not item:
        raise ContractError("array field declares no item schema")
    if _is_array_schema(item):
        raise ContractError(
            "nested array items are unsupported by the diagnostics projection"
        )
    projected = {
        "items_type": item.get("type"),
        "items_required": sorted(item.get("required") or []),
        "items_additional_properties": _schema_additional_properties(item),
        "items_properties": _schema_properties(item, defs, depth),
    }
    if "enum" in item:
        projected["items_enum"] = _sorted_enum(item["enum"])
    if constraints := _schema_constraints(item):
        projected["items_constraints"] = constraints
    return projected


def _schema_field(
    name: str, schema: Dict[str, Any], required: bool, defs: Dict[str, Any]
) -> Dict[str, Any]:
    """Normalize one diagnostics schema property."""
    schema = _resolve_ref(schema, defs)
    field: Dict[str, Any] = {
        "name": name,
        "required": required,
        "type": schema.get("type"),
        "nullable": "null" in (schema.get("type") or [])
        if isinstance(schema.get("type"), list)
        else False,
    }
    if "enum" in schema:
        field["enum"] = _sorted_enum(schema["enum"])
    if constraints := _schema_constraints(schema):
        field["constraints"] = constraints
    field |= _schema_nested(schema, defs, 0)
    return field


def extract_diagnostics(reader: SourceReader) -> Dict[str, Any]:
    """Extract the diagnostics wire schema contract."""
    schema = json.loads(reader.read_text(DIAGNOSTICS_SCHEMA))
    require(isinstance(schema, dict), "diagnostics schema must be a JSON object")
    defs = schema.get("$defs") or {}
    required = schema.get("required") or []
    properties = schema.get("properties") or {}
    require(isinstance(defs, dict), "diagnostics schema definitions must be a mapping")
    require(isinstance(required, list), "diagnostics schema required must be a list")
    require(
        isinstance(properties, dict),
        "diagnostics schema properties must be a mapping",
    )
    require(
        all(isinstance(name, str) for name in required),
        "diagnostics schema required entries must be strings",
    )
    require(len(properties) > 0, "diagnostics schema declares no properties")
    fields = [
        _schema_field(name, properties[name], name in required, defs)
        for name in sorted(properties)
    ]
    version_field = properties.get("schema_version") or {}
    return {
        "schema_version": version_field.get("const"),
        "type": schema.get("type"),
        "additional_properties": _schema_additional_properties(schema),
        "constraints": _schema_constraints(schema),
        "required": sorted(required),
        "fields": fields,
        "methods": ["GET", "HEAD"],
        "read_only": True,
    }


def extract_ffi(reader: SourceReader) -> Dict[str, Any]:
    """Extract the generated header's export set and ABI handshake version."""
    header = reader.read_text(FFI_HEADER)
    live_header = _mask_comments(header)
    version_match = re.search(r"#define\s+MARKDOWN_ABI_VERSION\s+(\d+)", live_header)
    if version_match is None:
        raise ContractError("generated FFI header has no ABI version")
    prototypes: List[str] = []
    buffer = ""
    for line in live_header.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("*"):
            continue
        buffer = f"{buffer} {stripped}".strip()
        if stripped.endswith(";"):
            prototypes.append(re.sub(r"\s+", " ", buffer))
            buffer = ""
    symbols = []
    for prototype in prototypes:
        match = re.search(r"\b(markdown_[a-z0-9_]+)\s*\(", prototype)
        if match is None:
            continue
        prefix = prototype[: match.start()].strip()
        symbols.append(
            {
                "name": match[1],
                "signature": prototype.rstrip(";").strip(),
                "return_type": prefix.replace("*", " *").strip(),
            }
        )
    require(len(symbols) > 0, "generated FFI header declares no exports")
    names = [symbol["name"] for symbol in symbols]
    require(len(names) == len(set(names)), "duplicate FFI export declarations")
    handshake = sorted(
        name for name in names if name.startswith("markdown_abi_")
    )
    return {
        "abi_version": int(version_match[1]),
        "classification": "INTERNAL_ONLY",
        "header": FFI_HEADER,
        "count": len(symbols),
        "symbols": sorted_by(symbols, "name"),
        "handshake_symbols": handshake,
    }


def _matrix_target(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Project one normalized release-matrix row onto the support contract."""
    version = entry["nginx_version"]
    parts = version.split(".")
    require(len(parts) == 3, "release matrix row has a malformed NGINX version")
    return {
        "nginx_minor": f"{parts[0]}.{parts[1]}",
        "nginx_version": version,
        "artifact_type": entry["artifact_type"],
        "os": entry["os"],
        "libc": entry["libc"],
        "arch": entry["arch"],
        "support_tier": entry["support_tier"],
        "release_blocking": bool(entry["release_blocking"]),
    }


def _support_contract(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the support contract as one entry per matrix row.

    Every row keeps its exact patch version and its own tier, so two patches of
    the same minor that disagree cannot collapse into one value.  The
    comparator groups by minor version when it evaluates coverage.
    """
    identities = [
        (
            row["nginx_version"],
            row["artifact_type"],
            row["os"],
            row["libc"],
            row["arch"],
        )
        for row in rows
    ]
    require(
        len(identities) == len(set(identities)),
        "release matrix declares duplicate support rows",
    )
    return {
        "counts": {
            "total": len(rows),
            "supported": sum(
                row["support_tier"] == "supported" for row in rows
            ),
            "best_effort": sum(
                row["support_tier"] == "best-effort" for row in rows
            ),
            "experimental": sum(
                row["support_tier"] == "experimental" for row in rows
            ),
            "release_blocking": sum(bool(row["release_blocking"])
                                for row in rows),
        },
        "targets": sorted(
            rows,
            key=lambda item: (
                item["nginx_version"],
                item["artifact_type"],
                item["os"],
                item["libc"],
                item["arch"],
            ),
        ),
    }


# nFPM content types.  A file type installs the entry's own bytes at dst.
# dir creates a directory, symlink installs a link, and ghost names a path
# that RPM owns without shipping it and other packagers ignore.  A tree type
# installs files the contents list does not name, so the projection rejects
# it, as it rejects any type it does not know.
_NFPM_FILE_TYPES = frozenset(
    {"", "file", "config", "config|noreplace", "config|missingok", "license", "licence"}
)
_NFPM_NON_FILE_TYPES = frozenset({"dir", "symlink", "ghost"})


def _nfpm_contents(contents: Any, scope: str) -> List[Dict[str, str]]:
    """Project nFPM contents entries with their packager scope and type.

    An omitted ``type`` is a regular file, as in nFPM itself.
    """
    require(isinstance(contents, list), f"nFPM {scope} must be a YAML list")
    entries = []
    for item in contents:
        require(isinstance(item, dict), "nFPM contents entries must be mappings")
        destination = item.get("dst")
        packager = item.get("packager", "")
        content_type = item.get("type", "")
        require(
            isinstance(destination, str) and bool(destination),
            "nFPM contents entry has no destination",
        )
        require(
            isinstance(packager, str) and packager in ("", "deb", "rpm"),
            "nFPM contents entry has an unsupported packager",
        )
        require(
            isinstance(content_type, str)
            and content_type in _NFPM_FILE_TYPES | _NFPM_NON_FILE_TYPES,
            "nFPM contents entry for {} has an unsupported type: {!r}".format(
                destination, content_type
            ),
        )
        entries.append(
            {"dst": destination, "packager": packager, "type": content_type}
        )
    return entries


def _layout_entries(
    entries: List[Dict[str, str]], packager: str
) -> List[Dict[str, str]]:
    """Return the effective contents entries for one package family.

    Only RPM records ghost entries; every other packager drops them.
    """
    return sorted(
        (
            entry
            for entry in entries
            if entry["packager"] in ("", packager)
            and (packager == "rpm" or entry["type"] != "ghost")
        ),
        key=lambda entry: entry["dst"],
    )


def _shipped_files(entries: List[Dict[str, str]], suffix: str) -> List[str]:
    """Return shipped regular files whose destination ends with ``suffix``."""
    return [
        entry["dst"]
        for entry in entries
        if entry["dst"].endswith(suffix) and entry["type"] in _NFPM_FILE_TYPES
    ]


def _load_nfpm_document(text: str) -> Dict[str, Any]:
    """Parse and validate the nFPM YAML root mapping."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ContractError(f"malformed nFPM YAML: {error}") from error
    require(isinstance(document, dict), "nFPM config must be a YAML mapping")
    return document


def _nfpm_package_name(document: Dict[str, Any]) -> str:
    """Return the validated package name from one nFPM document."""
    package_name = document.get("name")
    require(
        isinstance(package_name, str)
        and re.fullmatch(r"[A-Za-z0-9._-]+", package_name) is not None,
        "nFPM config declares no valid package name",
    )
    return package_name


def _nfpm_override(overrides: Dict[str, Any], packager: str) -> Dict[str, Any]:
    """Return one package family's validated override mapping."""
    package_override = overrides.get(packager) or {}
    require(
        isinstance(package_override, dict),
        f"nFPM {packager} override must be a YAML mapping",
    )
    return package_override


def _nfpm_effective(
    document: Dict[str, Any], package_override: Dict[str, Any], key: str
) -> Any:
    """Return the value nFPM uses for one overridable key.

    nFPM merges an override over the root configuration and skips empty
    values, so an absent or empty override list inherits the root list.
    """
    value = package_override.get(key)
    return document.get(key) if value is None or value == [] else value


def _nfpm_dependencies(
    document: Dict[str, Any], package_override: Dict[str, Any], packager: str
) -> List[str]:
    """Return one validated package family's effective dependency list."""
    dependencies = _nfpm_effective(document, package_override, "depends") or []
    require(
        isinstance(dependencies, list)
        and all(isinstance(item, str) for item in dependencies),
        f"nFPM {packager} dependencies must be a string list",
    )
    return dependencies


def _nfpm_layout(
    document: Dict[str, Any], package_override: Dict[str, Any], packager: str
) -> List[Dict[str, str]]:
    """Return one package family's effective contents entries."""
    if package_override.get("contents") in (None, []):
        entries = _nfpm_contents(document.get("contents"), "contents")
        require(len(entries) > 0, "nFPM config declares no contents entries")
    else:
        entries = _nfpm_contents(
            package_override["contents"], f"{packager} override contents"
        )
    return _layout_entries(entries, packager)


def _nfpm_deb_constraints(dependencies: List[str]) -> List[List[str]]:
    """Extract recognized NGINX constraints from DEB dependency strings."""
    constraints = set()
    pattern = r"nginx\s+\(([=<>!]+)\s+\$\{([A-Z_]+)\}\)"
    for dependency in dependencies:
        if match := re.fullmatch(pattern, dependency):
            constraints.add((match[1], match[2]))
    return [list(item) for item in sorted(constraints)]


def _nfpm_rpm_constraints(dependencies: List[str]) -> List[str]:
    """Extract recognized NGINX constraints from RPM dependency strings."""
    constraints = set()
    pattern = r"nginx(-r\$\{NGINX_VERSION\}|\s*>=\s*\$\{NGINX_VERSION\})"
    for dependency in dependencies:
        if match := re.fullmatch(pattern, dependency):
            constraints.add(match[1].replace(" ", ""))
    return sorted(constraints)


def _nfpm_contract(text: str) -> Dict[str, Any]:
    """Extract the package name, install layout, and dependency shape."""
    document = _load_nfpm_document(text)
    package_name = _nfpm_package_name(document)
    if document.get("contents") is not None:
        # nFPM rejects a malformed root list even when both families
        # override it, so the extractor validates it in that case as well.
        _nfpm_contents(document["contents"], "contents")
    overrides = document.get("overrides") or {}
    require(isinstance(overrides, dict), "nFPM overrides must be a YAML mapping")
    deb_override = _nfpm_override(overrides, "deb")
    rpm_override = _nfpm_override(overrides, "rpm")
    deb_entries = _nfpm_layout(document, deb_override, "deb")
    rpm_entries = _nfpm_layout(document, rpm_override, "rpm")
    deb_paths = [entry["dst"] for entry in deb_entries]
    rpm_paths = [entry["dst"] for entry in rpm_entries]
    deb_dependencies = _nfpm_dependencies(document, deb_override, "deb")
    rpm_dependencies = _nfpm_dependencies(document, rpm_override, "rpm")
    return {
        "package_name": package_name,
        "deb_install_paths": deb_paths,
        "rpm_install_paths": rpm_paths,
        "deb_module_path": _shipped_files(deb_entries, ".so"),
        "rpm_module_path": _shipped_files(rpm_entries, ".so"),
        "deb_load_snippet_paths": _shipped_files(deb_entries, ".conf"),
        "rpm_load_snippet_paths": _shipped_files(rpm_entries, ".conf"),
        "deb_nginx_constraints": _nfpm_deb_constraints(deb_dependencies),
        "rpm_nginx_constraints": _nfpm_rpm_constraints(rpm_dependencies),
    }


def extract_packaging(reader: SourceReader) -> Dict[str, Any]:
    """Extract package identity, install layout, and support tiers."""
    matrix = json.loads(reader.read_text(RELEASE_MATRIX))
    rows = [_matrix_target(entry) for entry in matrix.get("entries") or []]
    require(len(rows) > 0, "release matrix declares no entries")
    contract = _nfpm_contract(reader.read_text(NFPM_CONFIG))
    contract["support"] = _support_contract(rows)
    contract["matrix_schema_version"] = matrix.get("schema_version")
    return contract


def extract_http(reader: SourceReader) -> Dict[str, Any]:
    """Bind the behavioural HTTP contract to the tests that prove it."""
    behaviours: Dict[str, Any] = {}
    files: Dict[str, str] = {}
    for behaviour in sorted(HTTP_CONTRACT_TESTS):
        paths = []
        for path in HTTP_CONTRACT_TESTS[behaviour]:
            files[path] = digest_of(reader.read_text(path))
            paths.append(path)
        behaviours[behaviour] = {
            "tests": sorted(paths),
            "executed_by": HTTP_EXECUTION_TARGETS[behaviour],
        }
    return {
        "evidence_class": "unit_and_e2e_golden",
        "static_extraction_limit": (
            "These files are the executed proof of the HTTP contract. "
            "This manifest binds their content and does not claim that a "
            "static digest proves runtime behaviour."
        ),
        "behaviours": behaviours,
        "test_file_digests": {path: files[path] for path in sorted(files)},
    }


EXTRACTORS = {
    "directives": extract_directives,
    "metrics": extract_metrics,
    "reasons": extract_reasons,
    "diagnostics": extract_diagnostics,
    "http": extract_http,
    "packaging": extract_packaging,
    "ffi": extract_ffi,
}


def extract_surfaces(reader: SourceReader, surfaces: Optional[List[str]] = None) -> Dict[str, Any]:
    """Extract every requested surface from one source revision."""
    names = surfaces or sorted(EXTRACTORS)
    unknown = [name for name in names if name not in EXTRACTORS]
    require(not unknown, f'unknown contract surface: {", ".join(unknown)}')
    result = {}
    for name in names:
        try:
            result[name] = EXTRACTORS[name](reader)
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise ContractError(f"cannot extract the {name} surface: {error}") from error
    return result
