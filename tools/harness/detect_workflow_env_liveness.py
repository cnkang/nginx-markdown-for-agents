#!/usr/bin/env python3
"""
detect_workflow_env_liveness.py — Rule 13 extension (ci-gating).

Every $VAR referenced in a workflow run block must be defined in scope:
the same step's env:, the job's env:, the workflow's env:, an earlier
GITHUB_ENV export in the same job, a shell assignment inside the run
block itself, or the known GitHub/runner environment.

Historical issues: 609fff74 (MATRIX_ARCH was step-local and never
exported, so the glibc tarball step read an empty value and always took
the unsupported-architecture branch), deb19efd (empty musl_matrix did
not fail the prepare step).

Usage:
    PYTHONPATH=. python3 tools/harness/detect_workflow_env_liveness.py

Exit codes: 0 clean, 1 violations found.
"""

import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# Variables always considered defined (GitHub/runner/shell contract).
KNOWN_ENV_RE = re.compile(
    r"^(?:GITHUB_[A-Z_]+|RUNNER_[A-Z_]+|ACTIONS_[A-Z_]+|CI|HOME|PATH|"
    r"PWD|OLDPWD|SHELL|USER|LANG|LC_ALL|TMPDIR|TEMP|TMP|SHLVL|IFS|"
    r"PS1|PS4|MAKEFLAGS|MFLAGS|CDPATH|ENV|BASH_ENV|LINENO|OPTARG|"
    r"OPTIND|RANDOM|SECONDS|REPLY|PIPESTATUS|BASH_REMATCH|EUID|UID|"
    r"PPID|HOSTNAME|OSTYPE|MACHTYPE|BASH_SOURCE|BASH_VERSION|FUNCNAME|"
    r"BASHPID|COLUMNS|LINES|HISTFILE|_)$"
)

# Allowlist: "workflow-name:VAR:justification" — substring workflow match.
ALLOWLIST = []

STEP_ID_ENV_RE = re.compile(r"^steps\.([a-zA-Z_][\w-]*)\.outputs\.")


OS_RELEASE_VARS = {
    "NAME", "PRETTY_NAME", "ID", "ID_LIKE", "VERSION_ID",
    "VERSION_CODENAME", "VERSION", "BUILD_ID",
}


def _merge_continuations(run_text):
    """Join backslash-continued lines so multi-line exports stay intact."""
    return run_text.replace("\\\n", " ")


class _ShellMaskState:
    """Mutable state for masking one shell line."""

    def __init__(self, line):
        self.line = line
        self.output = list(line)
        self.contexts = [None]
        self.heredoc_delimiters = []


def _mask_single_quoted_character(state, index):
    character = state.line[index]
    state.output[index] = " " if character != "\n" else "\n"
    if character == "'":
        # Pop the closed quote frame so the enclosing context (command
        # substitution, outer quote) resumes; leaving the frame in place
        # would make the next ) pop the wrong frame and turn the next
        # closing quote into an opening one.
        state.contexts.pop()
    return index + 1


def _mask_double_quoted_character(state, index):
    character = state.line[index]
    if character == "\\" and index + 1 < len(state.line):
        if state.line[index + 1] == "$":
            state.output[index + 1] = " "
        return index + 2
    if (
        character == "$"
        and index + 1 < len(state.line)
        and state.line[index + 1] == "("
    ):
        state.contexts.append(None)
        return index + 2
    if character == '"':
        state.contexts.pop()
    return index + 1


def _mask_quoted_character(state, index):
    if state.contexts[-1] == "'":
        return _mask_single_quoted_character(state, index)
    return _mask_double_quoted_character(state, index)


def _mask_escaped_character(state, index):
    if state.line[index + 1] == "$":
        state.output[index + 1] = " "
    return index + 2


def _is_command_substitution(state, index):
    return (
        state.line[index] == "$"
        and index + 1 < len(state.line)
        and state.line[index + 1] == "("
    )


def _is_comment_start(state, index):
    if index == 0:
        return True
    previous = state.line[index - 1]
    return previous.isspace() or previous in ";|&()<>"


def _mask_comment(state, index):
    for rest in range(index, len(state.line)):
        if state.line[rest] != "\n":
            state.output[rest] = " "


def _skip_heredoc_whitespace(line, index):
    while index < len(line) and line[index] in " \t":
        index += 1
    return index


