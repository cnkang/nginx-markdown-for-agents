#!/usr/bin/env python3
"""Sync every dependency surface with the baseline converter Cargo.lock.

Dependabot updates only ``components/rust-converter/Cargo.lock``. The other
dependency surfaces - the sub-workspace Cargo.lock files and the numbered
``THIRD-PARTY-NOTICES`` version entries - are invisible to it, and a bump
that stops at the baseline lock then fails the Rust Security Audit gate
(Rule 49). This tool applies that sync mechanically instead of by hand:

1. Sub-workspace locks (fuzz, corpus tooling, E2E harness, perf thresholds): every shared
   package whose resolved version differs from the baseline and is not
   documented in ``CROSS_LOCK_ALLOWED_DIVERGENCES`` is moved to the baseline
   version with a targeted ``cargo update --precise``. A full lock rebuild
   is avoided on purpose: it would rewrite the documented divergences.
2. ``THIRD-PARTY-NOTICES`` Section B: every numbered Rust-crate entry that
   lists a version the baseline lock no longer resolves is rewritten to the
   baseline version. Entries outside Section B (C libraries, build tools)
   and entries for crates absent from the baseline lock are never touched.

Modes:

- default: apply the sync. Runs ``cargo update`` per stale shared package
  (needs cargo and crates.io access) and rewrites the notices file.
- ``--check``: report out-of-sync surfaces and exit 1 without writing
  anything or invoking cargo, so it is safe as a pre-commit hook.

A dependency with no notices entry yet cannot be synced automatically: the
entry needs a license URL that only a human can supply. The coverage checker
keeps that failure red, and the dependabot sync workflow refuses to push
while the checker still fails.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.lib.executable_validation import (  # noqa: E402
    resolve_approved_executable,
)

from tools.ci.check_third_party_notices import (  # noqa: E402
    CARGO_LOCK,
    CROSS_LOCK_ALLOWED_DIVERGENCES,
    NOTICES_PATH,
    SUB_WORKSPACE_CARGO_LOCKS,
    SUB_WORKSPACE_CARGO_TOMLS,
    display_path,
    lock_package_versions,
)

# A numbered notices entry: "12. brotli 9.1.1 (Rust crate)".  The parenthetical
# scope suffix is optional and kept verbatim; only the version token changes.
_NOTICES_ENTRY_RE = re.compile(
    r"^(?P<prefix>\s*\d+\.\s+)(?P<name>[A-Za-z][A-Za-z0-9_.-]*)"
    r"(?P<sep>\s+)(?P<version>\S+)"
    r"(?P<suffix>\s+\(.*\))?$"
)
_SECTION_HEADER_RE = re.compile(r"^Section [A-Z]:")
# Line endings recognized when a matched line keeps its terminator.
_LINE_ENDING_RE = re.compile(r"(\r\n|\n|\r)\Z")


@dataclass
class LockUpdate:
    """One targeted cargo update that aligns a sub lock with the baseline."""

    cargo_toml: Path
    lock: Path
    name: str
    stale_version: str
    baseline_version: str


@dataclass
class SyncPlan:
    """Everything the sync would change, plus findings that need a human."""

    lock_updates: list[LockUpdate] = field(default_factory=list)
    # Maps a 0-based line index in the notices text to the full replacement
    # line, terminator included.
    notice_rewrites: dict[int, str] = field(default_factory=dict)
    skipped_divergences: list[str] = field(default_factory=list)
    manual_attention: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Baseline package -> resolved versions.  Apply mode revalidates each
    # planned update against the lock as it changes, so it needs the same
    # baseline view the plan was built from.
    baseline_packages: dict[str, set[str]] = field(default_factory=dict)

    def out_of_sync(self) -> bool:
        """Return whether anything is stale, ambiguous, or broken."""
        return bool(
            self.lock_updates
            or self.notice_rewrites
            or self.manual_attention
            or self.errors
        )


def lock_name_variants(name: str) -> set[str]:
    """Return the dash/underscore spellings one lock package name can take."""
    return {name, name.replace("-", "_"), name.replace("_", "-")}


def match_lock_name(entry_name: str, baseline: dict[str, set[str]]) -> str | None:
    """Resolve a notices entry name to its baseline lock package, if any."""
    for variant in lock_name_variants(entry_name.lower()):
        if variant in baseline:
            return variant
    return None


def _version_sort_key(version: str) -> tuple[int, ...]:
    """Order plain semver-ish versions numerically for target selection."""
    core = version.split("+", 1)[0].split("-", 1)[0]
    key: list[int] = []
    for piece in core.split("."):
        try:
            key.append(int(piece))
        except ValueError:
            key.append(0)
    return tuple(key)


def select_baseline_target(
    stale_version: str, candidates: set[str]
) -> str | None:
    """Pick the baseline version a stale copy can be --precise'd to.

    The baseline may legitimately carry several versions of one crate (syn
    2.x and 3.x coexist in the converter lock).  Cargo only accepts a
    ``--precise`` target that satisfies the sub-workspace's own version
    requirement, and the safest deterministic proxy for that requirement is
    the same major - or, for 0.x crates where the minor carries the breaking
    edge, the same minor.  A stale version with no compatible candidate is
    reported for manual attention instead of being force-moved across a
    breaking boundary.
    """
    same_major = [
        candidate
        for candidate in candidates
        if candidate.split(".", 1)[0] == stale_version.split(".", 1)[0]
    ]
    if stale_version.split(".", 1)[0] == "0":
        prefix = stale_version.split(".")[:2]
        same_major = [
            candidate for candidate in same_major if candidate.split(".")[:2] == prefix
        ]
    if not same_major:
        return None
    return max(same_major, key=_version_sort_key)


def _stale_shared_packages(
    sub_lock: Path,
    baseline: dict[str, set[str]],
    allowed_for_lock: dict[str, set[str]],
) -> tuple[list[tuple[str, str, str]], list[str], list[str]]:
    """Plan the stale shared packages in one sub-workspace lock.

    Returns (updates, skipped, manual) where updates are (name, stale,
    baseline) triples, skipped lists documented divergences, and manual lists
    stale packages for which no compatible baseline version exists.
    """
    updates: list[tuple[str, str, str]] = []
    skipped: list[str] = []
    manual: list[str] = []
    relative = display_path(sub_lock)
    sub_versions = lock_package_versions(sub_lock)
    for name, versions in sorted(sub_versions.items()):
        baseline_versions = baseline.get(name)
        if not baseline_versions:
            continue  # unique to this workspace: not a shared package
        exempt = allowed_for_lock.get(name, set())
        stale = sorted(versions - baseline_versions)
        skipped.extend(
            f"{relative}: documented divergence {name} {version}"
            for version in sorted(set(stale) & exempt)
        )
        unhandled = sorted(set(stale) - exempt)
        if not unhandled:
            continue
        for stale_version in unhandled:
            target = select_baseline_target(stale_version, baseline_versions)
            if target is None:
                joined = ", ".join(sorted(baseline_versions))
                manual.append(
                    f"{relative}: {name} {stale_version} is stale but the "
                    f"baseline has no compatible version ({joined}); sync "
                    "needs manual attention"
                )
                continue
            updates.append((name, stale_version, target))
    return updates, skipped, manual


def _plan_notices_line(
    index: int,
    line: str,
    baseline: dict[str, set[str]],
    manual: list[str],
) -> str | None:
    """Plan the rewrite for one Section B line, or return None to keep it.

    Appends to ``manual`` when the line is a stale baseline crate that has no
    compatible baseline version to move to.
    """
    ending_match = _LINE_ENDING_RE.search(line)
    ending = ending_match.group(1) if ending_match else ""
    entry = _NOTICES_ENTRY_RE.match(line[: len(line) - len(ending)])
    if entry is None:
        return None
    lock_name = match_lock_name(entry.group("name"), baseline)
    if lock_name is None:
        return None  # not a baseline crate: never rewrite
    baseline_versions = baseline[lock_name]
    stale_version = entry.group("version")
    if stale_version in baseline_versions:
        return None  # already current
    target = select_baseline_target(stale_version, baseline_versions)
    if target is None:
        joined = ", ".join(sorted(baseline_versions))
        manual.append(
            f"THIRD-PARTY-NOTICES line {index + 1}: {lock_name} "
            f"{stale_version} is stale but the baseline has no "
            f"compatible version ({joined}); sync needs manual attention"
        )
        return None
    return (
        f"{entry.group('prefix')}{entry.group('name')}"
        f"{entry.group('sep')}{target}"
        f"{entry.group('suffix') or ''}{ending}"
    )


def plan_notices_rewrites(
    notices_text: str,
    baseline: dict[str, set[str]],
) -> tuple[dict[int, str], list[str]]:
    """Plan Section B version rewrites for stale resolved versions.

    Returns (rewrites, manual).  Lines outside Section B never rewrite, so a
    C-library or build-tool entry that happens to share a crate name cannot
    be silently version-bumped.  Without a recognizable Section B header the
    whole notices file is reported for manual attention instead.
    """
    rewrites: dict[int, str] = {}
    manual: list[str] = []
    lines = notices_text.splitlines(keepends=True)
    in_section_b = False
    section_found = False
    for index, line in enumerate(lines):
        if _SECTION_HEADER_RE.match(line):
            in_section_b = line.startswith("Section B:")
            section_found = True
            continue
        if not in_section_b:
            continue
        replacement = _plan_notices_line(index, line, baseline, manual)
        if replacement is not None:
            rewrites[index] = replacement
    if not section_found:
        manual.append(
            "THIRD-PARTY-NOTICES: no 'Section B:' header found; notices "
            "sync needs manual attention"
        )
    return rewrites, manual


def build_sync_plan(
    baseline_lock: Path,
    sub_workspaces: list[tuple[Path, Path]],
    allowed: dict[str, dict[str, set[str]]],
    notices_text: str | None,
) -> SyncPlan:
    """Compute every stale dependency surface without touching any file."""
    plan = SyncPlan()
    try:
        baseline = lock_package_versions(baseline_lock)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        plan.errors.append(
            f"cannot read baseline Cargo.lock {display_path(baseline_lock)}: {exc}"
        )
        return plan
    plan.baseline_packages = baseline

    for cargo_toml, sub_lock in sub_workspaces:
        if not cargo_toml.is_file():
            continue  # mirrors the checker: no manifest, nothing to sync
        if not sub_lock.is_file():
            plan.manual_attention.append(
                f"Cargo.lock missing for {display_path(cargo_toml)}; "
                "generate it by hand before syncing"
            )
            continue
        allowed_for_lock = allowed.get(display_path(sub_lock), {})
        try:
            updates, skipped, manual = _stale_shared_packages(
                sub_lock, baseline, allowed_for_lock
            )
        except (OSError, tomllib.TOMLDecodeError) as exc:
            plan.errors.append(
                f"cannot read Cargo.lock {display_path(sub_lock)}: {exc}"
            )
            continue
        plan.skipped_divergences.extend(skipped)
        plan.manual_attention.extend(manual)
        plan.lock_updates.extend(
            LockUpdate(
                cargo_toml=cargo_toml,
                lock=sub_lock,
                name=name,
                stale_version=stale_version,
                baseline_version=baseline_version,
            )
            for name, stale_version, baseline_version in updates
        )

    if notices_text is not None:
        plan.notice_rewrites, notices_manual = plan_notices_rewrites(
            notices_text, baseline
        )
        plan.manual_attention.extend(notices_manual)
    return plan


def build_cargo_update_command(cargo: str, update: LockUpdate) -> list[str]:
    """Build one targeted cargo update command.

    ``name@stale`` disambiguates the package spec, so a lock that carries
    several versions of the same crate still updates exactly the stale copy.
    """
    return [
        cargo,
        "update",
        "--manifest-path",
        str(update.cargo_toml),
        "--package",
        f"{update.name}@{update.stale_version}",
        "--precise",
        update.baseline_version,
    ]


def _refresh_update(
    update: LockUpdate,
    baseline: dict[str, set[str]],
    allowed_for_lock: dict[str, set[str]],
) -> tuple[LockUpdate | None, str | None]:
    """Re-derive one planned update from the sub-lock as it is now.

    A targeted ``cargo update`` for one package can move a shared transitive
    dependency past the version the plan recorded: updating regex to 1.13.2
    pulls regex-automata 0.4.19 while a later plan entry still names 0.4.16.
    Cargo then rejects ``--package name@old`` because that version is gone.
    Re-reading the lock keeps every package spec valid: an entry the cascade
    already moved to a baseline version is dropped, and one that landed on a
    different stale version is re-targeted instead of failing the whole sync.
    """
    try:
        versions = lock_package_versions(update.lock).get(update.name, set())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return None, f"cannot read Cargo.lock {display_path(update.lock)}: {exc}"
    baseline_versions = baseline.get(update.name, set())
    exempt = allowed_for_lock.get(update.name, set())
    stale = sorted((versions - baseline_versions) - exempt)
    if not stale:
        return None, None  # an earlier update in this run already moved it
    stale_version = (
        update.stale_version
        if update.stale_version in stale
        else max(stale, key=_version_sort_key)
    )
    target = select_baseline_target(stale_version, baseline_versions)
    if target is None:
        joined = ", ".join(sorted(baseline_versions))
        return None, (
            f"{display_path(update.lock)}: {update.name} {stale_version} is "
            f"stale but the baseline has no compatible version ({joined}); "
            "sync needs manual attention"
        )
    return replace(
        update, stale_version=stale_version, baseline_version=target
    ), None


def apply_lock_updates(
    updates: list[LockUpdate],
    cargo: str,
    baseline: dict[str, set[str]],
    allowed: dict[str, dict[str, set[str]]],
) -> list[str]:
    """Run every planned cargo update and return the failures."""
    errors: list[str] = []
    for update in updates:
        current, error = _refresh_update(
            update, baseline, allowed.get(display_path(update.lock), {})
        )
        if error is not None:
            errors.append(error)
            continue
        if current is None:
            continue
        command = build_cargo_update_command(cargo, current)
        # Audited against opengrep python.lang.security.audit.dangerous-
        # subprocess-use-audit: no shell is involved (argv list), cargo
        # resolves through the approved-executable allowlist, and the
        # name/version arguments sit in value position of --package and
        # --precise, where a leading dash stays a cargo argument error and
        # cannot inject new options.  Same shape as the subprocess call in
        # tools/ci/check_third_party_notices.py.
        completed = subprocess.run(  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            errors.append(
                f"cargo update failed for {current.name} in "
                f"{display_path(current.lock)}"
                + (f": {detail[-300:]}" if detail else "")
            )
    return errors


def apply_notice_rewrites(notices_path: Path, rewrites: dict[int, str]) -> list[str]:
    """Rewrite the planned notices lines in place, byte-exact elsewhere."""
    if not rewrites:
        return []
    try:
        # NOSONAR suppression for pythonsecurity:S2083: the path is the
        # repository's THIRD-PARTY-NOTICES constant, never caller- or
        # content-controlled; only the file's own text is read back.
        raw = notices_path.read_bytes()  # NOSONAR
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"cannot read {display_path(notices_path)}: {exc}"]
    lines = text.splitlines(keepends=True)
    for index, replacement in rewrites.items():
        if index >= len(lines):
            return [f"{display_path(notices_path)} changed while syncing"]
        lines[index] = replacement
    try:
        # NOSONAR suppression for pythonsecurity:S2083: the path is the
        # repository's THIRD-PARTY-NOTICES constant; the tainted-looking
        # argument is file content, written back as content, and never used
        # to build a path.
        notices_path.write_bytes("".join(lines).encode("utf-8"))  # NOSONAR
    except OSError as exc:
        return [f"cannot write {display_path(notices_path)}: {exc}"]
    return []


def describe_changes(plan: SyncPlan) -> list[str]:
    """Render the planned changes as one human-readable line each."""
    lines = [
        f"would update {display_path(update.lock)}: {update.name} "
        f"{update.stale_version} -> {update.baseline_version}"
        for update in plan.lock_updates
    ]
    lines.extend(
        f"would rewrite THIRD-PARTY-NOTICES line {index + 1} with: "
        f"{replacement.rstrip()}"
        for index, replacement in sorted(plan.notice_rewrites.items())
    )
    return lines


def _load_notices_text(notices_path: Path) -> tuple[str | None, list[str]]:
    """Read the notices file, returning (text, errors)."""
    try:
        return notices_path.read_text(encoding="utf-8"), []
    except (OSError, UnicodeDecodeError) as exc:
        return None, [f"cannot read {display_path(notices_path)}: {exc}"]


def _report_check(plan: SyncPlan, baseline_lock: Path) -> int:
    """Print what check mode found and map it to an exit code."""
    for line in describe_changes(plan):
        print(line)
    if plan.skipped_divergences:
        print("documented divergences left unchanged:")
        for line in plan.skipped_divergences:
            print(f"  - {line}")
    if not plan.out_of_sync():
        print("dependency surfaces are in sync")
        return 0
    for line in plan.manual_attention + plan.errors:
        print(f"  - {line}")
    print(
        "dependency surfaces are out of sync with the baseline lock "
        f"({display_path(baseline_lock)}); run "
        "python3 tools/ci/sync_dependency_surfaces.py to fix"
    )
    return 1


def _apply_plan(
    plan: SyncPlan,
    notices_path: Path,
    cargo: str | None,
    allowed: dict[str, dict[str, set[str]]],
) -> int:
    """Apply the sync plan and map the outcome to an exit code."""
    if not plan.out_of_sync():
        print("dependency surfaces are already in sync")
        return 0
    if plan.errors or plan.manual_attention:
        for line in plan.manual_attention + plan.errors:
            print(f"  - {line}")
        print("cannot sync automatically; fix the items above by hand")
        return 1
    for line in describe_changes(plan):
        print(line)
    if cargo is None:
        print("cargo is unavailable; cannot run the targeted lock updates")
        return 1
    errors = apply_lock_updates(
        plan.lock_updates, cargo, plan.baseline_packages, allowed
    )
    errors.extend(apply_notice_rewrites(notices_path, plan.notice_rewrites))
    if errors:
        for line in errors:
            print(f"  - {line}")
        print(
            "sync failed; re-run python3 tools/ci/sync_dependency_surfaces.py "
            "to finish"
        )
        return 1
    print(
        "synced dependency surfaces with the baseline lock "
        f"({len(plan.notice_rewrites)} notices entries rewritten)"
    )
    return 0


def run_sync(
    *,
    check_only: bool,
    baseline_lock: Path,
    sub_workspaces: list[tuple[Path, Path]],
    allowed: dict[str, dict[str, set[str]]],
    notices_path: Path,
    cargo: str | None,
) -> int:
    """Plan and, unless ``check_only``, apply the sync.  Returns an exit code."""
    notices_text, load_errors = _load_notices_text(notices_path)
    plan = build_sync_plan(baseline_lock, sub_workspaces, allowed, notices_text)
    plan.errors.extend(load_errors)
    if check_only:
        return _report_check(plan, baseline_lock)
    return _apply_plan(plan, notices_path, cargo, allowed)


def main(argv: list[str] | None = None) -> int:
    """Run the dependency-surface sync from the repository layout."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--check"]:
        check_only = True
    elif argv:
        print("usage: tools/ci/sync_dependency_surfaces.py [--check]", file=sys.stderr)
        return 2
    else:
        check_only = False

    sub_workspaces = list(
        zip(SUB_WORKSPACE_CARGO_TOMLS, SUB_WORKSPACE_CARGO_LOCKS, strict=True)
    )
    cargo = resolve_approved_executable("cargo")
    return run_sync(
        check_only=check_only,
        baseline_lock=CARGO_LOCK,
        sub_workspaces=sub_workspaces,
        allowed=CROSS_LOCK_ALLOWED_DIVERGENCES,
        notices_path=NOTICES_PATH,
        cargo=cargo,
    )


if __name__ == "__main__":
    raise SystemExit(main())
