#!/usr/bin/env python3
"""Enforce the repository's exact Rust compiler and public MSRV contract."""

from __future__ import annotations

import re

import yaml
import sys
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATHS = (
    Path("components/rust-converter/Cargo.toml"),
    Path("components/rust-converter/fuzz/Cargo.toml"),
    Path("tools/e2e-harness/Cargo.toml"),
    Path("tools/corpus/test-corpus-conversion/Cargo.toml"),
)
BASELINE_ACTION_WORKFLOWS = (
    Path(".github/workflows/ci.yml"),
    Path(".github/workflows/codeql.yml"),
    Path(".github/workflows/macos-smoke.yml"),
    Path(".github/workflows/nightly-perf.yml"),
    Path(".github/workflows/real-nginx-ims.yml"),
    Path(".github/workflows/sonarcloud.yml"),
)
NIGHTLY_ACTION_WORKFLOWS = (Path(".github/workflows/nightly-fuzz.yml"),)
# Observation workflows run the full suite (exact toolchain) and fuzz
# (nightly) jobs; each workflow must declare the exact toolchain at least
# once and any additional toolchain declarations must be either the exact
# channel or the nightly fuzz toolchain.
OBSERVATION_ACTION_WORKFLOWS = (
    Path(".github/workflows/nightly-observation.yml"),
    Path(".github/workflows/weekly-observation.yml"),
)
RELEASE_WORKFLOWS = (
    Path(".github/workflows/release-packages.yml"),
    Path(".github/workflows/release-rpm.yml"),
)
RELEASE_DOCKERFILES = (
    Path("tools/build_release/Dockerfile.glibc"),
    Path("tools/build_release/Dockerfile.musl"),
)
CURRENT_BUILD_DOCS = (
    Path("README.md"),
    Path("README_zh-CN.md"),
    Path("CONTRIBUTING.md"),
    Path("docs/guides/PACKAGE_COMPATIBILITY.md"),
    Path("docs/FAQ.md"),
    Path("docs/guides/INSTALLATION.md"),
    Path("docs/guides/KUBERNETES_DEPLOYMENT.md"),
    Path("docs/guides/OPERATIONS.md"),
    Path("docs/project/PROJECT_STATUS.md"),
)
EXACT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
ACTION_TOOLCHAIN_RE = re.compile(r"^\s*toolchain:\s*['\"]?([^'\"\s#]+)", re.MULTILINE)
RELEASE_TOOLCHAIN_RE = re.compile(
    r"^\s*RUST_TOOLCHAIN:\s*['\"]?([^'\"\s#]+)", re.MULTILINE
)


def _read_toml(path: Path) -> dict:
    """Read a repository-owned TOML file."""
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _read_text(root: Path, relative_path: Path, errors: list[str]) -> str | None:
    """Read a required text file and record a clear error on failure."""
    path = root / relative_path
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"{relative_path}: cannot read required file: {exc}")
        return None


def _load_canonical_versions(root: Path, errors: list[str]) -> tuple[str, str] | None:
    """Return the exact toolchain and matching major.minor MSRV."""
    relative_path = Path("rust-toolchain.toml")
    try:
        data = _read_toml(root / relative_path)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"{relative_path}: cannot load canonical toolchain: {exc}")
        return None

    exact = str(data.get("toolchain", {}).get("channel", ""))
    if not EXACT_VERSION_RE.fullmatch(exact):
        errors.append(
            f"{relative_path}: toolchain.channel must be an exact MAJOR.MINOR.PATCH "
            f"version, found {exact!r}"
        )
        return None
    major, minor, _patch = exact.split(".")
    return exact, f"{major}.{minor}"


def _check_manifests(root: Path, expected_msrv: str, errors: list[str]) -> None:
    """Check every supported first-party Cargo manifest's public MSRV."""
    for relative_path in MANIFEST_PATHS:
        try:
            package = _read_toml(root / relative_path).get("package", {})
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            errors.append(f"{relative_path}: cannot load manifest: {exc}")
            continue
        actual = str(package.get("rust-version", ""))
        if actual != expected_msrv:
            errors.append(
                f"{relative_path}: package.rust-version is {actual!r}; "
                f"expected {expected_msrv!r} from rust-toolchain.toml"
            )