def _extract_heredoc_delimiter(line, index):
    if index < len(line) and line[index] == "-":
        index += 1
    index = _skip_heredoc_whitespace(line, index)
    if index >= len(line):
        return None
    if line[index] in "'\"":
        quote = line[index]
        end = line.find(quote, index + 1)
        return line[index + 1:end] if end != -1 else None
    end = index
    while end < len(line) and (line[end].isalnum() or line[end] == "_"):
        end += 1
    return line[index:end] if end > index else None


def _record_heredoc_delimiter(state, index):
    delimiter = _extract_heredoc_delimiter(state.line, index + 2)
    if delimiter is not None:
        state.heredoc_delimiters.append(delimiter)


def _mask_unquoted_character(state, index):
    character = state.line[index]
    if character == "\\" and index + 1 < len(state.line):
        return _mask_escaped_character(state, index), False
    if character == "'":
        state.output[index] = " "
        state.contexts.append("'")
        return index + 1, False
    if character == '"':
        state.contexts.append('"')
        return index + 1, False
    if _is_command_substitution(state, index):
        state.contexts.append(None)
        return index + 2, False
    if character == ")" and len(state.contexts) > 1:
        state.contexts.pop()
        return index + 1, False
    if character == "#" and _is_comment_start(state, index):
        _mask_comment(state, index)
        return len(state.line), True
    if (
        character == "<"
        and index + 1 < len(state.line)
        and state.line[index + 1] == "<"
    ):
        _record_heredoc_delimiter(state, index)
    return index + 1, False


def _mask_shell_line(line):
    """Mask shell text where parameter expansion is not performed."""
    state = _ShellMaskState(line)
    index = 0
    while index < len(line):
        if state.contexts[-1] is not None:
            index = _mask_quoted_character(state, index)
            continue
        index, stopped = _mask_unquoted_character(state, index)
        if stopped:
            break
    return "".join(state.output), state.heredoc_delimiters


def _blank_shell_line(line):
    return "".join("\n" if char == "\n" else " " for char in line)


def _consume_heredoc_line(line, pending_heredocs, masked_lines):
    delimiter = pending_heredocs[0]
    terminator = line.rstrip("\r\n")
    if terminator.startswith("\t"):
        terminator = terminator.lstrip("\t")
    if terminator == delimiter:
        pending_heredocs.pop(0)
        masked_lines.append(line)
        return
    masked_lines.append(_blank_shell_line(line))


def _mask_shell_non_expanding(run_text):
    """Mask comments, quoted literals, escaped dollars, and heredoc bodies."""
    masked_lines = []
    pending_heredocs = []
    for line in run_text.splitlines(keepends=True):
        if pending_heredocs:
            _consume_heredoc_line(line, pending_heredocs, masked_lines)
            continue
        masked, heredocs = _mask_shell_line(line)
        masked_lines.append(masked)
        pending_heredocs.extend(heredocs)
    return "".join(masked_lines)


def extract_run_vars(run_text):
    """Return variable names referenced by $VAR / ${VAR} in a run block."""
    refs = set()
    if not isinstance(run_text, str):
        return refs
    masked = _mask_shell_non_expanding(_merge_continuations(run_text))
    # Mask ${{ }} expressions; they are not shell variables.
    masked = re.sub(r"\$\{\{.*?\}\}", " ", masked)
    for pattern in (
        r"\$\{(?:#)?([A-Za-z_][A-Za-z0-9_]*)",
        r"\$([A-Za-z_][A-Za-z0-9_]*)",
    ):
        for match in re.finditer(pattern, masked):
            name = match.group(1)
            if not KNOWN_ENV_RE.match(name):
                refs.add(name)
    return refs


def extract_shell_definitions(run_text):
    """Return variables assigned within the run block itself."""
    defined = set()
    if not isinstance(run_text, str):
        return defined
    text = _merge_continuations(run_text)
    sourced_os_release = bool(
        re.search(r"(?:^|[\s;(])(?:\.|source)\s+/etc/os-release\b", text,
                  re.MULTILINE)
    )
    if sourced_os_release:
        defined |= OS_RELEASE_VARS
    for line in text.splitlines():
        stripped = line.strip()
        # export NAME=... / NAME=... command
        match = re.match(r"^(?:export\s+)?([A-Za-z_]\w*)=", stripped)
        if match:
            defined.add(match.group(1))
            continue
        # case branches: amd64) TARGET="..."
        match = re.match(r"^[^)]*\)\s+(?:export\s+)?([A-Za-z_]\w*)=",
                         stripped)
        if match:
            defined.add(match.group(1))
            continue
        match = re.match(r"^for\s+([A-Za-z_]\w*)\s+in\b", stripped)
        if match:
            defined.add(match.group(1))
            continue
        # while IFS= read -r NAME  (loop variable is in scope in the body)
        match = re.search(r"\bread\s+(?:-[a-zA-Z]+\s+)*([A-Za-z_][A-Za-z0-9_\s]*)", stripped)
        if match:
            for var in match.group(1).split():
                if re.match(r"^[A-Za-z_]\w*$", var) and var not in ("do", "done", "then", "fi"):
                    defined.add(var)
            continue
    return defined


