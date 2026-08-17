#!/usr/bin/env python3
"""
detect_doc_sync.py — Documentation Synchronization Detection (Rule 9, 49)

Rule 9 (docs-tooling): Documentation must reflect the actual implementation.
Rule 49 (docs-tooling): THIRD-PARTY-NOTICES must stay in sync with resolved
  dependency versions.

This detector blocks when it finds documentation drift. It is intentionally
conservative to avoid false positives.

Detection strategy:
  1. Check if CHANGELOG.md exists and has version entries
  2. Check if README mentions key configuration directives
  3. Check if installation guide is current and valid
  4. Check public config contract (directive tables, chart sync,
     Prometheus catalog, inventory, removed symbol absence)

Usage:
  python3 tools/harness/detect_doc_sync.py [directory]
    directory defaults to project root

Exit codes:
  0 — no warnings found
  1 — one or more warnings found
"""

import re
import sys
from pathlib import Path
from typing import List

# Add tools directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path


DIRECTIVES_PATH = Path(
    "components/nginx-module/src/ngx_http_markdown_config_directives_impl.h"
)
DIRECTIVE_NAMES_PATH = Path(
    "components/nginx-module/src/ngx_http_markdown_directive_names.h"
)
HANDLERS_PATH = Path(
    "components/nginx-module/src/ngx_http_markdown_config_handlers_impl.h"
)
CHART_TEMPLATE_PATH = Path("charts/nginx-markdown/templates/configmap.yaml")
CHART_VALUES_PATH = Path("charts/nginx-markdown/values.yaml")
CONFIGURATION_GUIDE_PATH = Path("docs/guides/CONFIGURATION.md")
MIGRATION_GUIDE_PATH = Path("docs/guides/MIGRATION-0.9.2.md")
PUBLIC_INVENTORY_PATH = Path("docs/architecture/PUBLIC_SURFACE_INVENTORY.md")
STREAMING_TROUBLESHOOTING_PATH = Path(
    "docs/guides/streaming-troubleshooting.md"
)
PROFILE_INVENTORY_PATH = Path("docs/architecture/profile-inventory.md")
PROMETHEUS_RENDERER_PATH = Path(
    "components/nginx-module/src/ngx_http_markdown_metrics_v1_renderer.h"
)
PROMETHEUS_GUIDE_PATH = Path("docs/guides/prometheus-metrics.md")
PRODUCTION_SYMBOL_SURFACES = (
    Path("components/nginx-module/src"),
    Path("components/rust-converter/src"),
    Path("charts/nginx-markdown"),
    Path("examples"),
    Path("tools/e2e"),
    Path("tools/sonar"),
    Path(".github/workflows"),
    Path("packaging"),
)
ACTIVE_CONFIG_SURFACES = (
    Path("examples/production"),
    Path("examples/nginx-configs"),
    Path("examples/kubernetes"),
    Path("tools/e2e"),
    Path("components/nginx-module/tests/e2e"),
    Path("tests/e2e"),
    Path("tools/sonar"),
    Path(".sonar"),
    Path(".sonarcloud.properties"),
    Path(".github/workflows/sonarcloud.yml"),
    Path(".github/workflows/real-nginx-ims.yml"),
)
TEXT_SUFFIXES = {
    ".c", ".conf", ".h", ".md", ".properties", ".py", ".rs", ".sh",
    ".toml", ".txt", ".yaml", ".yml",
}
REMOVED_STREAM_SYMBOL_RE = re.compile(
    r"\bstream\s*\.\s*engine\b|\bSTREAM_ENGINE\b"
)


def check_changelog_exists(project_root: Path) -> List[str]:
    """Check that CHANGELOG.md exists and has version entries."""
    changelog = project_root / 'CHANGELOG.md'
    if not changelog.exists():
        return ["CHANGELOG.md not found"]
    
    try:
        content = changelog.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as exc:
        return [f"CHANGELOG.md exists but is unreadable: {exc}"]
    
    # Check if it has at least one version entry
    if not re.search(r'##\s+\[\d+\.\d+\.\d+\]', content):
        return ["CHANGELOG.md has no version entries"]
    
    return []


