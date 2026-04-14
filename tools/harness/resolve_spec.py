#!/usr/bin/env python3
"""Resolve the current spec from explicit hints, local pointers, and task text."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


PASS = "PASS"
FAIL = "FAIL"
SKIP_NOT_PRESENT = "SKIP_NOT_PRESENT"
WARN_NEEDS_AUTHOR_REVIEW = "WARN_NEEDS_AUTHOR_REVIEW"

REPO_ROOT = Path(__file__).resolve().parents[2]
SPECS_ROOT = REPO_ROOT / ".kiro" / "specs"
POINTER_CANDIDATES = [
    REPO_ROOT / ".kiro" / "active-spec.json",
    REPO_ROOT / ".kiro" / "active-spec.txt",
]
TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
STOP_WORDS = {
    "spec",
    "task",
    "tasks",
    "feature",
    "features",
    "bug",
    "bugfix",
    "fix",
    "work",
    "current",
    "continue",
    "next",
    "with",
    "from",
    "that",
    "this",
    "then",
}


@dataclass(frozen=True)
class SpecCandidate:
    dir_name: str
    spec_id: str
    spec_type: str
    workflow_type: str


@dataclass(frozen=True)
class Resolution:
    status: str
    reason: str
    chosen: dict | None
    candidates: list[dict]
    pointer: str | None = None


def discover_specs(specs_root: Path | None = None) -> list[SpecCandidate]:
    specs_root = specs_root or SPECS_ROOT
    if not specs_root.exists():
        return []
    specs: list[SpecCandidate] = []
    for config_path in sorted(specs_root.glob("*/.config.kiro")):
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        specs.append(
            SpecCandidate(
                dir_name=config_path.parent.name,
                spec_id=data.get("specId", ""),
                spec_type=data.get("specType", ""),
                workflow_type=data.get("workflowType", ""),
            )
        )
    return specs


def _normalize(text: str) -> str:
    return text.strip().lower()


def _tokenize(*values: str) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        for token in TOKEN_RE.findall(_normalize(value)):
            if token not in STOP_WORDS and len(token) >= 3:
                tokens.add(token)
    return tokens


def _match_by_identity(candidates: list[SpecCandidate], value: str) -> list[SpecCandidate]:
    needle = _normalize(value)
    matches = []
    for candidate in candidates:
        haystack = {
            _normalize(candidate.dir_name),
            _normalize(candidate.spec_id),
        }
        if needle in haystack or needle in _normalize(candidate.dir_name) or needle in _normalize(candidate.spec_id):
            matches.append(candidate)
    return matches


def _read_pointer(
    pointer_candidates: list[Path] | None = None,
) -> tuple[str | None, str | None]:
    pointer_candidates = pointer_candidates or POINTER_CANDIDATES
    for path in pointer_candidates:
        if not path.exists():
            continue
        if path.suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            try:
                path_display = str(path.relative_to(REPO_ROOT))
            except ValueError:
                path_display = str(path)
            return (
                data.get("spec"),
                path_display,
            )
        try:
            value = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if value:
            try:
                path_display = str(path.relative_to(REPO_ROOT))
            except ValueError:
                path_display = str(path)
            return value, path_display
    return None, None


def _score_candidates(candidates: list[SpecCandidate], hints: list[str]) -> list[tuple[int, SpecCandidate]]:
    hint_tokens = _tokenize(*hints)
    if not hint_tokens:
        return []
    scored: list[tuple[int, SpecCandidate]] = []
    for candidate in candidates:
        candidate_tokens = _tokenize(candidate.dir_name, candidate.spec_id, candidate.spec_type)
        score = len(candidate_tokens & hint_tokens)
        if score:
            scored.append((score, candidate))
    scored.sort(key=lambda item: (-item[0], item[1].dir_name))
    return scored


def _candidate_dict(candidate: SpecCandidate, *, score: int | None = None) -> dict:
    data = asdict(candidate)
    if score is not None:
        data["score"] = score
    return data


def resolve_spec(
    *,
    explicit_specs: list[str] | None = None,
    hints: list[str] | None = None,
    specs_root: Path | None = None,
    pointer_candidates: list[Path] | None = None,
) -> Resolution:
    explicit_specs = explicit_specs or []
    hints = hints or []
    candidates = discover_specs(specs_root)
    if not candidates:
        return Resolution(
            status=SKIP_NOT_PRESENT,
            reason="spec root not present",
            chosen=None,
            candidates=[],
        )

    for explicit in explicit_specs:
        matches = _match_by_identity(candidates, explicit)
        if len(matches) == 1:
            return Resolution(
                status=PASS,
                reason=f"explicit spec match: {explicit}",
                chosen=_candidate_dict(matches[0]),
                candidates=[_candidate_dict(matches[0])],
            )
        if len(matches) > 1:
            return Resolution(
                status=WARN_NEEDS_AUTHOR_REVIEW,
                reason=f"explicit spec is ambiguous: {explicit}",
                chosen=None,
                candidates=[_candidate_dict(candidate) for candidate in matches],
            )

    pointer_value, pointer_path = _read_pointer(pointer_candidates)
    if pointer_value:
        matches = _match_by_identity(candidates, pointer_value)
        if len(matches) == 1:
            return Resolution(
                status=PASS,
                reason=f"active pointer match: {pointer_value}",
                chosen=_candidate_dict(matches[0]),
                candidates=[_candidate_dict(matches[0])],
                pointer=pointer_path,
            )
        if len(matches) > 1:
            return Resolution(
                status=WARN_NEEDS_AUTHOR_REVIEW,
                reason=f"active pointer is ambiguous: {pointer_value}",
                chosen=None,
                candidates=[_candidate_dict(candidate) for candidate in matches],
                pointer=pointer_path,
            )
        return Resolution(
            status=WARN_NEEDS_AUTHOR_REVIEW,
            reason=f"active pointer does not match any local spec: {pointer_value}",
            chosen=None,
            candidates=[_candidate_dict(candidate) for candidate in candidates],
            pointer=pointer_path,
        )

    scored = _score_candidates(candidates, hints)
    if not scored:
        return Resolution(
            status=WARN_NEEDS_AUTHOR_REVIEW,
            reason="no spec could be bound confidently from hints",
            chosen=None,
            candidates=[_candidate_dict(candidate) for candidate in candidates],
        )

    best_score, best_candidate = scored[0]
    tied = [item for item in scored if item[0] == best_score]
    if len(tied) > 1:
        return Resolution(
            status=WARN_NEEDS_AUTHOR_REVIEW,
            reason="multiple specs match the current hints",
            chosen=None,
            candidates=[_candidate_dict(candidate, score=score) for score, candidate in tied],
        )

    return Resolution(
        status=PASS,
        reason="best hint match",
        chosen=_candidate_dict(best_candidate, score=best_score),
        candidates=[_candidate_dict(candidate, score=score) for score, candidate in scored],
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", action="append", default=[], help="explicit spec name or specId")
    parser.add_argument(
        "--hint",
        action="append",
        default=[],
        help="task text or other hint text used for matching",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print machine-readable JSON",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    resolution = resolve_spec(explicit_specs=args.spec, hints=args.hint)
    payload = asdict(resolution)

    if args.json:
        print(json.dumps(payload, ensure_ascii=True))
    else:
        print(f"{resolution.status}: {resolution.reason}")
        if resolution.pointer:
            print(f"pointer: {resolution.pointer}")
        if resolution.chosen:
            print(f"chosen: {resolution.chosen['dir_name']} ({resolution.chosen['spec_id']})")
        if resolution.status != PASS:
            print("candidates:")
            for candidate in resolution.candidates:
                print(f"- {candidate['dir_name']} ({candidate['spec_id']})")

    return 0 if resolution.status in {PASS, SKIP_NOT_PRESENT} else 1


if __name__ == "__main__":
    raise SystemExit(main())
