#!/usr/bin/env python3
"""Find and validate same-commit release qualification artifacts.

Only a successful workflow_dispatch run for the exact candidate commit can
provide reusable fuzz and short-soak evidence. Any discovery or validation
failure is treated as a cache miss so the release workflow runs both checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import urllib.parse
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
for _path in (str(REPO_ROOT), str(REPO_ROOT / "tools")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from tools.lib.path_validation import (  # noqa: E402
    validate_read_path,
    validate_write_path_within_root,
)

SCHEMA_VERSION = "release.qualification-bundle.v1"
WORKFLOW_PATH = ".github/workflows/release-packages.yml"
ATTESTATION_NAME = "qualification-attestation.json"
FUZZ_RECORD = "fuzz-qualification-record.json"
SOAK_MANIFEST = "short-soak-scenario-manifest.json"
SOAK_RECORD = "soak-qualification-record.json"
SHA_RE = re.compile(r"^[\da-f]{40}$", re.ASCII)
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$", re.ASCII)
REPOSITORY_RE = re.compile(r"^[A-Za-z\d_.-]+/[A-Za-z\d_.-]+$", re.ASCII)
CANDIDATE_SHA_LABEL = "candidate SHA"
FUZZ_RECORD_LABEL = "fuzz record"
SOAK_MANIFEST_LABEL = "short-soak manifest"
SOAK_RECORD_LABEL = "short-soak record"
FUZZ_BLOCKING_PASS_ERROR = f"{FUZZ_RECORD_LABEL} is not a blocking pass"
SOAK_PASS_ERROR = f"{SOAK_RECORD_LABEL} is not a pass"


class QualificationReuseError(ValueError):
    """Report missing, stale, or malformed reusable qualification evidence."""


def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise QualificationReuseError(f"{label} must be a full lowercase SHA")
    return value


def _require_version(value: object) -> str:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise QualificationReuseError("release version is malformed")
    return value


def _require_repository(value: object) -> str:
    if not isinstance(value, str) or not REPOSITORY_RE.fullmatch(value):
        raise QualificationReuseError("repository name is malformed")
    return value


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _environment_root(name: str) -> pathlib.Path:
    value = os.environ.get(name)
    if not value:
        raise QualificationReuseError(f"{name} is missing")
    root = validate_read_path(value, purpose=name)
    if not root.is_dir():
        raise QualificationReuseError(f"{name} must be a directory")
    return root


def _read_path_within(path: pathlib.Path, root: pathlib.Path,
                      label: str) -> pathlib.Path:
    if path.is_symlink():
        raise QualificationReuseError(f"{label} must not be a symbolic link")
    try:
        resolved_root = validate_read_path(root, purpose=f"{label} root")
        resolved = validate_read_path(path, purpose=label)
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise QualificationReuseError(f"{label} is outside its trusted root") from exc
    return resolved


def _write_path_within(path: pathlib.Path | str, root: pathlib.Path,
                       label: str) -> pathlib.Path:
    try:
        resolved_root = validate_read_path(root, purpose=f"{label} root")
        return validate_write_path_within_root(
            path, resolved_root, purpose=label
        )
    except ValueError as exc:
        raise QualificationReuseError(
            f"{label} is outside its trusted root"
        ) from exc


def _read_json(path: pathlib.Path, label: str, *, root: pathlib.Path) -> dict:
    try:
        safe_path = _read_path_within(path, root, label)
        _regular_file(safe_path, label)
        value = json.loads(safe_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualificationReuseError(f"{label} is missing or malformed") from exc
    if not isinstance(value, dict):
        raise QualificationReuseError(f"{label} must be a JSON object")
    return value


def _regular_file(path: pathlib.Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise QualificationReuseError(f"{label} must be a regular file")


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    safe_path = validate_read_path(path, purpose="bundle entry")
    with safe_path.open("rb") as stream:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _tree_files(root: pathlib.Path) -> dict[str, str]:
    if root.is_symlink():
        raise QualificationReuseError("qualification bundle directory is missing")
    root = validate_read_path(root, purpose="qualification bundle")
    if not root.is_dir():
        raise QualificationReuseError("qualification bundle directory is missing")
    files: dict[str, str] = {}
    for current, directories, names in os.walk(
        root, followlinks=False, onerror=_raise_walk_failure
    ):
        current_path = pathlib.Path(current)
        for name in directories:
            if (current_path / name).is_symlink():
                raise QualificationReuseError("bundle contains a symbolic link")
        for name in names:
            path = current_path / name
            _regular_file(path, "bundle entry")
            relative = path.relative_to(root).as_posix()
            if relative == ATTESTATION_NAME:
                continue
            files[relative] = _sha256_file(path)
    return dict(sorted(files.items()))


def _raise_walk_failure(error: OSError) -> None:
    raise QualificationReuseError("bundle directory cannot be enumerated") from error


def _copy_regular_file(source: pathlib.Path, destination: pathlib.Path,
                       *, source_root: pathlib.Path,
                       destination_root: pathlib.Path) -> None:
    safe_source = _read_path_within(source, source_root, source.name)
    _regular_file(safe_source, source.name)
    safe_destination = _write_path_within(
        destination, destination_root, destination.name
    )
    safe_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(safe_source, safe_destination)


def _source_context_from_env() -> dict:
    repository = _require_repository(os.environ.get("GITHUB_REPOSITORY"))
    candidate_sha = _require_sha(
        os.environ.get("GITHUB_SHA"), CANDIDATE_SHA_LABEL
    )
    version = _require_version(os.environ.get("RELEASE_VERSION"))
    ref = os.environ.get("GITHUB_REF", "")
    branch = os.environ.get("GITHUB_REF_NAME", "")
    workflow_ref = os.environ.get("RELEASE_WORKFLOW_REF", "")
    workflow_sha = _require_sha(
        os.environ.get("RELEASE_WORKFLOW_SHA"), "workflow SHA"
    )
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise QualificationReuseError("only workflow_dispatch runs can be reused")
    if not ref.startswith("refs/heads/") or ref != f"refs/heads/{branch}":
        raise QualificationReuseError("preflight must run from a branch ref")
    if not branch:
        raise QualificationReuseError("preflight branch is missing")
    expected_workflow_ref = f"{repository}/{WORKFLOW_PATH}@{ref}"
    if workflow_ref != expected_workflow_ref or workflow_sha != candidate_sha:
        raise QualificationReuseError(
            f"workflow source does not match {CANDIDATE_SHA_LABEL}"
        )
    if os.environ.get("REQUIRE_BLOCKING_INPUT", "").lower() != "true":
        raise QualificationReuseError("require_blocking input must be true")
    if os.environ.get("FINAL_METADATA_INPUT", "").lower() != "true":
        raise QualificationReuseError("final_metadata input must be true")
    if not run_id.isdecimal() or int(run_id) < 1:
        raise QualificationReuseError("workflow run ID is malformed")
    if not run_attempt.isdecimal() or int(run_attempt) < 1:
        raise QualificationReuseError("workflow run attempt is malformed")
    return {
        "candidate_sha": candidate_sha,
        "release_version": version,
        "repository": repository,
        "inputs": {"require_blocking": True, "final_metadata": True},
        "source_run": {
            "id": int(run_id),
            "attempt": int(run_attempt),
            "event": "workflow_dispatch",
            "ref": ref,
            "branch": branch,
            "workflow_ref": workflow_ref,
            "workflow_sha": workflow_sha,
        },
    }


def _validate_create_context(context: dict) -> tuple[str, str, str, dict]:
    candidate_sha = _require_sha(context.get("candidate_sha"), CANDIDATE_SHA_LABEL)
    version = _require_version(context.get("release_version"))
    repository = _require_repository(context.get("repository"))
    if context.get("inputs") != {
        "require_blocking": True,
        "final_metadata": True,
    }:
        raise QualificationReuseError("source run did not enable required inputs")
    source = context.get("source_run")
    if not isinstance(source, dict):
        raise QualificationReuseError("source run context is missing")
    _validate_source_context(source, candidate_sha, repository)
    return candidate_sha, version, repository, source


def _validate_source_context(source: dict, candidate_sha: str,
                             repository: str) -> None:
    branch = source.get("branch")
    if not isinstance(branch, str) or not branch:
        raise QualificationReuseError("source branch is missing")
    if source.get("event") != "workflow_dispatch":
        raise QualificationReuseError("source run event is not workflow_dispatch")
    if source.get("workflow_sha") != candidate_sha:
        raise QualificationReuseError(
            f"workflow source does not match {CANDIDATE_SHA_LABEL}"
        )
    if source.get("ref") != f"refs/heads/{source.get('branch', '')}":
        raise QualificationReuseError("source branch and ref do not match")
    expected_ref = f"{repository}/{WORKFLOW_PATH}@{source['ref']}"
    if source.get("workflow_ref") != expected_ref:
        raise QualificationReuseError("source workflow ref is unexpected")
    for key in ("id", "attempt"):
        if not _positive_int(source.get(key)):
            raise QualificationReuseError(f"source run {key} is malformed")


def _load_qualification_records(source_dir: pathlib.Path,
                                candidate_sha: str,
                                *, trusted_root: pathlib.Path) -> dict:
    records = {
        FUZZ_RECORD: _read_json(
            source_dir / FUZZ_RECORD, FUZZ_RECORD_LABEL, root=trusted_root
        ),
        SOAK_MANIFEST: _read_json(
            source_dir / SOAK_MANIFEST, SOAK_MANIFEST_LABEL, root=trusted_root
        ),
        SOAK_RECORD: _read_json(
            source_dir / SOAK_RECORD, SOAK_RECORD_LABEL, root=trusted_root
        ),
    }
    _validate_qualification_records(records, candidate_sha)
    return records


def _validate_qualification_records(records: dict, candidate_sha: str) -> None:
    identities = (
        (FUZZ_RECORD, FUZZ_RECORD_LABEL),
        (SOAK_MANIFEST, SOAK_MANIFEST_LABEL),
        (SOAK_RECORD, SOAK_RECORD_LABEL),
    )
    for key, label in identities:
        if records[key].get("candidate_sha") != candidate_sha:
            raise QualificationReuseError(f"{label} {CANDIDATE_SHA_LABEL} is stale")
    if records[FUZZ_RECORD].get("blocking_pass") is not True:
        raise QualificationReuseError(FUZZ_BLOCKING_PASS_ERROR)
    if records[SOAK_RECORD].get("status") != "pass":
        raise QualificationReuseError(SOAK_PASS_ERROR)


def _copy_bundle_inputs(source_dir: pathlib.Path, bundle_dir: pathlib.Path,
                        *, trusted_root: pathlib.Path) -> None:
    source_dir = _read_path_within(
        source_dir, trusted_root, "release evidence directory"
    )
    bundle_dir = _write_path_within(
        bundle_dir, trusted_root, "qualification bundle"
    )
    bundle_dir.mkdir(parents=True)
    for name in (FUZZ_RECORD, SOAK_MANIFEST, SOAK_RECORD):
        _copy_regular_file(
            source_dir / name,
            bundle_dir / name,
            source_root=source_dir,
            destination_root=bundle_dir,
        )
    _copy_fuzz_logs(source_dir, bundle_dir, trusted_root=trusted_root)


def _copy_fuzz_logs(source_dir: pathlib.Path, bundle_dir: pathlib.Path,
                    *, trusted_root: pathlib.Path) -> None:
    source_dir = _read_path_within(
        source_dir, trusted_root, "release evidence directory"
    )
    bundle_dir = _write_path_within(
        bundle_dir, trusted_root, "qualification bundle"
    )
    fuzz_logs = source_dir / "fuzz-logs"
    if fuzz_logs.exists() or fuzz_logs.is_symlink():
        if fuzz_logs.is_symlink() or not fuzz_logs.is_dir():
            raise QualificationReuseError("fuzz logs must be a regular directory")
        safe_source = _read_path_within(fuzz_logs, source_dir, "fuzz logs")
        safe_destination = _write_path_within(
            bundle_dir / "fuzz-logs", bundle_dir, "fuzz logs"
        )
        shutil.copytree(safe_source, safe_destination, symlinks=True)
        _tree_files(bundle_dir)


def _write_attestation(bundle_dir: pathlib.Path, candidate_sha: str,
                       version: str, repository: str, source: dict) -> None:
    attestation = {
        "schema_version": SCHEMA_VERSION,
        "candidate_sha": candidate_sha,
        "release_version": version,
        "repository": repository,
        "workflow_path": WORKFLOW_PATH,
        "source_run": source,
        "inputs": {"require_blocking": True, "final_metadata": True},
        "checks": {"fuzz": "pass", "short_soak": "pass"},
        "files": _tree_files(bundle_dir),
    }
    attestation_path = _write_path_within(
        bundle_dir / ATTESTATION_NAME, bundle_dir, "qualification attestation"
    )
    attestation_path.write_text(
        json.dumps(attestation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def create_bundle(source_dir: pathlib.Path, bundle_dir: pathlib.Path,
                  context: dict, *, trusted_root: pathlib.Path) -> None:
    """Copy successful qualification records and write their attestation."""
    source_dir = _read_path_within(
        source_dir, trusted_root, "release evidence directory"
    )
    if not source_dir.is_dir():
        raise QualificationReuseError("release evidence directory is missing")
    bundle_dir = _write_path_within(
        bundle_dir, trusted_root, "qualification bundle"
    )
    if bundle_dir.exists() or bundle_dir.is_symlink():
        raise QualificationReuseError("qualification bundle output already exists")
    candidate_sha, version, repository, source = _validate_create_context(context)
    _load_qualification_records(
        source_dir, candidate_sha, trusted_root=trusted_root
    )
    _copy_bundle_inputs(source_dir, bundle_dir, trusted_root=trusted_root)
    _write_attestation(bundle_dir, candidate_sha, version, repository, source)


def _validate_source_run(run: dict, candidate_sha: str) -> None:
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise QualificationReuseError("source workflow run did not succeed")
    if run.get("event") != "workflow_dispatch":
        raise QualificationReuseError("source workflow run is not a dispatch")
    if run.get("head_sha") != candidate_sha:
        raise QualificationReuseError("source workflow run SHA does not match")
    path = run.get("path")
    if not isinstance(path, str) or path.split("@", 1)[0] != WORKFLOW_PATH:
        raise QualificationReuseError("source workflow path does not match")
    if not isinstance(run.get("head_branch"), str) or not run["head_branch"]:
        raise QualificationReuseError("source workflow branch is missing")
    if not _positive_int(run.get("id")) or not _positive_int(run.get("run_attempt")):
        raise QualificationReuseError("source workflow run identity is malformed")


def _validate_bundle_files(bundle_dir: pathlib.Path, expected_sha: str,
                           expected_version: str,
                           *, trusted_root: pathlib.Path) -> dict:
    bundle_dir = _read_path_within(
        bundle_dir, trusted_root, "qualification bundle"
    )
    if not bundle_dir.is_dir():
        raise QualificationReuseError("qualification bundle directory is missing")
    _regular_file(bundle_dir / ATTESTATION_NAME, "qualification attestation")
    attestation = _read_json(
        bundle_dir / ATTESTATION_NAME,
        "qualification attestation",
        root=trusted_root,
    )
    if attestation.get("schema_version") != SCHEMA_VERSION:
        raise QualificationReuseError("qualification attestation schema is unsupported")
    if attestation.get("candidate_sha") != expected_sha:
        raise QualificationReuseError("qualification attestation SHA does not match")
    if attestation.get("release_version") != expected_version:
        raise QualificationReuseError("qualification attestation version does not match")
    if attestation.get("workflow_path") != WORKFLOW_PATH:
        raise QualificationReuseError("qualification attestation workflow is unexpected")
    if attestation.get("inputs") != {
        "require_blocking": True,
        "final_metadata": True,
    }:
        raise QualificationReuseError("source run did not enable required inputs")
    if attestation.get("checks") != {"fuzz": "pass", "short_soak": "pass"}:
        raise QualificationReuseError("qualification checks are not both passing")
    expected_files = attestation.get("files")
    actual_files = _tree_files(bundle_dir)
    if not isinstance(expected_files, dict) or expected_files != actual_files:
        raise QualificationReuseError("qualification bundle file digest mismatch")
    required = {FUZZ_RECORD, SOAK_MANIFEST, SOAK_RECORD}
    if not required.issubset(actual_files):
        raise QualificationReuseError("qualification bundle is missing a record")
    _load_qualification_records(
        bundle_dir, expected_sha, trusted_root=trusted_root
    )
    return attestation


def verify_bundle(bundle_dir: pathlib.Path, candidate_sha: str,
                  release_version: str, repository: str, source_run: dict,
                  *, trusted_root: pathlib.Path) -> None:
    """Validate the artifact attestation against GitHub's run metadata."""
    candidate_sha = _require_sha(candidate_sha, CANDIDATE_SHA_LABEL)
    release_version = _require_version(release_version)
    repository = _require_repository(repository)
    _validate_source_run(source_run, candidate_sha)
    attestation = _validate_bundle_files(
        bundle_dir, candidate_sha, release_version, trusted_root=trusted_root
    )
    source = attestation.get("source_run")
    if not isinstance(source, dict):
        raise QualificationReuseError("attested source run is missing")
    branch = source_run["head_branch"]
    expected_ref = f"refs/heads/{branch}"
    expected_workflow_ref = f"{repository}/{WORKFLOW_PATH}@{expected_ref}"
    checks = {
        "id": source_run["id"],
        "attempt": source_run["run_attempt"],
        "event": "workflow_dispatch",
        "ref": expected_ref,
        "branch": branch,
        "workflow_ref": expected_workflow_ref,
        "workflow_sha": candidate_sha,
    }
    if attestation.get("repository") != repository or source != checks:
        raise QualificationReuseError("attestation does not match the source run")