def check_readme_mentions_key_features(project_root: Path) -> List[str]:
    """Check that README mentions key features."""
    warnings = []

    for readme_name in ['README.md', 'README_zh-CN.md']:
        readme = project_root / readme_name
        if not readme.exists():
            continue

        try:
            content = readme.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as exc:
            warnings.append(f"{readme_name}: unreadable: {exc}")
            continue

        # Check for key configuration directives
        key_directives = [
            'markdown_filter',
            'markdown_limits',
            'markdown_streaming',
        ]
        warnings.extend(
            f"{readme_name}: Key directive '{directive}' not mentioned"
            for directive in key_directives
            if directive not in content
        )
    return warnings


def check_installation_guide_current(project_root: Path) -> List[str]:
    """Check that installation guide references current version patterns."""
    warnings = []
    
    install_guide = project_root / 'docs' / 'guides' / 'INSTALLATION.md'
    if not install_guide.exists():
        return warnings
    
    try:
        content = install_guide.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as exc:
        warnings.append(f"INSTALLATION.md unreadable: {exc}")
        return warnings

    # Check that it has version examples (not checking specific version)
    if not _has_version_example(content):
        warnings.append("INSTALLATION.md has no version examples")

    return warnings


def _has_version_example(content: str) -> bool:
    """Return True when the text contains a dotted semantic version."""
    for token in content.split():
        candidate = token.strip("`*()[]{}<>,.;:\"'")
        if candidate.startswith("v"):
            candidate = candidate[1:]
        parts = candidate.split(".")
        if len(parts) != 3:
            continue
        if all(part.isdigit() for part in parts):
            return True
    return False


def _read_required(
    project_root: Path, relative_path: Path, errors: List[str]
) -> str | None:
    """Read a required worktree file and append an actionable error on failure."""
    path = project_root / relative_path
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"{relative_path}: required contract surface is unreadable: {exc}")
        return None


def _read_optional(project_root: Path, relative_path: Path) -> str | None:
    """Read a worktree file, returning None when absent or unreadable.

    Used for drift-guard inputs whose absence is not a contract violation."""
    path = project_root / relative_path
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _extract_directive_entry(content: str, directive: str) -> str | None:
    """Extract one ngx_command_t initializer from the directive table."""
    pattern = re.compile(
        r"\{\s*ngx_string\(\"" + re.escape(directive)
        + r"\"\),(.*?)(?=\n\s*\},)",
        re.DOTALL,
    )
    match = pattern.search(content)
    return match[1] if match is not None else None


def _expand_directive_macros(content: str) -> tuple[str, list[str]]:
    """Expand canonical directive-name macros before table inspection.

    Returns ``(expanded, errors)``: a directive-name macro used in a
    table entry but never #defined is reported as an error instead of
    being silently treated as its own literal directive string.
    """
    definitions = dict(
        re.findall(
            r"^#define\s+(NGX_HTTP_MARKDOWN_DIRECTIVE_[A-Z0-9_]+)"
            r"\s+(?:\\\s*)?\"([^\"]+)\"",
            content,
            flags=re.MULTILINE,
        )
    )
    errors: list[str] = []

    def _substitute(match: re.Match[str]) -> str:
        macro = match[1]
        defined = definitions.get(macro)
        if defined is None:
            errors.append(
                f"directive-name macro {macro!r} used in ngx_string() "
                "is not #defined"
            )
        return f'ngx_string("{defined if defined is not None else macro}")'

    return (
        re.sub(
            r"ngx_string\((NGX_HTTP_MARKDOWN_DIRECTIVE_[A-Z0-9_]+)\)",
            _substitute,
            content,
        ),
        errors,
    )


def _check_directive_table(content: str) -> List[str]:
    """Validate active streaming directive registration."""
    errors: List[str] = []
    active = _extract_directive_entry(content, "markdown_streaming")
    if active is None or "ngx_http_markdown_streaming," not in active:
        errors.append(
            f"{DIRECTIVES_PATH}: markdown_streaming must use the active "
            "ngx_http_markdown_streaming handler"
        )
    # markdown_streaming_engine reject-only stub has been removed in 0.9.2;
    # NGINX's built-in "unknown directive" error is sufficient.
    removed = _extract_directive_entry(content, "markdown_streaming_engine")
    if removed is not None:
        errors.append(
            f"{DIRECTIVES_PATH}: markdown_streaming_engine reject-only stub "
            "must be removed (0.9.2 surface reset)"
        )
    return errors


