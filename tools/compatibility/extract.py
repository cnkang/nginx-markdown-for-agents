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

from .model import ContractError, digest_of, require, sorted_by
from .sources import SourceReader

DIRECTIVES_IMPL = "components/nginx-module/src/ngx_http_markdown_config_directives_impl.h"
DIRECTIVE_NAMES = "components/nginx-module/src/ngx_http_markdown_directive_names.h"
METRICS_REGISTRY = "schemas/metrics-v1.registry.json"
METRICS_RENDERER = "components/nginx-module/src/ngx_http_markdown_metrics_v1_renderer.h"
# Both HTTP contract groups below run through the C unit suite.
UNIT_SUITE_TARGET = "make test-nginx-unit"
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

# Executor for each HTTP behaviour group.  These targets run in CI, not inside
# this gate: the gate proves the contract text did not move, the targets prove
# the behaviour still holds.
HTTP_EXECUTION_TARGETS = {
    "accept_negotiation": UNIT_SUITE_TARGET + " / make test-all-e2e",
    "conditional_304": UNIT_SUITE_TARGET + " / make test-all-e2e",
    "head_request": UNIT_SUITE_TARGET,
    "header_plan_vary_content_type": UNIT_SUITE_TARGET,
    "pre_post_commit": UNIT_SUITE_TARGET,
    "status_and_range": "make test-all-e2e",
    "auth_and_cache": "make test-all-e2e",
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
        lambda match: 'ngx_string("{}")'.format(
            definitions.get(match.group(1), match.group(1))
        ),
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
    require(len(fields) == 6, "malformed ngx_command_t row: {}".format(entry.strip()))
    return fields


def _args_contract(flags: str) -> str:
    """Map NGINX command flags to the inventory argument contract."""
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


def _context_contract(flags: str) -> List[str]:
    """Return the directive contexts declared by the command flags."""
    return [name for token, name in _CONTEXT_FLAGS if token in flags]


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
    if body.startswith("/*"):
        body = body[2:]
    if body.endswith("*/"):
        body = body[:-2]
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
    for line in lines:
        if not line or line.startswith(name) is False:
            continue
        return _strip_syntax_note(line[len(name):].strip())
    return ""


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
        value = _comment_value(lines, label)
        if value:
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
            "unexpected directive name outside the markdown_ namespace: {}".format(name),
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
        "reject_only_count": sum(
            1 for entry in entries if entry["handler"].endswith("removed_directive")
        ),
    }


def _metric_family(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize one declared metric family."""
    labels = []
    for label in entry.get("labels") or []:
        labels.append(
            {
                "name": label["name"],
                "values": sorted(label.get("values") or []),
                "dynamic": bool(label.get("dynamic", False)),
            }
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
    return pairs


def _type_families(joined: str) -> Dict[str, str]:
    """Return the family-to-type map the renderer declares."""
    families: Dict[str, str] = {}
    for match in re.finditer(r"# TYPE (\S+) (\S+)\\n", joined):
        families[match.group(1)] = match.group(2)
    require(len(families) > 0, "metrics renderer declares no # TYPE families")
    return families


def _collect_series(
    joined: str, families: Dict[str, str]
) -> Tuple[Dict[str, set], Dict[str, List[List[str]]]]:
    """Collect emitted series and label sets per family."""
    raw_series: Dict[str, set] = {name: set() for name in families}
    labels: Dict[str, List[List[str]]] = {name: [] for name in families}
    for match in re.finditer(r"(nginx_markdown_[a-z0-9_]+)\{([^{}]*)\}", joined):
        family = match.group(1)
        if family not in families:
            continue
        pairs = _parse_label_pairs(match.group(2))
        if not pairs:
            continue
        label_names = sorted(name for name, _ in pairs)
        raw_series[family].add("|".join(name + "=" + value for name, value in pairs))
        if label_names not in labels[family]:
            labels[family].append(label_names)
    return raw_series, labels


def _emitted_series(renderer: str) -> Dict[str, Any]:
    """Extract emitted families, types, label names, and literal series."""
    joined = _join_c_string_literals(renderer)
    families = _type_families(joined)
    raw_series, labels = _collect_series(joined, families)
    series = {
        name: [item.split("|") for item in sorted(raw_series[name])]
        for name in sorted(raw_series)
    }
    return {
        "families": {name: families[name] for name in sorted(families)},
        "label_sets": {name: labels[name] for name in sorted(labels)},
        "series": series,
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
    for reason in reasons:
        entries.append(
            {
                "discriminant": reason["discriminant"],
                "key": reason["key"],
                "stage": reason.get("default_stage"),
                "outcome": reason.get("outcome"),
                "default_origin": reason.get("default_origin"),
                "allowed_origins": sorted(reason.get("allowed_origins") or []),
                "operator_visible": bool(reason.get("operator_visible")),
            }
        )
    ordered = sorted_by(entries, "discriminant")
    discriminants = [entry["discriminant"] for entry in ordered]
    require(
        discriminants == list(range(len(discriminants))),
        "reason discriminants are not a contiguous 0..N-1 set",
    )
    return {
        "registry_schema_version": (registry.get("metadata") or {}).get("schema_version"),
        "count": len(ordered),
        "entries": ordered,
    }


def _schema_field(name: str, schema: Dict[str, Any], required: bool) -> Dict[str, Any]:
    """Normalize one diagnostics schema property."""
    field: Dict[str, Any] = {
        "name": name,
        "required": required,
        "type": schema.get("type"),
        "nullable": "null" in (schema.get("type") or [])
        if isinstance(schema.get("type"), list)
        else False,
    }
    if "enum" in schema:
        field["enum"] = sorted(schema["enum"])
    if schema.get("type") == "object" or "properties" in schema:
        field["additional_properties"] = schema.get("additionalProperties")
        child_required = sorted(schema.get("required") or [])
        field["required_children"] = child_required
        field["properties"] = sorted(
            (
                {
                    "name": child,
                    "type": child_schema.get("type"),
                }
                for child, child_schema in (schema.get("properties") or {}).items()
            ),
            key=lambda item: item["name"],
        )
    if schema.get("type") == "array":
        item = schema.get("items") or {}
        field["items_type"] = item.get("type")
        field["items_required"] = sorted(item.get("required") or [])
        field["items_additional_properties"] = item.get("additionalProperties")
    return field


def extract_diagnostics(reader: SourceReader) -> Dict[str, Any]:
    """Extract the diagnostics wire schema contract."""
    schema = json.loads(reader.read_text(DIAGNOSTICS_SCHEMA))
    required = schema.get("required") or []
    properties = schema.get("properties") or {}
    require(len(properties) > 0, "diagnostics schema declares no properties")
    fields = [
        _schema_field(name, properties[name], name in required)
        for name in sorted(properties)
    ]
    version_field = properties.get("schema_version") or {}
    return {
        "schema_version": version_field.get("const"),
        "type": schema.get("type"),
        "additional_properties": schema.get("additionalProperties"),
        "required": sorted(required),
        "fields": fields,
        "methods": ["GET", "HEAD"],
        "read_only": True,
    }


def extract_ffi(reader: SourceReader) -> Dict[str, Any]:
    """Extract the generated header's export set and ABI handshake version."""
    header = reader.read_text(FFI_HEADER)
    version_match = re.search(r"#define\s+MARKDOWN_ABI_VERSION\s+(\d+)", header)
    if version_match is None:
        raise ContractError("generated FFI header has no ABI version")
    prototypes: List[str] = []
    buffer = ""
    for line in header.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("*"):
            continue
        buffer = (buffer + " " + stripped).strip()
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
                "name": match.group(1),
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
        "abi_version": int(version_match.group(1)),
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
        "nginx_minor": "{}.{}".format(parts[0], parts[1]),
        "nginx_version": version,
        "os": entry["os"],
        "libc": entry["libc"],
        "arch": entry["arch"],
        "support_tier": entry["support_tier"],
        "release_blocking": bool(entry["release_blocking"]),
    }


