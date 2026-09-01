#!/usr/bin/env python3
"""Validate that GitHub Actions workflow files are consistent with release-matrix.json.

Ensures that:
  1. Canonical release workflows (release-packages, release-binaries, install-verify)
     dynamically read their matrix from tools/release-matrix.json (no hardcoded versions).
  2. Any hardcoded NGINX versions found in workflow files exist in release-matrix.json.
  3. Legacy/non-canonical workflows that hardcode versions are flagged as warnings
     (not errors) when the version still exists in the matrix.

Subset filters (e.g., only stable, only glibc) are allowed and documented in
release-matrix.json's owner_workflow fields.

Exit code 0 = consistent, exit code 1 = inconsistencies found.

Usage:
    python3 tools/release/matrix/validate_workflow_matrix_consumers.py

Part of release matrix source of truth (Requirement 5).
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.path_validation import validate_read_path
from official_docker_matrix import load_official_docker_entries

# Paths relative to the repository root
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
MATRIX_PATH = REPO_ROOT / "tools" / "release-matrix.json"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# Canonical workflows that MUST read from release-matrix.json dynamically.
# These should not hardcode NGINX version lists.
CANONICAL_DYNAMIC_WORKFLOWS = {
    "release-packages.yml",
    "release-binaries.yml",
    "install-verify.yml",
}

# Workflows explicitly marked as legacy/non-canonical.
# Hardcoded versions here produce warnings, not errors.
LEGACY_WORKFLOWS = {
    "release-rpm.yml",
}

RELEASE_PACKAGES_WORKFLOW = "release-packages.yml"
OFFICIAL_DOCKER_WORKFLOW = ".github/workflows/official-nginx-docker.yml"
OFFICIAL_DOCKER_WORKFLOW_REF = "./.github/workflows/official-nginx-docker.yml"

# Candidate semantic versions are classified as NGINX versions only when the
# same workflow line explicitly associates them with NGINX. This avoids numeric
# range guesses that eventually misclassify Rust, Python, or tool releases.
NGINX_VERSION_RE = re.compile(r"(?<![0-9.])\d+\.\d+\.\d+(?![0-9.])")
NGINX_CONTEXT_RE = re.compile(r"\bnginx\b|\bnginx[_-]", re.IGNORECASE)
NGINX_BLOCK_KEY_RE = re.compile(
    r"^(?P<indent>\s*)nginx(?:[_-]versions?)?\s*:", re.IGNORECASE
)

# Patterns that indicate dynamic matrix resolution from release-matrix.json
DYNAMIC_RESOLUTION_PATTERNS = [
    "tools/release-matrix.json",
    "release-matrix.json",
]

# Patterns to exclude from version detection (comments, descriptions, examples)
EXCLUDE_CONTEXT_PATTERNS = [
    r"^\s*#",           # YAML comments
    r"description:",    # Input descriptions
    r"e\.g\.",          # Example mentions
    r"Example:",        # Example labels
    r"# Example:",      # Comment examples
    r"Floor\s*=",       # Code comment about version math
]


def _canonical_entries(data: object) -> tuple[list[dict], str | None]:
    """
    Validate and return the matrix document's canonical entries.

    Parameters:
        data (object): Matrix data to validate.

    Returns:
        tuple[list[dict], str | None]: The non-empty canonical entries list and
        `None` when valid; otherwise, an empty list and a validation error message.
    """
    if not isinstance(data, dict):
        return [], "Matrix document root must be an object"
    aliases = [key for key in ("matrix", "additional_artifacts") if key in data]
    if aliases:
        return [], (
            "Matrix document must carry the canonical 'entries' list; "
            "legacy keys are not accepted: "
            + ", ".join(aliases)
        )
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        return [], "Matrix file must carry a non-empty canonical 'entries' list"
    if not all(isinstance(entry, dict) for entry in entries):
        return [], "Matrix canonical 'entries' must contain only objects"
    for index, entry in enumerate(entries):
        for field in ("nginx", "nginx_version"):
            if field in entry and not isinstance(entry[field], str):
                return [], (
                    f"Matrix entry {index} field {field!r} must be a string"
                )
    return entries, None


def load_matrix_versions(path: Path) -> set[str]:
    """Load all NGINX versions from release-matrix.json.

    Returns the set of all NGINX version strings in canonical ``entries``.
    """
    validated = validate_read_path(path, purpose="workflow matrix validation")
    with open(validated, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries, error = _canonical_entries(data)
    if error is not None:
        raise ValueError(error)

    versions: set[str] = set()
    for entry in entries:
        if v := entry.get("nginx"):
            versions.add(v)
        if v := entry.get("nginx_version"):
            versions.add(v)

    return versions


def _is_excluded_line(line: str) -> bool:
    """Check if a line should be excluded from version detection."""
    return any(re.search(pat, line) for pat in EXCLUDE_CONTEXT_PATTERNS)


def _uses_dynamic_resolution(content: str) -> bool:
    """Check if workflow content reads matrix from release-matrix.json."""
    return any(pattern in content for pattern in DYNAMIC_RESOLUTION_PATTERNS)


def _nginx_context_for_line(
    line: str, nginx_block_indent: int | None
) -> tuple[bool, int | None]:
    """Return whether *line* is NGINX-scoped and the updated YAML block indent."""
    stripped = line.strip()
    indent = len(line) - len(line.lstrip())
    block_match = NGINX_BLOCK_KEY_RE.match(line)
    if block_match is not None:
        nginx_block_indent = len(block_match.group("indent"))
    elif (
        stripped
        and not stripped.startswith("#")
        and nginx_block_indent is not None
        and indent <= nginx_block_indent
    ):
        nginx_block_indent = None

    explicit_context = NGINX_CONTEXT_RE.search(line) is not None
    nested_context = nginx_block_indent is not None and indent > nginx_block_indent
    return explicit_context or nested_context, nginx_block_indent


def extract_hardcoded_versions(content: str) -> list[tuple[int, str, str]]:
    """Extract hardcoded NGINX version references from workflow content.

    Returns a list of (line_number, version_string, line_content) tuples
    for versions that appear to be hardcoded NGINX version references
    (not in excluded contexts like comments/descriptions/examples).
    """
    found: list[tuple[int, str, str]] = []
    nginx_block_indent: int | None = None

    for lineno, line in enumerate(content.splitlines(), start=1):
        in_nginx_context, nginx_block_indent = _nginx_context_for_line(
            line, nginx_block_indent
        )
        if _is_excluded_line(line) or not in_nginx_context:
            continue

        found.extend(
            (lineno, match.group(0), line.strip())
            for match in NGINX_VERSION_RE.finditer(line)
        )

    return found


def validate_canonical_workflows() -> tuple[list[str], list[str]]:
    """Validate canonical workflows use dynamic resolution.

    Returns (errors, warnings) lists.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for wf_name in sorted(CANONICAL_DYNAMIC_WORKFLOWS):
        wf_path = WORKFLOWS_DIR / wf_name
        if not wf_path.exists():
            warnings.append(f"Canonical workflow not found: {wf_name}")
            continue

        content = wf_path.read_text(encoding="utf-8")

        if not _uses_dynamic_resolution(content):
            errors.append(
                f"Canonical workflow {wf_name} does not reference "
                f"release-matrix.json for dynamic matrix resolution"
            )
            continue

        # Even though dynamic, canonical workflows must not hardcode any
        # NGINX version — all versions must come from the matrix.
        hardcoded = extract_hardcoded_versions(content)
        errors.extend(
            f"{wf_name}:{lineno}: canonical workflow must not hardcode "
            f"version '{version}' (line: {line_ctx!r})"
            for lineno, version, line_ctx in hardcoded
        )
    return errors, warnings