def _extract_flavor_handler(content: str) -> str | None:
    """Extract the markdown_flavor handler body from its implementation file."""
    match = re.search(
        r"\nngx_http_markdown_flavor\(.*?\n\}\n",
        content,
        flags=re.DOTALL,
    )
    return match[0] if match is not None else None


def _check_flavor_handler(content: str) -> List[str]:
    """Ensure only commonmark/gfm are active and retired flavors fail closed."""
    handler = _extract_flavor_handler(content)
    if handler is None:
        return [f"{HANDLERS_PATH}: markdown_flavor handler not found"]

    assignments = set(
        re.findall(r"mcf->flavor\s*=\s*(NGX_HTTP_MARKDOWN_FLAVOR_[A-Z_]+)", handler)
    )
    expected = {
        "NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK",
        "NGX_HTTP_MARKDOWN_FLAVOR_GFM",
    }
    errors: List[str] = []
    if assignments != expected:
        errors.append(
            f"{HANDLERS_PATH}: markdown_flavor active assignments must be exactly "
            f"commonmark/gfm, found {sorted(assignments)}"
        )
    rejection_tokens = (
        'mdx_str[] = "mdx"',
        'org_str[] = "org-mode"',
        "&value[1], mdx_str",
        "&value[1], org_str",
        "never had distinct conversion semantics",
        "return NGX_CONF_ERROR;",
    )
    if any(token not in handler for token in rejection_tokens):
        errors.append(
            f"{HANDLERS_PATH}: markdown_flavor must explicitly reject both mdx "
            "and org-mode with the compatibility explanation"
        )
    return errors


def _is_text_contract_file(path: Path) -> bool:
    """Check if a path is a text contract file (text suffix, not pycache)."""
    return (
        path.is_file()
        and path.suffix in TEXT_SUFFIXES
        and "__pycache__" not in path.parts
    )


def _iter_worktree_text_files(project_root: Path, surfaces) -> List[Path]:
    """Return text contract files from tracked, modified, and untracked surfaces."""
    files: List[Path] = []
    for relative_path in surfaces:
        path = project_root / relative_path
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            files.append(path)
        elif path.is_dir():
            files.extend(
                candidate
                for candidate in path.rglob("*")
                if _is_text_contract_file(candidate)
            )
    return sorted(set(files))


def _scan_for_pattern(
    project_root: Path, surfaces, pattern: re.Pattern[str], label: str
) -> List[str]:
    """Find forbidden patterns in current worktree files without Git filtering."""
    errors: List[str] = []
    for path in _iter_worktree_text_files(project_root, surfaces):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{path.relative_to(project_root)}: unreadable: {exc}")
            continue
        if pattern.search(content):
            errors.append(
                f"{path.relative_to(project_root)}: forbidden {label} is present"
            )
    return errors


def _check_chart_contract(template: str, values: str) -> List[str]:
    """Validate the public Helm streaming policy mapping."""
    errors: List[str] = []
    required_template = re.compile(
        r"markdown_streaming\s+\{\{\s*\.Values\.markdown\.streaming\.mode\s*\}\};"
    )
    if required_template.search(template) is None:
        errors.append(
            f"{CHART_TEMPLATE_PATH}: must emit markdown_streaming from "
            ".Values.markdown.streaming.mode"
        )
    forbidden_template = (
        "markdown_streaming_engine",
        ".Values.markdown.streaming.engine",
    )
    if any(token in template for token in forbidden_template):
        errors.append(f"{CHART_TEMPLATE_PATH}: legacy streaming engine key is forbidden")
    if re.search(r"(?m)^ {2}streaming:\s*$", values) is None or re.search(
        r"(?m)^ {4}mode:\s*[\"']?(?:off|auto|force)[\"']?\s*$", values
    ) is None:
        errors.append(
            f"{CHART_VALUES_PATH}: markdown.streaming.mode must define an "
            "off|auto|force policy"
        )
    if re.search(r"(?m)^ {4}engine:\s*", values):
        errors.append(f"{CHART_VALUES_PATH}: markdown.streaming.engine is forbidden")
    return errors


