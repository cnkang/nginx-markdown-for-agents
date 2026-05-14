#!/usr/bin/env python3
"""Route harness maintenance changes to risk packs and verification commands."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "tools", "lib"))
from path_validation import validate_read_path  # noqa: E402


def _find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent

    while True:
        if (current / "AGENTS.md").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent

    raise SystemExit(
        f"cannot locate repository root from {start} (expected AGENTS.md in ancestors)"
    )


REPO_ROOT = _find_repo_root(Path(__file__).resolve())
DEFAULT_MANIFEST = REPO_ROOT / "docs" / "harness" / "routing-manifest.json"
PHASE_ORDER = {"cheap-blocker": 0, "focused-semantic": 1, "umbrella": 2}


def _load_manifest(path: Path) -> dict:
    """Load and validate the routing manifest JSON file.

    Performs path validation, JSON parsing, and schema checks for
    required keys and types.  Exits with an error message if the
    manifest is malformed.

    Args:
        path: Path to the routing-manifest.json file.

    Returns:
        Parsed manifest as a dictionary.
    """
    validated = validate_read_path(path, purpose="routing manifest")
    try:
        data = json.loads(validated.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"failed to load manifest {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise SystemExit(f"manifest {path} must be a JSON object")

    required_keys = ("risk_packs", "verification_families")
    missing = [key for key in required_keys if key not in data]
    if missing:
        raise SystemExit(
            f"manifest {path} missing required keys: {', '.join(sorted(missing))}"
        )

    if not isinstance(data["risk_packs"], list):
        raise SystemExit(f"manifest {path}: 'risk_packs' must be a list")
    if not isinstance(data["verification_families"], dict):
        raise SystemExit(f"manifest {path}: 'verification_families' must be a dict")

    for family in data["verification_families"].keys():
        if not isinstance(family, str) or not family.strip():
            raise SystemExit(
                f"manifest {path}: verification_families keys must be non-empty strings"
            )

    for index, pack in enumerate(data["risk_packs"]):
        if not isinstance(pack, dict):
            raise SystemExit(f"manifest {path}: risk_packs[{index}] must be an object")
        required_pack_keys = ("id", "doc", "paths", "keywords", "verification_families")
        missing_pack = [key for key in required_pack_keys if key not in pack]
        if missing_pack:
            raise SystemExit(
                "manifest "
                f"{path}: risk_packs[{index}] missing keys: {', '.join(sorted(missing_pack))}"
            )
        if not isinstance(pack["id"], str) or not pack["id"].strip():
            raise SystemExit(f"manifest {path}: risk_packs[{index}].id must be a non-empty string")
        if not isinstance(pack["doc"], str) or not pack["doc"].strip():
            raise SystemExit(
                f"manifest {path}: risk_packs[{index}].doc must be a non-empty string"
            )
        if not isinstance(pack["paths"], list):
            raise SystemExit(f"manifest {path}: risk_packs[{index}].paths must be a list")
        if not isinstance(pack["keywords"], list):
            raise SystemExit(
                f"manifest {path}: risk_packs[{index}].keywords must be a list"
            )
        if not isinstance(pack["verification_families"], list):
            raise SystemExit(
                "manifest "
                f"{path}: risk_packs[{index}].verification_families must be a list"
            )

        for item_index, pattern in enumerate(pack["paths"]):
            if not isinstance(pattern, str) or not pattern.strip():
                raise SystemExit(
                    f"manifest {path}: risk_packs[{index}].paths[{item_index}] "
                    "must be a non-empty string"
                )
        for item_index, keyword in enumerate(pack["keywords"]):
            if not isinstance(keyword, str) or not keyword.strip():
                raise SystemExit(
                    f"manifest {path}: risk_packs[{index}].keywords[{item_index}] "
                    "must be a non-empty string"
                )
        for item_index, family in enumerate(pack["verification_families"]):
            if not isinstance(family, str) or not family.strip():
                raise SystemExit(
                    "manifest "
                    f"{path}: risk_packs[{index}].verification_families[{item_index}] "
                    "must be a non-empty string"
                )
            if family not in data["verification_families"]:
                raise SystemExit(
                    "manifest "
                    f"{path}: risk_packs[{index}].verification_families "
                    f"contains unknown family '{family}'"
                )

    return data


_GIT_REF_PATTERN = re.compile(r"^[a-zA-Z0-9._/\-~^@{}]+$")


def _validate_git_ref(ref: str) -> str:
    """Validate a git ref for safe use in subprocess commands (defense-in-depth)."""
    if not ref or ref.startswith("-"):
        raise SystemExit(f"invalid base ref: {ref!r}")
    if not _GIT_REF_PATTERN.match(ref):
        raise SystemExit(f"invalid base ref: {ref!r}")
    return ref


def _git_diff_files(base: str | None) -> list[str]:
    """Get the list of changed files from git diff.

    Args:
        base: Base branch/commit for the diff. If None, uses staged changes.

    Returns:
        Sorted list of changed file paths relative to repo root.
    """
    cmd = ["git", "diff", "--name-only", "--diff-filter=d"]
    if base:
        cmd.append(f"{_validate_git_ref(base)}...HEAD")
    try:
        out = subprocess.check_output(
            cmd,
            cwd=REPO_ROOT,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"git diff failed with exit {exc.returncode}: {exc.output.strip()}"
        ) from exc
    files = [line.strip() for line in out.splitlines() if line.strip()]
    return sorted(set(files))


def _git_status_files() -> list[str]:
    """Get the list of working-tree files from git status.

    Expands directory entries by listing their tracked contents.

    Returns:
        Sorted list of file paths relative to repo root.
    """
    cmd = ["git", "status", "--porcelain"]
    try:
        out = subprocess.check_output(
            cmd,
            cwd=REPO_ROOT,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"git status failed with exit {exc.returncode}: {exc.output.strip()}"
        ) from exc

    files: list[str] = []
    for entry in _parse_status_output(out):
        abs_entry = REPO_ROOT / entry
        if abs_entry.is_dir():
            cmd = ["git", "ls-files", "--others", "--exclude-standard", "--", entry]
            try:
                nested = subprocess.check_output(
                    cmd,
                    cwd=REPO_ROOT,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            except subprocess.CalledProcessError:
                nested = ""
            nested_files = [line.strip() for line in nested.splitlines() if line.strip()]
            if nested_files:
                files.extend(nested_files)
            else:
                files.append(entry.rstrip("/"))
        else:
            files.append(entry)
    return sorted(set(files))


def _normalize_files(raw: list[str]) -> list[str]:
    """Normalize and deduplicate a list of file paths.

    Splits comma-separated entries, normalizes backslashes to forward
    slashes, and removes duplicates.

    Args:
        raw: Raw file path strings (may be comma-separated).

    Returns:
        Sorted list of unique normalized file paths.
    """
    files: list[str] = []
    for value in raw:
        for part in value.split(","):
            item = part.strip().replace("\\", "/")
            if item:
                files.append(item)
    return sorted(set(files))


def _parse_status_output(output: str) -> list[str]:
    """Parse git status --porcelain output into file paths.

    Handles renamed files (old -> new), deleted files (skipped),
    and directory entries.

    Args:
        output: Raw output from git status --porcelain.

    Returns:
        List of file paths extracted from the status output.
    """
    parsed: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        index_status = line[0]
        worktree_status = line[1]
        if index_status == "D" or worktree_status == "D":
            continue
        entry = line[3:].strip()
        if not entry:
            continue
        if " -> " in entry:
            old_path, new_path = entry.split(" -> ", 1)
            old_path = old_path.strip()
            new_path = new_path.strip()
            if old_path:
                parsed.append(old_path)
            if new_path:
                parsed.append(new_path)
            continue
        parsed.append(entry)
    return parsed


def _match_path(path: str, pattern: str) -> bool:
    """Check whether a file path matches a glob pattern.

    Supports ** recursive patterns and standard fnmatch syntax.

    Args:
        path: File path to test (backslashes are normalized).
        pattern: Glob pattern to match against.

    Returns:
        True if the path matches the pattern.
    """
    path = path.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch(path, pattern)


def _pack_matches(pack: dict, files: list[str], hint_text: str) -> dict | None:
    """Check whether a risk pack matches the given files and hint text.

    A pack matches if any of its path patterns match the changed files
    or any of its keywords appear in the hint text or file paths.

    Args:
        pack: Risk pack dictionary from the manifest.
        files: List of changed file paths.
        hint_text: Additional text to search for keyword matches.

    Returns:
        Match result dictionary with hits and score, or None if no match.
    """
    path_hits: list[str] = []
    for file_path in files:
        if any(_match_path(file_path, pattern) for pattern in pack.get("paths", [])):
            path_hits.append(file_path)

    keyword_hits: list[str] = []
    haystack = f"{hint_text} {' '.join(files)}".lower()
    for keyword in pack.get("keywords", []):
        needle = str(keyword).lower()
        if needle and _keyword_matches_haystack(needle, haystack):
            keyword_hits.append(str(keyword))

    if not path_hits and not keyword_hits:
        return None

    uniq_path_hits = sorted(set(path_hits))
    uniq_keyword_hits = sorted(set(keyword_hits))

    return {
        "id": pack["id"],
        "doc": pack["doc"],
        "verification_families": pack.get("verification_families", []),
        "path_hits": uniq_path_hits,
        "keyword_hits": uniq_keyword_hits,
        "score": (2 * len(uniq_path_hits)) + len(uniq_keyword_hits),
    }


def _keyword_matches_haystack(needle: str, haystack: str) -> bool:
    """Check whether a keyword appears as a whole word in the haystack.

    Uses word-boundary matching to avoid partial matches within
    identifiers or compound words.

    Args:
        needle: Keyword to search for.
        haystack: Text to search in (should be lowercase).

    Returns:
        True if the keyword appears as a whole word.
    """
    pattern = rf"(?<![0-9a-z_]){re.escape(needle)}(?![0-9a-z_])"
    return re.search(pattern, haystack) is not None


def _verification_plan(manifest: dict, families: list[str]) -> dict[str, list]:
    """Build a phased verification plan from matched verification families.

    Groups families by their phase (cheap-blocker, focused-semantic,
    umbrella) and sorts them in execution order.

    Args:
        manifest: Parsed routing manifest dictionary.
        families: List of verification family names to plan for.

    Returns:
        Dictionary with 'plan' (sorted list of family entries) and
        'unknown_verification_families' (families not in manifest).
    """
    family_map = manifest.get("verification_families", {})
    seen = set()
    plan: list[dict] = []
    unknown_families: list[str] = []
    for order, family in enumerate(families):
        if family in seen:
            continue
        seen.add(family)
        payload = family_map.get(family)
        if not isinstance(payload, dict):
            unknown_families.append(family)
            continue
        phase = str(payload.get("phase", "focused-semantic"))
        commands = payload.get("commands", [])
        plan.append(
            {
                "family": family,
                "phase": phase,
                "commands": commands if isinstance(commands, list) else [],
                "order": order,
            }
        )
    plan.sort(key=lambda item: (PHASE_ORDER.get(item["phase"], 99), item["order"]))
    for item in plan:
        item.pop("order", None)
    return {
        "plan": plan,
        "unknown_verification_families": sorted(unknown_families),
    }


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the harness route tool.

    Returns:
        Parsed namespace with manifest, from_git, base, file, hint, and json fields.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--from-git", action="store_true")
    parser.add_argument("--base", help="Optional git base for --from-git.")
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        help="Changed file path. Repeat or use comma-separated values.",
    )
    parser.add_argument(
        "--hint",
        action="append",
        default=[],
        help="Optional task hint text to help keyword routing.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    return parser.parse_args()


def main() -> int:
    """Entry point for the harness route CLI.

    Loads the manifest, collects changed files (from git or explicit args),
    matches them against risk packs, builds a verification plan, and
    outputs the routing result as human-readable text or JSON.

    Returns:
        Exit code: 0 always (routing is informational).
    """
    args = _parse_args()
    manifest = _load_manifest(Path(args.manifest))

    files = _normalize_files(args.file)
    if args.from_git:
        files = sorted(set(files + _git_status_files() + _git_diff_files(args.base)))

    hint_text = " ".join(args.hint).strip()

    matched = []
    for pack in manifest.get("risk_packs", []):
        if not isinstance(pack, dict):
            continue
        hit = _pack_matches(pack, files, hint_text)
        if hit is not None:
            matched.append(hit)

    matched.sort(key=lambda item: (-item["score"], item["id"]))

    families: list[str] = []
    for pack in matched:
        families.extend(pack["verification_families"])
    verification = _verification_plan(manifest, families)
    plan = verification["plan"]
    unknown_families = verification["unknown_verification_families"]

    payload = {
        "files": files,
        "hints": args.hint,
        "matched_risk_packs": matched,
        "verification_plan": plan,
        "unknown_verification_families": unknown_families,
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"files: {len(files)}")
        for file_path in files:
            print(f"  - {file_path}")
        print(f"matched risk packs: {len(matched)}")
        for pack in matched:
            print(f"  - {pack['id']} ({pack['doc']})")
            if pack["path_hits"]:
                print("    path hits:")
                for path_hit in pack["path_hits"]:
                    print(f"      - {path_hit}")
            if pack["keyword_hits"]:
                print("    keyword hits:")
                for keyword_hit in pack["keyword_hits"]:
                    print(f"      - {keyword_hit}")
        print(f"verification families: {len(plan)}")
        for item in plan:
            print(f"  - [{item['phase']}] {item['family']}")
            for command in item["commands"]:
                print(f"      {command}")
        if unknown_families:
            joined = ", ".join(unknown_families)
            print(
                "warning: unknown verification families "
                "(no entry in manifest.verification_families): "
                f"{joined}",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