def validate_legacy_workflows(
    matrix_versions: set[str],
) -> tuple[list[str], list[str]]:
    """Validate legacy workflows reference only known matrix versions.

    Legacy workflows may hardcode versions (they are retained for
    compatibility), but those versions must still exist in the matrix.
    Unknown versions are errors; known versions produce informational warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for wf_name in sorted(LEGACY_WORKFLOWS):
        wf_path = WORKFLOWS_DIR / wf_name
        if not wf_path.exists():
            continue

        content = wf_path.read_text(encoding="utf-8")
        hardcoded = extract_hardcoded_versions(content)

        for lineno, version, line_ctx in hardcoded:
            if version not in matrix_versions:
                errors.append(
                    f"{wf_name}:{lineno}: legacy workflow references "
                    f"version '{version}' not in release-matrix.json "
                    f"(line: {line_ctx!r})"
                )
            else:
                warnings.append(
                    f"{wf_name}:{lineno}: legacy workflow hardcodes "
                    f"version '{version}' (subset of matrix — OK)"
                )

    return errors, warnings


def validate_other_workflows(
    matrix_versions: set[str],
) -> tuple[list[str], list[str]]:
    """Check remaining workflows for hardcoded versions not in the matrix.

    Workflows not classified as canonical or legacy are scanned for
    hardcoded version references. Versions not in the matrix are errors.
    """
    errors: list[str] = []
    warnings: list[str] = []

    known_workflows = CANONICAL_DYNAMIC_WORKFLOWS | LEGACY_WORKFLOWS

    if not WORKFLOWS_DIR.exists():
        return errors, warnings

    for wf_path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        if wf_path.name in known_workflows:
            continue

        content = wf_path.read_text(encoding="utf-8")
        hardcoded = extract_hardcoded_versions(content)

        errors.extend(
            f"{wf_path.name}:{lineno}: references version '{version}' not in release-matrix.json (line: {line_ctx!r})"
            for lineno, version, line_ctx in hardcoded
            if version not in matrix_versions
        )
    return errors, warnings


def validate_owner_workflow_refs(matrix_path: Path) -> list[str]:
    """
    Verify that matrix `owner_workflow` references point to existing files.

    Parameters:
        matrix_path (Path): Path to the release matrix file.

    Returns:
        list[str]: Validation errors for missing referenced workflows.
    """
    errors: list[str] = []

    validated = validate_read_path(matrix_path, purpose="owner workflow check")
    with open(validated, "r", encoding="utf-8") as f:
        data = json.load(f)

    all_entries, error = _canonical_entries(data)
    if error is not None:
        errors.append(error)
        return errors

    for i, entry in enumerate(all_entries):
        wf = entry.get("owner_workflow", "")
        if not wf:
            continue

        wf_path = REPO_ROOT / wf
        if not wf_path.exists():
            errors.append(
                f"Matrix entry {i}: owner_workflow '{wf}' does not exist"
            )

    return errors


def _release_blocking_docker_owners(entries: list[dict]) -> set[str]:
    """Return the owner workflows for release-blocking Docker image entries.

    Parameters:
        entries (list[dict]): Matrix entries to inspect.

    Returns:
        set[str]: Owner workflow paths referenced by release-blocking Docker image entries.
    """
    return {
        entry.get("owner_workflow", "")
        for entry in entries
        if entry.get("artifact_type") == "docker-image"
        and entry.get("release_blocking") is True
        and entry.get("owner_workflow", "")
    }


def _validate_official_docker_gate(
    canonical_content: str, publish_needs: set[str]
) -> list[str]:
    """
    Validate the release package workflow's official Docker release gate and publish dependency.

    Parameters:
        canonical_content (str): Contents of the canonical release package workflow.
        publish_needs (set[str]): Job identifiers that the publish job depends on.

    Returns:
        list[str]: Validation error messages.
    """
    errors: list[str] = []
    official_job = _workflow_job_block(
        canonical_content, "official-docker-release-gate"
    )
    if official_job is None:
        errors.append(
            "release-packages.yml does not define "
            "official-docker-release-gate for release-blocking Docker artifacts"
        )
    elif _job_uses(official_job) != OFFICIAL_DOCKER_WORKFLOW_REF:
        errors.append(
            "release-packages.yml official-docker-release-gate must use "
            f"{OFFICIAL_DOCKER_WORKFLOW_REF!r}, got "
            f"{_job_uses(official_job)!r}"
        )
    if "official-docker-release-gate" not in publish_needs:
        errors.append(
            "release-packages.yml publish job does not depend on "
            "official-docker-release-gate"
        )
    return errors


def _validate_docker_owner_workflows(owners: set[str]) -> list[str]:
    """
    Validate that referenced Docker owner workflows expose a top-level workflow_call trigger.

    Parameters:
        owners (set[str]): Repository-relative paths to Docker owner workflow files.

    Returns:
        list[str]: Error messages for existing owner workflows that do not expose workflow_call.
    """
    errors: list[str] = []
    for owner in sorted(owners):
        owner_path = REPO_ROOT / owner
        if not owner_path.exists():
            continue
        owner_content = owner_path.read_text(encoding="utf-8")
        if not _has_top_level_workflow_call(owner_content):
            errors.append(
                f"{owner} must expose workflow_call before it can be a "
                "release-blocking reusable Docker gate"
            )
    return errors


def validate_release_blocking_publish_dag(matrix_path: Path) -> list[str]:
    """
    Validate that release-blocking Docker artifacts are gated before canonical publication.

    Parameters:
        matrix_path (Path): Path to the release matrix file.

    Returns:
        list[str]: Validation errors, or an empty list when the publication DAG is valid.
    """
    validated = validate_read_path(
        matrix_path, purpose="release-blocking publish DAG check"
    )
    with open(validated, "r", encoding="utf-8") as f:
        data = json.load(f)

    entries, error = _canonical_entries(data)
    if error is not None:
        return [error]

    docker_owners = _release_blocking_docker_owners(entries)
    if not docker_owners:
        return []

    canonical_path = WORKFLOWS_DIR / RELEASE_PACKAGES_WORKFLOW
    if not canonical_path.exists():
        return [
            "Release-blocking Docker entries exist but the canonical "
            f"workflow is missing: {canonical_path}"
        ]
    canonical_content = canonical_path.read_text(encoding="utf-8")
    errors = _validate_official_docker_gate(
        canonical_content, _publish_job_needs(canonical_content)
    )
    errors.extend(_validate_docker_owner_workflows(docker_owners))
    return errors


def _workflow_job_block(content: str, job_name: str) -> list[str] | None:
    """Return one top-level job block using bounded indentation parsing."""
    lines = content.splitlines()
    start = None
    for index, line in enumerate(lines):
        if len(line) - len(line.lstrip(" ")) == 2 and line.strip() == f"{job_name}:":
            start = index
            break
    if start is None:
        return None

    block = [lines[start]]
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            indent = len(line) - len(line.lstrip(" "))
            if indent <= 2:
                break
        block.append(line)
    return block


def _job_uses(job_block: list[str]) -> str | None:
    """Return the exact reusable-workflow reference from a job block."""
    for line in job_block[1:]:
        if len(line) - len(line.lstrip(" ")) != 4:
            continue
        key, separator, value = line.strip().partition(":")
        if key == "uses" and separator:
            return value.strip().strip("'\"")
    return None


OFFICIAL_DOCKER_RUNNER = (
    "${{ matrix.arch == 'arm64' && 'ubuntu-24.04-arm' || 'ubuntu-latest' }}"
)
OFFICIAL_DOCKER_MATRIX_INCLUDE = "${{ fromJson(needs.prepare.outputs.matrix) }}"


def _workflow_document(content: str) -> tuple[dict[str, object] | None, str | None]:
    """Parse a workflow and return its mapping root or a validation error."""
    try:
        document = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        return None, f"official Docker workflow is not valid YAML: {exc}"
    if not isinstance(document, dict):
        return None, "official Docker workflow root must be a mapping"
    if not isinstance(document.get("jobs"), dict):
        return None, "official Docker workflow must define a jobs mapping"
    return document, None


def _workflow_step(job: object, name: str | None = None, step_id: str | None = None) -> dict[str, object] | None:
    """Find a named or identified executable step in a parsed job."""
    if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
        return None
    for step in job["steps"]:
        if not isinstance(step, dict):
            continue
        if name is not None and step.get("name") == name:
            return step
        if step_id is not None and step.get("id") == step_id:
            return step
    return None


def _python_heredoc(run: object) -> str | None:
    """Return the Python body from the resolver step's quoted heredoc."""
    if not isinstance(run, str):
        return None
    lines = run.splitlines()
    try:
        start = next(
            index for index, line in enumerate(lines)
            if line.strip() == "python3 - <<'PY'"
        )
        end = next(
            index for index in range(start + 1, len(lines))
            if lines[index].strip() == "PY"
        )
    except StopIteration:
        return None
    return "\n".join(lines[start + 1 : end])


