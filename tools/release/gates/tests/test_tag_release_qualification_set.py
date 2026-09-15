"""Tests for the tag release blocking-qualification set.

The tag release path (``.github/workflows/release-packages.yml``) must run the
same candidate-bound qualification stages as the Makefile
``release-gates-check-092`` target's [9/14]-[13/14] stages.  These tests pin
the two surfaces together so they cannot drift.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"
MAKEFILE = REPO_ROOT / "Makefile"

# Makefile stage name -> validator module that the stage invokes.  Must stay
# in lockstep with release-gates-check-092 stages [9/14]-[13/14].
QUALIFICATION_STAGES = {
    "release-candidate-evidence-check": "validate_release_candidate_evidence.py",
    "artifact-registry-check": "validate_artifact_registry.py",
    "release-evidence-manifest-check": "validate_release_evidence_manifest.py",
    "test-rust-fuzz-qualification": "validate_fuzz_qualification.py",
    "test-e2e-rust-soak": "validate_soak_qualification.py",
}

STREAMING_EVIDENCE_VALIDATOR = "validate_streaming_evidence.py"
TAG_RULESET_VALIDATOR = "verify_tag_ref_protection.py"


def _workflow_release_gate_body() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.find("  release-gate:")
    if start == -1:
        raise AssertionError(
            f"{WORKFLOW.name}: missing '  release-gate:' job marker"
        )
    end = text.find("  integrity-checksums:", start)
    if end == -1:
        raise AssertionError(
            f"{WORKFLOW.name}: missing '  integrity-checksums:' job marker "
            "after the release-gate job"
        )
    return text[start:end]


def _workflow_publish_body() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.find("  publish:")
    if start == -1:
        raise AssertionError(
            f"{WORKFLOW.name}: missing '  publish:' job marker"
        )
    rest = text[start:]
    # Stop at the next top-level job marker so a later job cannot leak
    # into the publish job's own configuration.  MULTILINE makes `$`
    # match the end of any line, not only the end of the whole text.
    next_job = re.search(r"\n  [A-Za-z0-9_-]+:$", rest, flags=re.MULTILINE)
    if next_job is not None:
        rest = rest[: next_job.start()]
    return rest


def _makefile_092_target() -> str:
    text = MAKEFILE.read_text(encoding="utf-8")
    start = text.find("release-gates-check-092: release-gates-check-092-canonical")
    if start == -1:
        raise AssertionError(
            f"{MAKEFILE.name}: missing 'release-gates-check-092: "
            "release-gates-check-092-canonical' target line"
        )
    end = text.find("\nrelease-matrix-check:", start)
    if end == -1:
        raise AssertionError(
            f"{MAKEFILE.name}: missing 'release-matrix-check:' target "
            "after release-gates-check-092"
        )
    return text[start:end]


def test_release_gate_job_runs_all_qualification_validators() -> None:
    body = _workflow_release_gate_body()
    for stage, validator in QUALIFICATION_STAGES.items():
        assert validator in body, (
            f"release-gate job must run {validator} (Makefile stage "
            f"'{stage}'); the tag release qualification set has drifted "
            "from release-gates-check-092"
        )


def test_makefile_092_runs_all_qualification_stages() -> None:
    target = _makefile_092_target()
    for stage in QUALIFICATION_STAGES:
        assert f"$(MAKE) {stage}" in target, (
            f"release-gates-check-092 must invoke '{stage}' as a blocking stage"
        )


def test_release_gate_runs_the_shared_contract_validators() -> None:
    """Tag CI must enforce the tag and streaming contracts directly."""
    body = _workflow_release_gate_body()
    assert TAG_RULESET_VALIDATOR in body
    assert '--repo "${GITHUB_REPOSITORY}"' in body
    assert STREAMING_EVIDENCE_VALIDATOR in body


def test_makefile_092_runs_the_shared_contract_validators() -> None:
    """The local blocking target must not omit either contract validator."""
    target = _makefile_092_target()
    assert TAG_RULESET_VALIDATOR in target
    assert "$(MAKE) streaming-evidence-check" in target


def test_publish_hard_depends_on_release_gate() -> None:
    body = _workflow_publish_body()
    # The qualification validators run inside release-gate, so publish's
    # success condition on release-gate carries the qualification.
    assert "needs.release-gate.result == 'success'" in body


RC_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "rc-release-gates.yml"


def _release_jobs() -> dict:
    import yaml

    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _rc_jobs() -> dict:
    import yaml

    return yaml.safe_load(RC_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _rc_triggers() -> dict:
    import yaml

    document = yaml.safe_load(RC_WORKFLOW.read_text(encoding="utf-8"))
    # YAML reads a bare `on` key as the boolean True.
    return document.get("on", document.get(True, {}))


def _publish_allowed(results: dict[str, str], event: str = "push") -> bool:
    """Evaluate the publish condition with the given upstream results."""
    condition = _release_jobs()["publish"]["if"]
    # The workflow folds the condition over several lines.
    condition = " ".join(condition.split())
    condition = condition.replace("&&", " and ").replace("||", " or ")
    condition = condition.replace("always()", "True")
    condition = re.sub(
        r"github\.event_name == '([a-z_]+)'",
        lambda match: str(event == match.group(1)),
        condition,
    )
    condition = re.sub(
        r"needs\.([a-z-]+)\.result == '([a-z]+)'",
        lambda match: str(results.get(match.group(1)) == match.group(2)),
        condition,
    )
    assert "needs." not in condition, condition
    return bool(eval(condition, {"__builtins__": {}}, {}))  # noqa: S307 - our own condition


def test_the_release_workflow_calls_the_candidate_gates() -> None:
    """The canonical publication path owns the heavy qualification."""
    assert (
        _release_jobs()["rc-release-gates"]["uses"]
        == "./.github/workflows/rc-release-gates.yml"
    )


def test_the_candidate_gates_are_reusable_and_not_tag_triggered() -> None:
    """One authoritative qualification per candidate, not two."""
    triggers = _rc_triggers()
    assert "workflow_call" in triggers
    assert "workflow_dispatch" in triggers
    assert "push" not in triggers


def test_tag_publish_needs_the_candidate_gates() -> None:
    publish = _release_jobs()["publish"]
    assert "rc-release-gates" in publish["needs"]
    assert "needs.rc-release-gates.result == 'success'" in publish["if"]


def test_any_non_success_candidate_result_blocks_publication() -> None:
    """Neither failure, cancellation nor a skip may publish the tag."""
    green = {
        "musl-build": "success",
        "integrity-checksums": "success",
        "release-gate": "success",
        "official-docker-release-gate": "success",
        "integrity-signature": "success",
    }
    assert _publish_allowed({**green, "rc-release-gates": "success"}) is True
    for outcome in ("failure", "cancelled", "skipped"):
        assert _publish_allowed({**green, "rc-release-gates": outcome}) is False


def test_the_candidate_gates_run_the_called_commit() -> None:
    """The evidence records the SHA the caller checked out, not a pinned ref."""
    steps = _rc_jobs()["real-nginx-e2e"]["steps"]
    checkout = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert "ref" not in checkout.get("with", {})
    assert 'os.environ["GITHUB_SHA"]' in RC_WORKFLOW.read_text(encoding="utf-8")


def test_manual_qualification_is_explicitly_defined() -> None:
    """A hand-run candidate gate is defined, and it is not a second tag path."""
    assert "workflow_dispatch" in _rc_triggers()