def _support_contract(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate rows into the support-set contract used for comparison."""
    grouped: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        key = (row["nginx_minor"], row["os"], row["libc"], row["arch"])
        bucket = grouped.setdefault(
            key,
            {
                "nginx_minor": row["nginx_minor"],
                "os": row["os"],
                "libc": row["libc"],
                "arch": row["arch"],
                "support_tier": row["support_tier"],
                "release_blocking": row["release_blocking"],
                "patches": [],
            },
        )
        if row["nginx_version"] not in bucket["patches"]:
            bucket["patches"].append(row["nginx_version"])
    for bucket in grouped.values():
        bucket["patches"].sort()
    return {
        "counts": {
            "total": len(rows),
            "supported": sum(1 for row in rows if row["support_tier"] == "supported"),
            "best_effort": sum(
                1 for row in rows if row["support_tier"] == "best-effort"
            ),
            "experimental": sum(
                1 for row in rows if row["support_tier"] == "experimental"
            ),
            "release_blocking": sum(1 for row in rows if row["release_blocking"]),
        },
        "targets": sorted(
            grouped.values(),
            key=lambda item: (
                item["nginx_minor"],
                item["os"],
                item["libc"],
                item["arch"],
            ),
        ),
    }


def _nfpm_contract(text: str) -> Dict[str, Any]:
    """Extract the package name, install layout, and dependency shape."""
    name_match = re.search(r'^name:\s*"?([A-Za-z0-9._-]+)"?\s*$', text, re.MULTILINE)
    if name_match is None:
        raise ContractError("nFPM config declares no package name")
    destinations = sorted(set(re.findall(r'dst:\s*"([^"]+)"', text)))
    deb_paths = sorted(
        path for path in destinations if path.startswith("/usr/lib/nginx/modules")
    )
    rpm_paths = sorted(
        path for path in destinations if path.startswith("/usr/lib64/nginx/modules")
    )
    deb_depends = sorted(set(re.findall(r'"nginx \(([=<>!]+) \$\{([A-Z_]+)\}\)"', text)))
    rpm_depends = sorted(
        {
            item.replace(" ", "")
            for item in re.findall(
                r'"nginx(-r\$\{NGINX_VERSION\}|\s*>=\s*\$\{NGINX_VERSION\})"', text
            )
        }
    )
    return {
        "package_name": name_match.group(1),
        "deb_module_path": deb_paths,
        "rpm_module_path": rpm_paths,
        "load_snippet_paths": sorted(
            filter(
                lambda path: "/modules" in path and path.endswith(".conf"),
                destinations,
            )
        ),
        "deb_nginx_constraints": [list(item) for item in deb_depends],
        "rpm_nginx_constraints": rpm_depends,
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
            payload = reader.read_bytes(path)
            files[path] = digest_of(payload.decode("utf-8", "replace"))
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
    require(not unknown, "unknown contract surface: {}".format(", ".join(unknown)))
    result = {}
    for name in names:
        try:
            result[name] = EXTRACTORS[name](reader)
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise ContractError(
                "cannot extract the {} surface: {}".format(name, error)
            )
    return result
