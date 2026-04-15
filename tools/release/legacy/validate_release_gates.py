#!/usr/bin/env python3
"""Main entry point for 0.4.0 release gate validation.

Orchestrates all sub-spec document validation checks and prints
a clear pass/fail summary per check. Returns non-zero exit code
on any validation failure.

Requirements: 3.1, 3.2, 3.3, 3.4, 10.3
"""

import argparse
import os
import sys
from typing import Callable, List, Tuple

# Allow running as `python3 tools/release/legacy/validate_release_gates.py` from
# the project root by ensuring the project root is on sys.path.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tools.release.legacy.release_gate_checks import (  # noqa: E402
    check_document_existence,
    check_requirements_completeness,
    check_design_completeness,
    check_boundary_descriptions,
    check_dod_evaluation_tables,
    check_checklist_verifiability,
)


def _run_checks(
    specs_dir: str, release_gates_dir: str
) -> List[Tuple[str, bool, List[str]]]:
    """Run all validation checks and collect results."""
    results: List[Tuple[str, bool, List[str]]] = []

    def run_check(
        check_name: str,
        check_fn: Callable[[str], Tuple[bool, List[str]]],
        target_dir: str,
    ) -> None:
        """Run one check and normalize exceptions into a failed result entry."""
        try:
            passed, msgs = check_fn(target_dir)
        except Exception as exc:
            passed, msgs = False, [f"Exception: {exc}"]
        results.append((check_name, passed, msgs))

    run_check(
        "Document Existence (Property 2)",
        check_document_existence,
        specs_dir,
    )
    run_check(
        "Requirements Completeness (Property 1)",
        check_requirements_completeness,
        specs_dir,
    )
    run_check(
        "Design Completeness (Property 1 — R3.4)",
        check_design_completeness,
        specs_dir,
    )
    run_check(
        "Boundary Descriptions (Property 3)",
        check_boundary_descriptions,
        specs_dir,
    )
    run_check(
        "DoD Evaluation Tables (Property 5)",
        check_dod_evaluation_tables,
        specs_dir,
    )
    run_check(
        "Checklist Verifiability (Property 11)",
        check_checklist_verifiability,
        release_gates_dir,
    )

    return results


def _print_results(results: List[Tuple[str, bool, List[str]]]) -> bool:
    """Print results and return True if all checks passed."""
    all_passed = True
    print("=" * 60)
    print("0.4.0 Release Gate Validation")
    print("=" * 60)

    for check_name, passed, msgs in results:
        status = "PASS" if passed else "FAIL"
        print(f"\n[{status}] {check_name}")
        for msg in msgs:
            print(f"  {msg}")
        if not passed:
            all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("RESULT: All release gate checks passed.")
    else:
        failed = [name for name, gate_passed, _ in results if not gate_passed]
        print(f"RESULT: {len(failed)} check(s) failed:")
        for name in failed:
            print(f"  - {name}")
    print("=" * 60)

    return all_passed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate 0.4.0 release gate documents and conventions."
    )
    parser.add_argument(
        "--specs-dir",
        default=".kiro/specs/",
        help="Path to the specs directory (default: .kiro/specs/)",
    )
    parser.add_argument(
        "--release-gates-dir",
        default="docs/project/release-gates/",
        help="Path to the release gates directory (default: docs/project/release-gates/)",
    )
    args = parser.parse_args()

    results = _run_checks(args.specs_dir, args.release_gates_dir)
    all_passed = _print_results(results)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