def _has_matrix_loader_code(run: object) -> bool:
    """Require the executable resolver script to load the canonical matrix."""
    source = _python_heredoc(run)
    if source is None:
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return _has_matrix_loader_import(tree) and _has_matrix_loader_call(tree)


def _has_matrix_loader_import(tree: ast.AST) -> bool:
    """Return whether the resolver imports the canonical loader."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "official_docker_matrix":
            continue
        return any(
            alias.name == "load_official_docker_entries" for alias in node.names
        )
    return False


def _is_matrix_path_call(node: ast.AST) -> bool:
    """Return whether an AST call points to the canonical matrix file."""
    if not isinstance(node, ast.Call):
        return False
    if not isinstance(node.func, ast.Name) or node.func.id != "Path":
        return False
    if len(node.args) != 1:
        return False
    argument = node.args[0]
    return isinstance(argument, ast.Constant) and argument.value == (
        "tools/release-matrix.json"
    )


def _has_matrix_loader_call(tree: ast.AST) -> bool:
    """Return whether the resolver loads the canonical matrix path."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "load_official_docker_entries" or len(node.args) != 1:
            continue
        return _is_matrix_path_call(node.args[0])
    return False


def _build_args(value: object) -> dict[str, str]:
    """Parse the bounded key/value build-argument block."""
    if not isinstance(value, str):
        return {}
    result: dict[str, str] = {}
    for line in value.splitlines():
        key, separator, argument = line.partition("=")
        if separator:
            result[key] = argument
    return result


