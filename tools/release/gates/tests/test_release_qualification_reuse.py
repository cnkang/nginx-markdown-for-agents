"""Regression tests for exact-SHA release qualification reuse."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.release.gates import release_qualification_reuse as reuse

CANDIDATE_SHA = "a" * 40
REPOSITORY = "cnkang/nginx-markdown-for-agents"
BRANCH = "rehearsal/v0.9.4-candidate"
REF = f"refs/heads/{BRANCH}"
WORKFLOW_REF = f"{REPOSITORY}/{reuse.WORKFLOW_PATH}@{REF}"


def _context() -> dict:
    return {
        "candidate_sha": CANDIDATE_SHA,
        "release_version": "0.9.4",
        "repository": REPOSITORY,
        "inputs": {"require_blocking": True, "final_metadata": True},
        "source_run": {
            "id": 1234,
            "attempt": 2,
            "event": "workflow_dispatch",
            "ref": REF,
            "branch": BRANCH,
            "workflow_ref": WORKFLOW_REF,
            "workflow_sha": CANDIDATE_SHA,
        },
    }


def _source_files(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / reuse.FUZZ_RECORD).write_text(
        json.dumps({"candidate_sha": CANDIDATE_SHA, "blocking_pass": True}),
        encoding="utf-8",
    )
    (root / reuse.SOAK_MANIFEST).write_text(
        json.dumps({"candidate_sha": CANDIDATE_SHA}), encoding="utf-8"
    )
    (root / reuse.SOAK_RECORD).write_text(
        json.dumps({"candidate_sha": CANDIDATE_SHA, "status": "pass"}),
        encoding="utf-8",
    )
    logs = root / "fuzz-logs"
    logs.mkdir()
    (logs / "target.log").write_text("completed", encoding="utf-8")
    return root


def _api_run(run_id: int, attempt: int, *, sha: str = CANDIDATE_SHA,
             branch: str = BRANCH, conclusion: str = "success") -> dict:
    return {
        "id": run_id,
        "run_attempt": attempt,
        "head_sha": sha,
        "head_branch": branch,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": conclusion,
        "path": f"{reuse.WORKFLOW_PATH}@refs/heads/{branch}",
        "run_started_at": f"2026-11-20T00:00:{run_id % 60:02d}Z",
    }


def _build_bundle(tmp_path: Path) -> tuple[Path, dict]:
    source = _source_files(tmp_path / "source")
    bundle = tmp_path / "bundle"
    reuse.create_bundle(source, bundle, _context())
    return bundle, _api_run(1234, 2)


def test_bundle_is_bound_to_candidate_run_and_every_file(tmp_path: Path) -> None:
    bundle, source_run = _build_bundle(tmp_path)

    reuse.verify_bundle(
        bundle, CANDIDATE_SHA, "0.9.4", REPOSITORY, source_run
    )

    attestation = json.loads(
        (bundle / reuse.ATTESTATION_NAME).read_text(encoding="utf-8")
    )
    assert attestation["source_run"]["id"] == source_run["id"]
    assert attestation["source_run"]["attempt"] == source_run["run_attempt"]
    assert "fuzz-logs/target.log" in attestation["files"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_sha", "b" * 40),
        ("workflow_sha", "b" * 40),
        ("event", "push"),
        ("ref", "refs/tags/v0.9.4"),
    ],
)
def test_create_rejects_preflight_context_mismatches(
    tmp_path: Path, field: str, value: str
) -> None:
    source = _source_files(tmp_path / "source")
    context = _context()
    if field == "candidate_sha":
        context[field] = value
    else:
        context["source_run"][field] = value

    with pytest.raises(reuse.QualificationReuseError):
        reuse.create_bundle(source, tmp_path / "bundle", context)


def test_create_rejects_dispatch_without_both_release_inputs(tmp_path: Path) -> None:
    source = _source_files(tmp_path / "source")
    context = _context()
    context["inputs"]["final_metadata"] = False

    with pytest.raises(reuse.QualificationReuseError, match="required inputs"):
        reuse.create_bundle(source, tmp_path / "bundle", context)


def test_dispatch_context_requires_matching_workflow_and_release_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_SHA": CANDIDATE_SHA,
        "RELEASE_VERSION": "0.9.4",
        "GITHUB_REF": REF,
        "GITHUB_REF_NAME": BRANCH,
        "RELEASE_WORKFLOW_REF": WORKFLOW_REF,
        "RELEASE_WORKFLOW_SHA": CANDIDATE_SHA,
        "GITHUB_RUN_ID": "1234",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "REQUIRE_BLOCKING_INPUT": "true",
        "FINAL_METADATA_INPUT": "true",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    context = reuse._source_context_from_env()
    assert context["candidate_sha"] == CANDIDATE_SHA
    assert context["source_run"]["id"] == 1234
    assert context["source_run"]["attempt"] == 2

    monkeypatch.setenv("RELEASE_WORKFLOW_SHA", "b" * 40)
    with pytest.raises(reuse.QualificationReuseError, match="workflow source"):
        reuse._source_context_from_env()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda run: run.update(head_sha="b" * 40),
        lambda run: run.update(event="push"),
        lambda run: run.update(conclusion="failure"),
        lambda run: run.update(run_attempt=3),
        lambda run: run.update(path=".github/workflows/other.yml@refs/heads/main"),
    ],
)
def test_verify_rejects_mismatched_github_run(
    tmp_path: Path, mutate
) -> None:
    bundle, source_run = _build_bundle(tmp_path)
    mutate(source_run)

    with pytest.raises(reuse.QualificationReuseError):
        reuse.verify_bundle(
            bundle, CANDIDATE_SHA, "0.9.4", REPOSITORY, source_run
        )


def test_verify_rejects_changed_artifact_file(tmp_path: Path) -> None:
    bundle, source_run = _build_bundle(tmp_path)
    (bundle / reuse.SOAK_RECORD).write_text("{}\n", encoding="utf-8")

    with pytest.raises(reuse.QualificationReuseError, match="digest mismatch"):
        reuse.verify_bundle(
            bundle, CANDIDATE_SHA, "0.9.4", REPOSITORY, source_run
        )


def test_lookup_uses_newest_successful_run_with_unexpired_exact_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest_without_artifact = _api_run(200, 1)
    older_with_artifact = _api_run(100, 2)
    failed = _api_run(300, 1, conclusion="failure")
    stale_sha = _api_run(400, 1, sha="b" * 40)
    runs = [newest_without_artifact, older_with_artifact, failed, stale_sha]

    def fake_api_request(url: str, token: str) -> tuple[dict, str]:
        del token
        if "/actions/workflows/" in url:
            return {"workflow_runs": runs}, ""
        if "/actions/runs/200/artifacts" in url:
            return {"artifacts": []}, ""
        if "/actions/runs/100/artifacts" in url:
            return {
                "artifacts": [{
                    "name": f"release-qualification-{CANDIDATE_SHA}-2",
                    "expired": False,
                    "workflow_run": {
                        "id": 100,
                        "head_sha": CANDIDATE_SHA,
                    },
                }]
            }, ""
        raise AssertionError(f"unexpected API request: {url}")

    monkeypatch.setattr(reuse, "_api_request", fake_api_request)
    found = reuse.find_reusable_run(
        REPOSITORY, CANDIDATE_SHA, "https://api.github.com", "token"
    )

    assert found == (older_with_artifact,
                     f"release-qualification-{CANDIDATE_SHA}-2")


def test_lookup_ignores_expired_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _api_run(100, 1)

    def fake_api_request(url: str, token: str) -> tuple[dict, str]:
        del token
        if "/actions/workflows/" in url:
            return {"workflow_runs": [run]}, ""
        return {
            "artifacts": [{
                "name": f"release-qualification-{CANDIDATE_SHA}-1",
                "expired": True,
            }]
        }, ""

    monkeypatch.setattr(reuse, "_api_request", fake_api_request)
    assert reuse.find_reusable_run(
        REPOSITORY, CANDIDATE_SHA, "https://api.github.com", "token"
    ) is None


def test_discovery_api_failure_emits_a_cache_miss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setenv("GITHUB_SHA", CANDIDATE_SHA)
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.com")
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv(
        "RELEASE_QUALIFICATION_RUN_METADATA", str(tmp_path / "source-run.json")
    )

    def fail_lookup(*args, **kwargs):
        raise OSError("temporary API outage")

    monkeypatch.setattr(reuse, "find_reusable_run", fail_lookup)
    assert reuse._discover_from_env() == 0
    assert output.read_text(encoding="utf-8") == "found=false\n"


def test_restore_copies_only_the_requested_evidence(tmp_path: Path) -> None:
    bundle, _ = _build_bundle(tmp_path)
    output = tmp_path / "artifacts" / "release"
    release_dir = output / "0.9.4"
    release_dir.mkdir(parents=True)

    assert reuse._restore_from_args(
        type("Args", (), {
            "bundle_dir": bundle,
            "candidate_sha": CANDIDATE_SHA,
            "release_version": "0.9.4",
            "output_root": output,
        })(),
        "soak",
    ) == 0
    assert (release_dir / reuse.SOAK_MANIFEST).is_file()
    assert (release_dir / reuse.SOAK_RECORD).is_file()
    assert not (release_dir / reuse.FUZZ_RECORD).exists()
