#!/usr/bin/env python3
"""Render documentation sections from release-matrix.json.

Generates and validates machine-generated documentation sections delimited by
HTML comment markers:

    <!-- BEGIN:release-matrix:section-name -->
    ...generated content...
    <!-- END:release-matrix:section-name -->

Modes:
    --check          Validate that generated sections in target docs match what
                     the matrix would produce. Exits non-zero on mismatch and
                     shows a unified diff of expected vs actual.
    --write          Replace marker-delimited sections in target docs with
                     freshly generated content.
    --release-notes  Emit release notes summary to stdout.

Exit codes:
    0  Success
    1  Check mismatch or runtime error
    2  Schema validation error

The authoritative matrix source is tools/release-matrix.json.
Schema: tools/release-matrix.schema.json.

Part of spec 40: Release Matrix Source of Truth.
"""


from __future__ import annotations

import argparse
import contextlib
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from path_validation import validate_read_path  # noqa: E402
from path_validation import validate_write_path_within_root  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "tools" / "release-matrix.json"
SCHEMA_PATH = ROOT / "tools" / "release-matrix.schema.json"

# Marker patterns
BEGIN_RE = re.compile(
    r"^([ \t]*)<!-- BEGIN:release-matrix:([a-z][a-z0-9_-]*) -->\s*$"
)
END_RE = re.compile(
    r"^([ \t]*)<!-- END:release-matrix:([a-z][a-z0-9_-]*) -->\s*$"
)

# Section registry: maps target file (relative to ROOT) -> list of section names
SECTION_REGISTRY: dict[str, list[str]] = {
    "README.md": ["support-matrix"],
    "docs/COMPATIBILITY.md": ["compatibility-matrix"],
    "docs/guides/INSTALLATION.md": ["installation-matrix"],
    "docs/project/PROJECT_STATUS.md": ["status-matrix"],
    "README_zh-CN.md": ["support-matrix"],
    "docs/guides/PACKAGE_DISTRIBUTION.md": ["distribution-matrix"],
}

# All known section names (for validation)
KNOWN_SECTIONS: set[str] = set()
for _sections in SECTION_REGISTRY.values():
    KNOWN_SECTIONS.update(_sections)

# Tier definitions from requirement 3
TIER_DEFINITIONS: dict[str, str] = {
    "supported": "CI passes, artifact produced, install verified, release gate blocks.",
    "experimental": "Available, not guaranteed, CI non-blocking, noted in release notes.",
    "best-effort": "Source only, docs only, not a gate.",
    "unsupported": "No artifacts, no commitment.",
}

# Map legacy tier names to canonical tiers
TIER_ALIASES: dict[str, str] = {
    "full": "supported",
    "source_only": "best-effort",
}


# ---------------------------------------------------------------------------
# Matrix loading and normalization
# ---------------------------------------------------------------------------


def load_matrix(path: Path | None = None) -> dict[str, Any]:
    """Load and return the release matrix JSON."""
    p = path or MATRIX_PATH
    validated = validate_read_path(p, purpose="release matrix loading")
    with open(validated, encoding="utf-8") as f:
        return json.load(f)