def _check_workflow_group(
    root: Path,
    paths: tuple[Path, ...],
    pattern: re.Pattern[str],
    expected: str,
    label: str,
    errors: list[str],
) -> None:
    """Check every matching compiler declaration in a workflow group."""
    for relative_path in paths:
        content = _read_text(root, relative_path, errors)
        if content is None:
            continue
        versions = pattern.findall(content)
        if not versions:
            errors.append(f"{relative_path}: missing required {label} declaration")
            continue
        for version in versions:
            if version != expected:
                errors.append(
                    f"{relative_path}: {label} is {version!r}; expected {expected!r}"
                )


def _check_observation_versions(
    relative_path: Path, versions: list[str], exact: str, errors: list[str]
) -> None:
    """Validate one observation workflow's declared compiler channels."""
    if not versions:
        errors.append(f"{relative_path}: missing required toolchain declaration")
        return
    if exact not in versions:
        errors.append(
            f"{relative_path}: must declare the exact toolchain {exact!r}"
        )
    for version in versions:
        if version != exact and version != "nightly":
            errors.append(
                f"{relative_path}: unexpected toolchain {version!r} "
                f"(expected {exact!r} or 'nightly')"
            )


def _check_observation_workflows(root: Path, exact: str, errors: list[str]) -> None:
    """Observation workflows use the exact channel, plus optional nightly."""
    for relative_path in OBSERVATION_ACTION_WORKFLOWS:
        content = _read_text(root, relative_path, errors)
        if content is not None:
            _check_observation_versions(
                relative_path, ACTION_TOOLCHAIN_RE.findall(content), exact, errors
            )


RUST_IMAGE_RE = re.compile(r"(?<![\w.-])rust:([A-Za-z0-9._${}-]+)")
IMAGE_VERSION_RE = re.compile(r"^(\d+\.\d+\.\d+)(?:-|$)")


def _image_version(tag: str) -> str | None:
    """Return the version part of a Rust image tag, if it has one."""
    match = IMAGE_VERSION_RE.match(tag)
    return match.group(1) if match else None


def _env_keys(mapping: object) -> set[str]:
    """Return the environment names an `env:` mapping declares."""
    if not isinstance(mapping, dict):
        return set()
    return {str(key) for key in mapping}


def _workflow_document(content: str) -> dict:
    """Parse a workflow, returning an empty mapping when it does not parse."""
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError:
        return {}
    return document if isinstance(document, dict) else {}


def _declared_version_values(document: dict) -> list[str]:
    """Return every `RUST_VERSION` value the workflow declares.

    Reading the parsed document covers inline mappings (`env: {RUST_VERSION: …}`)
    that a line pattern would miss, and the workflow and job levels are read
    directly so a job that only calls a reusable workflow is included too.
    """
    mappings: list[dict] = []
    for candidate in (document.get("env"),):
        if isinstance(candidate, dict):
            mappings.append(candidate)
    jobs = document.get("jobs")
    if isinstance(jobs, dict):
        for job in jobs.values():
            if isinstance(job, dict) and isinstance(job.get("env"), dict):
                mappings.append(job["env"])
    for visible, _run in _step_visible_envs(document):
        mappings.append(visible)
    # A version may be written unquoted, in which case YAML hands back a number
    # rather than a string; compare whatever was recorded as text.
    return [
        str(value)
        for mapping in mappings
        for value in [mapping.get("RUST_VERSION")]
        if value is not None
    ]


def _job_env(job: dict, inherited: dict) -> dict:
    """Return the environment a job adds to the one it inherits."""
    merged = dict(inherited)
    if isinstance(job.get("env"), dict):
        merged.update(job["env"])
    return merged


def _job_images(job: dict, visible: dict) -> list[tuple[str, set[str]]]:
    """Return the images a job runs as its own container, with its environment."""
    names = {str(key) for key in visible}
    images: list[tuple[str, set[str]]] = []
    container = job.get("container")
    if isinstance(container, str):
        # `container: rust:1.99` names the image directly.
        images.append((container, names))
    elif isinstance(container, dict) and isinstance(container.get("image"), str):
        images.append((container["image"], names))
    services = job.get("services")
    if isinstance(services, dict):
        for service in services.values():
            if isinstance(service, dict) and isinstance(service.get("image"), str):
                images.append((service["image"], names))
    return images


def _container_images(document: dict) -> list[tuple[str, set[str]]]:
    """Return every job or service container image, with the environment it sees."""
    inherited = document.get("env")
    inherited = inherited if isinstance(inherited, dict) else {}
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return []
    found: list[tuple[str, set[str]]] = []
    for job in jobs.values():
        if isinstance(job, dict):
            found.extend(_job_images(job, _job_env(job, inherited)))
    return found


