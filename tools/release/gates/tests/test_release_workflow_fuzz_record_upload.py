"""The Release Gate must upload the fuzz qualification record on failure too.

The record and the per-target raw logs are the only post-mortem evidence
when the fuzz qualification fails; the workflow's other upload steps sit on
success paths, so this step is asserted to be unconditional (``if:
always()``) and to point at the paths the validator actually writes.  The
paths are taken from the validator's own constants, so renaming a record
path without updating the workflow fails here.
"""

from __future__ import annotations

from pathlib import Path

import yaml

import tools.release.gates.validate_fuzz_qualification as validator

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"


def _release_gate_steps() -> list[object]:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document["jobs"]["release-gate"]["steps"]


def test_fuzz_record_upload_is_unconditional_and_matches_the_validator() -> None:
    uploads = [
        step for step in _release_gate_steps()
        if isinstance(step, dict)
        and step.get("name") == "Upload fuzz qualification record"
    ]
    assert len(uploads) == 1, "the record upload step is missing"
    step = uploads[0]
    assert step.get("if") == "always()", (
        "the record upload must survive a failing fuzz qualification")
    path_text = step.get("with", {}).get("path", "")
    record = validator.DEFAULT_RECORD.rstrip("/")
    log_dir = validator.DEFAULT_LOG_DIR.rstrip("/")
    assert record in path_text
    assert log_dir + "/" in path_text