def normalize_entry(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a legacy matrix entry to the canonical schema format.

    Handles field aliases (nginx->nginx_version, os_type->libc, support_tier
    alias mapping, arch normalization) so the generator works with both old
    and new formats.
    """
    entry = dict(raw)

    # Normalize nginx version field
    if "nginx_version" not in entry and "nginx" in entry:
        entry["nginx_version"] = entry["nginx"]

    # Normalize libc field
    if "libc" not in entry and "os_type" in entry:
        entry["libc"] = entry["os_type"]

    # Normalize arch (x86_64->amd64, aarch64->arm64)
    arch = entry.get("arch", "")
    if arch == "x86_64":
        entry["arch"] = "amd64"
    elif arch == "aarch64":
        entry["arch"] = "arm64"

    # Normalize support tier via aliases
    tier = entry.get("support_tier", "")
    if tier in TIER_ALIASES:
        entry["support_tier"] = TIER_ALIASES[tier]

    return entry


def get_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract and normalize entries from the matrix data.

    Reads from 'entries' array if present (spec-40 canonical format),
    otherwise falls back to legacy 'matrix' array.
    """
    raw_entries: list[dict[str, Any]]
    if "entries" in data and data["entries"]:
        raw_entries = data["entries"]
    elif "matrix" in data:
        raw_entries = data["matrix"]
    else:
        return []

    return [normalize_entry(e) for e in raw_entries]


def resolve_tier(data: dict[str, Any], raw_tier: str) -> str:
    """Resolve a tier value through tier_mapping if present, then aliases."""
    mapping = data.get("tier_mapping", {})
    resolved = mapping.get(raw_tier, raw_tier)
    return TIER_ALIASES.get(resolved, resolved)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def validate_schema(data: dict[str, Any]) -> list[str]:
    """Validate matrix data against the schema.

    Uses jsonschema if available, otherwise performs basic structural checks.
    Returns a list of error messages (empty means valid).
    """
    errors: list[str] = []

    # Try jsonschema library first
    with contextlib.suppress(ImportError):
        import jsonschema  # type: ignore[import-untyped]

        if SCHEMA_PATH.exists():
            return _validate_against_jsonschema(data, jsonschema, errors)
    # Fallback: basic structural checks (no jsonschema available)
    if "schema_version" not in data:
        errors.append("Missing required field: schema_version")

    entries = get_entries(data)
    if not entries:
        errors.append("No entries found (neither 'entries' nor 'matrix' array)")
        return errors

    required_fields = {
        "nginx_version", "nginx_channel", "os", "libc", "arch",
        "artifact_type", "test_level", "support_tier",
        "release_blocking", "owner_workflow",
    }
    valid_tiers = {"supported", "experimental", "best-effort", "unsupported"}
    valid_channels = {"stable", "mainline", "oldstable"}

    for i, entry in enumerate(entries):
        if missing := required_fields - set(entry.keys()):
            errors.append(f"Entry {i}: missing fields: {sorted(missing)}")
            continue

        tier = entry.get("support_tier", "")
        if tier not in valid_tiers:
            errors.append(
                f"Entry {i}: invalid support_tier '{tier}' "
                f"(expected one of {sorted(valid_tiers)})"
            )

        channel = entry.get("nginx_channel", "")
        if channel not in valid_channels:
            errors.append(
                f"Entry {i}: invalid nginx_channel '{channel}' "
                f"(expected one of {sorted(valid_channels)})"
            )

        blocking = entry.get("release_blocking")
        if blocking is not None and not isinstance(blocking, bool):
            errors.append(
                f"Entry {i}: release_blocking must be boolean, "
                f"got {type(blocking).__name__}"
            )

    return errors


def _validate_against_jsonschema(data, jsonschema, errors):
    """Validate matrix entries against the JSON schema file."""
    schema_validated = validate_read_path(SCHEMA_PATH, purpose="release matrix schema")
    with open(schema_validated, encoding="utf-8") as f:
        schema = json.load(f)
    # Build a normalized entries-format document for validation
    entries = get_entries(data)
    validate_data = {
        "schema_version": data.get("schema_version", "1.0"),
        "entries": entries,
    }
    try:
        jsonschema.validate(validate_data, schema)
    except jsonschema.ValidationError as e:
        errors.append(f"Schema validation: {e.message}")
    return errors


# ---------------------------------------------------------------------------
# Marker parsing
# ---------------------------------------------------------------------------


class MarkerError(Exception):
    """Raised for malformed marker structure."""


def _handle_begin_marker(
    section_name: str,
    i: int,
    open_section: str | None,
    open_start: int,
    sections: dict[str, tuple[int, int, str]],
) -> tuple[str, int]:
    """Validate and register a BEGIN marker. Returns (section_name, line_index)."""
    if open_section is not None:
        raise MarkerError(
            f"Nested BEGIN marker for '{section_name}' at line {i + 1} "
            f"while '{open_section}' is still open "
            f"(started at line {open_start + 1})"
        )
    if section_name in sections:
        raise MarkerError(
            f"Duplicate section '{section_name}' at line {i + 1}"
        )
    return section_name, i


def _handle_end_marker(
    section_name: str,
    i: int,
    open_section: str | None,
    open_start: int,
    lines: list[str],
    sections: dict[str, tuple[int, int, str]],
) -> None:
    """Validate and close an END marker."""
    if open_section is None:
        raise MarkerError(
            f"END marker for '{section_name}' at line {i + 1} "
            f"without matching BEGIN"
        )
    if section_name != open_section:
        raise MarkerError(
            f"END marker for '{section_name}' at line {i + 1} "
            f"does not match open section '{open_section}' "
            f"(started at line {open_start + 1})"
        )
    inner_lines = lines[open_start + 1 : i]
    sections[section_name] = (open_start, i, "".join(inner_lines))


def parse_markers(content: str) -> dict[str, tuple[int, int, str]]:
    """Parse marker-delimited sections in file content.

    Returns a dict mapping section_name -> (start_line, end_line, existing_content)
    where start_line is the line index of BEGIN and end_line is the line index of
    END.

    Raises MarkerError on mismatched BEGIN/END or duplicate section names.
    """
    lines = content.splitlines(keepends=True)
    sections: dict[str, tuple[int, int, str]] = {}
    open_section: str | None = None
    open_start: int = -1

    for i, line in enumerate(lines):
        begin_m = BEGIN_RE.match(line)
        end_m = END_RE.match(line)

        if begin_m:
            open_section, open_start = _handle_begin_marker(
                begin_m.group(2), i, open_section, open_start, sections
            )
        elif end_m:
            _handle_end_marker(
                end_m.group(2), i, open_section, open_start, lines, sections
            )
            open_section = None

    if open_section is not None:
        raise MarkerError(
            f"Missing END marker for section '{open_section}' "
            f"(BEGIN at line {open_start + 1})"
        )

    return sections


def replace_section_content(
    content: str, section_name: str, new_content: str
) -> str:
    """Replace the content between BEGIN/END markers for a given section."""
    lines = content.splitlines(keepends=True)
    result: list[str] = []
    in_section = False
    replaced = False

    for line in lines:
        begin_m = BEGIN_RE.match(line)
        end_m = END_RE.match(line)

        if begin_m and begin_m.group(2) == section_name:
            result.append(line)
            if new_content and not new_content.endswith("\n"):
                result.append(new_content + "\n")
            elif new_content:
                result.append(new_content)
            in_section = True
            replaced = True
        elif end_m and end_m.group(2) == section_name:
            in_section = False
            result.append(line)
        elif not in_section:
            result.append(line)

    if not replaced:
        raise MarkerError(
            f"Section '{section_name}' not found in file content"
        )

    return "".join(result)


# ---------------------------------------------------------------------------
# Content generators
# ---------------------------------------------------------------------------


def _version_sort_key(version: str) -> tuple[int, ...]:
    """Convert version string to tuple for numeric sorting."""
    parts: list[int] = []
    for part in version.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _generate_support_matrix(
    entries: list[dict[str, Any]], data: dict[str, Any]
) -> str:
    """Generate the support matrix table (NGINX versions x platforms x tiers)."""
    additional = data.get("additional_artifacts", [])

    lines = [
        "",
        "| NGINX | Channel | OS | libc | Arch | Artifact | Tier | Blocking |",
        "|-------|---------|-----|------|------|----------|------|----------|",
    ]

    sorted_entries = sorted(
        entries,
        key=lambda e: (
            _version_sort_key(e.get("nginx_version", "")),
            e.get("nginx_channel", ""),
            e.get("os", ""),
            e.get("arch", ""),
        ),
        reverse=True,
    )

    for entry in sorted_entries:
        tier = resolve_tier(data, entry.get("support_tier", ""))
        blocking = "Yes" if entry.get("release_blocking") else "No"
        lines.append(
            f"| {entry.get('nginx_version', '')} "
            f"| {entry.get('nginx_channel', '')} "
            f"| {entry.get('os', '')} "
            f"| {entry.get('libc', '')} "
            f"| {entry.get('arch', '')} "
            f"| {entry.get('artifact_type', '')} "
            f"| {tier} "
            f"| {blocking} |"
        )

    if additional:
        lines.extend([
            "",
            "**Additional Artifacts:**",
            "",
            "| Artifact | Channel | Tier | Blocking | Note |",
            "|----------|---------|------|----------|------|",
        ])
        for art in additional:
            tier = resolve_tier(data, art.get("support_tier", ""))
            blocking = "Yes" if art.get("release_blocking") else "No"
            channel = art.get("nginx_channel", "all")
            lines.append(
                f"| {art.get('artifact_type', '')} "
                f"| {channel} "
                f"| {tier} "
                f"| {blocking} "
                f"| {art.get('note', '')} |"
            )

    lines.append("")
    return "\n".join(lines)


def _generate_tier_legend(
    _entries: list[dict[str, Any]], _data: dict[str, Any]
) -> str:
    """Generate the tier legend section."""
    lines = [
        "",
        "| Tier | Definition |",
        "|------|------------|",
    ]
    lines.extend(
        f"| **{tier}** | {definition} |"
        for tier, definition in TIER_DEFINITIONS.items()
    )
    lines.append("")
    return "\n".join(lines)


def _generate_artifact_summary(
    entries: list[dict[str, Any]], data: dict[str, Any]
) -> str:
    """Generate artifact availability summary."""
    additional = data.get("additional_artifacts", [])

    # Group by artifact type
    artifacts: dict[str, dict[str, Any]] = {}
    for e in entries:
        atype = e.get("artifact_type", "unknown")
        if atype not in artifacts:
            artifacts[atype] = {"tiers": set(), "archs": set(), "count": 0}
        artifacts[atype]["tiers"].add(resolve_tier(data, e.get("support_tier", "")))
        artifacts[atype]["archs"].add(e.get("arch", "?"))
        artifacts[atype]["count"] += 1

    for art in additional:
        atype = art.get("artifact_type", "unknown")
        if atype not in artifacts:
            artifacts[atype] = {"tiers": set(), "archs": set(), "count": 0}
        artifacts[atype]["tiers"].add(resolve_tier(data, art.get("support_tier", "")))
        if "arch" in art:
            artifacts[atype]["archs"].add(art["arch"])
        artifacts[atype]["count"] += 1

    lines = [
        "",
        "| Artifact Type | Architectures | Tier(s) | Combinations |",
        "|---------------|---------------|---------|--------------|",
    ]

    for atype in sorted(artifacts.keys()):
        info = artifacts[atype]
        archs = ", ".join(sorted(info["archs"])) if info["archs"] else "various"
        tiers = ", ".join(sorted(info["tiers"]))
        lines.append(f"| {atype} | {archs} | {tiers} | {info['count']} |")

    lines.append("")
    return "\n".join(lines)


def _generate_platform_summary(
    entries: list[dict[str, Any]], data: dict[str, Any]
) -> str:
    """Generate platform support summary."""
    # Group by os/libc combination
    platforms: dict[str, dict[str, Any]] = {}
    for e in entries:
        key = f"{e.get('os', '?')}/{e.get('libc', '?')}"
        if key not in platforms:
            platforms[key] = {"archs": set(), "versions": set(), "tiers": set()}
        platforms[key]["archs"].add(e.get("arch", "?"))
        platforms[key]["versions"].add(e.get("nginx_version", "?"))
        platforms[key]["tiers"].add(resolve_tier(data, e.get("support_tier", "")))

    lines = [
        "",
        "| Platform | Architectures | NGINX Versions | Tier(s) |",
        "|----------|---------------|----------------|---------|",
    ]

    for platform in sorted(platforms.keys()):
        info = platforms[platform]
        archs = ", ".join(sorted(info["archs"]))
        versions = ", ".join(
            sorted(info["versions"], key=_version_sort_key, reverse=True)
        )
        tiers = ", ".join(sorted(info["tiers"]))
        lines.append(f"| {platform} | {archs} | {versions} | {tiers} |")

    lines.append("")
    return "\n".join(lines)


def _generate_compatibility_matrix(
    entries: list[dict[str, Any]], data: dict[str, Any]
) -> str:
    """Generate full compatibility details for docs/COMPATIBILITY.md.

    Includes all columns: NGINX version, channel, OS, libc, arch, artifact,
    test level, tier, blocking flag, and owner workflow.
    """
    lines = [
        "",
        "## Platform Compatibility Matrix",
        "",
        "| NGINX Version | Channel | OS | libc | Arch | Artifact "
        "| Test Level | Tier | Blocking | Workflow |",
        "|---------------|---------|-----|------|------|----------"
        "|------------|------|----------|----------|",
    ]

    sorted_entries = sorted(
        entries,
        key=lambda e: (
            _version_sort_key(e.get("nginx_version", "")),
            e.get("nginx_channel", ""),
            e.get("os", ""),
            e.get("arch", ""),
        ),
        reverse=True,
    )

    for entry in sorted_entries:
        tier = resolve_tier(data, entry.get("support_tier", ""))
        blocking = "Yes" if entry.get("release_blocking") else "No"
        lines.append(
            f"| {entry.get('nginx_version', '')} "
            f"| {entry.get('nginx_channel', '')} "
            f"| {entry.get('os', '')} "
            f"| {entry.get('libc', '')} "
            f"| {entry.get('arch', '')} "
            f"| {entry.get('artifact_type', '')} "
            f"| {entry.get('test_level', '')} "
            f"| {tier} "
            f"| {blocking} "
            f"| `{entry.get('owner_workflow', '')}` |"
        )

    lines.extend(("", "### Tier Definitions", ""))
    lines.extend(
        f"- **{tier_name}**: {definition}"
        for tier_name, definition in TIER_DEFINITIONS.items()
    )
    lines.append("")

    return "\n".join(lines)


def _generate_installation_matrix(
    entries: list[dict[str, Any]], data: dict[str, Any]
) -> str:
    """Generate installation availability for docs/guides/INSTALLATION.md.

    Groups entries by artifact type to show which packages are available
    for which platforms.
    """
    additional = data.get("additional_artifacts", [])

    # Group by artifact type
    artifacts: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        atype = entry.get("artifact_type", "unknown")
        artifacts.setdefault(atype, []).append(entry)

    lines = [
        "",
        "## Available Packages by Platform",
        "",
    ]

    for atype in sorted(artifacts.keys()):
        aentries = sorted(
            artifacts[atype],
            key=lambda e: (
                _version_sort_key(e.get("nginx_version", "")),
                e.get("os", ""),
                e.get("arch", ""),
            ),
            reverse=True,
        )
        lines.extend(
            (
                f"### {atype}",
                "",
                "| NGINX | Channel | OS | libc | Arch | Tier |",
                "|-------|---------|-----|------|------|------|",
            )
        )
        for entry in aentries:
            tier = resolve_tier(data, entry.get("support_tier", ""))
            lines.append(
                f"| {entry.get('nginx_version', '')} "
                f"| {entry.get('nginx_channel', '')} "
                f"| {entry.get('os', '')} "
                f"| {entry.get('libc', '')} "
                f"| {entry.get('arch', '')} "
                f"| {tier} |"
            )
        lines.append("")

    if additional:
        lines.extend(("### Additional Distribution Channels", ""))
        for art in additional:
            tier = resolve_tier(data, art.get("support_tier", ""))
            lines.append(
                f"- **{art.get('artifact_type', '')}** ({tier}): "
                f"{art.get('note', '')}"
            )
        lines.append("")

    return "\n".join(lines)


def _count_tiers_and_blocking(
    entries: list[dict[str, Any]],
    additional: list[dict[str, Any]],
    data: dict[str, Any],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Count entries per tier and collect release-blocking entries."""
    tier_counts: dict[str, int] = {}
    blocking_entries: list[dict[str, Any]] = []

    for entry in (*entries, *additional):
        tier = resolve_tier(data, entry.get("support_tier", ""))
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        if entry.get("release_blocking"):
            blocking_entries.append(entry)

    return tier_counts, blocking_entries


def _blocking_entry_desc(entry: dict[str, Any]) -> str:
    """Build a human-readable description for a blocking entry."""
    if "nginx_version" in entry:
        return (
            f"{entry.get('nginx_version', '')} "
            f"{entry.get('os', '')} "
            f"{entry.get('libc', '')} "
            f"{entry.get('arch', '')} "
            f"{entry.get('artifact_type', '')}"
        )
    return entry.get("note", entry.get("artifact_type", ""))


def _generate_status_matrix(
    entries: list[dict[str, Any]], data: dict[str, Any]
) -> str:
    """Generate tier summary and release-blocking entries for PROJECT_STATUS.md."""
    additional = data.get("additional_artifacts", [])
    tier_counts, blocking_entries = _count_tiers_and_blocking(
        entries, additional, data
    )

    lines = [
        "",
        "## Release Matrix Summary",
        "",
        "### Tier Distribution",
        "",
        "| Tier | Count |",
        "|------|-------|",
    ]
    for tier_name in ["supported", "experimental", "best-effort", "unsupported"]:
        count = tier_counts.get(tier_name, 0)
        if count > 0:
            lines.append(f"| {tier_name} | {count} |")
    lines.extend(("", "### Release-Blocking Entries", ""))
    if blocking_entries:
        lines.extend(("| Entry | Workflow |", "|-------|----------|"))
        for entry in blocking_entries:
            desc = _blocking_entry_desc(entry)
            lines.append(f"| {desc} | `{entry.get('owner_workflow', '')}` |")
    else:
        lines.append("No release-blocking entries defined.")
    lines.append("")

    return "\n".join(lines)


def _generate_distribution_matrix(
    entries: list[dict[str, Any]], data: dict[str, Any]
) -> str:
    """Generate package distribution channels for PACKAGE_DISTRIBUTION.md."""
    additional = data.get("additional_artifacts", [])

    # Group entries by workflow
    by_workflow: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        wf = entry.get("owner_workflow", "unknown")
        by_workflow.setdefault(wf, []).append(entry)

    lines = [
        "",
        "### Release Matrix Distribution Overview",
        "",
    ]

    if additional:
        lines.extend(
            (
                "### Package Types",
                "",
                "| Type | Channel | Tier | Blocking | Details |",
                "|------|---------|------|----------|---------|",
            )
        )
        for art in additional:
            tier = resolve_tier(data, art.get("support_tier", ""))
            blocking = "Yes" if art.get("release_blocking") else "No"
            channel = art.get("nginx_channel", "all")
            lines.append(
                f"| {art.get('artifact_type', '')} "
                f"| {channel} "
                f"| {tier} "
                f"| {blocking} "
                f"| {art.get('note', '')} |"
            )
        lines.append("")

    lines.extend(
        (
            "### Build Workflows",
            "",
            "| Workflow | Entries | Tiers |",
            "|----------|---------|-------|",
        )
    )
    for wf, wf_entries in sorted(by_workflow.items()):
        tiers = sorted(
            {resolve_tier(data, e.get("support_tier", "")) for e in wf_entries}
        )
        lines.append(f"| `{wf}` | {len(wf_entries)} | {', '.join(tiers)} |")
    lines.append("")

    return "\n".join(lines)


def _resolve_section_doc_path(file_path: Path) -> Path:
    """Resolve and restrict documentation target paths."""
    validated = validate_write_path_within_root(
        file_path, ROOT, purpose="release matrix doc"
    )
    allowed_targets = {
        (ROOT / rel_path).resolve() for rel_path in SECTION_REGISTRY
    }
    if validated not in allowed_targets:
        raise ValueError(
            f"Refusing to write unregistered release matrix documentation "
            f"target: {validated}"
        )
    return validated


# Section name -> generator function mapping
SECTION_GENERATORS: dict[
    str,
    Any,
] = {
    "support-matrix": _generate_support_matrix,
    "tier-legend": _generate_tier_legend,
    "artifact-summary": _generate_artifact_summary,
    "platform-summary": _generate_platform_summary,
    "compatibility-matrix": _generate_compatibility_matrix,
    "installation-matrix": _generate_installation_matrix,
    "status-matrix": _generate_status_matrix,
    "distribution-matrix": _generate_distribution_matrix,
}


def generate_section(
    section_name: str, entries: list[dict[str, Any]], data: dict[str, Any]
) -> str:
    """Generate content for a named section."""
    generator = SECTION_GENERATORS.get(section_name)
    if generator is None:
        raise ValueError(f"No generator for section '{section_name}'")
    return generator(entries, data)


# ---------------------------------------------------------------------------
# Release notes
# ---------------------------------------------------------------------------


def generate_release_notes(
    data: dict[str, Any],
    entries: list[dict[str, Any]],
    previous_data: dict[str, Any] | None = None,
) -> str:
    """Generate release notes markdown from the matrix.

    Includes:
      1. Supported NGINX versions with channels
      2. Artifact availability table (type x tier x platforms)
      3. Platform/architecture coverage summary
      4. Tier summary with counts
      5. Changes from previous (if previous_data provided)
    """
    additional = data.get("additional_artifacts", [])

    lines: list[str] = [
        "## Platform Support Matrix",
        "",
        "### Supported NGINX Versions",
        "",
    ]
    versions = sorted(
        {e.get("nginx_version", "") for e in entries if e.get("nginx_version")},
        key=_version_sort_key,
        reverse=True,
    )
    for v in versions:
        channel = next(
            (e.get("nginx_channel", "?") for e in entries if e.get("nginx_version") == v),
            "?",
        )
        lines.append(f"- {v} ({channel})")
    lines.extend(
        (
            "",
            "### Artifacts",
            "",
            "| Type | Tier | Platforms |",
            "|------|------|-----------|",
        )
    )
    artifact_rows = _rn_build_artifact_rows(data, entries, additional)
    lines.extend(
        f"| {row['type']} | {row['tier']} | {row['platforms']} |"
        for row in artifact_rows
    )
    lines.extend(("", "### Coverage Summary", ""))
    tier_counts: dict[str, int] = {}
    for entry in entries:
        tier = resolve_tier(data, entry.get("support_tier", ""))
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    for art in additional:
        tier = resolve_tier(data, art.get("support_tier", ""))
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    tier_descriptions = {
        "supported": "release blocking",
        "experimental": "non-blocking",
        "best-effort": "",
    }
    for tier in ("supported", "experimental", "best-effort"):
        count = tier_counts.get(tier, 0)
        desc = tier_descriptions.get(tier, "")
        if count > 0:
            if desc:
                lines.append(f"- **{tier}**: {count} entries ({desc})")
            else:
                lines.append(f"- **{tier}**: {count} entries")
    lines.append("")

    # --- Section 4: Changes from previous ---
    if previous_data is not None:
        changes = _rn_generate_changes(data, entries, previous_data)
        lines.extend(changes)

    return "\n".join(lines)


def _rn_normalize_arch(arch: str) -> str:
    """Normalize architecture names for release notes display."""
    mapping = {"x86_64": "amd64", "aarch64": "arm64"}
    return mapping.get(arch, arch)


def _rn_display_artifact(artifact: str) -> str:
    """Map artifact_type to human-friendly display name."""
    mapping = {
        "dynamic-module": "Dynamic module (binary)",
        "deb-package": "DEB package",
        "rpm-package": "RPM package",
        "docker-image": "Docker image",
        "homebrew-formula": "Homebrew formula",
        "source": "Source",
    }
    return mapping.get(artifact, artifact)


def _rn_build_artifact_rows(
    data: dict[str, Any],
    entries: list[dict[str, Any]],
    additional: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build artifact table rows grouped by type and tier."""
    rows: list[dict[str, str]] = []

    # Group matrix entries by (artifact_type, tier) -> set of platform strings
    type_tier_platforms: dict[tuple[str, str], set[str]] = {}
    for entry in entries:
        artifact = entry.get("artifact_type", "unknown")
        tier = resolve_tier(data, entry.get("support_tier", "unknown"))
        os_name = entry.get("os", "unknown")
        libc = entry.get("libc", "")
        arch = _rn_normalize_arch(entry.get("arch", ""))
        platform_str = f"{os_name} {libc} {arch}"
        key = (artifact, tier)
        type_tier_platforms.setdefault(key, set()).add(platform_str)

    for (artifact, tier), platforms in sorted(type_tier_platforms.items()):
        display_type = _rn_display_artifact(artifact)
        platform_summary = _rn_summarize_platforms(platforms)
        rows.append({"type": display_type, "tier": tier, "platforms": platform_summary})

    # Additional artifacts (docker, deb, rpm, homebrew, source)
    for item in additional:
        artifact = item.get("artifact_type", "unknown")
        tier = resolve_tier(data, item.get("support_tier", "unknown"))
        display_type = _rn_display_artifact(artifact)
        note = item.get("note", "")
        os_name = item.get("os", "")
        platform_summary = note or (os_name or "any")
        rows.append({"type": display_type, "tier": tier, "platforms": platform_summary})

    return rows


def _rn_summarize_platforms(platforms: set[str]) -> str:
    """Collapse a set of platform strings into a compact summary.

    Groups by os+libc and collects unique architectures.
    """
    groups: dict[str, set[str]] = {}
    for p in platforms:
        parts = p.split()
        if len(parts) >= 3:
            os_libc = f"{parts[0]} {parts[1]}"
            arch = parts[2]
        elif len(parts) == 2:
            os_libc = parts[0]
            arch = parts[1]
        else:
            os_libc = p
            arch = ""
        groups.setdefault(os_libc, set())
        if arch:
            groups[os_libc].add(arch)

    summaries = []
    for os_libc, arches in sorted(groups.items()):
        if arches:
            arch_str = "/".join(sorted(arches))
            summaries.append(f"{os_libc} {arch_str}")
        else:
            summaries.append(os_libc)
    return ", ".join(summaries)


def _rn_entry_key(entry: dict[str, Any]) -> tuple[str, ...]:
    """Build a unique key for a matrix entry."""
    version = entry.get("nginx_version") or entry.get("nginx", "")
    os_name = entry.get("os", "")
    libc = entry.get("libc", "")
    arch = _rn_normalize_arch(entry.get("arch", ""))
    artifact = entry.get("artifact_type", "")
    return (version, os_name, libc, arch, artifact)


def _rn_generate_changes(
    data: dict[str, Any],
    current_entries: list[dict[str, Any]],
    previous_data: dict[str, Any],
) -> list[str]:
    """Generate a 'Changes from Previous' section comparing two matrices."""
    lines: list[str] = ["### Changes from Previous", ""]
    prev_entries = get_entries(previous_data)

    cur_versions = {
        e.get("nginx_version", "")
        for e in current_entries
        if e.get("nginx_version")
    }
    prev_versions = {
        e.get("nginx_version", "")
        for e in prev_entries
        if e.get("nginx_version")
    }

    added_versions = sorted(
        cur_versions - prev_versions,
        key=_version_sort_key,
        reverse=True,
    )
    removed_versions = sorted(
        prev_versions - cur_versions,
        key=_version_sort_key,
        reverse=True,
    )

    cur_keys = {_rn_entry_key(e) for e in current_entries}
    prev_keys = {_rn_entry_key(e) for e in prev_entries}
    added_keys = cur_keys - prev_keys
    removed_keys = prev_keys - cur_keys

    # Detect tier changes for entries present in both
    tier_changes = _rn_detect_tier_changes(data, current_entries, prev_entries, previous_data=previous_data)

    if added_versions:
        _rn_append_change_bullets(
            lines, "**Added versions:**", added_versions
        )
    if removed_versions:
        _rn_append_change_bullets(
            lines, "**Removed versions:**", removed_versions
        )
    # Platform-level adds/removes (excluding version-level changes)
    platform_added = [k for k in added_keys if k[0] not in added_versions]
    platform_removed = [k for k in removed_keys if k[0] not in removed_versions]

    if platform_added:
        lines.extend((f"**Added platforms:** {len(platform_added)} new entries", ""))
    if platform_removed:
        lines.extend(
            (
                f"**Removed platforms:** {len(platform_removed)} entries removed",
                "",
            )
        )
    if tier_changes:
        _rn_append_change_bullets(
            lines, "**Tier changes:**", tier_changes
        )
    if not (added_versions or removed_versions or platform_added
            or platform_removed or tier_changes):
        lines.extend(("No changes detected.", ""))
    return lines


def _rn_append_change_bullets(
    lines: list[str],
    heading: str,
    items: list[str],
) -> None:
    lines.append(heading)
    lines.extend(f"- {item}" for item in items)
    lines.append("")


def _rn_detect_tier_changes(
    data: dict[str, Any],
    current_entries: list[dict[str, Any]],
    previous_entries: list[dict[str, Any]],
    previous_data: dict[str, Any] | None = None,
) -> list[str]:
    """Detect tier changes for entries present in both matrices."""
    changes: list[str] = []
    prev_resolve_data = previous_data if previous_data is not None else data
    prev_map: dict[tuple[str, ...], str] = {}
    for entry in previous_entries:
        key = _rn_entry_key(entry)
        prev_map[key] = resolve_tier(prev_resolve_data, entry.get("support_tier", "unknown"))

    for entry in current_entries:
        key = _rn_entry_key(entry)
        if key in prev_map:
            cur_tier = resolve_tier(data, entry.get("support_tier", "unknown"))
            prev_tier = prev_map[key]
            if cur_tier != prev_tier:
                desc = (
                    f"{key[0]} {key[1]} {key[2]} {key[3]} {key[4]}: "
                    f"{prev_tier} \u2192 {cur_tier}"
                )
                changes.append(desc)
    return changes


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


def check_file(
    file_path: Path,
    entries: list[dict[str, Any]],
    data: dict[str, Any],
    _verbose: bool = False,  # noqa: ARG001 — kept for API symmetry with write_file
) -> list[str]:
    """Check a single file for marker consistency.

    Returns list of errors (mismatch = error). Missing files and missing
    markers are treated as warnings (printed to stderr) and do not cause
    a non-zero exit, since markers may not have been added yet (wave 2).
    """
    errors: list[str] = []
    file_path = _resolve_section_doc_path(file_path)
    rel_path = file_path.relative_to(ROOT).as_posix()

    if not file_path.exists():
        print(
            f"WARNING: {rel_path}: target file not found",
            file=sys.stderr,
        )
        return errors

    content = file_path.read_text(encoding="utf-8")

    try:
        sections = parse_markers(content)
    except MarkerError as e:
        errors.append(f"{rel_path}: {e}")
        return errors

    if not sections:
        print(
            f"WARNING: {rel_path}: no release-matrix markers "
            f"(will be added in wave 2)",
            file=sys.stderr,
        )
        return errors

    # Warn about unknown sections
    for section_name in sections:
        if section_name not in KNOWN_SECTIONS:
            print(
                f"WARNING: {rel_path}: unknown section '{section_name}'",
                file=sys.stderr,
            )

    # Check expected sections
    expected_sections = SECTION_REGISTRY.get(rel_path, [])
    for section_name in expected_sections:
        if section_name not in sections:
            print(
                f"WARNING: {rel_path}: expected section '{section_name}' "
                f"not found (markers not yet added)",
                file=sys.stderr,
            )
            continue

        _, _, existing = sections[section_name]
        expected = generate_section(section_name, entries, data)

        if existing.rstrip("\n") != expected.rstrip("\n"):
            # Show unified diff for mismatch
            diff = difflib.unified_diff(
                expected.splitlines(keepends=True),
                existing.splitlines(keepends=True),
                fromfile="expected",
                tofile="actual",
            )
            mismatch_msg = (
                f"MISMATCH: {rel_path} section '{section_name}'"
            )
            if diff_text := "".join(diff):
                mismatch_msg += f"\n{diff_text}"
            errors.append(mismatch_msg)

    return errors


def write_file(
    file_path: Path,
    entries: list[dict[str, Any]],
    data: dict[str, Any],
    verbose: bool = False,
) -> list[str]:
    """Write generated content into marker sections of a file.

    Returns list of errors encountered.
    """
    errors: list[str] = []
    try:
        file_path = _resolve_section_doc_path(file_path)
    except ValueError as e:
        errors.append(str(e))
        return errors

    rel_path = file_path.relative_to(ROOT).as_posix()

    if not file_path.exists():
        errors.append(f"{rel_path}: file does not exist (cannot write)")
        return errors

    content = file_path.read_text(encoding="utf-8")

    try:
        sections = parse_markers(content)
    except MarkerError as e:
        errors.append(f"{rel_path}: {e}")
        return errors

    # Warn about unknown sections
    for section_name in sections:
        if section_name not in KNOWN_SECTIONS and verbose:
            print(
                f"  WARNING: {rel_path}: unknown section '{section_name}'",
                file=sys.stderr,
            )

    expected_sections = SECTION_REGISTRY.get(rel_path, [])
    for section_name in expected_sections:
        if section_name not in sections:
            errors.append(
                f"{rel_path}: expected section '{section_name}' not found "
                f"(add markers to the file)"
            )
            continue

        new_content = generate_section(section_name, entries, data)
        try:
            content = replace_section_content(content, section_name, new_content)
        except MarkerError as e:
            errors.append(f"{rel_path}: {e}")
            return errors

    # SONAR_NOTE(S6553): file_path is resolved and allowlisted above.
    file_path.write_text(content, encoding="utf-8")
    # SONAR_NOTE(S2083): Target doc paths are validated to stay within ROOT.
    if verbose:
        print(f"  Updated: {rel_path}", file=sys.stderr)

    return errors


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Generate/validate documentation sections from release-matrix.json.",
        epilog="Part of spec 40: Release Matrix Source of Truth.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check",
        action="store_true",
        help="Validate generated sections match matrix (exit 1 on mismatch).",
    )
    group.add_argument(
        "--write",
        action="store_true",
        help="Write generated sections into target docs.",
    )
    group.add_argument(
        "--release-notes",
        action="store_true",
        help="Print release notes summary to stdout.",
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=MATRIX_PATH,
        help="Path to release-matrix.json (default: tools/release-matrix.json).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Verbose output.",
    )
    parser.add_argument(
        "--previous",
        type=Path,
        default=None,
        help="Path to a previous release-matrix.json for change comparison (--release-notes only).",
    )
    return parser


def _handle_release_notes(
    matrix_data: dict[str, Any],
    entries: list[dict[str, Any]],
    previous_path: Path | None,
) -> int:
    """Handle --release-notes mode. Returns exit code."""
    previous_data = None
    if previous_path:
        if not previous_path.exists():
            print(
                f"ERROR: Previous matrix not found: {previous_path}",
                file=sys.stderr,
            )
            return 1
        try:
            previous_data = load_matrix(previous_path)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(
                f"ERROR: Failed to load previous matrix: {e}",
                file=sys.stderr,
            )
            return 1
    print(generate_release_notes(matrix_data, entries, previous_data))
    return 0


def _handle_check_or_write(
    matrix_data: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    check: bool,
    verbose: bool,
) -> int:
    """Handle --check or --write mode. Returns exit code."""
    all_errors: list[str] = []

    for rel_path in SECTION_REGISTRY:
        file_path = ROOT / rel_path
        if check:
            errors = check_file(file_path, entries, matrix_data, _verbose=verbose)
        else:
            errors = write_file(file_path, entries, matrix_data, verbose=verbose)
        all_errors.extend(errors)

    if all_errors:
        mode_label = "CHECK" if check else "WRITE"
        print(
            f"{mode_label} FAILED ({len(all_errors)} error(s)):",
            file=sys.stderr,
        )
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        if check:
            print(
                "\nRun 'python3 tools/render_release_matrix_docs.py --write' "
                "to update.",
                file=sys.stderr,
            )
        return 1

    if check:
        print("CHECK PASSED: all sections consistent with matrix.", file=sys.stderr)
    else:
        print("WRITE COMPLETE: all sections updated.", file=sys.stderr)
    return 0


def main() -> int:
    """Main entry point."""
    args = _build_arg_parser().parse_args()

    # Load matrix
    try:
        matrix_data = load_matrix(args.matrix)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"ERROR: Failed to load matrix: {e}", file=sys.stderr)
        return 2

    if schema_errors := validate_schema(matrix_data):
        print("Schema validation failed:", file=sys.stderr)
        for err in schema_errors:
            print(f"  - {err}", file=sys.stderr)
        return 2

    # Get normalized entries
    entries = get_entries(matrix_data)
    if not entries:
        print("WARNING: No entries found in release matrix.", file=sys.stderr)

    # Dispatch mode
    if args.release_notes:
        return _handle_release_notes(matrix_data, entries, args.previous)

    return _handle_check_or_write(
        matrix_data, entries, check=args.check, verbose=args.verbose
    )


if __name__ == "__main__":
    sys.exit(main())
