"""Release gate check functions for 0.4.0 validation.

Each function returns a tuple of (passed: bool, messages: list[str]).
"""

import os
import re
from typing import Iterator, Optional, Tuple, List

from tools.release.legacy.release_constants import SUBSPECS_KEYWORDS

# Required sections in requirements documents (Property 1)
#
# Governance and tooling specs (e.g. overall-scope, benchmark-corpus) use a
# "Cross-Cutting Constraints" section that covers compatibility, security,
# rollout, observability, and migration concerns in a single block.  The
# patterns below accept that heading as an alternative so that both
# feature-oriented and governance-oriented requirements documents pass.
_REQUIRED_SECTIONS = [
    (r"goals", "Goals"),
    (r"non[- ]?goals", "Non-Goals"),
    (r"compatib|cross.?cutting", "Compatibility Impact"),
    (r"test\s*plan|testing|test\s*strat|test\s*matrix", "Test Plan"),
    (r"documentation|cross.?cutting", "Documentation Impact"),
    (r"rollout|cross.?cutting|release\s*train", "Rollout Impact"),
    (r"observability|cross.?cutting|metric", "Observability Impact"),
    (r"security|cross.?cutting|sanitiz", "Security Considerations"),
    (r"migration|operator\s*guidance|cross.?cutting", "Migration / Operator Guidance"),
]

# Required design document answer fields (Property 1 — R3.4)
#
# R3.4 requires each design doc to *address* these topics, not necessarily
# under a dedicated heading.  Some specs discuss backward compatibility in
# "Scope Anchors", rollback in "Error Handling / Fallback Strategy", etc.
# Tooling-only specs (benchmark, packaging) may not have runtime backward
# compatibility concerns but still reference "existing" infrastructure.
# The check searches the full document content (not just headings) for
# evidence that each topic is addressed.
_DESIGN_REQUIRED_FIELDS = [
    (r"backward.?compat|compat.*preserv|unchanged.*behav|existing.*infra|no\s+runtime|documentation.only|no.*public.*api|no.*ffi|byte.identical|output.*equivalen", "Backward Compatibility"),
    (r"config.*directive|directive.*config|new.*directive|no\s+new.*directive|naming.*convention|configuration", "Configuration Directives"),
    (r"metric|log.*change|observ|instrument|report", "Metrics or Logs"),
    (r"test", "Testing"),
    (r"roll.?out|deploy|enabl|install|first.?run|cookbook|ci\s+integrat", "Rollout"),
    (r"roll.?back|revert|disabl|fail.?open|fallback|error\s+handl", "Rollback"),
    (r"not.?done|non.?goal|out.?of.?scope|intentionally|scope.*anchor|not.*streaming|no\s+new", "What Is Not Done"),
    (r"0\.4\.0.*vs|long.?term|architecture.*commit|scope.*anchor|0\.5|deferred|documentation.only|no\s+runtime", "0.4.0 vs Long-Term"),
]

# Required boundary description fields (Property 3)
_BOUNDARY_FIELDS = [
    "capability",
    "0.4.0 scope",
    "0.5.x scope",
    "rationale",
    "prerequisites",
]

# Required DoD checkpoints (Property 5)
_DOD_CHECKPOINTS = [
    "functionally correct",
    "observable",
    "rollback-safe",
    "documented",
    "auditable",
    "compatible",
]

# Canonical filenames for sub-spec documents (avoids literal duplication)
_REQUIREMENTS_DOC = "requirements.md"
_DESIGN_DOC = "design.md"


# Verifiable action indicators for checklist items (Property 11)
# Each item must reference a specific artifact, command, or review action (R10.3).
_VERIFIABLE_INDICATORS = [
    # ── command patterns ──
    r"make\s+\S+",          # make command
    r"cargo\s+\S+",         # any cargo command
    r"npm\s+\S+",           # any npm command
    r"python3?\s+\S+",      # any python command
    r"curl\s+",             # curl command
    # ── file path / extension patterns ──
    r"docs/",               # file path reference
    r"tools/",              # file path reference
    r"components/",         # file path reference
    r"specs/",              # file path reference
    r"\.(md|py|ya?ml|json|toml|sh|c|h|rs|txt)\b",  # file extension reference
    # ── specific artifact verbs (objective, not subjective) ──
    r"\bgenerated\b",       # artifact was generated
    r"\barchived\b",        # artifact was archived
    r"\bcoverage\b",        # coverage metric
    r"\bidentical\b",       # exact-match assertion
    r"\bunchanged\b",       # no-diff assertion
    r"\bmatched?\b",        # pattern/value match
    # ── qualified action phrases (require specifying what/where) ──
    r"\bverif(y|ied)\s+by\b",        # "verified by" requires specifier
    r"\bpass(es)?\s+(on|in|with)\b",  # "passes on/in/with" requires context
    # ── CI / test type references ──
    r"CI\b",                # CI reference
    r"e2e\b",               # test reference
    r"unit test",           # test reference
    r"integration test",    # test reference
    # ── specific reference patterns ──
    r"\bPR\s*#?\d+",        # PR reference (e.g. PR #42)
    r"run\s+#?\d+",         # CI run reference (e.g. run #123)
    r"\bcheck\S*\.py\b",    # check script reference
    r"release.?gate",       # release gate reference
    r"NGINX\s+\d",          # version reference
]

_FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})")


def _find_missing_terms(content_lower: str, required_terms: List[str]) -> List[str]:
    """Return missing required lowercase terms from the content."""
    return [term for term in required_terms if term not in content_lower]


def _read_utf8_file(path: str) -> Tuple[Optional[str], Optional[str]]:
    """Read UTF-8 text file and return (content, error_message)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read(), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, str(exc)


def _iter_markdown_filenames(directory: str) -> Iterator[str]:
    """Yield markdown filenames in sorted order for deterministic output."""
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".md"):
            continue
        yield fname


_DOD_HEADING_RE = re.compile(r"^(#{1,6})\s+dod evaluation\s*$", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_PLACEHOLDER_STATUS_RE = re.compile(r"✅\s*/\s*❌")
_VALID_STATUS_RE = re.compile(r"✅|❌|\bpass\b|\bfail\b")


def _strip_fenced_blocks(content: str) -> str:
    """Remove fenced code block content, replacing each line with an empty line.

    Handles backtick and tilde fences.  A closing fence must use the same
    character as the opening fence and be at least as long.  Fences using a
    *different* character inside an open block are treated as content (this
    correctly handles nested fences, e.g. ``` inside ~~~~).
    """
    lines = content.split("\n")
    result: List[str] = []
    active_fence: Optional[Tuple[str, int]] = None

    for line in lines:
        active_fence, should_skip = _advance_fence_state(active_fence, line)
        if should_skip:
            result.append("")
        else:
            result.append(line)

    return "\n".join(result)


def _extract_dod_section(content_lower: str) -> Optional[str]:
    """Return the text between a DoD evaluation heading and the next same-level heading."""
    content_lower = _strip_fenced_blocks(content_lower)
    match = _DOD_HEADING_RE.search(content_lower)
    if match is None:
        return None
    heading_level = len(match.group(1))
    start = match.end()
    # Find the next heading of same or higher level (fewer or equal '#' chars).
    next_heading = re.compile(
        r"^#{1," + str(heading_level) + r"}\s",
        re.MULTILINE,
    )
    end_match = next_heading.search(content_lower, start)
    return content_lower[start:end_match.start()] if end_match else content_lower[start:]


def _collect_dod_table_rows(section: str) -> List[str]:
    """Collect non-header, non-separator table rows from a DoD section."""
    rows: List[str] = []
    for line in section.splitlines():
        if not _TABLE_ROW_RE.match(line):
            continue
        if _TABLE_SEPARATOR_RE.match(line):
            continue
        lower_line = line.lower()
        if "checkpoint" in lower_line and "status" in lower_line:
            continue
        rows.append(line)
    return rows


def _classify_checkpoint(
    checkpoint: str, table_rows: List[str],
) -> Optional[str]:
    """Return 'missing', 'placeholder', or None (valid) for a checkpoint."""
    matched_row = next((row for row in table_rows if checkpoint in row), None)
    if matched_row is None:
        return "missing"
    if _PLACEHOLDER_STATUS_RE.search(matched_row):
        return "placeholder"
    return None if _VALID_STATUS_RE.search(matched_row) else "placeholder"


def _dod_checkpoints_for_content(
    content_lower: str,
) -> Tuple[bool, List[str], List[str]]:
    """Return DoD presence, missing checkpoints, and placeholder-only checkpoints.

    Looks for a structured markdown table under a "DoD Evaluation" heading.
    Each checkpoint must appear in a table row with a concrete status indicator
    (✅, ❌, Pass, or Fail) — template placeholders like "✅/❌" are rejected.
    """
    section = _extract_dod_section(content_lower)
    if section is None:
        return False, [], []

    table_rows = _collect_dod_table_rows(section)

    missing: List[str] = []
    placeholder: List[str] = []

    for checkpoint in _DOD_CHECKPOINTS:
        status = _classify_checkpoint(checkpoint, table_rows)
        if status == "missing":
            missing.append(checkpoint)
        elif status == "placeholder":
            placeholder.append(checkpoint)

    return True, missing, placeholder


def _extract_checklist_item(line: str) -> str:
    """Return checklist item text for markdown checkboxes."""
    stripped = line.strip()
    # Parse checklist markers without regex backtracking:
    # accepted forms are "- [ ] ...", "- [x] ...", "- [X] ...", etc.
    if len(stripped) < 5:
        return ""
    if not stripped.startswith("- ["):
        return ""
    return "" if stripped[4] != "]" else stripped[5:].lstrip()


def _parse_fence(line: str) -> Optional[Tuple[str, int]]:
    """Return (fence_char, fence_len) if the line starts a fenced block token."""
    match = _FENCE_PATTERN.match(line)
    if match is None:
        return None
    token = match.group(1)
    return token[0], len(token)


def _has_verifiable_indicator(text: str) -> bool:
    """Return True if text contains any verifiable action indicator."""
    return any(
        re.search(indicator, text, re.IGNORECASE)
        for indicator in _VERIFIABLE_INDICATORS
    )


def _advance_fence_state(
    active_fence: Optional[Tuple[str, int]],
    line: str,
) -> Tuple[Optional[Tuple[str, int]], bool]:
    """Return updated fence state and whether to skip this line."""
    fence = _parse_fence(line)
    if active_fence is None:
        return (None, False) if fence is None else (fence, True)
    if fence is None:
        return active_fence, True

    fence_char, fence_len = fence
    active_char, active_len = active_fence
    if fence_char == active_char and fence_len >= active_len:
        return None, True
    return active_fence, True


def _checklist_stats(lines: List[str]) -> Tuple[int, List[str]]:
    """Return checklist item count and list of unverifiable checklist items."""
    item_count = 0
    unverifiable: List[str] = []
    active_fence: Optional[Tuple[str, int]] = None

    for line in lines:
        # Ignore checklist-like examples embedded inside fenced code blocks.
        active_fence, should_skip = _advance_fence_state(active_fence, line)
        if should_skip:
            continue

        item_text = _extract_checklist_item(line)
        if not item_text:
            continue

        item_count += 1
        if not _has_verifiable_indicator(item_text):
            unverifiable.append(item_text)

    return item_count, unverifiable


def _evaluate_dod_table_result(
    name: str,
    fname: str,
    content_lower: str,
) -> Tuple[bool, bool, Optional[str]]:
    """Return DoD presence, validity and report message for one markdown file."""
    has_dod_evaluation, missing, placeholder = _dod_checkpoints_for_content(
        content_lower,
    )
    if not has_dod_evaluation:
        return False, True, None

    failures: List[str] = []
    if missing:
        failures.append(
            f"  FAIL  {name}/{fname} DoD table missing checkpoints: "
            + ", ".join(missing),
        )
    if placeholder:
        failures.append(
            f"  FAIL  {name}/{fname} DoD table has placeholder status for: "
            + ", ".join(placeholder),
        )

    if failures:
        return True, False, "\n".join(failures)
    return True, True, f"  PASS  {name}/{fname} DoD table has all checkpoints"


def _find_subspecs_dirs(specs_dir: str) -> List[str]:
    """Return sub-spec directories matching known 0.4.0 keywords."""
    if not os.path.isdir(specs_dir):
        return []

    try:
        entries = sorted(os.listdir(specs_dir))
    except OSError:
        return []

    dirs = []
    for entry in entries:
        full = os.path.join(specs_dir, entry)
        if not os.path.isdir(full):
            continue
        lower = entry.lower()
        for kw in SUBSPECS_KEYWORDS:
            if kw in lower:
                dirs.append(full)
                break
    return dirs


def check_document_existence(specs_dir: str) -> Tuple[bool, List[str]]:
    """Property 2: Verify requirements.md and design.md exist for each sub-spec."""
    messages: List[str] = []
    subspecs = _find_subspecs_dirs(specs_dir)

    if not subspecs:
        messages.append(f"WARNING: No sub-spec directories found under {specs_dir}")
        return False, messages

    all_found = True
    for d in subspecs:
        name = os.path.basename(d)
        for doc in (_REQUIREMENTS_DOC, _DESIGN_DOC):
            path = os.path.join(d, doc)
            if os.path.isfile(path):
                messages.append(f"  PASS  {name}/{doc} exists")
            else:
                messages.append(f"  FAIL  {name}/{doc} missing")
                all_found = False

    return all_found, messages


def check_requirements_completeness(specs_dir: str) -> Tuple[bool, List[str]]:
    """Property 1: Verify each requirements doc contains all required sections."""
    messages: List[str] = []
    subspecs = _find_subspecs_dirs(specs_dir)

    if not subspecs:
        messages.append(f"WARNING: No sub-spec directories found under {specs_dir}")
        return False, messages

    all_complete = True
    for d in subspecs:
        name = os.path.basename(d)
        req_path = os.path.join(d, _REQUIREMENTS_DOC)
        if not os.path.isfile(req_path):
            messages.append(f"  SKIP  {name}/{_REQUIREMENTS_DOC} not found")
            continue

        content, read_error = _read_utf8_file(req_path)
        if read_error is not None:
            all_complete = False
            messages.append(f"  FAIL  {name}/{_REQUIREMENTS_DOC} read error: {read_error}")
            continue
        assert content is not None
        content = content.lower()

        missing = []
        missing.extend(
            label
            for pattern, label in _REQUIRED_SECTIONS
            if not re.search(r"^#{1,4}\s+.*" + pattern, content, re.MULTILINE)
        )
        if missing:
            all_complete = False
            messages.append(
                f"  FAIL  {name}/{_REQUIREMENTS_DOC} missing sections: "
                + ", ".join(missing)
            )
        else:
            messages.append(f"  PASS  {name}/{_REQUIREMENTS_DOC} has all required sections")

    return all_complete, messages


def check_design_completeness(specs_dir: str) -> Tuple[bool, List[str]]:
    """Property 1 — R3.4: Verify each design doc addresses all required answer fields."""
    messages: List[str] = []
    subspecs = _find_subspecs_dirs(specs_dir)

    if not subspecs:
        messages.append(f"WARNING: No sub-spec directories found under {specs_dir}")
        return False, messages

    all_complete = True
    for d in subspecs:
        name = os.path.basename(d)
        design_path = os.path.join(d, _DESIGN_DOC)
        if not os.path.isfile(design_path):
            messages.append(f"  SKIP  {name}/{_DESIGN_DOC} not found")
            continue

        content, read_error = _read_utf8_file(design_path)
        if read_error is not None:
            all_complete = False
            messages.append(f"  FAIL  {name}/{_DESIGN_DOC} read error: {read_error}")
            continue
        assert content is not None
        content = content.lower()

        missing = []
        missing.extend(
            label
            for pattern, label in _DESIGN_REQUIRED_FIELDS
            if not re.search(pattern, content, re.MULTILINE)
        )
        if missing:
            all_complete = False
            messages.append(
                f"  FAIL  {name}/{_DESIGN_DOC} missing required fields: "
                + ", ".join(missing)
            )
        else:
            messages.append(
                f"  PASS  {name}/{_DESIGN_DOC} has all required answer fields"
            )

    return all_complete, messages


def check_boundary_descriptions(specs_dir: str) -> Tuple[bool, List[str]]:
    """Property 3: Verify each sub-spec design.md has boundary descriptions."""
    messages: List[str] = []
    subspecs = _find_subspecs_dirs(specs_dir)

    if not subspecs:
        messages.append(f"WARNING: No sub-spec directories found under {specs_dir}")
        return False, messages

    all_present = True
    for d in subspecs:
        name = os.path.basename(d)
        design_path = os.path.join(d, _DESIGN_DOC)
        if not os.path.isfile(design_path):
            messages.append(f"  SKIP  {name}/{_DESIGN_DOC} not found")
            continue

        content, read_error = _read_utf8_file(design_path)
        if read_error is not None:
            all_present = False
            messages.append(f"  FAIL  {name}/{_DESIGN_DOC} read error: {read_error}")
            continue
        assert content is not None

        content_lower = content.lower()

        # Check for boundary description section.
        # Accept "scope anchor" as an equivalent concept — many specs use
        # "Scope Anchors" to describe what belongs to 0.4.0 vs what is
        # deferred, which serves the same purpose as a boundary description.
        # Also accept "key design decisions" — documentation-only and
        # tooling specs use this heading to describe scope boundaries.
        has_boundary = (
            "boundary description" in content_lower
            or "scope anchor" in content_lower
            or "key design decision" in content_lower
        )
        if not has_boundary:
            all_present = False
            messages.append(
                f"  FAIL  {name}/{_DESIGN_DOC} has no boundary description or scope anchors section"
            )
            continue

        missing_fields = []
        _BOUNDARY_FIELD_PATTERNS = [
            (r"capability|scope", "capability"),
            (r"0\.4\.0\s+scope|0\.4\.0|existing\s+\w+", "0.4.0 scope"),
            (
                r"0\.5\.x\s+scope|0\.5|deferred|long.?term"
                + r"|no.*runtime|not.*streaming|tooling\s+only"
                + r"|documentation.only|no.*change.*to\s+default"
                + r"|out\s+of\s+scope|interface\s+frozen"
                + r"|no\s+new\s+config|no\s+new\s+directive|unchanged",
                "0.5.x scope",
            ),
            (
                r"rationale|because|why\s+the\s+boundary"
                + r"|single\s+source\s+of\s+truth|to\s+set\s+clear"
                + r"|this\s+avoid|this\s+keep|not\s+a\s+new",
                "rationale",
            ),
            (
                r"prerequisit|before.*deferred|require.*before"
                + r"|must\s+integrate|stop\s+line|existing.*infra"
                + r"|frozen|out\s+of\s+scope|minimum\s+supported"
                + r"|all\s+code\s+follow|follow.*steering",
                "prerequisites",
            ),
        ]
        missing_fields.extend(
            label
            for pattern, label in _BOUNDARY_FIELD_PATTERNS
            if not re.search(pattern, content_lower)
        )
        if missing_fields:
            all_present = False
            messages.append(
                f"  FAIL  {name}/{_DESIGN_DOC} boundary/scope-anchor section missing fields: "
                + ", ".join(missing_fields)
            )
        else:
            messages.append(
                f"  PASS  {name}/{_DESIGN_DOC} has boundary description or scope anchors with all fields"
            )

    return all_present, messages


def check_dod_evaluation_tables(specs_dir: str) -> Tuple[bool, List[str]]:
    """Property 5: Validate DoD evaluation tables in completion artifacts."""
    messages: List[str] = []
    subspecs = _find_subspecs_dirs(specs_dir)

    if not subspecs:
        messages.append(f"WARNING: No sub-spec directories found under {specs_dir}")
        return True, messages  # vacuously true

    found_any = False
    valid = True

    for d in subspecs:
        name = os.path.basename(d)
        for fname in _iter_markdown_filenames(d):
            fpath = os.path.join(d, fname)
            content, read_error = _read_utf8_file(fpath)
            if read_error is not None:
                messages.append(f"  WARN  {name}/{fname} read error: {read_error}")
                continue
            assert content is not None
            stripped = _strip_fenced_blocks(content)

            has_dod_evaluation, file_valid, message = _evaluate_dod_table_result(
                name=name,
                fname=fname,
                content_lower=stripped.lower(),
            )
            if not has_dod_evaluation:
                continue
            found_any = True
            if not file_valid:
                valid = False
            assert message is not None
            messages.extend(message.splitlines())

    if not found_any:
        messages.append(
            "  WARN  No sub-specs have declared completion yet "
            "(no DoD evaluation tables found) — passing vacuously"
        )

    return valid, messages


def check_checklist_verifiability(
    release_gates_dir: str,
) -> Tuple[bool, List[str]]:
    """Property 11: Validate release checklist items are verifiable."""
    messages: List[str] = []
    checklist_path = os.path.join(release_gates_dir, "release-checklist.md")

    if not os.path.isfile(checklist_path):
        messages.append(f"  FAIL  {checklist_path} not found")
        return False, messages

    content, read_error = _read_utf8_file(checklist_path)
    if read_error is not None:
        messages.append(f"  FAIL  {checklist_path} read error: {read_error}")
        return False, messages
    assert content is not None
    item_count, unverifiable = _checklist_stats(content.splitlines())
    all_verifiable = not unverifiable

    if item_count == 0:
        messages.append("  WARN  No checklist items found in release-checklist.md")
        return True, messages

    messages.append(f"  INFO  Found {item_count} checklist items")

    if unverifiable:
        messages.extend(
            f"  FAIL  Non-verifiable item: {item}" for item in unverifiable
        )
    else:
        messages.append("  PASS  All checklist items have verifiable references")

    return all_verifiable, messages
