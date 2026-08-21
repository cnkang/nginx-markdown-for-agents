"""Pytest tests for detect_workflow_env_liveness.py (Rule 13).

Adversarial fixtures reproduce the 609fff74 defect: a step-local env var
referenced by a later step that reads an empty value at runtime.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import detect_workflow_env_liveness as module


def _step_findings(job_name, index, step, workflow_env, job_env, exported):
    run_text = step.get("run")
    if run_text is None:
        return [], set()
    scoped = workflow_env | job_env | module.env_keys(step.get("env"))
    scoped |= exported
    local = module.extract_shell_definitions(run_text)
    findings = [
        (job_name, index + 1, var)
        for var in sorted(module.extract_run_vars(run_text))
        if var not in scoped and var not in local
    ]
    return findings, module.extract_github_env_exports(run_text)


def _job_findings(job_name, job, workflow_env):
    job_env = module.env_keys(job.get("env"))
    exported = set()
    findings = []
    for index, step in enumerate(job.get("steps") or []):
        step_findings, step_exports = _step_findings(
            job_name, index, step, workflow_env, job_env, exported
        )
        findings.extend(step_findings)
        exported |= step_exports
    return findings


def findings_for_workflow(doc):
    workflow_env = module.env_keys(doc.get("env"))
    findings = []
    for job_name, job in (doc.get("jobs") or {}).items():
        findings.extend(_job_findings(job_name, job, workflow_env))
    return findings


def test_step_local_var_used_by_later_step_is_violation():
    doc = {
        "jobs": {"build": {"steps": [
            {"run": "echo ok",
             "env": {"MATRIX_ARCH": "amd64"}},
            {"run": "echo ${MATRIX_ARCH}"},
        ]}},
    }
    hits = findings_for_workflow(doc)
    assert hits == [("build", 2, "MATRIX_ARCH")]


def test_job_env_reference_passes():
    doc = {
        "jobs": {"build": {
            "env": {"NFPM_ARCH": "amd64"},
            "steps": [{"run": "echo ${NFPM_ARCH}"}],
        }},
    }
    assert findings_for_workflow(doc) == []


def test_github_env_direct_export_passes():
    doc = {"jobs": {"build": {"steps": [
        {"run": "echo \"NGINX_VERSION=1.28.0\" >> \"$GITHUB_ENV\""},
        {"run": "echo ${NGINX_VERSION}"},
    ]}}}
    assert findings_for_workflow(doc) == []


def test_github_env_heredoc_block_export_passes():
    run = (
        "{\n"
        "  echo \"PKG_VERSION=0.9.2\"\n"
        "  echo \"NFPM_ARCH=amd64\"\n"
        "} >> \"$GITHUB_ENV\"\n"
    )
    doc = {"jobs": {"build": {"steps": [
        {"run": run},
        {"run": "echo ${NFPM_ARCH}-${PKG_VERSION}"},
    ]}}}
    assert findings_for_workflow(doc) == []


def test_backslash_continued_export_passes():
    run = (
        "echo \"NGINX_SRC=${GITHUB_WORKSPACE}/nginx-1.28.0\" \\\n"
        "  >> \"$GITHUB_ENV\"\n"
    )
    exports = module.extract_github_env_exports(run)
    assert "NGINX_SRC" in exports


def test_case_branch_assignment_passes():
    run = (
        "case \"${MATRIX_ARCH}\" in\n"
        "  amd64) RUST_TARGET=\"x86_64-unknown-linux-gnu\" ;;\n"
        "esac\n"
        "echo \"${RUST_TARGET}\"\n"
    )
    assert "RUST_TARGET" in module.extract_shell_definitions(run)


def test_os_release_sourcing_passes():
    run = (
        "CODENAME=$(. /etc/os-release && echo \"$VERSION_CODENAME\")\n"
        "DISTRO_ID=$(. /etc/os-release && echo \"$ID\")\n"
        "echo \"${CODENAME} ${DISTRO_ID}\"\n"
    )
    defined = module.extract_shell_definitions(run)
    assert "VERSION_CODENAME" in defined and "ID" in defined


def test_while_read_loop_variable_passes():
    run = (
        "while IFS= read -r keygrip; do\n"
        "  echo \"${keygrip}\"\n"
        "done\n"
    )
    assert "keygrip" in module.extract_shell_definitions(run)


def test_awk_single_quoted_field_not_a_reference():
    run = "printf '%s\\n' \"$(awk '{print $NF}' file.txt)\"\n"
    assert "NF" not in module.extract_run_vars(run)


def test_github_runner_vars_are_known():
    run = "echo ${GITHUB_WORKSPACE} ${RUNNER_TEMP} ${HOME}\n"
    assert module.extract_run_vars(run) == set()


def test_allowlist_requires_justification():
    assert module.is_allowlisted("ci.yml", "SOME_VAR") is False
