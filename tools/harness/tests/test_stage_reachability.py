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
    plain = "true\nmake root\n"

    assert reach.literal_script_lines(plain) == ["true", "make root"]
    assert reach.literal_script_lines(script) == []


def test_a_conditional_branch_is_not_evidence() -> None:
    """A target inside a branch that cannot be evaluated certifies nothing."""
    guarded = (
        "root:\n\t@true\n"
        "ifeq (1,0)\nhidden:\n\tpython3 " + CHECK + "\nendif\n"
    )
    # The same file without the conditional proves the entry really expands it.
    plain = "root:\n\t@true\nhidden:\n\tpython3 " + CHECK + "\n"

    assert CHECK in reach.reachable_commands(plain, ["make hidden"], PROFILE, [])
    reached = reach.reachable_commands(guarded, ["make hidden"], PROFILE, [])

    # The entry line itself is always reported; the guard is the detector.
    assert CHECK not in reached


def test_a_conditional_variable_override_is_not_knowable() -> None:
    """A variable an unevaluable branch rewrites cannot pick the target."""
    makefile = (
        "LIST := checked\n"
        "root:\n\t@$(MAKE) $(LIST)\n"
        "checked:\n\tpython3 " + CHECK + "\n"
        "other:\n\t@true\n"
        "ifeq (1,1)\nLIST := other\nendif\n"
    )

    reached = reach.reachable_commands(makefile, ["make root"], PROFILE, [])

    assert CHECK not in reached


def test_a_listing_invocation_does_not_expand_gates() -> None:
    """Only the invocation that runs the gates may expand them."""
    gates = ["make harness-security-checks"]
    plain = f"root:\n\tpython3 {PROFILE}\n"
    listing = f"root:\n\tpython3 {PROFILE} --list\n"

    assert "harness-security-checks" in reach.reachable_commands(
        plain, ["make root"], PROFILE, gates
    )
    reached = reach.reachable_commands(listing, ["make root"], PROFILE, gates)

    assert "harness-security-checks" not in reached


def test_an_option_makes_an_invocation_uncertain() -> None:
    """`make -n` prints a recipe instead of running it."""
    assert reach.make_targets("make -n root") == []
    assert reach.make_targets("make root") == ["root"]


def test_quoted_text_is_not_a_call() -> None:
    """A call written inside a multi-line string never runs."""
    assert reach.literal_script_lines('printf \'%s\\n\' "\nmake root\n"') == []


def test_a_conditional_assignment_keeps_the_earlier_value() -> None:
    """`?=` cannot replace a value that is already set."""
    makefile = (
        "LIST := checked\nLIST ?= other\n"
        "root:\n\t@$(MAKE) $(LIST)\n"
        "checked:\n\tpython3 " + CHECK + "\n"
        "other:\n\t@true\n"
    )

    reached = reach.reachable_commands(makefile, ["make root"], PROFILE, [])

    assert CHECK in reached


def test_make_flags_from_the_environment_are_refused() -> None:
    """`MAKEFLAGS` can carry -n, which prints a recipe instead of running it."""
    assert reach.make_targets("MAKEFLAGS=-n make root") == []
    assert reach.make_targets("make root") == ["root"]


def test_a_directory_change_makes_a_script_uncertain() -> None:
    """A script that moves elsewhere does not run the root repository's work."""
    script = "cd components/nginx-module\nmake root\n"

    assert reach.literal_script_lines(script) == []
    assert reach.literal_script_lines("make root\n") == ["make root"]


def test_an_append_to_a_simple_variable_expands_now() -> None:
    """`+=` on a simple variable must not pick up a later assignment."""
    makefile = (
        "LIST :=\nLIST += $(LATER)\n"
        "root:\n\tpython3 $(LIST)\n"
        "LATER := " + CHECK + "\n"
    )

    reached = reach.reachable_commands(makefile, ["make root"], PROFILE, [])

    assert CHECK not in reached


def test_prerequisites_expand_where_they_are_read() -> None:
    """The value at the declaration decides, as Make reads it there."""
    makefile = (
        "LIST = other\nroot: $(LIST)\n"
        "other:\n\t@true\n"
        "checked:\n\tpython3 " + CHECK + "\n"
        "LIST = checked\n"
    )

    reached = reach.reachable_commands(makefile, ["make root"], PROFILE, [])

    assert CHECK not in reached


def test_the_last_recipe_wins() -> None:
    """Make drops an overridden recipe instead of running both."""
    overridden = f"root:\n\tpython3 {CHECK}\nroot:\n\t@echo OTHER\n"
    only = f"root:\n\tpython3 {CHECK}\n"

    assert CHECK in reach.reachable_commands(only, ["make root"], PROFILE, [])
    assert CHECK not in reach.reachable_commands(overridden, ["make root"], PROFILE, [])


def test_an_ignored_failure_is_not_blocking_evidence() -> None:
    """A recipe prefixed with `-` cannot fail the build."""
    ignored = f"root:\n\t-python3 {CHECK}\n"
    blocking = f"root:\n\tpython3 {CHECK}\n"

    assert CHECK in reach.reachable_commands(blocking, ["make root"], PROFILE, [])
    assert CHECK not in reach.reachable_commands(ignored, ["make root"], PROFILE, [])