def _validate_prepare_job(jobs: dict[str, object]) -> list[str]:
    """Validate the executable matrix resolver job."""
    errors: list[str] = []
    prepare = jobs.get("prepare")
    if not isinstance(prepare, dict):
        return ["official Docker workflow is missing the prepare job"]
    outputs = prepare.get("outputs")
    if not isinstance(outputs, dict) or outputs.get("matrix") != "${{ steps.resolve.outputs.matrix }}":
        errors.append("prepare job must expose the resolve matrix output")
    resolve_step = _workflow_step(prepare, step_id="resolve")
    if resolve_step is None or not _has_matrix_loader_code(resolve_step.get("run")):
        errors.append("prepare job must execute the canonical Docker matrix resolver")
    return errors


def _validate_build_step(build: dict[str, object]) -> list[str]:
    """Validate that the official Docker build step uses matrix-bound tags and required build arguments.

    Parameters:
        build (dict[str, object]): The parsed `build-and-verify` job definition.

    Returns:
        list[str]: Validation error messages, or an empty list when the build step is valid.
    """
    build_step = _workflow_step(
        build, name="Build from source on official nginx base"
    )
    if build_step is None:
        return ["build-and-verify is missing the official Docker build step"]
    with_values = build_step.get("with")
    if not isinstance(with_values, dict):
        return ["official Docker build step must define its inputs"]
    errors: list[str] = []
    if with_values.get("tags") != "nginx-markdown-official-check:${{ matrix.docker_tag }}":
        errors.append("official Docker build tag must use matrix.docker_tag")
    build_args = _build_args(with_values.get("build-args"))
    required_args = {
        "NGINX_IMAGE": "${{ matrix.image_ref }}@${{ matrix.image_digest }}",
        "MODULE_REPO": "${{ steps.source.outputs.repo }}",
        "MODULE_REF": "${{ steps.source.outputs.ref }}",
        "MODULE_SHA": "${{ steps.source.outputs.sha }}",
    }
    for key, expected in required_args.items():
        if build_args.get(key) != expected:
            errors.append(f"official Docker build args must preserve {key}")
    return errors


