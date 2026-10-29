"""Tests for evidence-gated matrix promotion."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tools.lib.path_validation import validate_write_path_within_root
from tools.release.matrix import promote_matrix as promote_matrix_module
from tools.release.matrix.promote_matrix import (
    PromotionError,
    REPO_ROOT,
    _exclusive_lock,
    build_promoted_matrix,
    main,
)
from tools.release.matrix.tests.test_evidence_contract import _record, _ubuntu_record

PROMOTE_SCRIPT = Path(__file__).resolve().parents[1] / "promote_matrix.py"
WORK_ROOT = REPO_ROOT / ".test-tmp"


def _row(**overrides) -> dict:
    row = {
        "nginx_version": "1.30.6",
        "os": "linux",
        "libc": "glibc",
        "arch": "amd64",
        "artifact_type": "dynamic-module",
        "support_tier": "best-effort",
        "support_stage": "best-effort",
        "verification_state": "pending",
        "release_blocking": False,
    }
    row.update(overrides)
    return row


def _matrix(*rows: dict) -> dict:
    return {"schema_version": 1, "entries": list(rows)}


def test_promotion_sets_the_release_promise() -> None:
    matrix = _matrix(_row())
    promoted, summary = build_promoted_matrix(
        matrix, [_record()], demote_superseded=False
    )
    row = promoted["entries"][0]
    assert row["support_tier"] == "supported"
    assert row["support_stage"] == "primary"
    assert row["verification_state"] == "verified"
    assert row["release_blocking"] is True
    assert row["date"] == "2026-10-29"
    assert row["source"] == "nginx.org"
    assert summary == ["promote 1.30.6 linux/glibc/amd64: 1 row(s)"]


def test_record_without_a_row_is_refused() -> None:
    matrix = _matrix(_row(arch="arm64"))
    records = [_record()]
    with pytest.raises(PromotionError, match="covers no matrix row"):
        build_promoted_matrix(matrix, records, demote_superseded=False)


def test_manual_row_is_never_overwritten() -> None:
    matrix = _matrix(
        _row(
            nginx_version="1.24.0",
            os="ubuntu-24.04",
            artifact_type="deb-package",
            managed_by="manual",
            provenance={
                "kind": "ubuntu-default-repo",
                "reference": "ubuntu:24.04",
            },
        )
    )
    record = _ubuntu_record()
    records = [record]
    with pytest.raises(PromotionError, match="managed_by=manual"):
        build_promoted_matrix(matrix, records, demote_superseded=False)


def test_container_rows_are_not_promoted_by_evidence() -> None:
    matrix = _matrix(_row(artifact_type="docker-image"))
    records = [_record()]
    with pytest.raises(PromotionError, match="covers no matrix row"):
        build_promoted_matrix(matrix, records, demote_superseded=False)


def test_superseded_patch_is_demoted_without_losing_its_row() -> None:
    matrix = _matrix(
        _row(nginx_version="1.30.5", support_tier="supported", release_blocking=True),
        _row(nginx_version="1.30.6"),
    )
    promoted, summary = build_promoted_matrix(
        matrix, [_record()], demote_superseded=True
    )
    old = promoted["entries"][0]
    assert old["nginx_version"] == "1.30.5"
    assert old["support_tier"] == "best-effort"
    assert old["support_stage"] == "best-effort"
    assert old["release_blocking"] is False
    # Demotion is not evidence.  The row had no record, so it stays pending.
    assert old["verification_state"] == "pending"
    assert "demote superseded patch 1.30.5 to best-effort" in summary


def test_superseded_demotion_keeps_other_tracks() -> None:
    matrix = _matrix(
        _row(nginx_version="1.28.3", support_tier="supported", release_blocking=True),
        _row(nginx_version="1.30.6"),
    )
    promoted, _ = build_promoted_matrix(
        matrix, [_record()], demote_superseded=True
    )
    assert promoted["entries"][0]["support_tier"] == "supported"


def test_newer_existing_patch_is_not_demoted() -> None:
    matrix = _matrix(
        _row(nginx_version="1.30.6", support_tier="supported", release_blocking=True),
        _row(nginx_version="1.30.5", support_tier="supported", release_blocking=True),
    )
    promoted, _ = build_promoted_matrix(
        matrix, [_record()], demote_superseded=False
    )
    assert promoted["entries"][1]["release_blocking"] is True


def test_promotion_is_idempotent() -> None:
    matrix = _matrix(_row())
    first, _ = build_promoted_matrix(matrix, [_record()], demote_superseded=False)
    snapshot = json.dumps(first, sort_keys=True)
    second, _ = build_promoted_matrix(first, [_record()], demote_superseded=False)
    assert json.dumps(second, sort_keys=True) == snapshot


def test_invalid_record_is_refused() -> None:
    matrix = _matrix(_row())
    records = [_record(module_sha256="short")]
    with pytest.raises(PromotionError, match="is not promotable"):
        build_promoted_matrix(matrix, records, demote_superseded=False)


def _write_evidence(path: Path, **overrides) -> Path:
    record = _record(**overrides)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_check_mode_reports_a_stale_matrix(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(_matrix(_row())), encoding="utf-8")
    evidence = _write_evidence(tmp_path / "record.json")
    rc = main(
        [
            "--matrix",
            str(matrix_path),
            "--evidence",
            str(evidence),
            "--check",
        ],
        repo_root=tmp_path,
    )
    assert rc == 2


def test_write_mode_then_check_mode_passes(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(_matrix(_row())), encoding="utf-8")
    evidence = _write_evidence(tmp_path / "record.json")
    assert (
        main(
            ["--matrix", str(matrix_path), "--evidence", str(evidence)],
            repo_root=tmp_path,
        )
        == 0
    )
    assert (
        main(
            ["--matrix", str(matrix_path), "--evidence", str(evidence), "--check"],
            repo_root=tmp_path,
        )
        == 0
    )


def test_superseded_demotion_skips_distro_rows() -> None:
    """A distro row is bound to one package revision, not to an upstream patch.

    The distro row sits in the same minor track as the promoted version, so
    only the source exclusion keeps it out of the demotion.
    """
    distro_row = _row(
        nginx_version="1.30.5",
        os="ubuntu-24.04",
        artifact_type="deb-package",
        source="ubuntu-default-repo",
        support_tier="supported",
        release_blocking=True,
    )
    matrix = _matrix(
        distro_row,
        _row(nginx_version="1.30.5", support_tier="supported",
             release_blocking=True),
        _row(nginx_version="1.30.6"),
    )
    promoted, _ = build_promoted_matrix(
        matrix, [_record()], demote_superseded=True
    )
    assert promoted["entries"][0]["support_tier"] == "supported"
    assert promoted["entries"][0]["release_blocking"] is True


def test_superseded_demotion_skips_manual_rows() -> None:
    """A manual pin keeps its tier and its blocking flag."""
    manual_row = _row(
        nginx_version="1.30.5",
        managed_by="manual",
        support_tier="supported",
        release_blocking=True,
    )
    matrix = _matrix(manual_row, _row(nginx_version="1.30.6"))
    promoted, _ = build_promoted_matrix(
        matrix, [_record()], demote_superseded=True
    )
    assert promoted["entries"][0]["support_tier"] == "supported"
    assert promoted["entries"][0]["release_blocking"] is True


def _promoted_matrix_file(path: Path) -> Path:
    """Write a matrix that already reflects the default evidence record."""
    matrix, _ = build_promoted_matrix(
        _matrix(_row()), [_record()], demote_superseded=False
    )
    path.write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    return path


@pytest.fixture()
def repo_workdir() -> Iterator[Path]:
    """Yield a throwaway directory inside the repository root.

    The CLI measures ``--matrix`` against the repository root and a subprocess
    cannot be handed a different root, so a real ``promote_matrix.py`` run must
    target a path inside the checkout.  ``.test-tmp/`` is git-ignored and the
    directory is removed afterwards.
    """
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="promote-lock-", dir=WORK_ROOT))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _promoter_command(matrix_path: Path, record: Path) -> list[str]:
    return [
        sys.executable,
        str(PROMOTE_SCRIPT),
        "--matrix",
        str(matrix_path),
        "--evidence",
        str(record),
    ]


def _run_promoters(matrix_path: Path, records: list[Path]) -> None:
    """Run one promoter process per record, all against the same matrix."""
    processes = [
        subprocess.Popen(
            _promoter_command(matrix_path, record),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for record in records
    ]
    deadline = time.monotonic() + 120
    results: list[tuple[str, str]] = []
    try:
        for process in processes:
            remaining = max(1.0, deadline - time.monotonic())
            results.append(process.communicate(timeout=remaining))
    except subprocess.TimeoutExpired:
        for process in processes:
            process.kill()
            process.communicate()
        pytest.fail("concurrent promotion did not finish within 120 seconds")

    for process, (stdout, stderr) in zip(processes, results):
        assert process.returncode == 0, f"promoter failed: {stdout}{stderr}"


@pytest.mark.skipif(
    os.name == "nt", reason="Windows uses msvcrt.locking, not flock"
)
def test_concurrent_promotions_keep_every_row(repo_workdir: Path) -> None:
    """Eight promoters on eight rows must not lose a single promotion.

    Each process promotes one record that covers exactly one row.  A promoter
    that locks a stale inode reads an old matrix and writes it back over the
    other promotions, so a lost update leaves a row at ``best-effort``.
    """
    versions = [f"1.30.{patch}" for patch in range(1, 9)]
    filler = [
        _row(
            nginx_version="1.24.0",
            libc="musl",
            arch="arm64",
            target=f"filler-{index}",
        )
        for index in range(500)
    ]
    rows = [_row(nginx_version=version) for version in versions]
    matrix_path = repo_workdir / "matrix.json"
    matrix_path.write_text(
        json.dumps(_matrix(*rows, *filler)), encoding="utf-8"
    )
    records = [
        _write_evidence(
            repo_workdir / f"record-{index}.json",
            nginx_version=version,
            target_identity=f"nginx.org:{version}:linux:amd64",
        )
        for index, version in enumerate(versions)
    ]

    _run_promoters(matrix_path, records)

    final = json.loads(matrix_path.read_text(encoding="utf-8"))
    assert len(final["entries"]) == len(rows) + len(filler)
    promoted = {
        entry["nginx_version"]
        for entry in final["entries"]
        if entry["support_tier"] == "supported"
    }
    assert promoted == set(versions)
    for entry in final["entries"]:
        if entry["nginx_version"] in versions:
            assert entry["verification_state"] == "verified"
            assert entry["release_blocking"] is True
            assert entry["source"] == "nginx.org"


@pytest.mark.skipif(
    os.name == "nt", reason="Windows uses msvcrt.locking, not flock"
)
def test_lock_sidecar_persists_and_keeps_its_inode(repo_workdir: Path) -> None:
    """The lock file survives a promotion, so every promoter locks one inode.

    Replacing the sidecar would create a new inode and let a waiter and a
    newcomer hold "the lock" at the same time.  The inode check is the
    regression guard for the unlink, and the third promotion proves the lock
    still serializes promotions across processes.
    """
    versions = ["1.30.6", "1.30.7", "1.30.8"]
    matrix_path = repo_workdir / "matrix.json"
    matrix_path.write_text(
        json.dumps(_matrix(*[_row(nginx_version=v) for v in versions])),
        encoding="utf-8",
    )
    first = _write_evidence(repo_workdir / "first.json")
    second = _write_evidence(
        repo_workdir / "second.json",
        nginx_version="1.30.7",
        target_identity="nginx.org:1.30.7:linux:amd64",
    )
    third = _write_evidence(
        repo_workdir / "third.json",
        nginx_version="1.30.8",
        target_identity="nginx.org:1.30.8:linux:amd64",
    )
    lock_path = matrix_path.with_suffix(".json.lock")

    assert (
        main(
            ["--matrix", str(matrix_path), "--evidence", str(first)],
            repo_root=repo_workdir,
        )
        == 0
    )
    assert lock_path.exists()
    inode = lock_path.stat().st_ino

    assert (
        main(
            ["--matrix", str(matrix_path), "--evidence", str(second)],
            repo_root=repo_workdir,
        )
        == 0
    )
    assert lock_path.exists()
    assert lock_path.stat().st_ino == inode

    with _exclusive_lock(matrix_path, root=repo_workdir):
        child = subprocess.Popen(
            _promoter_command(matrix_path, third),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.5)
        blocked = child.poll() is None

    # The lock is released above, so the child may finish now.
    try:
        stdout, stderr = child.communicate(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
    assert blocked, "a promoter ignored the held lock"
    assert child.returncode == 0, f"promoter failed: {stdout}{stderr}"

    final = json.loads(matrix_path.read_text(encoding="utf-8"))
    tiers = [entry["support_tier"] for entry in final["entries"]]
    assert tiers == ["supported", "supported", "supported"]


@pytest.mark.skipif(
    os.name == "nt" or getattr(os, "geteuid", lambda: -1)() == 0,
    reason="POSIX mode bits and a non-root user are required",
)
def test_read_only_modes_need_no_writable_directory(tmp_path: Path) -> None:
    """``--check`` and ``--dry-run`` must not create the lock sidecar.

    A read-only checkout cannot accept the sidecar, so opening it would fail
    before the matrix is read and a read-only mode would report an error.
    """
    matrix_path = _promoted_matrix_file(tmp_path / "matrix.json")
    evidence = _write_evidence(tmp_path / "record.json")
    lock_path = matrix_path.with_suffix(".json.lock")
    original_mode = stat.S_IMODE(tmp_path.stat().st_mode)

    try:
        tmp_path.chmod(0o555)
        assert (
            main(
                [
                    "--matrix",
                    str(matrix_path),
                    "--evidence",
                    str(evidence),
                    "--check",
                ],
                repo_root=tmp_path,
            )
            == 0
        )
        assert (
            main(
                [
                    "--matrix",
                    str(matrix_path),
                    "--evidence",
                    str(evidence),
                    "--dry-run",
                ],
                repo_root=tmp_path,
            )
            == 0
        )
        assert not lock_path.exists()
    finally:
        tmp_path.chmod(original_mode)


@pytest.mark.skipif(
    os.name == "nt", reason="Windows uses msvcrt.locking, not flock"
)
def test_read_only_modes_do_not_validate_a_write_target(tmp_path: Path) -> None:
    """A read-only run does not require the matrix inside the write root.

    The write-root check guards a write that ``--check`` and ``--dry-run``
    never perform; only their own read validation may refuse a path.
    """
    matrix_path = _promoted_matrix_file(tmp_path / "matrix.json")
    evidence = _write_evidence(tmp_path / "record.json")
    unrelated_root = tmp_path / "checkout"

    assert (
        main(
            ["--matrix", str(matrix_path), "--evidence", str(evidence), "--check"],
            repo_root=unrelated_root,
        )
        == 0
    )
    assert (
        main(
            ["--matrix", str(matrix_path), "--evidence", str(evidence), "--dry-run"],
            repo_root=unrelated_root,
        )
        == 0
    )


def _promotion_root(tmp_path: Path) -> Path:
    """Return a repository root the write check is measured against."""
    root = tmp_path / "repo"
    root.mkdir()
    return root


def _entry_state(matrix_path: Path) -> str:
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    return matrix["entries"][0]["verification_state"]


def test_symlinked_matrix_path_outside_the_root_is_refused(tmp_path: Path) -> None:
    """A repo symlink that points outside is resolved, then refused.

    ``Path.resolve()`` follows the link, so the resolved target no longer sits
    under the root and the ``relative_to`` check rejects it.  The out-of-tree
    matrix keeps its content because the run never reaches the write.
    """
    root = _promotion_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    real_matrix = outside / "matrix.json"
    real_matrix.write_text(json.dumps(_matrix(_row())), encoding="utf-8")
    link = root / "matrix.json"
    link.symlink_to(real_matrix)
    evidence = _write_evidence(tmp_path / "record.json")

    with pytest.raises(ValueError, match="escapes root"):
        validate_write_path_within_root(link, root=root)

    rc = main(
        ["--matrix", str(link), "--evidence", str(evidence)], repo_root=root
    )
    assert rc == 1
    assert _entry_state(real_matrix) == "pending"


def test_symlinked_directory_in_the_matrix_path_is_refused(tmp_path: Path) -> None:
    """An intermediate symlink out of the tree is refused as well."""
    root = _promotion_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    real_matrix = outside / "matrix.json"
    real_matrix.write_text(json.dumps(_matrix(_row())), encoding="utf-8")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    through_link = root / "linked" / "matrix.json"
    evidence = _write_evidence(tmp_path / "record.json")

    with pytest.raises(ValueError, match="escapes root"):
        validate_write_path_within_root(through_link, root=root)

    rc = main(
        ["--matrix", str(through_link), "--evidence", str(evidence)], repo_root=root
    )
    assert rc == 1
    assert _entry_state(real_matrix) == "pending"


def test_matrix_path_with_a_traversal_component_is_refused(tmp_path: Path) -> None:
    """A ``..`` component is rejected before any resolution happens.

    ``sub/../matrix.json`` resolves to a file inside the root, so only the
    traversal check keeps it out: the write path grammar is narrow on purpose.
    """
    root = _promotion_root(tmp_path)
    (root / "sub").mkdir()
    matrix_path = root / "matrix.json"
    matrix_path.write_text(json.dumps(_matrix(_row())), encoding="utf-8")
    evidence = _write_evidence(tmp_path / "record.json")
    traversal = root / "sub" / ".." / "matrix.json"
    assert ".." in traversal.parts

    with pytest.raises(ValueError, match="traversal component"):
        validate_write_path_within_root(traversal, root=root)

    rc = main(
        ["--matrix", str(traversal), "--evidence", str(evidence)], repo_root=root
    )
    assert rc == 1
    assert _entry_state(matrix_path) == "pending"


def test_atomic_replace_swaps_a_temp_file_beside_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The temp file is a sibling of the matrix, so the swap stays on one device.

    ``os.replace`` is only atomic within one filesystem; a temp file in the
    system temp directory would silently degrade to a copy.  The swap is
    observed while it happens, and no ``.tmp`` file may survive it.
    """
    root = _promotion_root(tmp_path)
    matrix_path = root / "matrix.json"
    matrix_path.write_text(json.dumps(_matrix(_row())), encoding="utf-8")
    evidence = _write_evidence(tmp_path / "record.json")

    real_replace = os.replace
    swaps: list[tuple[Path, Path]] = []

    def recording_replace(src, dst, *args, **kwargs):
        source = Path(src)
        target = Path(dst)
        swaps.append((source, target))
        assert source.parent == target.parent, "the temp file is not a sibling"
        assert source.exists(), "the temp file was not written before the swap"
        assert source.stat().st_dev == target.stat().st_dev, "cross-device swap"
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(promote_matrix_module.os, "replace", recording_replace)

    rc = main(
        ["--matrix", str(matrix_path), "--evidence", str(evidence)], repo_root=root
    )
    assert rc == 0
    assert len(swaps) == 1
    assert swaps[0][1] == matrix_path.resolve()
    assert not list(root.rglob("*.tmp")), "a temp file survived the write"
    assert _entry_state(matrix_path) == "verified"


