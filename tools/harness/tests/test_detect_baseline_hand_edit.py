"""Pytest tests for detect_baseline_hand_edit.py (Rule 61 lifecycle).

Adversarial fixtures reproduce the churn shapes from the 2026-08-20/21
cluster: hand-edited finalized baselines (d7012e42/cf6aea8e revert pair,
8c899644 derived-field patch) and provenance drift.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import detect_baseline_hand_edit as module

# Captured at import time, before the autouse fixture below can install the
# always-true stub.  The sandbox tests restore this so their verdict comes
# from real git behaviour; a stub that always answers True can never reach
# the "not in history" branch that CI actually fails on.
_REAL_REPO_COMMIT_EXISTS = module.repo_commit_exists

# Same reasoning for the ref-anchor judgment: the sandbox tests below run it
# against real refs, while the schema-focused tests default to "anchored" so
# they keep auditing schema shapes rather than the host checkout's topology.
_REAL_REPO_COMMIT_ANCHORED = module.repo_commit_anchored

# Also captured at import time: make_baseline() reassigns module.REPO_ROOT
# without restoring it, so by the time a later test runs, REPO_ROOT may point
# at a spent tmp_path.  Tests that audit real repository artifacts bind this
# back explicitly instead of trusting whatever the previous test left behind.
_REAL_REPO_ROOT = module.REPO_ROOT

# Rule 61 clause 9 requires a *full* SHA-1 on archival imports.
_FULL_SHA_LEN = 40


@pytest.fixture(autouse=True)
def stub_repo_commit_exists(monkeypatch):
    """Default every test to "the measurement commit is present and anchored".

    Behaviourally identical to the module-level `setup_function` this
    replaces — the pre-existing tests still run against an always-true
    provenance presence check — but monkeypatch undoes it when the test
    ends.  The old assignment mutated the imported module permanently and
    leaked into every later test in the same pytest session, including
    tests in other files that import the same detector module.

    The anchor judgment is stubbed alongside it for the same reason the
    presence check is: these tests point REPO_ROOT at a bare tmp_path that
    is not a git repository at all, so a real anchor lookup there measures
    the fixture directory rather than the schema under test.  Anchor
    behaviour itself is covered by the sandbox tests that opt out through
    `_bind_real_git()`, and live by the full audit over perf/baselines.

    Ordering: this fixture completes during setup, so a `_bind_real_git()`
    call inside a test body always lands after it and wins.  Both patches
    share the one function-scoped `monkeypatch` instance, so its undo stack
    unwinds in reverse and the real implementation is restored either way.
    """
    monkeypatch.setattr(module, "repo_commit_exists", lambda sha: True)
    monkeypatch.setattr(module, "repo_commit_anchored",
                        lambda sha, stem: True)


def make_baseline(tmp_path, policy_overrides=None, benchmark_commit=None,
                  baseline_type="verbatim_run"):
    module.REPO_ROOT = tmp_path
    base_dir = tmp_path / "perf" / "baselines"
    base_dir.mkdir(parents=True, exist_ok=True)
    raw = base_dir / "module-baseline-t-raw.json"
    raw.write_text("{\"scenarios\": []}", encoding="utf-8")
    policy = {
        "type": baseline_type,
        "source_git_commit": "a" * 40,
        "source_run": "https://github.com/o/r/actions/runs/1/attempts/1",
        "source_artifact": "perf/baselines/module-baseline-t-raw.json",
        "source_artifact_sha256": module.hashlib.sha256(
            raw.read_bytes()).hexdigest(),
        "measurement_timestamp": "2026-08-21T00:00:00Z",
        "normalization": "none",
    }
    policy.update(policy_overrides or {})
    doc = {
        "module_benchmark": {
            "git_commit": benchmark_commit or policy["source_git_commit"],
            "timestamp": "2026-08-21T00:00:00Z",
        },
        "baseline_policy": policy,
    }
    baseline = base_dir / "module-baseline-t.json"
    import json
    baseline.write_text(json.dumps(doc), encoding="utf-8")
    return baseline


def test_clean_baseline_passes(tmp_path):
    findings = []
    module.audit_baseline(make_baseline(tmp_path), findings)
    assert findings == []


def test_missing_provenance_field_fails_closed(tmp_path):
    findings = []
    module.audit_baseline(
        make_baseline(tmp_path, {"source_run": ""}), findings)
    assert any("source_run" in f for f in findings)


def test_abbreviated_commit_rejected(tmp_path):
    findings = []
    module.audit_baseline(
        make_baseline(tmp_path, {"source_git_commit": "a" * 12}), findings)
    assert any("40-hex" in f for f in findings)


def test_benchmark_policy_commit_mismatch_rejected(tmp_path):
    findings = []
    module.audit_baseline(
        make_baseline(tmp_path, benchmark_commit="b" * 40), findings)
    assert any("does not match" in f for f in findings)


def test_non_utc_timestamp_rejected(tmp_path):
    findings = []
    module.audit_baseline(
        make_baseline(
            tmp_path,
            {"measurement_timestamp": "2026-08-21T00:00:00"},
        ),
        findings,
    )
    assert any("ISO-8601" in f for f in findings)


def test_digest_mismatch_rejected(tmp_path):
    findings = []
    module.audit_baseline(
        make_baseline(tmp_path, {"source_artifact_sha256": "c" * 64}),
        findings,
    )
    assert any("mismatch" in f for f in findings)


def test_absolute_artifact_path_rejected(tmp_path):
    findings = []
    module.audit_baseline(
        make_baseline(tmp_path, {"source_artifact": "/etc/passwd"}),
        findings,
    )
    assert any("repo-relative" in f for f in findings)


def test_archival_import_profile_skips_finalizer_fields(tmp_path):
    baseline = make_baseline(
        tmp_path,
        {
            "type": "verbatim_import",
            "source_run": "",
            "measurement_timestamp": "",
            "normalization": "none; metrics copied from retained report",
        },
        benchmark_commit="a" * 12,
    )
    findings = []
    module.audit_baseline(baseline, findings)
    assert findings == []


def test_changed_mode_flags_finalized_without_raw():
    findings = []
    module.check_changed(
        ["perf/baselines/module-baseline-092.json"], findings)
    assert len(findings) == 1
    assert "hand edits are forbidden" in findings[0]


def test_changed_mode_accepts_finalized_with_raw():
    findings = []
    module.check_changed(
        [
            "perf/baselines/module-baseline-092.json",
            "perf/baselines/module-baseline-092-raw.json",
            "perf/baselines/module-baseline-092-raw-probes/plain-small.json",
        ],
        findings,
    )
    assert findings == []


def test_changed_mode_ignores_unrelated_paths():
    findings = []
    module.check_changed(["docs/README.md", "Makefile"], findings)
    assert findings == []


def test_repo_commit_anchored_is_indeterminate_without_git(
    real_git_sandbox, monkeypatch
):
    """An unusable git cannot prove anchoring; the predicate says unknown."""
    origin, _tip_sha = real_git_sandbox
    _bind_real_git(monkeypatch, origin)
    monkeypatch.setattr(module, "_run_git", lambda args: None)
    assert module.repo_commit_anchored(
        "0" * 40, "baseline-stem"
    ) is None


def test_run_git_returns_none_when_git_unavailable(monkeypatch, tmp_path):
    """No candidate and no PATH hit leave the helper without a binary."""
    monkeypatch.setattr(module, "_GIT_BIN", None)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    module_result = module._run_git(["rev-parse", "HEAD"])
    assert module_result is None


def test_symlink_escaping_repo_root_rejected(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    # Target lives OUTSIDE the fake repository root.
    outside = tmp_path.parent / "h-baseline-escape-target.json"
    outside.write_text("{\"leaked\": true}", encoding="utf-8")
    base_dir = tmp_path / "perf" / "baselines"
    base_dir.mkdir(parents=True, exist_ok=True)
    link = base_dir / "module-baseline-t-raw.json"
    os.symlink(outside, link)
    policy = {
        "type": "verbatim_run",
        "source_git_commit": "a" * 40,
        "source_run": "https://github.com/o/r/actions/runs/1/attempts/1",
        "source_artifact": "perf/baselines/module-baseline-t-raw.json",
        "source_artifact_sha256": module.hashlib.sha256(
            outside.read_bytes()).hexdigest(),
        "measurement_timestamp": "2026-08-21T00:00:00Z",
        "normalization": "none",
    }
    doc = {
        "module_benchmark": {
            "git_commit": "a" * 40,
            "timestamp": "2026-08-21T00:00:00Z",
        },
        "baseline_policy": policy,
    }
    import json
    baseline = base_dir / "module-baseline-t.json"
    baseline.write_text(json.dumps(doc), encoding="utf-8")
    findings = []
    module.audit_baseline(baseline, findings)
    assert any("outside the repository" in f for f in findings)


# ── probe-directory acceptance path (advertised but previously unimplemented) ──

def test_changed_mode_accepts_probe_directory_regeneration():
    # A regeneration that touches only files under the `-raw-probes/`
    # directory (named e.g. plain-small.json) must satisfy the raw-input
    # companion requirement.  The old `-raw`-filename-only match rejected
    # this with a misleading "hand edits are forbidden" message.
    findings = []
    module.check_changed(
        [
            "perf/baselines/module-baseline-092.json",
            "perf/baselines/module-baseline-092-raw-probes/plain-small.json",
        ],
        findings,
    )
    assert findings == []


def test_shallow_clone_missing_commit_is_explicit_skip(capsys):
    # In a shallow clone a missing measurement commit is indeterminate:
    # the detector must print an explicit SKIP line AND record a finding
    # so the gate exits non-clean — a shallow checkout must never accept
    # provenance a full clone would reject (verdicts agree: both reject
    # unverifiable metadata).
    import detect_baseline_hand_edit as m
    # Directly verify the SKIP + finding contract:
    findings = []
    doc = {"module_benchmark": {"git_commit": "0" * 40}, "baseline_policy": {
        "source_git_commit": "0" * 40,
    }}
    # Force the indeterminate branch regardless of local clone state.
    original = m.repo_commit_exists
    m.repo_commit_exists = lambda sha: None
    try:
        m._check_commit_and_timestamp("module-baseline-092.json", doc,
                                      doc["baseline_policy"], findings)
    finally:
        m.repo_commit_exists = original
    assert len(findings) == 1, "indeterminate provenance must fail closed"
    assert "unverifiable in this shallow clone" in findings[0]
    captured = capsys.readouterr().err
    assert "SKIP module-baseline-092.json" in captured


# ─────────── Finding A: clone-topology / ref-topology exploration ───────────
# The input domain is a finite set of clone-topology x ref-topology
# combinations, so these cases enumerate the topologies exhaustively instead
# of generating them.  Each case builds its own sandbox repository: the host
# checkout carries measurement objects pulled in by an earlier
# `git fetch --depth=1 origin <sha>` and therefore cannot decide any of
# these verdicts.

_GIT_IDENTITY = (
    "-c",
    "user.email=harness@example.invalid",
    "-c",
    "user.name=Harness Fixture",
)


def _git(repo, *args):
    """Run one git command inside `repo`, returning stripped stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"git {' '.join(args)} failed in {repo} "
        f"(exit {result.returncode}): {result.stdout}{result.stderr}"
    )
    return result.stdout.strip()