def _validate_verify_step(build: dict[str, object]) -> list[str]:
    """Validate the matrix-bound runtime verification step."""
    verify_step = _workflow_step(build, name="Verify runtime behavior")
    if verify_step is None:
        return ["build-and-verify is missing the runtime verification step"]
    errors: list[str] = []
    run = verify_step.get("run")
    if not isinstance(run, str) or not run.lstrip().startswith(
        "bash ./tools/ci/verify_official_nginx_docker.sh"
    ):
        errors.append("runtime verification must execute the official Docker verifier")
    expected_env = {
        "IMAGE_NAME": "nginx-markdown-official-check:${{ matrix.docker_tag }}",
        "JOB_NGINX_TAG": "${{ matrix.image_ref }}",
        "EXPECTED_NGINX_VERSION": "${{ matrix.nginx_version }}",
        "IMAGE_REFERENCE": "${{ matrix.image_ref }}",
        "IMAGE_DIGEST": "${{ matrix.image_digest }}",
        "MATRIX_ROW_ID": "${{ matrix.matrix_row_id }}",
        "MATRIX_OS": "${{ matrix.os }}",
        "MATRIX_LIBC": "${{ matrix.libc }}",
        "MATRIX_ARCH": "${{ matrix.arch }}",
    }
    env = verify_step.get("env")
    for key, expected in expected_env.items():
        if not isinstance(env, dict) or env.get(key) != expected:
            errors.append(f"runtime verification must bind {key} from the matrix")
    return errors


