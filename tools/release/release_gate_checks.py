"""Release gate check functions for 0.4.0 validation.

Each function returns a tuple of (passed: bool, messages: list[str]).
"""

import os
import re
from typing import Iterator, Optional, Tuple, List

from tools.release.release_constants import SUBSPECS_KEYWORDS

# Required sections in requirements documents (Property 1)
_REQUIRED_SECTIONS = [
    (r"goals", "Goals"),
    (r"non[- ]?goals", "Non-Goals"),
    (r"compatib", "Compatibility Impact"),
    (r"test\s*plan|testing", "Test Plan"),
    (r"documentation", "Documentation Impact"),
    (r"rollout", "Rollout Impact"),
    (r"observability", "Observability Impact"),
    (r"security", "Security Considerations"),
    (r"migration|operator\s*guidance", "Migration / Operator Guidance"),
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


# Verifiable action indicators for checklist items (Property 11)
_VERIFIABLE_INDICATORS = [
    r"make\s+\S+",          # make command
    r"docs/",               # file path reference
    r"tools/",              # file path reference
    r"components/",         # file path reference
    r"\.kiro/",             # file path reference
    r"\.(md|py|ya?ml|json|toml|sh|c|h|rs|txt)\b",  # file extension reference
    r"\bpasses\b",          # action verb
    r"\bverified\b",        # action verb
    r"\bcomplete\b",        # action verb
    r"\bupdated\b",         # action verb
    r"\bdocumented\b",      # action verb
    r"\bgenerated\b",       # action verb
    r"\barchived\b",        # action verb
    r"\bcoverage\b",        # action verb
    r"\btested\b",          # action verb
    r"\bidentical\b",       # action verb
    r"\bunchanged\b",       # action verb
    r"\bmatched?\b",        # action verb
    r"\bobserve\b",         # action verb
    r"\bexecute\b",         # action verb
    r"\bperform\b",         # action verb
    r"\bfollowing\b",       # action verb
    r"\blisted\b",          # action verb
    r"CI\b",                # CI reference
    r"e2e\b",               # test reference
    r"unit test",           # test reference
    r"integration test",    # test reference
    r"cargo test",          # command reference
    r"NGINX\s+\d",          # version reference
]

_CHECKLIST_ITEM_PATTERN = re.compile(r"^- \[[^\]]\]\s*(.*)$")
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


def _dod_checkpoints_for_content(content_lower: str) -> Tuple[bool, List[str]]:
    """Return whether a DoD table exists and which checkpoints are missing."""
    has_dod_evaluation = "dod evaluation" in content_lower
    if not has_dod_evaluation:
        return False, []
    return True, _find_missing_terms(content_lower, _DOD_CHECKPOINTS)


def _extract_checklist_item(line: str) -> str:
    """Return checklist item text for markdown checkboxes."""
    stripped = line.strip()
    match = _CHECKLIST_ITEM_PATTERN.match(stripped)
    if match is None:
        return ""
    return match.group(1).strip()


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
        for doc in ("requirements.md", "design.md"):
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
        req_path = os.path.join(d, "requirements.md")
        if not os.path.isfile(req_path):
            messages.append(f"  SKIP  {name}/requirements.md not found")
            continue

        content, read_error = _read_utf8_file(req_path)
        if read_error is not None:
            all_complete = False
            messages.append(f"  FAIL  {name}/requirements.md read error: {read_error}")
            continue
        assert content is not None
        content = content.lower()

        missing = []
        for pattern, label in _REQUIRED_SECTIONS:
            # Look for a heading line containing the keyword
            if not re.search(r"^#{1,4}\s+.*" + pattern, content, re.MULTILINE):
                missing.append(label)

        if missing:
            all_complete = False
            messages.append(
                f"  FAIL  {name}/requirements.md missing sections: "
                + ", ".join(missing)
            )
        else:
            messages.append(f"  PASS  {name}/requirements.md has all required sections")

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
        design_path = os.path.join(d, "design.md")
        if not os.path.isfile(design_path):
            messages.append(f"  SKIP  {name}/design.md not found")
            continue

        content, read_error = _read_utf8_file(design_path)
        if read_error is not None:
            all_present = False
            messages.append(f"  FAIL  {name}/design.md read error: {read_error}")
            continue
        assert content is not None

        content_lower = content.lower()

        # Check for boundary description section
        if "boundary description" not in content_lower:
            all_present = False
            messages.append(f"  FAIL  {name}/design.md has no boundary description section")
            continue

        # Check for all five required fields
        missing_fields = []
        for field in _BOUNDARY_FIELDS:
            if field.lower() not in content_lower:
                missing_fields.append(field)

        if missing_fields:
            all_present = False
            messages.append(
                f"  FAIL  {name}/design.md boundary description missing fields: "
                + ", ".join(missing_fields)
            )
        else:
            messages.append(
                f"  PASS  {name}/design.md has boundary description with all fields"
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

            content_lower = content.lower()
            has_dod_evaluation, missing = _dod_checkpoints_for_content(content_lower)
            if not has_dod_evaluation:
                continue
            found_any = True
            if missing:
                valid = False
                messages.append(
                    f"  FAIL  {name}/{fname} DoD table missing checkpoints: "
                    + ", ".join(missing)
                )
            else:
                messages.append(
                    f"  PASS  {name}/{fname} DoD table has all checkpoints"
                )

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
    lines = content.splitlines()

    all_verifiable = True
    item_count = 0
    unverifiable: List[str] = []
    active_fence: Optional[Tuple[str, int]] = None

    for line in lines:
        # Ignore checklist-like examples embedded inside fenced code blocks.
        fence = _parse_fence(line)
        if active_fence is None:
            if fence is not None:
                active_fence = fence
                continue
        else:
            if fence is not None:
                fence_char, fence_len = fence
                active_char, active_len = active_fence
                if fence_char == active_char and fence_len >= active_len:
                    active_fence = None
            continue

        item_text = _extract_checklist_item(line)
        if not item_text:
            continue

        item_count += 1
        if not _has_verifiable_indicator(item_text):
            all_verifiable = False
            unverifiable.append(item_text)

    if item_count == 0:
        messages.append("  WARN  No checklist items found in release-checklist.md")
        return True, messages

    messages.append(f"  INFO  Found {item_count} checklist items")

    if unverifiable:
        for item in unverifiable:
            messages.append(f"  FAIL  Non-verifiable item: {item}")
    else:
        messages.append("  PASS  All checklist items have verifiable references")

    return all_verifiable, messages