def extract_github_env_exports(run_text):
    """Return variables exported to GITHUB_ENV by a run block.

    Handles both the direct form (`echo "NAME=..." >> "$GITHUB_ENV"`)
    and the grouped form (`{ echo "NAME=..."; ... } >> "$GITHUB_ENV"`).
    When any GITHUB_ENV append exists in the block, every `echo "NAME="`
    line in the block is treated as an export; over-collection only
    reduces false positives.
    """
    exported = set()
    if not isinstance(run_text, str):
        return exported
    text = _merge_continuations(run_text)
    if "GITHUB_ENV" not in text:
        return exported
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("{"):
            stripped = stripped[1:].lstrip()
        match = re.match(r"echo\s+\"?([A-Za-z_]\w*)=", stripped)
        if match:
            exported.add(match.group(1))
            continue
        match = re.search(r"[\"']?([A-Za-z_]\w*)[\"']?\s*=.*>>\s*[\"']?"
                          r"\$?GITHUB_ENV", line)
        if match:
            exported.add(match.group(1))
    return exported


def env_keys(env_section):
    keys = set()
    if isinstance(env_section, dict):
        keys.update(str(k) for k in env_section)
    return keys


def _load_workflow(path, findings):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        findings.append(f"{path.name}: unreadable workflow: {exc}")
        return None


def _report_undefined_variables(path, job_name, index, run_text, scoped,
                                findings):
    local_defined = extract_shell_definitions(run_text)
    for var in sorted(extract_run_vars(run_text)):
        if var in scoped or var in local_defined:
            continue
        if is_allowlisted(path.name, var):
            continue
        findings.append(
            f"{path.name}: job '{job_name}' step #{index + 1} "
            f"references undefined ${var}; define it in "
            f"step/job/workflow env or export via GITHUB_ENV "
            f"in an earlier step"
        )


def _check_step(path, job_name, index, step, workflow_env, job_env,
                exported, findings):
    if not isinstance(step, dict):
        return set()
    run_text = step.get("run")
    if run_text is None:
        return set()
    scoped = workflow_env | job_env | env_keys(step.get("env")) | exported
    _report_undefined_variables(
        path, job_name, index, run_text, scoped, findings
    )
    return extract_github_env_exports(run_text)


def _check_job(path, job_name, job, workflow_env, findings):
    if not isinstance(job, dict):
        return
    job_env = env_keys(job.get("env"))
    exported = set()
    for index, step in enumerate(job.get("steps") or []):
        exported |= _check_step(
            path, job_name, index, step, workflow_env, job_env, exported,
            findings
        )


def check_workflow(path, findings):
    doc = _load_workflow(path, findings)

    if not isinstance(doc, dict):
        return
    workflow_env = env_keys(doc.get("env"))
    jobs = doc.get("jobs") or {}

    for job_name, job in sorted(jobs.items()):
        _check_job(path, job_name, job, workflow_env, findings)


def is_allowlisted(workflow_name, var):
    for entry in ALLOWLIST:
        parts = entry.split(":")
        if len(parts) < 3 or len(parts[2].strip()) < 5:
            continue
        if parts[0] in workflow_name and parts[1] == var:
            return True
    return False


def main():
    if not WORKFLOW_DIR.is_dir():
        print(f"ERROR workflows directory missing: {WORKFLOW_DIR}",
              file=sys.stderr)
        return 2
    findings = []
    workflows = sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(
        WORKFLOW_DIR.glob("*.yaml"))
    checked = 0
    for path in workflows:
        check_workflow(path, findings)
        checked += 1
    for finding in findings:
        print(f"VIOLATION {finding}", file=sys.stderr)
    print(f"=== workflow env-liveness check: {checked} workflow(s), "
          f"{len(findings)} violation(s) ===", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