def _validate_build_job(jobs: dict[str, object]) -> list[str]:
    """
    Validate the official Docker workflow's build and verification job configuration.

    Parameters:
        jobs (dict[str, object]): Parsed workflow jobs keyed by job name.

    Returns:
        list[str]: Validation errors found in the build-and-verify job.
    """
    errors: list[str] = []
    build = jobs.get("build-and-verify")
    if not isinstance(build, dict):
        return ["official Docker workflow is missing the build-and-verify job"]
    if build.get("runs-on") != OFFICIAL_DOCKER_RUNNER:
        errors.append("build-and-verify must select runners from matrix.arch")
    strategy = build.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if not isinstance(matrix, dict) or matrix.get("include") != OFFICIAL_DOCKER_MATRIX_INCLUDE:
        errors.append("build-and-verify must consume prepare.outputs.matrix")
    errors.extend(_validate_build_step(build))
    errors.extend(_validate_verify_step(build))
    return errors


def _validate_official_docker_workflow(document: dict[str, object]) -> list[str]:
    """Validate the official Docker workflow's required jobs and their matrix, build, and verification configuration.

    Parameters:
        document (dict[str, object]): Parsed official Docker workflow document.

    Returns:
        list[str]: Validation error messages, or an empty list when the workflow is valid.
    """
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return ["official Docker workflow must define a jobs mapping"]
    errors = _validate_prepare_job(jobs)
    errors.extend(_validate_build_job(jobs))
    return errors


def validate_official_docker_matrix_coverage(matrix_path: Path) -> list[str]:
    """
    Ensure the official Docker workflow covers all configured release-blocking Docker matrix rows.

    Parameters:
        matrix_path (Path): Path to the Docker release matrix.

    Returns:
        list[str]: Validation errors, or an empty list when the matrix and workflow are valid.
    """
    errors: list[str] = []
    try:
        entries = load_official_docker_entries(matrix_path)
    except (OSError, ValueError) as exc:
        return [f"official Docker matrix cannot be resolved: {exc}"]

    workflow_path = WORKFLOWS_DIR / Path(OFFICIAL_DOCKER_WORKFLOW).name
    if not workflow_path.exists():
        return [f"official Docker workflow is missing: {workflow_path}"]
    content = workflow_path.read_text(encoding="utf-8")
    document, parse_error = _workflow_document(content)
    if parse_error is not None:
        errors.append(parse_error)
    elif document is not None:
        errors.extend(_validate_official_docker_workflow(document))

    expected_ids: set[str] = set()
    accepted_rows = 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(
                "official Docker matrix row "
                f"{index} must be an object"
            )
            continue
        row_id = entry.get("matrix_row_id")
        if not isinstance(row_id, str) or not row_id:
            errors.append(
                "official Docker matrix row "
                f"{index} is missing a non-empty matrix_row_id"
            )
            continue
        expected_ids.add(row_id)
        accepted_rows += 1
    if len(expected_ids) != accepted_rows:
        errors.append("official Docker matrix contains duplicate execution rows")
    if not expected_ids:
        errors.append("official Docker matrix contains no release-blocking rows")
    return errors


