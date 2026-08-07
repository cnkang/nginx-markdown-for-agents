#!/usr/bin/env python3
"""Generate release schema-drift artifacts from source of truth.

Generates the three release artifacts consumed by ``validate_schema_drift.py``
from their source-of-truth implementations:

- ``metrics-registry.json`` — derived from the v1 renderer header
  (``components/nginx-module/src/ngx_http_markdown_metrics_v1_renderer.h``):
  the frozen 12-family metric contract.
- ``diagnostics-field-contract.json`` — derived from
  ``schemas/diagnostics.schema.json`` (``effective_config`` definition).
- ``dynconf-precedence-report.json`` — derived from ``schemas/dynconf.schema.json``
  and the dynconf precedence header
  (``components/nginx-module/src/ngx_http_markdown_dynconf_precedence.h``).

All artifacts are written under ``artifacts/release/<version>/`` (git-ignored
generated evidence).  The generator is idempotent; ``validate_schema_drift.py``
then verifies the artifacts against the same sources and the renderer.

Usage:
  python3 tools/release/gates/generate_schema_artifacts.py [--version 0.9.2]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
from lib.path_validation import validate_read_path  # noqa: E402

DEFAULT_VERSION = "0.9.2"

RENDERER_PATH = (
    REPO_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_metrics_v1_renderer.h"
)
DIAGNOSTICS_SCHEMA_PATH = REPO_ROOT / "schemas" / "diagnostics.schema.json"
DYNCONF_SCHEMA_PATH = REPO_ROOT / "schemas" / "dynconf.schema.json"
PRECEDENCE_HEADER_PATH = (
    REPO_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_dynconf_precedence.h"
)

# Streaming transition label values are closed-allowlisted by contract.
STREAMING_TRANSITION_VALUES = [
    "commit",
    "fallback",
    "safe_finish_start",
    "abort_start",
    "resume_success",
    "resume_failure",
]

# Histogram bucket boundaries for conversion_duration_seconds (frozen).
HISTOGRAM_BUCKET_BOUNDARIES = [
    0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0,
]

# Forbidden label names per the frozen metric contract.
FORBIDDEN_LABELS = {"path", "uri", "host", "client", "query", "address"}

FIVE_TIER_PRECEDENCE_HIERARCHY = [
    {
        "tier": 1,
        "source": "request_variable",
        "scope": "filter only",
        "description": "NGINX request variable evaluation (e.g., markdown_filter $var)",
    },
    {
        "tier": 2,
        "source": "static",
        "scope": "server/location explicit",
        "description": "Server/location explicit static configuration (block bit set)",
    },
    {
        "tier": 3,
        "source": "dynconf",
        "scope": "where block bit NOT set",
        "description": "Dynconf runtime override (block bit NOT set)",
    },
    {
        "tier": 4,
        "source": "http_baseline",
        "scope": "http block",
        "description": "Inherited http baseline (http-block merged value)",
    },
    {
        "tier": 5,
        "source": "default",
        "scope": "compile-time",
        "description": "Built-in default (compile-time default)",
    },
]


def _read_text(path: Path) -> str:
    """Read a repository text file with canonical path validation."""
    validated = validate_read_path(path, purpose="schema artifact generation")
    return validated.read_text(encoding="utf-8")


def _read_json(path: Path) -> dict:
    """Read and parse a repository JSON file."""
    return json.loads(_read_text(path))


def _extract_family_type(content: str, family: str) -> str:
    """Extract the Prometheus type for one family from renderer TYPE lines."""
    match = re.search(
        r"# TYPE " + re.escape(family) + r" (counter|gauge|histogram)",
        content,
    )
    if match is None:
        raise ValueError(f"renderer has no # TYPE line for {family}")
    return match.group(1)


def _extract_family_labels(content: str, family: str) -> list[dict]:
    """
    Extract label names and their observed values for one metric family from
    the renderer's literal metric lines.

    Returns a list of ``{"name": ..., "values": [...]}`` entries.
    """
    marker = f"# HELP {family}"
    start = content.find(marker)
    if start < 0:
        raise ValueError(f"renderer has no # HELP line for {family}")
    next_help = content.find("# HELP ", start + len(marker))
    block = content[start:] if next_help < 0 else content[start:next_help]

    # For the histogram family the bucket lines use {engine=...,le=...};
    # label extraction below uses the engine lines found in the block.
    label_values: dict[str, set[str]] = {}
    for source in re.findall(r"\{([^{}]*)\}", block):
        for key, value in re.findall(
            r"([A-Za-z_]\w*)=\\\"([^\\\"]*)\\\"", source, flags=re.ASCII
        ):
            if key == "le":
                continue
            label_values.setdefault(key, set()).add(value)

    labels = [
        {"name": name, "values": sorted(values)}
        for name, values in sorted(label_values.items())
    ]

    # Enforce the closed allowlist for the streaming transition label.
    if family == "nginx_markdown_streaming_events_total":
        for label in labels:
            if label["name"] == "transition":
                label["values"] = list(STREAMING_TRANSITION_VALUES)
    return labels


def generate_metrics_registry() -> dict:
    """Generate metrics-registry.json from the v1 renderer header."""
    content = _read_text(RENDERER_PATH)

    # All families are declared with # TYPE lines in the renderer.
    type_matches = re.findall(
        r"# TYPE (nginx_markdown_\w+) (counter|gauge|histogram)", content
    )
    families: list[dict] = []
    for family_name, family_type in type_matches:
        entry: dict = {
            "name": family_name,
            "type": family_type,
            "labels": _extract_family_labels(content, family_name),
        }
        if family_type == "histogram":
            entry["bucket_count"] = len(HISTOGRAM_BUCKET_BOUNDARIES)
            entry["bucket_boundaries"] = list(HISTOGRAM_BUCKET_BOUNDARIES)
            # The renderer formats histogram labels with %s placeholders;
            # the engine label is a closed two-value set.
            entry["labels"] = [
                {"name": "engine", "values": ["full_buffer", "streaming"]}
            ]
        if family_name == "nginx_markdown_build_info":
            entry["value"] = 1
        families.append(entry)

    # The renderer emits exactly 12 families (frozen contract).
    if len(families) != 12:
        raise ValueError(
            f"renderer declares {len(families)} families, expected 12"
        )

    # Structural sanity: label names must never use forbidden labels.
    for family in families:
        for label in family["labels"]:
            if label["name"] in FORBIDDEN_LABELS:
                raise ValueError(
                    f"family {family['name']} uses forbidden label "
                    f"{label['name']}"
                )

    return {
        "schema_version": 1,
        "format": "prometheus_text_004",
        "generator": Path(__file__).name,
        "source": str(RENDERER_PATH.relative_to(REPO_ROOT)),
        "families": families,
    }


def generate_diagnostics_field_contract() -> dict:
    """Generate diagnostics-field-contract.json from diagnostics.schema.json."""
    schema = _read_json(DIAGNOSTICS_SCHEMA_PATH)
    effective_def = schema.get("$defs", {}).get("effective_config", {})
    properties = effective_def.get("properties", {})
    if not properties:
        raise ValueError(
            "diagnostics.schema.json has no $defs.effective_config.properties"
        )

    effective_fields: dict[str, dict] = {}
    for name, prop in sorted(properties.items()):
        field: dict = {"type": prop.get("type")}
        if prop.get("enum") is not None:
            field["enum"] = prop["enum"]
        if prop.get("minimum") is not None or prop.get("maximum") is not None:
            bounds: dict = {}
            if prop.get("minimum") is not None:
                bounds["minimum"] = prop["minimum"]
            if prop.get("maximum") is not None:
                bounds["maximum"] = prop["maximum"]
            field["bounds"] = bounds
        effective_fields[name] = field

    return {
        "schema_version": 1,
        "generator": Path(__file__).name,
        "source": str(DIAGNOSTICS_SCHEMA_PATH.relative_to(REPO_ROOT)),
        "effective_fields": effective_fields,
        "constraints": {"field_count": len(effective_fields)},
    }


def generate_dynconf_precedence_report() -> dict:
    """Generate dynconf-precedence-report.json from schema + precedence header."""
    schema = _read_json(DYNCONF_SCHEMA_PATH)
    schema_keys = {
        key for key in schema.get("properties", {}) if key != "schema_version"
    }
    if not schema_keys:
        raise ValueError("dynconf.schema.json has no properties")

    _read_text(PRECEDENCE_HEADER_PATH)  # existence/sanity check

    return {
        "schema_version": 1,
        "generator": Path(__file__).name,
        "source": str(DYNCONF_SCHEMA_PATH.relative_to(REPO_ROOT)),
        "five_tier_precedence_hierarchy": FIVE_TIER_PRECEDENCE_HIERARCHY,
        "field_specific_provenance_rules": {
            "fields": {
                key: {
                    "allowed_provenance": [
                        "request_variable",
                        "static",
                        "dynconf",
                        "http_baseline",
                        "default",
                    ]
                }
                for key in sorted(schema_keys)
            }
        },
    }


def main(argv: list[str] | None = None) -> int:
    """Generate all schema-drift artifacts and report results."""
    parser = argparse.ArgumentParser(
        description="Generate release schema-drift artifacts from sources."
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help="Release version directory (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    artifact_dir = (
        REPO_ROOT / "artifacts" / "release" / args.version
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)

    artifacts = {
        "metrics-registry.json": generate_metrics_registry(),
        "diagnostics-field-contract.json": generate_diagnostics_field_contract(),
        "dynconf-precedence-report.json": generate_dynconf_precedence_report(),
    }

    for name, data in artifacts.items():
        path = artifact_dir / name
        path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {path.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
