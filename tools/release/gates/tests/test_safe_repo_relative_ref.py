"""Regression tests for the shared canonical reference parser.

`safe_repo_relative_ref` is the single owner of repository-relative
reference semantics for the release gates.  Before consolidation, the
gate-manifests copy lacked the Windows drive-letter rejection and the two
validators disagreed on identical input (Rule 31 violation): a reference
like ``C:/evidence`` was rejected by one gate and silently accepted by the
other.  These tests pin the canonical behavior and its use by both gates.
"""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

from tools.lib.path_validation import safe_repo_relative_ref
from tools.release.gates import generate_release_gate_manifests as manifests
from tools.release.gates import validate_fuzz_qualification as qualification


def test_canonical_reference_accepted() -> None:
    assert safe_repo_relative_ref("artifacts/release/0.9.2/fuzz-logs/x.log") == (
        PurePosixPath("artifacts/release/0.9.2/fuzz-logs/x.log")
    )


def test_drive_letter_reference_rejected() -> None:
    """A Windows drive prefix must be rejected (the dropped-guard class)."""
    assert safe_repo_relative_ref("C:/evidence") is None
    assert safe_repo_relative_ref("c:relative") is None


def test_other_malformed_references_rejected() -> None:
    for malformed in (
        "",
        "/absolute/path",
        "back\\slash",
        ".",
        "./leading-dot",
        "nested/../escape",
        "double//separator",
    ):
        assert safe_repo_relative_ref(malformed) is None, malformed


def test_both_gates_share_one_parser() -> None:
    """Both validators call the same shared source (no drifting copy).

    The gates import the module under different sys.path spellings
    (`tools.lib.path_validation` vs `lib.path_validation` via the injected
    `tools` path), so identity is asserted by the defining module's file
    name plus identical behavior on the discriminating input.
    """
    for gate_fn in (manifests._safe_fuzz_reference, qualification._safe_record_path):
        assert type(gate_fn).__name__ == "function"
        module_file = Path(sys.modules[gate_fn.__module__].__file__).name
        assert module_file == "path_validation.py"
        assert gate_fn("artifacts/release/x.log") is not None
        assert gate_fn("C:/evidence") is None


def test_both_gates_reject_the_drive_letter_case() -> None:
    """The exact case the weaker copy used to accept, on both call sites."""
    assert manifests._safe_fuzz_reference("C:/evidence") is None
    assert qualification._safe_record_path("C:/evidence") is None