def _publish_job_needs(content: str) -> set[str]:
    """Return scalar, flow-sequence, or block-sequence ``needs`` entries.

    The workflow validator only needs the publish job's dependency names.
    Walking the small YAML structure directly keeps the check bounded and
    avoids a backtracking expression over the complete workflow document.
    """
    publish_indent = 2
    in_publish = False

    lines = content.splitlines()
    for index, line in enumerate(lines):
        parts = _publish_line_parts(line)
        if parts is None:
            continue
        indent, stripped = parts

        if not in_publish:
            in_publish = _is_publish_job(indent, stripped, publish_indent)
            continue
        if indent <= publish_indent:
            return set()
        if indent != publish_indent + 2:
            continue

        needs_value = _needs_value(stripped)
        if needs_value is not None:
            if needs_value:
                return _parse_inline_needs(needs_value)
            return _parse_block_needs(lines, index, indent)

    return set()


def _publish_line_parts(line: str) -> tuple[int, str] | None:
    """Return indentation and content for a meaningful workflow line."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    return len(line) - len(line.lstrip(" ")), stripped


def _is_publish_job(indent: int, stripped: str, publish_indent: int) -> bool:
    """Return whether a line starts the top-level ``publish`` job."""
    return indent == publish_indent and stripped == "publish:"


def _needs_value(stripped: str) -> str | None:
    """Return the inline value for a job-level ``needs`` key."""
    key, separator, value = stripped.partition(":")
    if key != "needs" or not separator:
        return None
    return value.strip()


def _parse_inline_needs(value: str) -> set[str]:
    """Parse a scalar or bounded flow-sequence dependency value."""
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
        entries = value.split(",")
    elif value.startswith("[") or value.endswith("]"):
        return set()
    else:
        entries = [value]

    return {
        entry.strip().strip("'\"")
        for entry in entries
        if entry.strip().strip("'\"")
    }


def _needs_block_step(
    indent: int, stripped: str, needs_indent: int, item_indent: int | None
) -> tuple[str, str, int | None]:
    """Classify one candidate line inside a ``needs:`` block.

    Returns ``(action, value, item_indent)`` where ``action`` is
    ``"break"`` (the block ended cleanly), ``"invalid"`` (the block is
    inconsistent and must be rejected), or ``"item"`` (``value`` carries
    the dependency name).  ``item_indent`` echoes the accepted item
    indentation back to the caller.
    """
    if indent < needs_indent:
        return "break", "", item_indent
    # YAML sequence items start with "- " (dash, whitespace).  A bare dash
    # glued to content ("-build") is a scalar, not a sequence item.
    if not re.match(r"^-[ \t]", stripped):
        if indent <= needs_indent:
            # A sibling mapping key ends the needs block.
            return "break", "", item_indent
        return "invalid", "", item_indent
    if item_indent is None:
        item_indent = indent
    if indent != item_indent:
        return "invalid", "", item_indent
    value = stripped[2:].strip().strip("'\"")
    if not value:
        return "invalid", "", item_indent
    return "item", value, item_indent


def _parse_block_needs(
    lines: list[str], needs_line_index: int, needs_indent: int
) -> set[str]:
    """Parse a YAML block sequence immediately below ``needs:``.

    Sequence items may sit at any indentation deeper than the ``needs:``
    key, including the same indentation as the key itself.  The first
    item fixes the block's item indentation and every remaining item must
    match it exactly; an inconsistent block is rejected as unparsable.
    """
    dependencies: set[str] = set()
    item_indent: int | None = None
    for line in lines[needs_line_index + 1 :]:
        parts = _publish_line_parts(line)
        if parts is None:
            continue
        indent, stripped = parts
        action, value, item_indent = _needs_block_step(
            indent, stripped, needs_indent, item_indent
        )
        if action == "break":
            break
        if action == "invalid":
            return set()
        dependencies.add(value)
    return dependencies


def _has_top_level_workflow_call(content: str) -> bool:
    """Return whether a reusable workflow declares its call entry point."""
    return any(
        len(line) - len(line.lstrip(" ")) == 2
        and line.strip() == "workflow_call:"
        for line in content.splitlines()
    )


def main() -> int:
    """Run workflow matrix consumer validation.

    Returns 0 on success, 1 if errors found.
    """
    if not MATRIX_PATH.exists():
        print(f"ERROR: Matrix file not found: {MATRIX_PATH}", file=sys.stderr)
        return 1

    if not WORKFLOWS_DIR.exists():
        print(
            f"ERROR: Workflows directory not found: {WORKFLOWS_DIR}",
            file=sys.stderr,
        )
        return 1

    try:
        matrix_versions = load_matrix_versions(MATRIX_PATH)
    except (OSError, ValueError) as exc:
        print(f"ERROR: Invalid release-matrix.json: {exc}", file=sys.stderr)
        return 1
    if not matrix_versions:
        print(
            "ERROR: No NGINX versions found in release-matrix.json",
            file=sys.stderr,
        )
        return 1

    all_errors: list[str] = []
    all_warnings: list[str] = []

    # 1. Canonical workflows must use dynamic resolution
    errors, warnings = validate_canonical_workflows()
    all_errors.extend(errors)
    all_warnings.extend(warnings)

    # 2. Legacy workflows must reference known versions
    errors, warnings = validate_legacy_workflows(matrix_versions)
    all_errors.extend(errors)
    all_warnings.extend(warnings)

    # 3. Other workflows should not hardcode unknown versions
    errors, warnings = validate_other_workflows(matrix_versions)
    all_errors.extend(errors)
    all_warnings.extend(warnings)

    # 4. owner_workflow references in matrix point to real files
    owner_errors = validate_owner_workflow_refs(MATRIX_PATH)
    all_errors.extend(owner_errors)

    # 5. release-blocking Docker artifacts must be in the publish DAG
    docker_dag_errors = validate_release_blocking_publish_dag(MATRIX_PATH)
    all_errors.extend(docker_dag_errors)

    # 6. Every blocking Docker row must be represented by the reusable gate's
    # generated execution contract.
    docker_matrix_errors = validate_official_docker_matrix_coverage(MATRIX_PATH)
    all_errors.extend(docker_matrix_errors)

    # Report results
    if all_warnings:
        print("Warnings:", file=sys.stderr)
        for i, warning in enumerate(all_warnings, 1):
            print(f"  {i}. {warning}", file=sys.stderr)
        print(file=sys.stderr)

    if all_errors:
        print(
            "Workflow matrix consumer check FAILED — found inconsistencies "
            "between workflows and release-matrix.json:",
            file=sys.stderr,
        )
        for i, error in enumerate(all_errors, 1):
            print(f"  {i}. {error}", file=sys.stderr)
        return 1

    # Success summary
    print("Workflow matrix consumer check PASSED.")
    print(f"  Matrix NGINX versions: {sorted(matrix_versions)}")
    print(f"  Canonical dynamic workflows: {len(CANONICAL_DYNAMIC_WORKFLOWS)}")
    print(f"  Legacy workflows checked: {len(LEGACY_WORKFLOWS)}")
    print(
        f"  Other workflows scanned: "
        f"{sum(1 for _ in WORKFLOWS_DIR.glob('*.yml')) - len(CANONICAL_DYNAMIC_WORKFLOWS) - len(LEGACY_WORKFLOWS)}"
    )
    if all_warnings:
        print(f"  Warnings: {len(all_warnings)} (non-blocking)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
