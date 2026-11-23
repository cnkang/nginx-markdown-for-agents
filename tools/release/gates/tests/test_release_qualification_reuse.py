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
    reuse.create_bundle(source, bundle, _context(), trusted_root=tmp_path)
    return bundle, _api_run(1234, 2)


def test_bundle_is_bound_to_candidate_run_and_every_file(tmp_path: Path) -> None:
    bundle, source_run = _build_bundle(tmp_path)

    reuse.verify_bundle(
        bundle,
        CANDIDATE_SHA,
        "0.9.4",
        REPOSITORY,
        source_run,
        trusted_root=tmp_path,
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
        reuse.create_bundle(
            source, tmp_path / "bundle", context, trusted_root=tmp_path
        )


def test_create_rejects_dispatch_without_both_release_inputs(tmp_path: Path) -> None:
    source = _source_files(tmp_path / "source")
    context = _context()
    context["inputs"]["final_metadata"] = False

    with pytest.raises(reuse.QualificationReuseError, match="required inputs"):
        reuse.create_bundle(
            source, tmp_path / "bundle", context, trusted_root=tmp_path
        )


@pytest.mark.parametrize("escape_source", [True, False])
def test_create_bundle_rejects_paths_outside_trusted_root(
    tmp_path: Path, escape_source: bool
) -> None:
    trusted_root = tmp_path / "workspace"
    trusted_root.mkdir()
    source_root = tmp_path / "outside-source"
    bundle_root = tmp_path / "outside-bundle"
    source = _source_files(
        source_root if escape_source else trusted_root / "source"
    )
    bundle = trusted_root / "bundle" if escape_source else bundle_root
    context = _context()

    with pytest.raises(reuse.QualificationReuseError, match="trusted root"):
        reuse.create_bundle(
            source, bundle, context, trusted_root=trusted_root
        )

    assert not bundle.exists()


def test_release_version_rejects_non_ascii_digits() -> None:
    with pytest.raises(reuse.QualificationReuseError, match="malformed"):
        reuse._require_version("٠.9.4")


def test_read_json_rejects_paths_outside_trusted_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted_root = tmp_path / "trusted"
    trusted_root.mkdir()
    outside_dir = tmp_path / "trusted-sibling"
    outside_dir.mkdir()
    outside = outside_dir / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")

    def fail_if_checked_before_validation(path: Path, label: str) -> None:
        pytest.fail(f"checked {label} before trusted-root validation: {path}")

    monkeypatch.setattr(
        reuse, "_regular_file", fail_if_checked_before_validation
    )
    with pytest.raises(reuse.QualificationReuseError, match="trusted root"):
        reuse._read_json(outside, "metadata", root=trusted_root)


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
            bundle,
            CANDIDATE_SHA,
            "0.9.4",
            REPOSITORY,
            source_run,
            trusted_root=tmp_path,
        )


def test_verify_rejects_changed_artifact_file(tmp_path: Path) -> None:
    bundle, source_run = _build_bundle(tmp_path)
    (bundle / reuse.SOAK_RECORD).write_text("{}\n", encoding="utf-8")

    with pytest.raises(reuse.QualificationReuseError, match="digest mismatch"):
        reuse.verify_bundle(
            bundle,
            CANDIDATE_SHA,
            "0.9.4",
            REPOSITORY,
            source_run,
            trusted_root=tmp_path,
        )


def test_lookup_uses_newest_successful_run_with_unexpired_exact_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest_without_artifact = _api_run(200, 1)
    older_with_artifact = _api_run(100, 2)
    newest_without_artifact["run_started_at"] = "2026-11-20T01:00:00Z"
    older_with_artifact["run_started_at"] = "2026-11-20T00:00:00Z"
    failed = _api_run(300, 1, conclusion="failure")
    stale_sha = _api_run(400, 1, sha="b" * 40)
    runs = [newest_without_artifact, older_with_artifact, failed, stale_sha]
    artifact_urls: list[str] = []

    def fake_api_request(url: str, token: str) -> tuple[dict, str]:
        del token
        if "/actions/workflows/" in url:
            return {"workflow_runs": runs}, ""
        if "/actions/runs/200/artifacts" in url:
            artifact_urls.append(url)
            return {"artifacts": []}, ""
        if "/actions/runs/100/artifacts" in url:
            artifact_urls.append(url)
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
    artifact_api = "https://api.github.com/repos/cnkang/nginx-markdown-for-agents"
    assert artifact_urls == [
        f"{artifact_api}/actions/runs/200/artifacts?per_page=100",
        f"{artifact_api}/actions/runs/100/artifacts?per_page=100",
    ]


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
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv(
        "RELEASE_QUALIFICATION_RUN_METADATA", str(tmp_path / "source-run.json")
    )

    def fail_lookup(*args, **kwargs):
        raise OSError("temporary API outage")

    monkeypatch.setattr(reuse, "find_reusable_run", fail_lookup)
    assert reuse._discover_from_env() == 0
    assert output.read_text(encoding="utf-8") == "found=false\n"