def _check_migration_table(content: str) -> List[str]:
    """Require the frozen explicit replacement surface in active docs."""
    normalized = content.replace("`", "")
    errors: List[str] = []
    required_fragments = (
        "markdown_streaming off | auto | force",
        "markdown_limits conversion_memory=",
        "markdown_limits streaming_buffer=",
    )
    errors.extend(
        f"{CONFIGURATION_GUIDE_PATH}: missing frozen explicit contract fragment "
        f"{fragment}"
        for fragment in required_fragments
        if fragment not in normalized
    )
    return errors


def _directive_registry(content: str) -> tuple[list[str], list[str]]:
    """Return all command-table directives and the reject-only subset."""
    entries = re.findall(
        r"\{\s*ngx_string\(\"(markdown_[^\"]+)\"\),((?:[^\n]|\n(?!\s*\},))*)",
        content,
        flags=re.DOTALL,
    )
    names = [name for name, _ in entries]
    rejected = [name for name, body in entries if "ngx_http_markdown_reject_" in body]
    return names, rejected


def _check_public_inventory(directives: str, inventory: str) -> List[str]:
    """Keep registry counts and reject-only classification synchronized."""
    errors: List[str] = []
    names, rejected = _directive_registry(directives)
    count_match = re.search(
        r"There are (\d+) `markdown_\*` command-table entries: "
        r"(\d+) active parser entries and\s+(\d+) reject-only migration entries",
        inventory,
    )
    if count_match is None:
        errors.append(
            f"{PUBLIC_INVENTORY_PATH}: directive registry count statement is missing"
        )
    else:
        documented = tuple(int(value) for value in count_match.groups())
        actual = (len(names), len(names) - len(rejected), len(rejected))
        if documented != actual:
            errors.append(
                f"{PUBLIC_INVENTORY_PATH}: directive counts {documented} do not "
                f"match command table {actual}"
            )

    reject_section = inventory.partition("### Reject-only migration directives")[2]
    errors.extend(
        f"{PUBLIC_INVENTORY_PATH}: reject-only directive {name} is missing from the reject-only registry"
        for name in rejected
        if f"`{name}`" not in reject_section
    )
    return errors


def _migration_removed_table_rows(migration: str) -> set[tuple[str, str]]:
    """Parse the \"| Removed Directive | Replacement |\" markdown table into
    (directive, replacement) rows.  The header line anchors the section; a
    non-table line ends it."""
    rows: set[tuple[str, str]] = set()
    in_table = False
    for line in migration.splitlines():
        stripped = line.strip()
        if stripped.startswith("| Removed Directive"):
            in_table = True
            continue
        if not in_table:
            continue
        if stripped.startswith(("|---", "|--")):
            continue
        if not stripped.startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) >= 2:
            directive = cells[0].strip("`").strip()
            replacement = cells[1].strip()
            rows.add((directive, replacement))
    return rows


def _check_migration_removed_table(directives: str, migration: str) -> List[str]:
    """Require every reject-only directive to appear as a ROW in the 0.9.2
    migration removed-directive table with a non-empty replacement (tests
    P3-2: the reject-only test list and the migration replacement guide must
    not drift).  A prose mention elsewhere in the document does NOT satisfy
    the check — the table row is what operators follow."""
    errors: List[str] = []
    _, rejected = _directive_registry(directives)
    table_rows = _migration_removed_table_rows(migration)
    for name in rejected:
        row = next((r for r in table_rows if r[0] == name), None)
        if row is None:
            errors.append(
                f"MIGRATION-0.9.2.md: removed directive {name} is missing "
                "from the migration Removed Directive table"
            )
        elif not row[1]:
            errors.append(
                f"MIGRATION-0.9.2.md: removed directive {name} has an empty "
                "replacement cell in the migration table"
            )
    return errors