def _commit(repo, body, message):
    """Create one commit in `repo` and return its full SHA."""
    (repo / "measurement.txt").write_text(body, encoding="utf-8")
    _git(repo, "add", "measurement.txt")
    _git(repo, *_GIT_IDENTITY, "commit", "--quiet", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def real_git_sandbox(tmp_path):
    """Return (origin_repo_path, tip_sha) for a real, non-shallow repository.

    Two commits, so a `--depth=1` clone of this origin is guaranteed to be
    missing the parent object — the topology E-A2 needs.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "--quiet")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    parent_sha = _commit(origin, "first measurement\n", "first measurement")
    tip_sha = _commit(origin, "second measurement\n", "second measurement")
    assert parent_sha != tip_sha
    return origin, tip_sha


def _write_sandbox_baseline(repo, sha, stem="module-baseline-092",
                            baseline_type="verbatim_run",
                            benchmark_commit=None):
    """Write a finalized baseline under `repo` bound to measurement `sha`.

    `benchmark_commit` defaults to `sha` so the document agrees with itself;
    pass a different commit to reproduce the provenance-drift shape where the
    benchmark ran at one commit and the finalized policy records another.
    """
    base_dir = repo / "perf" / "baselines"
    base_dir.mkdir(parents=True, exist_ok=True)
    raw = base_dir / f"{stem}-raw.json"
    raw.write_text("{\"scenarios\": []}", encoding="utf-8")
    policy = {
        "type": baseline_type,
        "source_git_commit": sha,
        "source_run": "https://github.com/o/r/actions/runs/1/attempts/1",
        "source_artifact": f"perf/baselines/{stem}-raw.json",
        "source_artifact_sha256": module.hashlib.sha256(
            raw.read_bytes()).hexdigest(),
        "measurement_timestamp": "2026-08-21T00:00:00Z",
        "normalization": "none",
    }
    doc = {
        "module_benchmark": {
            "git_commit": benchmark_commit or sha,
            "timestamp": "2026-08-21T00:00:00Z",
        },
        "baseline_policy": policy,
    }
    baseline = base_dir / f"{stem}.json"
    baseline.write_text(module.json.dumps(doc), encoding="utf-8")
    return baseline


def _bind_real_git(monkeypatch, repo):
    """Aim the detector at `repo` and undo the always-true stubs."""
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    monkeypatch.setattr(module, "repo_commit_exists",
                        _REAL_REPO_COMMIT_EXISTS)
    monkeypatch.setattr(module, "repo_commit_anchored",
                        _REAL_REPO_COMMIT_ANCHORED)


def test_ea1_full_checkout_rejects_commit_missing_from_history(
    real_git_sandbox, monkeypatch
):
    """E-A1: full clone + unknown source_git_commit must FAIL.

    The pre-existing suite cannot express this scenario: the default
    `stub_repo_commit_exists` fixture answers `True` for every SHA, so the
    False ("not in history") branch — the exact branch the CI provenance
    gate fails on — has zero coverage until a test opts out of the stub.
    """
    origin, _tip = real_git_sandbox
    _bind_real_git(monkeypatch, origin)
    assert module.repo_is_shallow() is False, "sandbox origin must be full"
    absent_sha = "dead" * 10
    assert module.repo_commit_exists(absent_sha) is False

    baseline = _write_sandbox_baseline(origin, absent_sha)
    findings = []
    module.audit_baseline(baseline, findings)
    assert len(findings) == 1, findings
    assert "not in history" in findings[0], findings[0]


def test_ea2_shallow_clone_fails_closed_then_passes_after_preparation(
    real_git_sandbox, monkeypatch, tmp_path, capsys
):
    """E-A2: shallow clone SKIPs and fails closed; preparation makes it PASS.

    A `--depth=1` clone cannot see the parent measurement object, so the
    verdict is indeterminate and must be recorded as a finding.  Provenance
    preparation is what clears it, and it must land on the same verdict a
    full clone reaches.

    Preparation has two parts, and the middle state below shows why both
    are required.  Fetching the object by explicit SHA satisfies presence
    only: the fetched commit lands as a second shallow root, and the
    graft that severs `origin/main`'s parent link means no ref in that
    clone reaches it — so the anchor judgment is still undecidable and
    still fails closed.  Fetching the durable measurement tag as well
    (the `+refs/tags/perf-baseline/*` refspec the CI preparation step
    uses) is what makes the verdict PASS, and it passes because an
    immutable ref really does anchor the commit.  Depth never grants a
    pass by itself.
    """
    origin, _tip = real_git_sandbox
    parent_sha = _git(origin, "rev-parse", "HEAD~1")
    clone = tmp_path / "shallow-clone"
    _git(tmp_path, "clone", "--quiet", "--depth=1", f"file://{origin}",
         str(clone))
    _bind_real_git(monkeypatch, clone)
    assert module.repo_is_shallow() is True, "clone must be shallow"
    assert module.repo_commit_exists(parent_sha) is None

    baseline = _write_sandbox_baseline(clone, parent_sha)
    findings = []
    module.audit_baseline(baseline, findings)
    assert len(findings) == 1, findings
    assert "unverifiable in this shallow clone" in findings[0], findings[0]
    captured = capsys.readouterr().err
    assert "SKIP perf/baselines/module-baseline-092.json" in captured, captured
    assert "not present in this shallow clone" in captured, captured

    _git(clone, "fetch", "--no-tags", "--depth=1", "origin", parent_sha)
    assert module.repo_commit_exists(parent_sha) is True
    object_only_findings = []
    module.audit_baseline(baseline, object_only_findings)
    assert len(object_only_findings) == 1, object_only_findings
    assert "anchor unverifiable" in object_only_findings[0], \
        object_only_findings[0]
    captured = capsys.readouterr().err
    assert "anchor not decidable" in captured, captured

    _git(origin, *_GIT_IDENTITY, "tag", "-a", "-m",
         "durable measurement anchor",
         "perf-baseline/module-baseline-092", parent_sha)
    _git(clone, "fetch", "--no-tags", "origin",
         "+refs/tags/perf-baseline/*:refs/tags/perf-baseline/*")
    assert module.repo_commit_anchored(
        parent_sha, "module-baseline-092") is True
    prepared_findings = []
    module.audit_baseline(baseline, prepared_findings)
    assert prepared_findings == [], prepared_findings


def test_ea3_orphan_object_present_but_unreferenced_is_rejected(
    real_git_sandbox, monkeypatch
):
    """E-A3: object present, referenced by no ref — must FAIL.

    This is the structure behind the CI failure.  `git cat-file -e` is a
    presence check, so on its own the detector cannot separate "reachable
    from a ref" from "the object merely happens to still be here".  The
    unfixed detector accepted this shape; the anchor check turns it into a
    `perf-baseline/<stem>` finding, which is what makes exit 0 mean
    "present AND anchored" rather than "present".

    The sandbox carries a tag so the anchor judgment is decidable here: a
    checkout with no tags at all cannot tell "unanchored" from "the refs
    were never fetched", and answers indeterminate by contract.
    """
    origin, tip_sha = real_git_sandbox
    _git(origin, "tag", "sandbox-marker-0.0.1")
    _git(origin, "checkout", "--quiet", "--detach")
    orphan_sha = _commit(origin, "orphan measurement\n",
                         "orphan measurement")
    _git(origin, "checkout", "--quiet", "main")
    assert _git(origin, "rev-parse", "HEAD") == tip_sha
    containing = _git(origin, "for-each-ref", "--contains", orphan_sha,
                      "--format=%(refname)")
    assert containing == "", f"orphan must be unreferenced, got {containing!r}"

    _bind_real_git(monkeypatch, origin)
    assert module.repo_commit_exists(orphan_sha) is True, "object present"
    assert module.repo_commit_anchored(
        orphan_sha, "module-baseline-092") is False, "present but unanchored"

    baseline = _write_sandbox_baseline(origin, orphan_sha)
    findings = []
    module.audit_baseline(baseline, findings)
    assert len(findings) == 1, findings
    assert "is not anchored by any ref" in findings[0], findings[0]
    assert "perf-baseline/module-baseline-092" in findings[0], findings[0]


# ───────────── Finding A: preservation of the existing contract ─────────────
# Assertions below were written from observed behaviour of the unfixed
# detector, not from the intended behaviour of the fix.  Each one locks a
# clause the remediation must not disturb.


def _real_archival_baseline():
    """Path to the checked-in `verbatim_import` archival pack."""
    return _REAL_REPO_ROOT / "perf" / "baselines" / \
        "module-baseline-brotli-091.json"


def test_verbatim_import_keeps_imported_provenance_fields(tmp_path):
    """Rule 61 clause 9: archival imports are not retro-fitted.

    Observed on the unfixed detector: a `verbatim_import` pack whose
    `source_run`, `measurement_timestamp` and `normalization` are blank —
    and whose `source_artifact_sha256` is blank — audits clean, because
    `_check_policy_fields` returns before the required-field sweep for this
    type.  The same shapes on a `verbatim_run` pack are rejected, so the
    exemption is type-scoped rather than a hole in the sweep.
    """
    archival = make_baseline(
        tmp_path,
        {
            "source_run": "",
            "measurement_timestamp": "",
            "normalization": "",
            "source_artifact_sha256": "",
        },
        baseline_type="verbatim_import",
    )
    findings = []
    module.audit_baseline(archival, findings)
    assert findings == [], findings

    # Negative control for the same shapes: the exemption must stay scoped
    # to verbatim_import.  If the type check were dropped, this half passes
    # too and the test fails.
    finalized = make_baseline(
        tmp_path,
        {
            "source_run": "",
            "measurement_timestamp": "",
            "normalization": "",
            "source_artifact_sha256": "",
        },
        baseline_type="verbatim_run",
    )
    finalized_findings = []
    module.audit_baseline(finalized, finalized_findings)
    assert any("source_run" in f for f in finalized_findings), \
        finalized_findings
    assert any("normalization" in f for f in finalized_findings), \
        finalized_findings


def test_verbatim_import_still_requires_full_sha_and_retained_artifact(
    tmp_path
):
    """Rule 61 clause 9: the two requirements that DO survive the exemption.

    Observed on the unfixed detector: an abbreviated `source_git_commit`
    yields "must be a full 40-hex SHA", and a `source_artifact` whose file
    is absent yields "retained raw artifact missing" — both for a
    `verbatim_import` pack.  Neither check is type-exempt.
    """
    abbreviated = make_baseline(
        tmp_path,
        {"source_git_commit": "a" * 12},
        baseline_type="verbatim_import",
    )
    findings = []
    module.audit_baseline(abbreviated, findings)
    assert any("40-hex" in f for f in findings), findings

    # Same pack, valid SHA, but the retained artifact is removed.
    intact = make_baseline(tmp_path, baseline_type="verbatim_import")
    (tmp_path / "perf" / "baselines" /
     "module-baseline-t-raw.json").unlink()
    missing_findings = []
    module.audit_baseline(intact, missing_findings)
    assert any("retained raw artifact missing" in f
               for f in missing_findings), missing_findings


def test_checked_in_archival_pack_audits_clean_without_retrofit(monkeypatch):
    """The real archival pack keeps its imported schema and audits clean.

    Observed: `perf/baselines/module-baseline-brotli-091.json` carries
    `type: verbatim_import`, a full 40-hex `source_git_commit`, an existing
    retained artifact, and NO `source_artifact_sha256` /
    `measurement_timestamp` at all.  Retro-fitting the finalizer schema
    onto it — or making the exemption conditional on those fields being
    present — would break this assertion.
    """
    monkeypatch.setattr(module, "REPO_ROOT", _REAL_REPO_ROOT)
    baseline = _real_archival_baseline()
    doc = module.json.loads(baseline.read_text(encoding="utf-8"))
    policy = doc["baseline_policy"]

    assert policy["type"] == "verbatim_import"
    assert len(policy["source_git_commit"]) == _FULL_SHA_LEN, policy
    assert module.FULL_SHA_RE.match(policy["source_git_commit"]), policy
    artifact = _REAL_REPO_ROOT / policy["source_artifact"]
    assert artifact.is_file(), artifact
    # Not retro-fitted: these finalizer-schema fields stay absent.
    assert "source_artifact_sha256" not in policy, policy
    assert "measurement_timestamp" not in policy, policy

    findings = []
    module.audit_baseline(baseline, findings)
    assert findings == [], findings


def test_indeterminate_provenance_exits_nonzero_not_clean(
    tmp_path, monkeypatch, capsys
):
    """Rule 61 / requirement 3.8: indeterminate must not exit 0.

    The pre-existing shallow-clone test drives
    `_check_commit_and_timestamp` directly, so it pins the SKIP-plus-finding
    contract but never the process verdict.  Observed here through the real
    `main()` entry point: an indeterminate presence check prints SKIP on
    stderr, records a VIOLATION, and returns 1.
    """
    baseline_dir = tmp_path / "perf" / "baselines"
    baseline_dir.mkdir(parents=True)
    _write_sandbox_baseline(tmp_path, "0" * 40)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "BASELINE_DIR", baseline_dir)
    monkeypatch.setattr(module, "repo_commit_exists", lambda sha: None)
    monkeypatch.setattr(sys, "argv", ["detect_baseline_hand_edit.py"])

    exit_code = module.main()

    assert exit_code == 1, "indeterminate provenance must fail closed"
    captured = capsys.readouterr().err
    assert "SKIP perf/baselines/module-baseline-092.json" in captured, captured
    assert "unverifiable in this shallow clone" in captured, captured
    assert "1 violation(s)" in captured, captured


def _harness_security_checks_recipe():
    """Return (target_line, recipe) for the harness-security-checks target.

    `recipe` is a list of (line_number, text) pairs; line numbers are
    1-based so a failure can be read straight against the Makefile.
    """
    makefile = _REAL_REPO_ROOT / "Makefile"
    lines = makefile.read_text(encoding="utf-8").splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith("harness-security-checks:"):
            start = index
            break
    assert start is not None, "harness-security-checks target not found"
    recipe = []
    for offset, line in enumerate(lines[start + 1:], start=start + 2):
        if line and not line[0].isspace():
            break
        recipe.append((offset, line))
    return start + 1, recipe


def test_detector_stays_a_blocking_harness_security_check():
    """Requirement 3.8: the detector stays a blocking gate.

    Observed: `Makefile:459` is a tab-indented
    `python3 tools/harness/detect_baseline_hand_edit.py` inside the
    `harness-security-checks:` recipe that starts at `Makefile:414`.  The
    assertion is structural rather than a line-number pin so unrelated edits
    above it do not break the test, while removal, a `-` error-ignore
    prefix, or a `|| true` suffix all do.
    """
    target_line, recipe = _harness_security_checks_recipe()
    matches = [
        (number, text) for number, text in recipe
        if "detect_baseline_hand_edit.py" in text
    ]
    assert len(matches) == 1, (
        f"expected exactly one detector invocation in the "
        f"harness-security-checks recipe at Makefile:{target_line}, "
        f"got {matches}"
    )
    number, text = matches[0]
    command = text.lstrip("\t ")
    assert not command.startswith("-"), (
        f"Makefile:{number} makes the detector non-blocking with a "
        f"make error-ignore prefix: {text!r}"
    )
    assert "|| true" not in text, (
        f"Makefile:{number} swallows the detector verdict: {text!r}"
    )
    assert "continue-on-error" not in text, (
        f"Makefile:{number} downgrades the detector: {text!r}"
    )
    assert "--changed" not in command, (
        f"Makefile:{number} runs changed-file mode instead of the full "
        f"audit: {text!r}"
    )
    assert command == "python3 tools/harness/detect_baseline_hand_edit.py", (
        f"Makefile:{number} full-audit invocation changed shape: {text!r}"
    )


# ───────── Finding A: detector verdicts resolved over real git refs ─────────
# Requirement 2.4 enumerates the verdicts this suite must cover.  Every test
# below opts out of the always-true stubs through `_bind_real_git()`, so each
# verdict is decided by real objects and real refs rather than by a stub that
# can only ever answer "present and anchored".
#
# The four schema-shaped cases (commit mismatch, digest mutation, artifact
# path escape, symlink escape) already have stubbed variants earlier in this
# file.  Restating them on the real git path is not duplication: the stubbed
# variants prove the schema check fires while provenance is ASSUMED good, and
# these prove it still fires — and still stands alone as the single finding —
# when provenance is genuinely resolved against a repository.  Each one pairs
# the adversarial fixture with a clean-fixture assertion so a finding can
# never be attributed to the sandbox topology.


def _orphan_commit(repo, label):
    """Create a commit in `repo` that no ref reaches, and return its SHA.

    A marker tag is added first when the repository carries none, because
    `repo_commit_anchored()` answers indeterminate for a tagless checkout: with
    zero tags it cannot separate "unanchored" from "the refs were never
    fetched".  A test that needs the unanchored FAIL verdict has to make the
    judgment decidable, or it lands on the indeterminate branch instead and
    asserts against the wrong contract.

    `label` keeps each orphan's tree distinct, so two orphans created in the
    same repository within the same second cannot collapse into one identical
    commit object.
    """
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if not _git(repo, "for-each-ref", "refs/tags", "--count=1"):
        _git(repo, "tag", "sandbox-marker-0.0.1")
    _git(repo, "checkout", "--quiet", "--detach")
    orphan_sha = _commit(repo, f"orphan measurement {label}\n",
                         f"orphan measurement {label}")
    _git(repo, "checkout", "--quiet", branch)
    containing = _git(repo, "for-each-ref", "--contains", orphan_sha,
                      "--format=%(refname)")
    assert containing == "", f"orphan must be unreferenced, got {containing!r}"
    return orphan_sha


def _git_stdout_spy(monkeypatch):
    """Record every `_git_stdout` invocation while keeping real behaviour."""
    calls = []
    real_git_stdout = module._git_stdout

    def spy(*args):
        calls.append(args)
        return real_git_stdout(*args)

    monkeypatch.setattr(module, "_git_stdout", spy)
    return calls


def test_benchmark_policy_commit_mismatch_rejected_over_real_git(
    real_git_sandbox, monkeypatch
):
    """2.4: benchmark commit != policy commit must FAIL on the real git path.

    Both SHAs are real commits from the same sandbox, which is the shape of
    the defect: the benchmark ran at one commit and the finalized policy
    records another.  The policy commit is present and anchored for real (the
    sandbox tip is reachable from refs/heads/main), so the single finding
    isolates the mismatch instead of trailing an unresolved-provenance one.
    """
    origin, tip_sha = real_git_sandbox
    parent_sha = _git(origin, "rev-parse", "HEAD~1")
    _bind_real_git(monkeypatch, origin)
    assert module.repo_commit_exists(tip_sha) is True
    assert module.repo_commit_anchored(tip_sha, "module-baseline-092") is True

    baseline = _write_sandbox_baseline(origin, tip_sha,
                                       benchmark_commit=parent_sha)
    findings = []
    module.audit_baseline(baseline, findings)
    assert len(findings) == 1, findings
    assert "does not match" in findings[0], findings[0]
    assert parent_sha in findings[0], findings[0]

    # Clean fixture, same sandbox: with the two commits agreeing the audit is
    # silent, so the finding above cannot come from the sandbox topology.
    agreed = _write_sandbox_baseline(origin, tip_sha)
    agreed_findings = []
    module.audit_baseline(agreed, agreed_findings)
    assert agreed_findings == [], agreed_findings


def test_digest_mutation_of_retained_raw_artifact_rejected(
    real_git_sandbox, monkeypatch
):
    """2.4: raw artifact mutated, finalized evidence untouched → FAIL.

    Rule 61 binds a finalized baseline to the exact bytes of its retained raw
    artifact.  Editing the raw file without re-running the finalizer is the
    churn shape this gate exists for: the finalized JSON keeps declaring the
    old digest, so the recomputed digest stops matching.  Provenance resolves
    for real here, so the digest finding stands alone.
    """
    origin, tip_sha = real_git_sandbox
    _bind_real_git(monkeypatch, origin)
    baseline = _write_sandbox_baseline(origin, tip_sha)

    # Clean fixture first: the pair is consistent before the mutation.
    clean_findings = []
    module.audit_baseline(baseline, clean_findings)
    assert clean_findings == [], clean_findings

    raw = origin / "perf" / "baselines" / "module-baseline-092-raw.json"
    before = raw.read_bytes()
    raw.write_text("{\"scenarios\": [{\"name\": \"hand-edited\"}]}",
                   encoding="utf-8")
    assert raw.read_bytes() != before, "the mutation must change the bytes"
    # The finalized document is deliberately NOT regenerated: it still
    # declares the digest of the pre-mutation artifact.
    declared = module.json.loads(baseline.read_text(encoding="utf-8"))
    assert declared["baseline_policy"]["source_artifact_sha256"] == \
        module.hashlib.sha256(before).hexdigest()

    findings = []
    module.audit_baseline(baseline, findings)
    assert len(findings) == 1, findings
    assert "source_artifact_sha256 mismatch" in findings[0], findings[0]
    assert "finalize_module_baseline.py" in findings[0], findings[0]


def test_source_artifact_path_escaping_repo_rejected_over_real_git(
    real_git_sandbox, monkeypatch
):
    """2.4: a source_artifact path that leaves the repository must FAIL.

    Covers the literal-path guard: an absolute path, and repo-relative paths
    that climb out with `..`.  All three are rejected before the file is read,
    so an attacker-chosen path never reaches the digest step — the target
    below exists on disk and is still refused.  The resolution-time half of
    the containment rule (Rule 33) is covered by the symlink test that
    follows, where the literal path is clean and only resolution reveals the
    escape.
    """
    origin, tip_sha = real_git_sandbox
    _bind_real_git(monkeypatch, origin)
    outside = origin.parent / "h-artifact-escape-target.json"
    outside.write_text("{\"leaked\": true}", encoding="utf-8")

    escapes = (
        "/etc/passwd",
        f"../{outside.name}",
        f"perf/baselines/../../{outside.name}",
    )
    for artifact in escapes:
        baseline = _write_sandbox_baseline(origin, tip_sha)
        doc = module.json.loads(baseline.read_text(encoding="utf-8"))
        doc["baseline_policy"]["source_artifact"] = artifact
        baseline.write_text(module.json.dumps(doc), encoding="utf-8")
        findings = []
        module.audit_baseline(baseline, findings)
        assert len(findings) == 1, (artifact, findings)
        assert "repo-relative" in findings[0], (artifact, findings[0])

    # Clean fixture: the in-repo artifact path the helper writes by default
    # passes, so the rejections above are about containment.
    clean = _write_sandbox_baseline(origin, tip_sha)
    clean_findings = []
    module.audit_baseline(clean, clean_findings)
    assert clean_findings == [], clean_findings


def test_symlink_escaping_repo_rejected_over_real_git(
    real_git_sandbox, monkeypatch
):
    """2.4: a retained artifact symlinked out of the repository must FAIL.

    The declared path is repo-relative and free of `..`, so the literal guard
    passes it; only resolving the link shows the bytes live outside the
    repository.  The digest still matches those bytes, which is the point:
    containment has to reject this on its own, without help from a content
    check.
    """
    import os
    origin, tip_sha = real_git_sandbox
    _bind_real_git(monkeypatch, origin)
    baseline = _write_sandbox_baseline(origin, tip_sha)
    raw = origin / "perf" / "baselines" / "module-baseline-092-raw.json"
    outside = origin.parent / "h-symlink-escape-target.json"
    outside.write_bytes(raw.read_bytes())
    raw.unlink()
    os.symlink(outside, raw)
    assert raw.is_symlink(), raw
    assert raw.is_file(), "the target must exist so only containment fails"

    findings = []
    module.audit_baseline(baseline, findings)
    assert len(findings) == 1, findings
    assert "outside the repository" in findings[0], findings[0]


def test_unanchored_commit_names_its_own_durable_tag(
    real_git_sandbox, monkeypatch
):
    """2.4: present-but-unreferenced must FAIL and name perf-baseline/<stem>.

    Presence is not provenance: an object no ref reaches survives only until
    the server collects it.  The remediation instruction therefore has to
    identify the tag the operator is missing, which means deriving it from the
    baseline file name rather than emitting a fixed string.  Two stems share
    one orphan commit here, and each finding must name its own stem and only
    its own — a hardcoded namespace constant cannot satisfy both halves.
    """
    origin, _tip = real_git_sandbox
    orphan_sha = _orphan_commit(origin, "stem-derivation")
    _bind_real_git(monkeypatch, origin)
    assert module.repo_commit_exists(orphan_sha) is True, "object present"

    stems = ("module-baseline-092", "module-baseline-brotli-091")
    for stem in stems:
        baseline = _write_sandbox_baseline(origin, orphan_sha, stem=stem)
        findings = []
        module.audit_baseline(baseline, findings)
        assert len(findings) == 1, (stem, findings)
        assert "is not anchored by any ref" in findings[0], (stem, findings[0])
        assert f"perf-baseline/{stem}" in findings[0], (stem, findings[0])
        assert "perf/baselines/README.md" in findings[0], (stem, findings[0])
        others = [f"perf-baseline/{s}" for s in stems if s != stem]
        for other in others:
            assert other not in findings[0], (stem, other, findings[0])


def test_canonical_anchor_tag_passes_through_the_fast_path(
    real_git_sandbox, monkeypatch
):
    """2.4: refs/tags/perf-baseline/<stem> → PASS, decided in constant time.

    Same orphan commit the previous test rejects; the durable tag is the only
    thing that changes, so the PASS is caused by the anchor existing rather
    than by a weakened check.  The spy proves the ref traversal is never
    reached: a reordered resolution chain would still answer True here while
    losing the constant-time bound the runtime argument rests on.
    """
    origin, _tip = real_git_sandbox
    orphan_sha = _orphan_commit(origin, "fast-path")
    stem = "module-baseline-092"
    baseline = _write_sandbox_baseline(origin, orphan_sha, stem=stem)

    # Adversarial half: without the tag this same fixture is rejected.
    _bind_real_git(monkeypatch, origin)
    unanchored_findings = []
    module.audit_baseline(baseline, unanchored_findings)
    assert len(unanchored_findings) == 1, unanchored_findings
    assert "is not anchored by any ref" in unanchored_findings[0], \
        unanchored_findings[0]

    _git(origin, *_GIT_IDENTITY, "tag", "-a", "-m",
         "durable measurement anchor",
         f"perf-baseline/{stem}", orphan_sha)
    calls = _git_stdout_spy(monkeypatch)
    assert module.repo_commit_anchored(orphan_sha, stem) is True
    assert calls, "the anchor judgment must consult git"
    assert calls[0][0] == "rev-parse", calls
    canonical = f"{module.ANCHOR_TAG_NAMESPACE}/{stem}^{{commit}}"
    assert canonical in calls[0], calls
    assert not any("--contains" in args for args in calls), calls

    findings = []
    module.audit_baseline(baseline, findings)
    assert findings == [], findings


def test_ordinary_tag_or_branch_anchor_passes_through_the_fallback(
    real_git_sandbox, monkeypatch
):
    """2.4: a commit reachable from an ordinary ref → PASS via the fallback.

    Models the checked-in archival pack module-baseline-brotli-091, whose
    measurement commit predates the durable-tag policy and is anchored by
    refs/heads/main and a release tag instead.  No canonical
    perf-baseline/<stem> tag exists in this sandbox, so the fast path must
    miss and the ref traversal must be what answers — dropping the fallback
    would turn every such pack into a false rejection.
    """
    origin, tip_sha = real_git_sandbox
    stem = "module-baseline-brotli-091"
    _git(origin, *_GIT_IDENTITY, "tag", "-a", "-m", "release",
         "v0.0.1", tip_sha)
    _bind_real_git(monkeypatch, origin)
    canonical = f"{module.ANCHOR_TAG_NAMESPACE}/{stem}^{{commit}}"
    assert module._git_stdout("rev-parse", "--verify", "--quiet",
                              canonical) == "", \
        "the fallback case must have no canonical anchor tag"
    containing = _git(origin, "for-each-ref", "--contains", tip_sha,
                      "--format=%(refname)").splitlines()
    assert "refs/heads/main" in containing, containing
    assert "refs/tags/v0.0.1" in containing, containing

    calls = _git_stdout_spy(monkeypatch)
    assert module.repo_commit_anchored(tip_sha, stem) is True
    assert any("--contains" in args for args in calls), calls

    baseline = _write_sandbox_baseline(origin, tip_sha, stem=stem)
    findings = []
    module.audit_baseline(baseline, findings)
    assert findings == [], findings


def test_verbatim_import_is_not_exempt_from_the_anchor_check(
    real_git_sandbox, monkeypatch
):
    """2.4: the archival exemption releases schema fields, never provenance.

    `_check_policy_fields` returns early for `verbatim_import` so the
    finalizer schema is never retro-fitted onto an archival pack, and the
    preservation tests above lock that in.  If the exemption also covered the
    anchor check, the exact failure this remediation addresses could resurface
    on an archival pack and no test would notice.  One orphan commit, both
    baseline types: the anchor finding must appear for each.
    """
    origin, _tip = real_git_sandbox
    orphan_sha = _orphan_commit(origin, "archival-anchor")
    stem = "module-baseline-brotli-091"
    _bind_real_git(monkeypatch, origin)

    for baseline_type in ("verbatim_run", "verbatim_import"):
        baseline = _write_sandbox_baseline(origin, orphan_sha, stem=stem,
                                           baseline_type=baseline_type)
        findings = []
        module.audit_baseline(baseline, findings)
        assert len(findings) == 1, (baseline_type, findings)
        assert "is not anchored by any ref" in findings[0], \
            (baseline_type, findings[0])
        assert f"perf-baseline/{stem}" in findings[0], \
            (baseline_type, findings[0])

    # Clean fixture: with the durable tag in place the archival pack audits
    # clean, so the finding above is the anchor judgment rather than the
    # archival profile itself being rejected.
    _git(origin, *_GIT_IDENTITY, "tag", "-a", "-m",
         "durable measurement anchor",
         f"perf-baseline/{stem}", orphan_sha)
    archival = _write_sandbox_baseline(origin, orphan_sha, stem=stem,
                                       baseline_type="verbatim_import")
    anchored_findings = []
    module.audit_baseline(archival, anchored_findings)
    assert anchored_findings == [], anchored_findings