def test_restore_copies_only_the_requested_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _build_bundle(tmp_path)
    output = tmp_path / "artifacts" / "release"
    release_dir = output / "0.9.4"
    release_dir.mkdir(parents=True)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))

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


def test_restore_failure_removes_only_paths_created_by_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _build_bundle(tmp_path)
    output = tmp_path / "artifacts" / "release"
    release_dir = output / "0.9.4"
    release_dir.mkdir(parents=True)
    (release_dir / "keep.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))

    def partial_copytree(source: Path, destination: Path, **kwargs) -> None:
        del source, kwargs
        destination.mkdir()
        (destination / "partial.log").write_text("partial", encoding="utf-8")
        raise OSError("injected copy failure")

    monkeypatch.setattr(reuse.shutil, "copytree", partial_copytree)
    args = type("Args", (), {
        "bundle_dir": bundle,
        "candidate_sha": CANDIDATE_SHA,
        "release_version": "0.9.4",
        "output_root": output,
    })()

    with pytest.raises(OSError, match="injected copy failure"):
        reuse._restore_from_args(args, "fuzz")

    assert (release_dir / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not (release_dir / reuse.FUZZ_RECORD).exists()
    assert not (release_dir / "fuzz-logs").exists()


def test_restore_collision_preserves_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _build_bundle(tmp_path)
    output = tmp_path / "artifacts" / "release"
    release_dir = output / "0.9.4"
    release_dir.mkdir(parents=True)
    manifest = release_dir / reuse.SOAK_MANIFEST
    manifest.write_text("existing", encoding="utf-8")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    args = type("Args", (), {
        "bundle_dir": bundle,
        "candidate_sha": CANDIDATE_SHA,
        "release_version": "0.9.4",
        "output_root": output,
    })()

    with pytest.raises(reuse.QualificationReuseError, match="overwrite"):
        reuse._restore_from_args(args, "soak")

    assert manifest.read_text(encoding="utf-8") == "existing"
    assert not (release_dir / reuse.SOAK_RECORD).exists()


def test_tree_files_fails_closed_when_walk_cannot_enumerate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _build_bundle(tmp_path)
    original_walk = reuse.os.walk

    def walk_with_error(root, *, followlinks, onerror):
        yield from original_walk(root, followlinks=followlinks, onerror=onerror)
        onerror(PermissionError("injected directory-listing failure"))

    monkeypatch.setattr(reuse.os, "walk", walk_with_error)
    with pytest.raises(
        reuse.QualificationReuseError,
        match="bundle directory cannot be enumerated",
    ):
        reuse._tree_files(bundle)


def test_verify_cli_rejects_source_run_outside_runner_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_source_run = tmp_path / "source-run.json"
    outside_source_run.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("RUNNER_TEMP", str(runner_temp))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
    args = type("Args", (), {
        "bundle_dir": runner_temp,
        "source_run": outside_source_run,
        "candidate_sha": CANDIDATE_SHA,
        "release_version": "0.9.4",
        "repository": REPOSITORY,
    })()

    with pytest.raises(reuse.QualificationReuseError, match="trusted root"):
        reuse._verify_from_args(args)


@pytest.mark.parametrize("invalid_path", ["source", "bundle"])
def test_create_cli_rejects_paths_outside_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_path: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "release-evidence"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
    source_dir = outside if invalid_path == "source" else source
    bundle_dir = workspace / "bundle"
    if invalid_path == "bundle":
        bundle_dir = outside / "bundle"
    args = type("Args", (), {
        "source_dir": source_dir,
        "bundle_dir": bundle_dir,
    })()

    with pytest.raises(ValueError, match="trusted root|escapes root"):
        reuse._create_from_env(args)