def _check_migration_removed_table_contract(
    project_root: Path, directives: str | None
) -> List[str]:
    """Read the migration guide and run the removed-table consistency check.

    Extracted so check_public_config_contract stays under the complexity
    threshold.  The guide is read softly: contexts without the migration
    document (test fixtures, older checkouts) skip the check instead of
    failing, since it is a drift guard, not a hard contract surface."""
    migration = _read_optional(project_root, MIGRATION_GUIDE_PATH)
    if directives is None or migration is None:
        return []
    return _check_migration_removed_table(directives, migration)


def _check_otel_reject_docs(directives: str, guide: str) -> List[str]:
    """Require every reject-only OTel control to be documented as rejected."""
    errors: List[str] = []
    _, rejected = _directive_registry(directives)
    for name in rejected:
        if not name.startswith("markdown_otel_"):
            continue
        section_match = re.search(
            rf"#### {re.escape(name)}\n(.*?)(?=\n#### |\n### |\Z)",
            guide,
            flags=re.DOTALL,
        )
        if (
            section_match is None
            or "reject-only in 0.9.1" not in section_match[1]
        ):
            errors.append(
                f"{CONFIGURATION_GUIDE_PATH}: {name} must be documented as "
                "reject-only in 0.9.1"
            )
    return errors


def _check_observability_examples(troubleshooting: str) -> List[str]:
    """Block known non-production diagnostics fields and retired metric names."""
    forbidden = {
        '"version"': "diagnostics version field",
        '"uptime_seconds"': "diagnostics uptime_seconds field",
        '"worker_pid"': "diagnostics worker_pid field",
        '"ttfb_last_seconds"': "diagnostics ttfb_last_seconds field",
        '"peak_memory_last_bytes"': "diagnostics peak_memory_last_bytes field",
        "nginx_markdown_streaming_choice_total": "retired streaming metric name",
    }
    errors: List[str] = [
        f"{STREAMING_TROUBLESHOOTING_PATH}: forbidden {label} is present"
        for token, label in forbidden.items()
        if token in troubleshooting
    ]
    return errors


def _check_active_directive_inventory(
    directives: str, profile_inventory: str
) -> List[str]:
    """Require the active-directive inventory to name every active entry."""
    names, rejected = _directive_registry(directives)
    active = sorted(set(names) - set(rejected))
    errors = [
        f"{PROFILE_INVENTORY_PATH}: active directive {name} is missing"
        for name in active
        if f"`{name}`" not in profile_inventory
    ]
    expected_default = "| `markdown_cache_validation` | ims_only |"
    if expected_default not in profile_inventory:
        errors.append(
            f"{PROFILE_INVENTORY_PATH}: markdown_cache_validation default "
            "must be ims_only"
        )
    return errors


def _check_prometheus_catalog(renderer: str, guide: str) -> List[str]:
    """Require every production renderer family to appear in the guide."""
    raw_families = set(re.findall(r"nginx_markdown_[a-z0-9_]+\b", renderer))
    histogram_suffixes = ("_bucket", "_sum", "_count")
    families = set()
    for name in raw_families:
        base = name
        # Derive the histogram base name from the "_bucket" family alone:
        # a renderer commonly emits only histogram suffix families (e.g.
        # `foo_bucket`) without the unsuffixed base metric, so requiring the
        # bare base to exist in raw_families would mis-flag legitimate docs.
        for suffix in histogram_suffixes:
            if name.endswith(suffix) and len(name) > len(suffix):
                base = name[: -len(suffix)]
                break
        families.add(base)
    return [
        f"{PROMETHEUS_GUIDE_PATH}: production metric family {name} is missing"
        for name in sorted(families)
        if f"`{name}`" not in guide
    ]


def _check_retired_metrics(project_root: Path) -> List[str]:
    """Reject retired metrics outside documented migration references."""
    errors = _scan_for_pattern(
        project_root,
        (Path("docs/guides"), Path("docs/features"), Path("docs/architecture")),
        re.compile(
            r"nginx_markdown_streaming_choice_total|"
            r"nginx_markdown_failures_total\{reason=\\?\""
            r"(?:memory_budget_exceeded|ffi_panic)\\?\"\}"
        ),
        "retired production metric name or failure label",
    )
    migration_pattern = re.compile(r"MIGRATION-\d")
    return [
        error
        for error in errors
        if not migration_pattern.search(Path(error.split(":")[0]).name)
    ]


