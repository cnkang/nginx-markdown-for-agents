"""Release gate check functions for 0.4.0 validation.

Each function returns a tuple of (passed: bool, messages: list[str]).
"""

import os
import re
from typing import Tuple, List

# Known 0.4.0 sub-spec directory keyword fragments
_SUBSPECS_KEYWORDS = [
    "overall-scope",
    "packaging",
    "benchmark",
    "rollout",
    "prometheus",
    "parser",
]

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
    r"\.\w+",               # file extension reference
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


def _find_subspecs_dirs(specs_dir: str) -> List[str]:
    """Return sub-spec directories matching known 0.4.0 keywords."""
    if not os.path.isdir(specs_dir):
        return []
    dirs = []
    for entry in sorted(os.listdir(specs_dir)):
        full = os.path.join(specs_dir, entry)
        if not os.path.isdir(full):
            continue
        lower = entry.lower()
        for kw in _SUBSPECS_KEYWORDS:
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

        with open(req_path, "r", encoding="utf-8") as f:
            content = f.read().lower()

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

        with open(design_path, "r", encoding="utf-8") as f:
            content = f.read()

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
        # Look for any markdown file that might contain a DoD evaluation
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(d, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()

            content_lower = content.lower()
            if "dod evaluation" not in content_lower:
                continue

            found_any = True
            missing = []
            for checkpoint in _DOD_CHECKPOINTS:
                if checkpoint not in content_lower:
                    missing.append(checkpoint)

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

    with open(checklist_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    all_verifiable = True
    item_count = 0
    unverifiable: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("- [ ]"):
            continue

        item_count += 1
        item_text = stripped[len("- [ ]"):].strip()

        # Check if the item contains at least one verifiable indicator
        found = False
        for indicator in _VERIFIABLE_INDICATORS:
            if re.search(indicator, item_text, re.IGNORECASE):
                found = True
                break

        if not found:
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