def test_demotion_does_not_claim_verification_without_a_record() -> None:
    """Demoting a patch is a support decision, not evidence.

    The evidence gate rejects a verified row that no record covers, so the
    demotion path must not write ``verified`` for a row that never had one.
    """
    superseded = _row(
        nginx_version="1.30.5",
        support_tier="supported",
        support_stage="primary",
        release_blocking=True,
        verification_state="pending",
    )
    current = _row(nginx_version="1.30.6")
    matrix = _matrix(superseded, current)

    promoted, _ = build_promoted_matrix(
        matrix, [_record()], demote_superseded=True
    )

    assert promoted["entries"][0]["support_tier"] == "best-effort"
    assert promoted["entries"][0]["release_blocking"] is False
    assert promoted["entries"][0]["verification_state"] == "pending"


def test_demotion_preserves_an_existing_verification() -> None:
    """A row that already had a record keeps its verified state."""
    superseded = _row(
        nginx_version="1.30.5",
        support_tier="supported",
        support_stage="primary",
        release_blocking=True,
        verification_state="verified",
    )
    current = _row(nginx_version="1.30.6")
    matrix = _matrix(superseded, current)

    promoted, _ = build_promoted_matrix(
        matrix, [_record()], demote_superseded=True
    )

    assert promoted["entries"][0]["support_tier"] == "best-effort"
    assert promoted["entries"][0]["verification_state"] == "verified"


def test_keep_previous_holds_a_baseline_pinned_patch() -> None:
    """The perf baselines pin a runtime, so its row must stay supported.

    A benchmark compares against a recorded environment.  Demoting the row
    that produces that environment's module would leave the perf gate with no
    artifact to download.
    """
    pinned = _row(
        nginx_version="1.30.4",
        support_tier="supported",
        support_stage="primary",
        release_blocking=True,
    )
    superseded = _row(
        nginx_version="1.30.5",
        support_tier="supported",
        support_stage="primary",
        release_blocking=True,
    )
    current = _row(nginx_version="1.30.6")
    matrix = _matrix(pinned, superseded, current)

    promoted, _ = build_promoted_matrix(
        matrix,
        [_record()],
        demote_superseded=True,
        keep_previous=frozenset({"1.30.4"}),
    )

    assert promoted["entries"][0]["support_tier"] == "supported"
    assert promoted["entries"][0]["release_blocking"] is True
    assert promoted["entries"][1]["support_tier"] == "best-effort"