def _read_directive_source(project_root: Path, errors: List[str]) -> str | None:
    """Read the command table and expand its shared name inventory."""
    directives = _read_required(project_root, DIRECTIVES_PATH, errors)
    directive_names = _read_required(project_root, DIRECTIVE_NAMES_PATH, errors)
    if directives is None or directive_names is None:
        return None
    expanded, macro_errors = _expand_directive_macros(
        f"{directives}\n{directive_names}"
    )
    errors.extend(macro_errors)
    return expanded


def check_public_config_contract(project_root: Path) -> List[str]:
    """Validate the frozen v0.9.2 operator-facing configuration contract."""
    errors: List[str] = []
    directives = _read_directive_source(project_root, errors)
    handlers = _read_required(project_root, HANDLERS_PATH, errors)
    chart_template = _read_required(project_root, CHART_TEMPLATE_PATH, errors)
    chart_values = _read_required(project_root, CHART_VALUES_PATH, errors)
    guide = _read_required(project_root, CONFIGURATION_GUIDE_PATH, errors)
    inventory = _read_required(project_root, PUBLIC_INVENTORY_PATH, errors)
    troubleshooting = _read_required(
        project_root, STREAMING_TROUBLESHOOTING_PATH, errors
    )
    profile_inventory = _read_required(project_root, PROFILE_INVENTORY_PATH, errors)
    prometheus_renderer = _read_required(project_root, PROMETHEUS_RENDERER_PATH, errors)
    prometheus_guide = _read_required(project_root, PROMETHEUS_GUIDE_PATH, errors)

    if directives is not None:
        errors.extend(_check_directive_table(directives))
    if handlers is not None:
        errors.extend(_check_flavor_handler(handlers))
    if chart_template is not None and chart_values is not None:
        errors.extend(_check_chart_contract(chart_template, chart_values))
    if guide is not None:
        errors.extend(_check_migration_table(guide))
    if directives is not None and inventory is not None:
        errors.extend(_check_public_inventory(directives, inventory))
    if directives is not None and guide is not None:
        errors.extend(_check_otel_reject_docs(directives, guide))
    errors.extend(_check_migration_removed_table_contract(project_root, directives))
    if troubleshooting is not None:
        errors.extend(_check_observability_examples(troubleshooting))
    if directives is not None and profile_inventory is not None:
        errors.extend(_check_active_directive_inventory(directives, profile_inventory))
    if prometheus_renderer is not None and prometheus_guide is not None:
        errors.extend(_check_prometheus_catalog(prometheus_renderer, prometheus_guide))

    errors.extend(
        _scan_for_pattern(
            project_root,
            PRODUCTION_SYMBOL_SURFACES,
            REMOVED_STREAM_SYMBOL_RE,
            "stream.engine/STREAM_ENGINE production symbol",
        )
    )
    errors.extend(
        _scan_for_pattern(
            project_root,
            ACTIVE_CONFIG_SURFACES,
            re.compile(r"\bmarkdown_streaming_engine\b"),
            "markdown_streaming_engine directive in an active config surface",
        )
    )
    errors.extend(_check_retired_metrics(project_root))
    return errors


def main():
    if len(sys.argv) > 1:
        project_root = Path(validate_read_path(sys.argv[1]))
    else:
        project_root = Path(__file__).parent.parent.parent
    
    if not project_root.exists():
        print(f"Project directory not found: {project_root}", file=sys.stderr)
        sys.exit(1)
    
    all_errors = []
    all_errors.extend(check_changelog_exists(project_root))
    all_errors.extend(check_readme_mentions_key_features(project_root))
    all_errors.extend(check_installation_guide_current(project_root))
    all_errors.extend(check_public_config_contract(project_root))
    
    if all_errors:
        print(
            f"Documentation sync check failed ({len(all_errors)} violation(s)):",
            file=sys.stderr,
        )
        for error in all_errors:
            print(f"  ERROR: {error}", file=sys.stderr)
        sys.exit(1)
    else:
        print("OK: Documentation synchronization checks passed")

    sys.exit(0)


if __name__ == '__main__':
    main()
