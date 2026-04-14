#!/usr/bin/env python3
"""Route harness maintenance changes to risk packs and verification commands."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath


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
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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

    return data


def _git_diff_files(base: str | None) -> list[str]:
    cmd = ["git", "diff", "--name-only"]
    if base:
        cmd.append(f"{base}...HEAD")
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
    for line in out.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:]
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        entry = entry.strip()
        if not entry:
            continue
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
    files: list[str] = []
    for value in raw:
        for part in value.split(","):
            item = part.strip().replace("\\", "/")
            if item:
                files.append(item)
    return sorted(set(files))


def _match_path(path: str, pattern: str) -> bool:
    candidate = PurePosixPath(path)
    if candidate.match(pattern):
        return True
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-3])
    return False


def _pack_matches(pack: dict, files: list[str], hint_text: str) -> dict | None:
    path_hits: list[str] = []
    for file_path in files:
        if any(_match_path(file_path, pattern) for pattern in pack.get("paths", [])):
            path_hits.append(file_path)

    keyword_hits: list[str] = []
    haystack = f"{hint_text} {' '.join(files)}".lower()
    for keyword in pack.get("keywords", []):
        needle = str(keyword).lower()
        if needle and needle in haystack:
            keyword_hits.append(str(keyword))

    if not path_hits and not keyword_hits:
        return None

    return {
        "id": pack["id"],
        "doc": pack["doc"],
        "verification_families": pack.get("verification_families", []),
        "path_hits": sorted(set(path_hits)),
        "keyword_hits": sorted(set(keyword_hits)),
        "score": (2 * len(path_hits)) + len(keyword_hits),
    }


def _verification_plan(manifest: dict, families: list[str]) -> list[dict]:
    family_map = manifest.get("verification_families", {})
    seen = set()
    plan: list[dict] = []
    for family in families:
        if family in seen:
            continue
        seen.add(family)
        payload = family_map.get(family)
        if not isinstance(payload, dict):
            continue
        phase = str(payload.get("phase", "focused-semantic"))
        commands = payload.get("commands", [])
        plan.append(
            {
                "family": family,
                "phase": phase,
                "commands": commands if isinstance(commands, list) else [],
            }
        )
    plan.sort(key=lambda item: (PHASE_ORDER.get(item["phase"], 99), item["family"]))
    return plan


def _parse_args() -> argparse.Namespace:
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
    plan = _verification_plan(manifest, families)

    payload = {
        "files": files,
        "hints": args.hint,
        "matched_risk_packs": matched,
        "verification_plan": plan,
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
