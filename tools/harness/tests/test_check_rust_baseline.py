"""Tests for the frozen repository Rust compiler and MSRV contract."""

from __future__ import annotations

from pathlib import Path

from tools.harness import check_rust_baseline as baseline


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_valid_fixture(root: Path) -> None:
    _write(root / "rust-toolchain.toml", '[toolchain]\nchannel = "1.97.0"\n')
    for path in baseline.MANIFEST_PATHS:
        _write(root / path, '[package]\nname = "fixture"\nrust-version = "1.97"\n')
    for path in baseline.BASELINE_ACTION_WORKFLOWS:
        _write(
            root / path,
            "steps:\n  - uses: dtolnay/rust-toolchain@sha\n"
            "    with:\n      toolchain: 1.97.0\n",
        )
    for path in baseline.NIGHTLY_ACTION_WORKFLOWS:
        _write(
            root / path,
            "steps:\n  - uses: dtolnay/rust-toolchain@sha\n"
            "    with:\n      toolchain: nightly\n",
        )
    for path in baseline.RELEASE_WORKFLOWS:
        _write(root / path, "env:\n  RUST_TOOLCHAIN: 1.97.0\n")
    for path in baseline.RELEASE_DOCKERFILES:
        _write(
            root / path,
            "COPY rust-toolchain.toml /src/rust-toolchain.toml\n"
            "RUN rustup toolchain install\n",
        )
    _write(
        root / "packaging/debian/control",
        "Build-Depends: cargo, rustc (>= 1.97), nginx-dev\n",
    )
    for path in baseline.CURRENT_BUILD_DOCS:
        _write(root / path, "Source builds require Rust 1.97.0 or newer.\n")


def test_valid_repository_contract_passes(tmp_path: Path) -> None:
    _write_valid_fixture(tmp_path)

    exact, msrv, errors = baseline.collect_errors(tmp_path)

    assert exact == "1.97.0"
    assert msrv == "1.97"
    assert errors == []


def test_manifest_msrv_drift_fails_with_path(tmp_path: Path) -> None:
    _write_valid_fixture(tmp_path)
    manifest = tmp_path / baseline.MANIFEST_PATHS[1]
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("1.97", "1.96"),
        encoding="utf-8",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any(str(baseline.MANIFEST_PATHS[1]) in error for error in errors)
    assert any("expected '1.97'" in error for error in errors)


def test_release_workflow_compiler_drift_fails(tmp_path: Path) -> None:
    _write_valid_fixture(tmp_path)
    workflow = tmp_path / baseline.RELEASE_WORKFLOWS[0]
    workflow.write_text("env:\n  RUST_TOOLCHAIN: stable\n", encoding="utf-8")

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any(str(baseline.RELEASE_WORKFLOWS[0]) in error for error in errors)
    assert any("expected '1.97.0'" in error for error in errors)


def test_unclassified_rust_workflow_fails(tmp_path: Path) -> None:
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/new-rust-job.yml",
        "steps:\n  - uses: dtolnay/rust-toolchain@sha\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("new-rust-job.yml" in error and "not classified" in error for error in errors)


def test_release_dockerfile_must_consume_canonical_toolchain(tmp_path: Path) -> None:
    _write_valid_fixture(tmp_path)
    _write(tmp_path / baseline.RELEASE_DOCKERFILES[0], "RUN cargo build --release\n")

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any(str(baseline.RELEASE_DOCKERFILES[0]) in error for error in errors)
    assert any("canonical rust-toolchain.toml" in error for error in errors)


def test_floating_canonical_toolchain_fails(tmp_path: Path) -> None:
    _write_valid_fixture(tmp_path)
    _write(tmp_path / "rust-toolchain.toml", '[toolchain]\nchannel = "stable"\n')

    exact, msrv, errors = baseline.collect_errors(tmp_path)

    assert exact is None
    assert msrv is None
    assert any("exact MAJOR.MINOR.PATCH" in error for error in errors)
