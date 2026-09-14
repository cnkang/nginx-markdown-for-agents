"""Prove stage edges by changing fixture files, never the resolver outputs."""
from pathlib import Path

import pytest

from tools.harness import check_harness_sync as sync
from tools.harness import stage_reachability as reach


CHECK = "tools/harness/detect_example.py"
PROFILE = "tools/ci/pre_push_profile.py"
MAKEFILE = f"""LIST := checked
pre-push-check:
\tpython3 {PROFILE}
root: intermediate
intermediate:
\t@$(MAKE) $(LIST)
checked:
\tpython3 {CHECK}
other:
\t@true
cycle: root cycle
"""


def write(path: Path, text: str) -> None:
    """Create one input surface in the disposable fixture repository."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Provide real entry files and leave every parsing function intact."""
    write(tmp_path / "AGENTS.md", "| 56 | build-safety | test |\n")
    write(tmp_path / CHECK, "# fixture detector\n")
    write(tmp_path / PROFILE, "# profile endpoint; runner is behavior-tested separately\n")
    write(tmp_path / "Makefile", MAKEFILE)
    write(tmp_path / ".pre-commit-config.yaml", 'repos:\n- repo: local\n  hooks:\n  - id: quick\n    entry: make root\n')
    write(tmp_path / ".github/workflows/check.yml", 'jobs:\n  check:\n    steps:\n    - run: make root\n')
    gate = dict(name="local", command=["make", "root"], needs_c_change=False, requires_nginx=False)
    write(tmp_path / "tools/ci/pre_push_gates.py", "GATES = " + repr([gate]))
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sync, "AGENTS_PATH", tmp_path / "AGENTS.md")
    return tmp_path


def verdict(stage):
    """Run the real mapping validator over one rule."""
    entry = dict(rule="56", summary="test", check=CHECK, files=["tools/**"],
                 stage=[stage], blocking=True, test=None, not_covered="dynamic calls")
    return sync._check_rule_checks({"rule_checks": [entry]})


@pytest.mark.parametrize("stage", ["save", "commit", "push", "ci"])
def test_disconnect_only_one_edge_then_restore(repo, stage):
    assert verdict(stage).status == sync.PASS
    write(repo / "Makefile", MAKEFILE.replace("root: intermediate", "root: other"))
    result = verdict(stage)
    assert result.status == sync.FAIL
    assert stage in result.detail
    write(repo / "Makefile", MAKEFILE)
    assert verdict(stage).status == sync.PASS


@pytest.mark.parametrize("entry", ["echo root", "echo make root", "make other"])
def test_hook_non_call_cannot_certify(repo, entry):
    path = repo / ".pre-commit-config.yaml"
    write(path, path.read_text().replace("make root", entry))
    assert verdict("commit").status == sync.FAIL


def test_manual_hook_is_not_commit(repo):
    path = repo / ".pre-commit-config.yaml"
    write(path, path.read_text() + "    stages: [manual]\n")
    assert verdict("commit").status == sync.FAIL


@pytest.mark.parametrize("run", ["echo make root", "make other", "'# make root'"])
def test_ci_other_call_does_not_certify(repo, run):
    path = repo / ".github/workflows/check.yml"
    write(path, path.read_text().replace("make root", run))
    assert verdict("ci").status == sync.FAIL


def test_disabled_ci_step(repo):
    path = repo / ".github/workflows/check.yml"
    write(path, path.read_text().replace("- run:", "- if: false\n      run:"))
    assert verdict("ci").status == sync.FAIL


@pytest.mark.parametrize("replacement", ["@true", f"echo {PROFILE}"])
def test_push_endpoint_disconnected(repo, replacement):
    write(repo / "Makefile", MAKEFILE.replace(f"python3 {PROFILE}", replacement))
    assert verdict("push").status == sync.FAIL


def test_removed_gate_is_not_reachable(repo):
    path = repo / "tools/ci/pre_push_gates.py"
    write(path, path.read_text().replace("'root'", "'other'"))
    assert verdict("push").status == sync.FAIL
    write(path, "GATES = []")
    assert verdict("push").status == sync.FAIL


def test_cycle_terminates(repo):
    path = repo / ".pre-commit-config.yaml"
    write(path, path.read_text().replace("make root", "make cycle"))
    assert verdict("commit").status == sync.PASS


def test_gate_declaration_selects_harness_ci():
    import yaml
    root = Path(__file__).resolve().parents[3]
    # BaseLoader preserves the YAML 'on' key and workflow strings verbatim.
    workflow = yaml.load((root / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    import fnmatch
    name = "tools/ci/pre_push_gates.py"
    for event in ("push", "pull_request"):
        assert any(fnmatch.fnmatchcase(name, p) for p in workflow["on"][event]["paths"])
    filters = next(step["with"]["filters"] for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
    assert any(fnmatch.fnmatchcase(name, p) for p in yaml.safe_load(filters)["harness_tooling"])


@pytest.mark.parametrize("body", [
    "if false; then\n  make root\nfi",
    "cat <<EOF\nmake root\nEOF",
    "exit 0\nmake root",
    "make root || true",
])
def test_dormant_or_nonblocking_workflow_commands_do_not_certify(repo, body):
    import yaml
    write(repo / ".github/workflows/check.yml", yaml.safe_dump(
        {"jobs": {"check": {"steps": [{"run": body}]}}}
    ))
    assert verdict("ci").status == sync.FAIL


@pytest.mark.parametrize("definition", ["LIST := $(LIST)", "LIST := $(LIST)x"])
def test_recursive_variables_cannot_certify(repo, definition):
    write(repo / "Makefile", MAKEFILE.replace("LIST := checked", definition))
    assert verdict("commit").status == sync.FAIL


def test_a_defined_function_is_not_a_call_site() -> None:
    """A shell function that never runs cannot certify the checks in its body."""
    script = "noop() {\n  make root\n}\ntrue"

    assert reach.literal_script_lines(script) == []


def test_a_conditional_branch_is_not_evidence() -> None:
    """A target inside a branch that cannot be evaluated certifies nothing."""
    makefile = (
        "root:\n\t@true\n"
        "ifeq (1,0)\nhidden:\n\tpython3 " + CHECK + "\nendif\n"
    )

    reached = reach.reachable_commands(makefile, ["root"], PROFILE, [])

    assert "hidden" not in reached
    assert CHECK not in reached


def test_a_listing_invocation_does_not_expand_gates() -> None:
    """Only the invocation that runs the gates may expand them."""
    makefile = f"root:\n\tpython3 {PROFILE} --list\n"

    reached = reach.reachable_commands(
        makefile, ["root"], PROFILE, ["make harness-security-checks"]
    )

    assert "harness-security-checks" not in reached
