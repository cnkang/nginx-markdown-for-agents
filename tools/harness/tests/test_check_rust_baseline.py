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
    for path in baseline.OBSERVATION_ACTION_WORKFLOWS:
        _write(
            root / path,
            "steps:\n  - uses: dtolnay/rust-toolchain@sha\n"
            "    with:\n      toolchain: 1.97.0\n"
            "  - uses: dtolnay/rust-toolchain@sha\n"
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

def test_container_rust_version_variable_drift_fails(tmp_path: Path) -> None:
    """The interpolated form is checked through its variable."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "env:\n  RUST_VERSION: 1.99.0\n"
        "jobs:\n  build:\n    steps:\n      - run: docker run rust:${RUST_VERSION}\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("RUST_VERSION is '1.99.0'" in error for error in errors), errors


def test_os_suffixed_image_tag_is_accepted(tmp_path: Path) -> None:
    """An OS suffix is not part of the version and must not be flagged."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    steps:\n      - run: docker run rust:1.97.0-alpine3.21\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert not any("rust:" in error for error in errors), errors


def test_container_rust_image_drift_fails(tmp_path: Path) -> None:
    """An image tag is a version declaration the inventory check cannot see."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    steps:\n"
        "      - run: docker run rust:1.99.9-alpine3.21\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("rust:1.99.9" in error or "1.99.9" in error for error in errors), errors


def test_container_rust_image_at_canonical_version_passes(tmp_path: Path) -> None:
    """The canonical version in an image tag raises no error."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    steps:\n"
        "      - run: docker run rust:1.97.0-alpine3.21 sh -c build\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert not any("rust:" in error for error in errors), errors


def test_container_rust_image_without_exact_version_fails(tmp_path: Path) -> None:
    """A suffixed or floating tag is not the canonical version."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    steps:\n"
        "      - run: docker run rust:alpine3.21\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("rust:alpine3.21" in error or "alpine3.21" in error for error in errors), errors


def test_interpolation_before_the_version_is_rejected(tmp_path: Path) -> None:
    """A variable ahead of RUST_VERSION could stand in for another version."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "env:\n  RUST_VERSION: 1.97.0\n"
        "jobs:\n  build:\n    steps:\n      - run: docker run rust:${MATRIX}${RUST_VERSION}\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("cannot resolve" in error for error in errors), errors


def test_declared_suffix_variable_is_accepted(tmp_path: Path) -> None:
    """A declared suffix such as an Alpine release does not carry the version."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "env:\n  RUST_VERSION: 1.97.0\n  ALPINE_VERSION: 3.21\n"
        "jobs:\n  build:\n    steps:\n"
        "      - run: docker run rust:${RUST_VERSION}-alpine${ALPINE_VERSION}\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert not any("rust:" in error for error in errors), errors


def test_undeclared_suffix_variable_is_rejected(tmp_path: Path) -> None:
    """An undeclared interpolation could stand in for another version."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "env:\n  RUST_VERSION: 1.97.0\n"
        "jobs:\n  build:\n    steps:\n"
        "      - run: docker run rust:${RUST_VERSION}-${SNEAKY}\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("cannot resolve" in error for error in errors), errors


def test_prefix_before_the_version_is_rejected(tmp_path: Path) -> None:
    """A prefix ahead of the version interpolation is not a version pin."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "env:\n  RUST_VERSION: 1.97.0\n"
        "jobs:\n  build:\n    steps:\n"
        "      - run: docker run rust:prefix${RUST_VERSION}-alpine3.21\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("cannot resolve" in error for error in errors), errors


def test_job_name_is_not_a_declared_environment_variable(tmp_path: Path) -> None:
    """An uppercase job key must not count as an environment name."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "env:\n  RUST_VERSION: 1.97.0\n"
        "jobs:\n  SNEAKY:\n    steps:\n"
        "      - run: docker run rust:${RUST_VERSION}-${SNEAKY}\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("cannot resolve" in error for error in errors), errors


def test_job_level_env_is_a_declared_variable(tmp_path: Path) -> None:
    """A job's own env mapping is visible to its steps."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "env:\n  RUST_VERSION: 1.97.0\n"
        "jobs:\n  build:\n    env:\n      ALPINE_VERSION: 3.21\n    steps:\n"
        "      - run: docker run rust:${RUST_VERSION}-alpine${ALPINE_VERSION}\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert not any("rust:" in error for error in errors), errors


def test_undeclared_version_variable_is_rejected(tmp_path: Path) -> None:
    """The version variable has to be declared, not merely named."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    steps:\n"
        "      - run: docker run rust:${RUST_VERSION}-alpine3.21\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("cannot resolve" in error for error in errors), errors


def test_inline_rust_version_mapping_is_checked(tmp_path: Path) -> None:
    """An inline `env:` mapping declares the version too."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    env: {RUST_VERSION: 1.99.0}\n    steps:\n"
        "      - run: echo hi\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("RUST_VERSION is '1.99.0'" in error for error in errors), errors


def test_job_container_image_is_checked(tmp_path: Path) -> None:
    """A job that runs in a Rust container must pin the canonical version."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    container:\n      image: rust:1.99.9-alpine3.21\n"
        "    steps:\n      - run: echo hi\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("1.99.9" in error for error in errors), errors


def test_service_image_is_checked(tmp_path: Path) -> None:
    """A service image is a version declaration as well."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    services:\n      runner:\n"
        "        image: rust:1.99.9-alpine3.21\n    steps:\n      - run: echo hi\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("1.99.9" in error for error in errors), errors


def test_job_without_steps_still_declares_a_version(tmp_path: Path) -> None:
    """A job that only calls a reusable workflow still carries its env."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  reuse:\n    uses: owner/repo/.github/workflows/x.yml\n"
        "    env:\n      RUST_VERSION: 1.99.0\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("RUST_VERSION is '1.99.0'" in error for error in errors), errors


def test_version_text_outside_env_is_not_a_declaration(tmp_path: Path) -> None:
    """A `RUST_VERSION:` that is not an env key must not be read as one."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "env:\n  RUST_VERSION: 1.97.0\n"
        "jobs:\n  build:\n    steps:\n"
        "      - run: |\n          echo RUST_VERSION: 9.9.9\n"
        "      - run: docker run rust:${RUST_VERSION}\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert not any("9.9.9" in error for error in errors), errors


def test_string_container_is_checked(tmp_path: Path) -> None:
    """`container:` may name the image directly as a string."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    container: rust:1.99.9-alpine3.21\n"
        "    steps:\n      - run: echo hi\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("1.99.9" in error for error in errors), errors


def test_unquoted_version_value_is_checked(tmp_path: Path) -> None:
    """An unquoted version arrives as a number, not as a string."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    env:\n      RUST_VERSION: 1.99\n    steps:\n"
        "      - run: echo hi\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("1.99" in error for error in errors), errors


def test_docker_action_image_is_checked(tmp_path: Path) -> None:
    """A step may run a container action through `uses: docker://`."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    steps:\n"
        "      - uses: docker://rust:1.99.9-alpine3.21\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("1.99.9" in error for error in errors), errors


def test_container_image_is_not_shell_expanded(tmp_path: Path) -> None:
    """A declarative image is literal, so a declared version cannot satisfy it."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        # The declaration matches the fixture's toolchain, so a version-drift
        # complaint cannot stand in for the interpolation one.
        "env:\n  RUST_VERSION: \"1.97.0\"\njobs:\n  build:\n    container:\n"
        "      image: rust:${RUST_VERSION}-alpine3.21\n    steps:\n"
        "      - run: cargo build\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    # The declaration matches the canonical version, so the only way this test
    # can pass is the interpolation complaint itself.
    assert any("cannot resolve" in error for error in errors), errors
    assert not any("declares" in error for error in errors), errors


def test_version_interpolation_rejects_a_trailing_word(tmp_path: Path) -> None:
    """`${RUST_VERSION}evil` would claim a version the check never saw."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "env:\n  RUST_VERSION: \"1.98.1\"\njobs:\n  build:\n    steps:\n"
        "      - run: docker run --rm rust:${RUST_VERSION}evil cargo build\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("${RUST_VERSION}evil" in error for error in errors), errors


def test_bare_rust_image_is_rejected(tmp_path: Path) -> None:
    """A tagless reference floats to `latest`, which is not the frozen version."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    container:\n      image: rust\n    steps:\n"
        "      - run: cargo build\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("carries no version tag" in error for error in errors), errors


def test_uses_step_environment_is_read(tmp_path: Path) -> None:
    """A `uses` step has no `run`, but its `env` can still declare the version."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    steps:\n      - uses: actions/checkout@v4\n"
        "        env:\n          RUST_VERSION: \"1.99.9\"\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("1.99.9" in error for error in errors), errors


def test_qualified_tagless_image_is_rejected(tmp_path: Path) -> None:
    """A registry-qualified reference with no tag floats just the same."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    container:\n      image: docker.io/library/rust\n"
        "    steps:\n      - run: cargo build\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("carries no version tag" in error for error in errors), errors


def test_digest_only_image_is_rejected(tmp_path: Path) -> None:
    """A digest pins bytes but names no version, so the check cannot see it."""
    _write_valid_fixture(tmp_path)
    _write(
        tmp_path / ".github/workflows/container-build.yml",
        "jobs:\n  build:\n    container:\n      image: rust@sha256:"
        + "a" * 64
        + "\n    steps:\n      - run: cargo build\n",
    )

    _exact, _msrv, errors = baseline.collect_errors(tmp_path)

    assert any("carries no version tag" in error for error in errors), errors