def _api_request(url: str, token: str) -> tuple[dict, str]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read())
        link_header = response.headers.get("Link", "")
    if not isinstance(payload, dict):
        raise QualificationReuseError("GitHub API returned an unexpected response")
    return payload, link_header


def _next_url(link_header: str, api_host: str) -> str | None:
    for part in link_header.split(","):
        match = re.search(r"<([^>]+)>\s*;\s*rel=\"next\"", part)
        if not match:
            continue
        candidate = match.group(1)
        parsed = urllib.parse.urlparse(candidate)
        if parsed.scheme != "https" or parsed.netloc != api_host:
            raise QualificationReuseError("GitHub API pagination link is untrusted")
        return candidate
    return None


def _api_list(url: str, token: str, key: str, api_host: str) -> list:
    items: list = []
    page_url: str | None = url
    page_count = 0
    while page_url:
        page_count += 1
        if page_count > 20:
            raise QualificationReuseError("GitHub API pagination limit exceeded")
        payload, link_header = _api_request(page_url, token)
        page_items = payload.get(key)
        if not isinstance(page_items, list):
            raise QualificationReuseError("GitHub API list response is malformed")
        items.extend(page_items)
        page_url = _next_url(link_header, api_host)
    return items


def _run_matches(run: dict, candidate_sha: str) -> bool:
    path = run.get("path")
    return (
        run.get("head_sha") == candidate_sha
        and run.get("event") == "workflow_dispatch"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and isinstance(run.get("head_branch"), str)
        and bool(run.get("head_branch"))
        and isinstance(path, str)
        and path.split("@", 1)[0] == WORKFLOW_PATH
        and _positive_int(run.get("id"))
        and _positive_int(run.get("run_attempt"))
    )