def _step_visible_envs(document: dict) -> list[tuple[dict, str]]:
    """Return (visible env mapping, run script) for every step in a workflow.

    A step sees the workflow's `env`, its job's `env` and its own, and nothing
    else.  Resolving them per step keeps a variable declared in an unrelated job
    from satisfying the image check.
    """
    workflow_env = document.get("env")
    workflow_env = workflow_env if isinstance(workflow_env, dict) else {}
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return []
    found: list[tuple[dict, str]] = []
    for job in jobs.values():
        found.extend(_job_step_envs(job, workflow_env))
    return found


def _step_environments(content: str) -> list[tuple[set[str], str]]:
    """Return (visible env names, run script) for every step in a workflow."""
    document = _workflow_document(content)
    if not document:
        return []
    return [
        ({str(key) for key in mapping}, run)
        for mapping, run in _step_visible_envs(document)
    ]


def _job_step_envs(job: object, inherited: dict) -> list[tuple[dict, str]]:
    """Return (visible env mapping, run script) for each step of one job."""
    if not isinstance(job, dict):
        return []
    job_env = _job_env(job, inherited)
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    found: list[tuple[dict, str]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        visible = dict(job_env)
        if isinstance(step.get("env"), dict):
            visible.update(step["env"])
        run = step.get("run")
        if isinstance(run, str):
            found.append((visible, run))
    return found



def _tag_variables(tag: str) -> list[str] | None:
    """Return the names a tag interpolates, or None when a `$` is not an exact
    `${NAME}` token.

    A bare `$NAME` and a doubled `${{NAME}}` are both valid GitHub expressions,
    so a token this pattern does not recognise has to count as unresolvable
    rather than being ignored.
    """
    names = re.findall(r"\$\{([A-Za-z0-9_]+)\}", tag)
    if "$" in re.sub(r"\$\{[A-Za-z0-9_]+\}", "", tag):
        return None
    return names


def _step_tag_errors(path: Path, visible: set[str], run: str, exact: str) -> list[str]:
    """Return the complaints for the Rust image tags one step script names."""
    errors: list[str] = []
    for tag in sorted(set(RUST_IMAGE_RE.findall(run))):
        complaint = _image_tag_error(path, tag, exact, visible)
        if complaint is not None:
            errors.append(complaint)
    return errors


def _image_tag_error(
    path: Path, tag: str, exact: str, declared: set[str]
) -> str | None:
    """Return the complaint about one image tag, or None when it is fine."""
    if "$" in tag:
        names = _tag_variables(tag)
        if names is None:
            return (
                f"{path}: Rust image tag {tag!r} interpolates a value this check "
                f"cannot resolve ({exact!r} expected)"
            )
        # The version position has to be the variable this check reads, and any
        # other interpolation has to be a declared value: an undeclared one
        # could stand in for a different version.  A declared suffix such as an
        # Alpine release is fine, because it does not carry the Rust version.
        if (
            not names
            or names[0] != "RUST_VERSION"
            or not tag.startswith("${RUST_VERSION}")
            or any(name not in declared for name in names)
        ):
            return (
                f"{path}: Rust image tag {tag!r} interpolates a value this check "
                f"cannot resolve ({exact!r} expected)"
            )
        return None

    version = _image_version(tag)
    if version is None:
        return (
            f"{path}: Rust image tag {tag!r} carries no exact MAJOR.MINOR.PATCH "
            f"version ({exact!r} expected)"
        )
    if version != exact:
        return (
            f"{path}: Rust image tag {tag!r} pins {version!r} but "
            f"rust-toolchain.toml declares {exact!r}"
        )
    return None


def _check_rust_container_images(root: Path, exact: str, errors: list[str]) -> None:
    """Check every way a workflow can pin the Rust version for a container.

    A workflow that installs Rust inside an image never declares an action
    toolchain, so the inventory check above cannot see it.  Two forms remain: a
    container image tag, and a `RUST_VERSION` variable that the tag interpolates.
    The version part of a tag must equal the canonical version; an operating
    system suffix such as `-alpine3.21` is not a version and does not matter.
    """
    workflows = Path(".github/workflows")
    for path in sorted((root / workflows).glob("*.y*ml")):
        content = path.read_text(encoding="utf-8")
        document = _workflow_document(content)
        # The parsed document is authoritative: a line pattern would also match
        # a `RUST_VERSION:` that is not an environment declaration at all.
        declared_values = set(_declared_version_values(document))
        for declared in sorted(declared_values):
            if declared != exact:
                errors.append(
                    f"{workflows / path.name}: RUST_VERSION is {declared!r} but "
                    f"rust-toolchain.toml declares {exact!r}"
                )
        # A job may run the module inside its own container, or a service may,
        # so those images are checked against the environment of that job.
        for image, visible in _container_images(document):
            errors.extend(
                _step_tag_errors(workflows / path.name, visible, image, exact)
            )
        # Each interpolation is judged against the environment visible where it
        # appears, so a name declared in an unrelated job or step cannot satisfy
        # the check.
        for visible, run in _step_environments(content):
            errors.extend(
                _step_tag_errors(workflows / path.name, visible, run, exact)
            )


def _check_workflow_inventory(root: Path, errors: list[str]) -> None:
    """Reject newly added Rust-installing workflows outside the frozen policy."""
    workflow_dir = root / ".github" / "workflows"
    known = set(BASELINE_ACTION_WORKFLOWS + NIGHTLY_ACTION_WORKFLOWS)
    known.update(OBSERVATION_ACTION_WORKFLOWS)
    known.update(RELEASE_WORKFLOWS)
    for path in sorted(workflow_dir.glob("*.y*ml")):
        relative_path = path.relative_to(root)
        content = _read_text(root, relative_path, errors)
        if content is None:
            continue
        installs_rust = "dtolnay/rust-toolchain" in content or "RUST_TOOLCHAIN:" in content
        if installs_rust and relative_path not in known:
            errors.append(
                f"{relative_path}: Rust-installing workflow is not classified by "
                "check_rust_baseline.py"
            )


def _check_release_dockerfiles(root: Path, errors: list[str]) -> None:
    """Ensure release images consume the canonical repository toolchain file."""
    required = ("COPY rust-toolchain.toml", "rustup toolchain install")
    for relative_path in RELEASE_DOCKERFILES:
        content = _read_text(root, relative_path, errors)
        if content is None:
            continue
        for snippet in required:
            if snippet not in content:
                errors.append(
                    f"{relative_path}: release compiler must consume the canonical "
                    f"rust-toolchain.toml (missing {snippet!r})"
                )


def _check_current_docs(
    root: Path, exact: str, expected_msrv: str, errors: list[str]
) -> None:
    """Check active build documentation without inspecting historical records."""
    for relative_path in CURRENT_BUILD_DOCS:
        content = _read_text(root, relative_path, errors)
        if content is None:
            continue
        if exact not in content and f"MSRV {expected_msrv}" not in content:
            errors.append(
                f"{relative_path}: current build documentation must mention Rust "
                f"{exact} or MSRV {expected_msrv}"
            )


def collect_errors(root: Path = REPO_ROOT) -> tuple[str | None, str | None, list[str]]:
    """Collect Rust baseline contract violations for *root*."""
    errors: list[str] = []
    versions = _load_canonical_versions(root, errors)
    if versions is None:
        return None, None, errors
    exact, msrv = versions

    _check_manifests(root, msrv, errors)
    _check_workflow_group(
        root,
        BASELINE_ACTION_WORKFLOWS,
        ACTION_TOOLCHAIN_RE,
        exact,
        "toolchain",
        errors,
    )
    _check_workflow_group(
        root,
        NIGHTLY_ACTION_WORKFLOWS,
        ACTION_TOOLCHAIN_RE,
        "nightly",
        "nightly toolchain",
        errors,
    )
    _check_observation_workflows(root, exact, errors)
    _check_workflow_group(
        root,
        RELEASE_WORKFLOWS,
        RELEASE_TOOLCHAIN_RE,
        exact,
        "RUST_TOOLCHAIN",
        errors,
    )
    _check_workflow_inventory(root, errors)
    _check_rust_container_images(root, exact, errors)
    _check_release_dockerfiles(root, errors)
    _check_current_docs(root, exact, msrv, errors)
    return exact, msrv, errors


def main() -> int:
    """Validate the checked-in Rust baseline contract."""
    exact, msrv, errors = collect_errors()
    if errors:
        print("Rust baseline consistency check FAILED:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"Rust baseline consistency check PASSED: toolchain={exact}, MSRV={msrv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