def _artifact_name(candidate_sha: str, run: dict) -> str:
    return f"release-qualification-{candidate_sha}-{run['run_attempt']}"


def _artifact_matches_run(artifact: object, run: dict, candidate_sha: str,
                          artifact_name: str) -> bool:
    if not isinstance(artifact, dict):
        return False
    if artifact.get("name") != artifact_name or artifact.get("expired") is not False:
        return False
    workflow_run = artifact.get("workflow_run")
    if not isinstance(workflow_run, dict):
        return True
    return (
        workflow_run.get("id") == run["id"]
        and workflow_run.get("head_sha") == candidate_sha
    )


def _run_has_live_artifact(api_root: str, repository: str, run: dict,
                           candidate_sha: str, token: str,
                           api_host: str) -> bool:
    url = (
        f"{api_root}/repos/{repository}/actions/runs/{run['id']}/artifacts"
        "?per_page=100"
    )
    artifacts = _api_list(url, token, "artifacts", api_host)
    artifact_name = _artifact_name(candidate_sha, run)
    return any(
        _artifact_matches_run(item, run, candidate_sha, artifact_name)
        for item in artifacts
    )


def find_reusable_run(repository: str, candidate_sha: str, api_url: str,
                      token: str) -> tuple[dict, str] | None:
    """Return the newest successful exact-SHA dispatch with a live bundle."""
    repository = _require_repository(repository)
    candidate_sha = _require_sha(candidate_sha, CANDIDATE_SHA_LABEL)
    parsed_api = urllib.parse.urlparse(api_url)
    if parsed_api.scheme != "https" or not parsed_api.netloc:
        raise QualificationReuseError("GitHub API URL must use HTTPS")
    api_root = api_url.rstrip("/")
    query = urllib.parse.urlencode({
        "event": "workflow_dispatch",
        "head_sha": candidate_sha,
        "status": "completed",
        "per_page": "100",
    })
    run_url = (
        f"{api_root}/repos/{repository}/actions/workflows/"
        f"release-packages.yml/runs?{query}"
    )
    runs = _api_list(run_url, token, "workflow_runs", parsed_api.netloc)
    runs = [run for run in runs if isinstance(run, dict)
            and _run_matches(run, candidate_sha)]
    runs.sort(key=lambda item: item.get("run_started_at", ""), reverse=True)
    for run in runs:
        artifact_name = _artifact_name(candidate_sha, run)
        if _run_has_live_artifact(
            api_root, repository, run, candidate_sha, token, parsed_api.netloc
        ):
            return run, artifact_name
    return None


def _write_outputs(values: dict[str, object]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{key}={value}" for key, value in values.items()]
    if output_path:
        safe_output = validate_read_path(
            _write_path_within(
                output_path, _environment_root("RUNNER_TEMP"), "GitHub output"
            ),
            must_exist=False,
            purpose="GitHub output",
        )
        with safe_output.open("a", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


def _discover_from_env() -> int:
    if (os.environ.get("GITHUB_EVENT_NAME") != "push"
            or os.environ.get("GITHUB_REF_TYPE") != "tag"):
        _write_outputs({"found": "false"})
        return 0
    outputs: dict[str, object] = {"found": "false"}
    try:
        result = find_reusable_run(
            os.environ.get("GITHUB_REPOSITORY", ""),
            os.environ.get("GITHUB_SHA", ""),
            os.environ.get("GITHUB_API_URL", ""),
            os.environ.get("GH_TOKEN", ""),
        )
        if result is None:
            print("No successful same-SHA qualification bundle; running full checks.")
        else:
            run, artifact_name = result
            metadata_value = os.environ.get("RELEASE_QUALIFICATION_RUN_METADATA", "")
            if not metadata_value:
                raise QualificationReuseError("source run metadata path is missing")
            metadata_path = _write_path_within(
                pathlib.Path(metadata_value), _environment_root("RUNNER_TEMP"),
                "source run metadata",
            )
            metadata_path.parent.mkdir(parents=True, exist_ok=True)
            metadata_path.write_text(
                json.dumps(run, indent=2) + "\n", encoding="utf-8"
            )
            outputs = {
                "found": "true",
                "run_id": run["id"],
                "artifact_name": artifact_name,
            }
    except Exception as exc:  # API, permission, or malformed-data failure is a cache miss.
        print(f"Reusable qualification lookup failed; running full checks: {exc}")
    _write_outputs(outputs)
    return 0


def _create_from_env(args: argparse.Namespace) -> int:
    workspace = _environment_root("GITHUB_WORKSPACE")
    source_dir = _read_path_within(
        args.source_dir, workspace, "release evidence directory"
    )
    bundle_dir = _write_path_within(
        args.bundle_dir, workspace, "qualification bundle"
    )
    create_bundle(
        source_dir,
        bundle_dir,
        _source_context_from_env(),
        trusted_root=workspace,
    )
    print(f"PASS: created qualification bundle at {args.bundle_dir}")
    return 0


def _verify_from_args(args: argparse.Namespace) -> int:
    runner_temp = _environment_root("RUNNER_TEMP")
    bundle_dir = _read_path_within(
        args.bundle_dir, runner_temp, "qualification bundle"
    )
    source_run_path = _read_path_within(
        args.source_run, runner_temp, "GitHub source run metadata"
    )
    source_run = _read_json(
        source_run_path, "GitHub source run metadata", root=runner_temp
    )
    verify_bundle(
        bundle_dir,
        args.candidate_sha,
        args.release_version,
        args.repository,
        source_run,
        trusted_root=runner_temp,
    )
    print("PASS: reusable qualification bundle matches the successful source run")
    return 0


def _restore_names(kind: str) -> tuple[tuple[str, bool], ...]:
    names = {
        "fuzz": ((FUZZ_RECORD, False), ("fuzz-logs", True)),
        "soak": ((SOAK_MANIFEST, False), (SOAK_RECORD, False)),
    }
    if kind not in names:
        raise QualificationReuseError("unknown qualification evidence kind")
    return names[kind]


def _restore_one(bundle_dir: pathlib.Path, output_root: pathlib.Path,
                 name: str, optional: bool) -> None:
    source = bundle_dir / name
    if not source.exists() and not source.is_symlink() and optional:
        return
    if source.is_symlink() or not source.exists():
        raise QualificationReuseError(f"qualification bundle is missing {name}")
    destination = output_root / name
    if destination.exists() or destination.is_symlink():
        raise QualificationReuseError(f"refusing to overwrite {destination}")
    if name == "fuzz-logs":
        if not source.is_dir():
            raise QualificationReuseError("fuzz logs must be a regular directory")
        safe_source = _read_path_within(source, bundle_dir, "fuzz logs")
        safe_destination = _write_path_within(
            destination, output_root, "fuzz logs"
        )
        shutil.copytree(safe_source, safe_destination)
        _tree_files(output_root)
        return
    _copy_regular_file(
        source,
        destination,
        source_root=bundle_dir,
        destination_root=output_root,
    )


def _remove_restore_target(path: pathlib.Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _restore_transaction(bundle_dir: pathlib.Path, output_root: pathlib.Path,
                         entries: tuple[tuple[str, bool], ...]) -> None:
    targets = [output_root / name for name, _ in entries]
    if any(path.exists() or path.is_symlink() for path in targets):
        raise QualificationReuseError("refusing to overwrite release evidence")
    try:
        for name, optional in entries:
            _restore_one(bundle_dir, output_root, name, optional)
    except (OSError, QualificationReuseError) as restore_error:
        cleanup_error = None
        for path in targets:
            try:
                _remove_restore_target(path)
            except OSError as error:
                cleanup_error = cleanup_error or error
        if cleanup_error:
            raise QualificationReuseError(
                "failed to clean up partial qualification restore"
            ) from restore_error
        raise


def _restore_from_args(args: argparse.Namespace, kind: str) -> int:
    version = _require_version(args.release_version)
    runner_temp = _environment_root("RUNNER_TEMP")
    workspace = _environment_root("GITHUB_WORKSPACE")
    bundle_dir = _read_path_within(
        args.bundle_dir, runner_temp, "qualification bundle"
    )
    _validate_bundle_files(
        bundle_dir, args.candidate_sha, version, trusted_root=runner_temp
    )
    output_root = _write_path_within(
        args.output_root, workspace, "release evidence root"
    ) / version
    if output_root.is_symlink() or not output_root.is_dir():
        raise QualificationReuseError("release evidence output directory is missing")
    _restore_transaction(bundle_dir, output_root, _restore_names(kind))
    print(f"PASS: restored reusable {kind} qualification evidence")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("discover", help="find a completed same-SHA dispatch run")
    create = subparsers.add_parser("create", help="write a preflight evidence bundle")
    create.add_argument("--source-dir", type=pathlib.Path, required=True)
    create.add_argument("--bundle-dir", type=pathlib.Path, required=True)
    verify = subparsers.add_parser("verify", help="verify a downloaded bundle")
    verify.add_argument("--bundle-dir", type=pathlib.Path, required=True)
    verify.add_argument("--source-run", type=pathlib.Path, required=True)
    verify.add_argument("--candidate-sha", required=True)
    verify.add_argument("--release-version", required=True)
    verify.add_argument("--repository", required=True)
    for kind in ("fuzz", "soak"):
        restore = subparsers.add_parser(f"restore-{kind}")
        restore.add_argument("--bundle-dir", type=pathlib.Path, required=True)
        restore.add_argument("--candidate-sha", required=True)
        restore.add_argument("--release-version", required=True)
        restore.add_argument("--output-root", type=pathlib.Path, default=pathlib.Path("artifacts/release"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "discover":
            return _discover_from_env()
        if args.command == "create":
            return _create_from_env(args)
        if args.command == "verify":
            return _verify_from_args(args)
        return _restore_from_args(args, args.command.removeprefix("restore-"))
    except (OSError, QualificationReuseError, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
