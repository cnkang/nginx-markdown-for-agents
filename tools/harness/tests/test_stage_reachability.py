"""Prove stage edges by changing fixture files, never the resolver outputs."""
import json
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
    write(tmp_path / "tools/ci/pre_push_gates.json", json.dumps([gate]))
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
    path = repo / "tools/ci/pre_push_gates.json"
    write(path, path.read_text().replace('"root"', '"other"'))
    assert verdict("push").status == sync.FAIL
    write(path, "[]")
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
    name = "tools/ci/pre_push_gates.json"
    for event in ("push", "pull_request"):
        assert any(fnmatch.fnmatchcase(name, p) for p in workflow["on"][event]["paths"])
    filters = next(step["with"]["filters"] for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "filter")
    assert any(fnmatch.fnmatchcase(name, p) for p in yaml.safe_load(filters)["harness_tooling"])


def test_ci_filters_cover_build_and_harness_support_surfaces() -> None:
    """Changes to helper entrypoints must select their dependent CI jobs."""
    import fnmatch
    import yaml

    root = Path(__file__).resolve().parents[3]
    workflow = yaml.load(
        (root / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    top_paths = set(workflow["on"]["pull_request"]["paths"])
    assert "build.sh" in top_paths
    assert ".clusterfuzzlite/**" in top_paths

    filters_step = next(
        step
        for step in workflow["jobs"]["changes"]["steps"]
        if step.get("id") == "filter"
    )
    filters = yaml.safe_load(filters_step["with"]["filters"])
    required = {
        "rust": [
            "components/nginx-module/src/markdown_converter.h",
            "tools/ci/coverage_gate.py",
        ],
        "nginx": [
            "components/rust-converter/include/markdown_converter.h",
            "tools/ci/coverage_gate.py",
        ],
        "e2e": ["tools/ci/verify_real_nginx_ims.sh"],
        "harness_tooling": [
            "tools/ci/coverage_gate.py",
            "tools/ci/validate_required_workflow_contexts.py",
            "tools/ci/test_validate_required_workflow_contexts.py",
            "tools/ci/verify_real_nginx_ims.sh",
            "build.sh",
            ".clusterfuzzlite/**",
        ],
    }
    for name, paths in required.items():
        for path in paths:
            assert any(fnmatch.fnmatchcase(path, pattern) for pattern in filters[name]), (
                name,
                path,
            )


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


def test_a_prerequisite_open_at_read_time_expands_empty(tmp_path) -> None:
    """A reference Make cannot resolve while reading expands to nothing."""
    makefile = "root: $(LATER)\n" f"checked:\n\tpython3 {CHECK}\n" "LATER = checked\n"

    dry = _make_dry_run(tmp_path, makefile, "root")
    assert CHECK not in dry
    assert CHECK not in reach.reachable_commands(makefile, ["make root"], PROFILE, [])


def test_a_prerequisite_defined_before_its_line_still_expands(tmp_path) -> None:
    """The positive counterpart: a resolvable reference keeps its value."""
    makefile = "LATER = checked\n" "root: $(LATER)\n" f"checked:\n\tpython3 {CHECK}\n"

    dry = _make_dry_run(tmp_path, makefile, "root")
    assert CHECK in dry
    assert CHECK in reach.reachable_commands(makefile, ["make root"], PROFILE, [])


def test_the_resolvable_sibling_of_an_open_reference_still_certifies(tmp_path) -> None:
    """`root: good $(LATER)` builds `good`; the open reference adds nothing."""
    makefile = f"root: good $(LATER)\ngood:\n\tpython3 {CHECK}\nLATER = checked\n"

    dry = _make_dry_run(tmp_path, makefile, "root")
    assert CHECK in dry
    assert CHECK in reach.reachable_commands(makefile, ["make root"], PROFILE, [])


def test_an_existing_file_prerequisite_is_a_satisfied_leaf(tmp_path) -> None:
    """A file that exists in the tree is a satisfied leaf prerequisite."""
    (tmp_path / "Makefile").write_text("", encoding="utf-8")
    makefile = "root: Makefile\n\tpython3 tools/check.py\n"

    reached = reach.reachable_commands(
        makefile, ["make root"], PROFILE, [], root=tmp_path
    )

    assert "tools/check.py" in reached


def test_a_missing_file_prerequisite_still_breaks(tmp_path) -> None:
    """A prerequisite that names nothing in the tree stays uncertifiable."""
    makefile = "root: missing-file.xyz\n\tpython3 tools/check.py\n"

    reached = reach.reachable_commands(
        makefile, ["make root"], PROFILE, [], root=tmp_path
    )

    assert "tools/check.py" not in reached


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


def test_the_ignored_status_prefix_survives_other_prefixes() -> None:
    """Make's @, - and + prefixes come in any order."""
    recipes = [f"root:\n\t-python3 {CHECK}\n", f"root:\n\t@-python3 {CHECK}\n",
               f"root:\n\t-@python3 {CHECK}\n"]

    for makefile in recipes:
        assert CHECK not in reach.reachable_commands(makefile, ["make root"], PROFILE, [])


def test_a_conditional_assignment_invalidates_the_earlier_value() -> None:
    """A branch assignment must not leave the outside value in place."""
    makefile = (
        "LIST := checked\n"
        "ifeq (1,1)\nLIST := other\nendif\n"
        "root: $(LIST)\n"
        "checked:\n\tpython3 " + CHECK + "\n"
        "other:\n\t@true\n"
    )

    reached = reach.reachable_commands(makefile, ["make root"], PROFILE, [])

    assert CHECK not in reached


def test_an_override_assignment_wins_like_make() -> None:
    """`override LIST := other` replaces the earlier value."""
    makefile = (
        "LIST := checked\n"
        "override LIST := other\n"
        "root: $(LIST)\n"
        "other:\n\t@true\n"
        f"checked:\n\tpython3 {CHECK}\n"
    )

    reached = reach.reachable_commands(makefile, ["make root"], PROFILE, [])
    assert "true" in reached
    assert CHECK not in reached


def test_an_export_prefixed_assignment_is_read() -> None:
    """`export LIST := other` assigns LIST, exactly as Make reads it."""
    makefile = (
        "LIST := checked\n"
        "export LIST := other\n"
        "root: $(LIST)\n"
        "other:\n\t@true\n"
        f"checked:\n\tpython3 {CHECK}\n"
    )

    reached = reach.reachable_commands(makefile, ["make root"], PROFILE, [])
    assert "true" in reached
    assert CHECK not in reached


def test_an_override_inside_a_branch_still_invalidates() -> None:
    """A branch assignment with a prefix is poison like any other."""
    makefile = (
        "LIST := checked\n"
        "ifeq (1,1)\noverride LIST := other\nendif\n"
        "root: $(LIST)\n"
        f"checked:\n\tpython3 {CHECK}\n"
    )

    reached = reach.reachable_commands(makefile, ["make root"], PROFILE, [])
    assert CHECK not in reached


def test_an_operator_glued_to_an_operand_is_not_a_plain_command() -> None:
    """`||true` reaches the tokenizer as one word."""
    assert reach.command_words("python3 tools/harness/detect_example.py ||true") == []
    assert reach.command_words("make root") == ["make", "root"]


def test_an_overridden_recipe_replaces_its_predecessor() -> None:
    """A `make` call written in an old recipe is not a live prerequisite."""
    overridden = (
        "root:\n\tmake checked\nroot:\n\ttrue\n"
        "checked:\n\tpython3 " + CHECK + "\n"
    )
    merged = (
        "root: dep1\nroot: dep2\n"
        "dep1:\n\t@true\ndep2:\n\tpython3 " + CHECK + "\n"
    )

    assert CHECK in reach.reachable_commands(merged, ["make root"], PROFILE, [])
    assert CHECK not in reach.reachable_commands(overridden, ["make root"], PROFILE, [])


def test_an_undeclared_prerequisite_makes_its_target_unknown(tmp_path) -> None:
    """Make refuses `root: missing`; its recipe must not certify anything."""
    makefile = f"root: missing\n\tpython3 {CHECK}\n"

    dry = _make_dry_run(tmp_path, makefile, "root")
    assert "No rule to make target" in dry
    assert CHECK not in reach.reachable_commands(makefile, ["make root"], PROFILE, [])


def test_a_broken_prerequisite_propagates_to_its_dependents(tmp_path) -> None:
    """A subtree reachable only through a broken target stays unknown."""
    makefile = f"root: intermediate\nintermediate: missing\n\tpython3 {CHECK}\n"

    dry = _make_dry_run(tmp_path, makefile, "root")
    assert "No rule to make target" in dry
    assert CHECK not in reach.reachable_commands(makefile, ["make root"], PROFILE, [])


def test_a_sibling_of_a_missing_prerequisite_is_not_certified(tmp_path) -> None:
    """`root: good missing` runs neither branch, so nothing is proven."""
    makefile = f"root: good missing\n\tpython3 {CHECK}\ngood:\n\t@true\n"

    dry = _make_dry_run(tmp_path, makefile, "root")
    assert "No rule to make target" in dry
    assert CHECK not in reach.reachable_commands(makefile, ["make root"], PROFILE, [])


def test_a_fully_declared_graph_still_certifies(tmp_path) -> None:
    """The positive counterpart: declared prerequisites keep the evidence."""
    makefile = f"root: good\n\tpython3 {CHECK}\ngood:\n\t@true\n"

    assert "No rule to make target" not in _make_dry_run(tmp_path, makefile, "root")
    assert CHECK in reach.reachable_commands(makefile, ["make root"], PROFILE, [])


def _make_dry_run(tmp_path, makefile: str, target: str) -> str:
    """What Make itself says it would run, for comparison."""
    import subprocess

    (tmp_path / "Makefile").write_text(makefile, encoding="utf-8")
    result = subprocess.run(
        ["make", "-n", target], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    # Diagnostics ("No rule to make target ...") arrive on stderr; combine both
    # streams so wording checks hold whichever stream a host's make uses.
    return result.stdout + result.stderr


def test_a_superseded_recipe_is_not_used_when_its_replacement_cannot_fail(tmp_path) -> None:
    """Make replaced the recipe, so the old command is not a live call."""
    makefile = f"root:\n\tpython3 {CHECK}\nroot:\n\t-echo noop\n"

    assert CHECK not in _make_dry_run(tmp_path, makefile, "root")
    assert CHECK not in reach.reachable_commands(makefile, ["make root"], PROFILE, [])


def test_a_branch_that_may_redefine_a_target_makes_it_unknown(tmp_path) -> None:
    """A target a branch redefines has no certain recipe."""
    makefile = f"root:\n\tpython3 {CHECK}\nifeq (1,1)\nroot:\n\techo noop\nendif\n"

    assert CHECK not in _make_dry_run(tmp_path, makefile, "root")
    assert CHECK not in reach.reachable_commands(makefile, ["make root"], PROFILE, [])


@pytest.mark.parametrize("directive,target,expected", [
    ("", "checked", True),
    (".IGNORE:", "checked", False),
    (".IGNORE: checked", "checked", False),
    (".IGNORE: other", "checked", True),
    (".IGNORE: outer", "outer", False),
    (".IGNORE: parent", "parent", True),
])
def test_ignore_agrees_with_real_make(repo, directive, target, expected):
    """Compare blocking evidence with Make's actual failure propagation."""
    import subprocess
    write(repo / CHECK, "raise SystemExit(7)\n")
    makefile = f"""{directive}
checked:
\tpython3 {CHECK}
outer:
\t$(MAKE) checked
parent: checked
\t@true
other:
\t@true
"""
    write(repo / "Makefile", makefile)
    result = subprocess.run(["make", target], cwd=repo, capture_output=True, text=True)
    reached = reach.reachable_commands(makefile, [f"make {target}"], PROFILE, [])
    assert (result.returncode != 0) == expected, result.stderr
    assert sync._is_invoked(CHECK, reached) == expected


@pytest.mark.parametrize("directive", [
    "ifeq (1,1)\n.IGNORE: checked\nendif",
    ".IGNORE: $(UNKNOWN)",
])
def test_unknown_ignore_scope_fails_mapping(repo, directive):
    write(repo / "Makefile", directive + "\n" + MAKEFILE)
    result = verdict("commit")
    assert result.status == sync.FAIL
    assert ".IGNORE" in result.detail


def test_an_indented_comment_does_not_condemn_a_script() -> None:
    """A comment with leading space is still a comment."""
    script = "make root\n  # build the module\n  make checked\n"

    assert reach.literal_script_lines(script) != []


def test_an_entry_that_names_an_interpreter_invokes_the_path() -> None:
    """`entry: python3 tools/x.py` runs the path the same way a line does."""
    assert sync._is_invoked("tools/harness/detect_example.py", "entry: python3 tools/harness/detect_example.py")
    assert not sync._is_invoked("tools/harness/detect_example.py", "entry: echo detect_example.py")


def test_an_interpreter_option_does_not_hide_the_script() -> None:
    """`bash -euo pipefail x.sh` runs x.sh, so that is the path it reaches."""
    assert sync._invocation_target(["bash", "-euo", "pipefail", "tools/x.sh"]) == "tools/x.sh"
    assert sync._invocation_target(["python3", "-m", "pytest", "tests/"]) is None
    assert sync._invocation_target(["python3", "tools/x.py"]) == "tools/x.py"


def test_interpreter_value_and_separator_options_are_parsed_safely() -> None:
    """Value-taking flags are consumed and ``--`` exposes the script."""
    assert sync._invocation_target(["python3", "-I", "tools/x.py"]) == "tools/x.py"
    assert sync._invocation_target(["python3", "-X", "utf8", "tools/x.py"]) == "tools/x.py"
    assert sync._invocation_target(["python3", "-Xutf8", "tools/x.py"]) == "tools/x.py"
    assert sync._invocation_target(["bash", "-O", "extglob", "tools/x.sh"]) == "tools/x.sh"
    assert sync._invocation_target(["bash", "--", "tools/x.sh"]) == "tools/x.sh"
    assert sync._invocation_target(["python3", "--unknown", "tools/x.py"]) is None


def test_a_name_that_looks_like_a_directive_is_not_one() -> None:
    """`endif_var := value` neither closes a branch nor opens a target."""
    open_branch = (
        "ifeq ($(UNKNOWN),yes)\nendif_var := value\n"
        "root:\n\tpython3 " + CHECK + "\n"
    )
    closed_branch = open_branch.replace("endif_var := value", "endif")

    with pytest.raises(ValueError, match="unclosed"):
        reach.reachable_commands(open_branch, ["make root"], PROFILE, [])
    assert CHECK in reach.reachable_commands(closed_branch, ["make root"], PROFILE, [])


def test_a_target_that_starts_with_a_conditional_word_is_not_a_directive() -> None:
    """`ifeq-cache:`, `endif-clean:` and `else-clean:` are targets."""
    makefile = (
        "ifeq-cache:\n\t@true\n"
        "endif-clean:\n\t@true\n"
        "else-clean:\n\t@true\n"
        "root: ifeq-cache\n\tpython3 " + CHECK + "\n"
    )

    reached = reach.reachable_commands(makefile, ["make root"], PROFILE, [])

    assert CHECK in reached


def test_a_target_that_starts_with_include_is_not_an_include_directive() -> None:
    """`include-deps:` and `sinclude-stubs:` are targets, not includes."""
    makefile = (
        "include-deps:\n\t@true\n"
        "sinclude-stubs:\n\t@true\n"
        "root: include-deps\n\tpython3 " + CHECK + "\n"
    )

    reached = reach.reachable_commands(makefile, ["make root"], PROFILE, [])

    assert CHECK in reached


@pytest.mark.parametrize(
    "makefile, message",
    [
        ("endif\nroot:\n\t@true\n", "unmatched"),
        ("ifeq (1,1)\nroot:\n\t@true\n", "unclosed"),
        ("include generated.mk\nroot:\n\t@true\n", "included"),
    ],
)
def test_uncertain_makefile_structure_fails_closed(makefile: str, message: str) -> None:
    """Malformed or split Makefiles cannot establish a stage edge."""
    with pytest.raises(ValueError, match=message):
        reach.reachable_commands(makefile, ["make root"], PROFILE, [])


def test_order_only_separator_is_not_a_prerequisite_token() -> None:
    """`a | b` keeps both groups traversable and drops the bare `|` token."""
    makefile = (
        "root: left | right\n\tpython3 " + CHECK + "\n"
        "left:\n\t@true\n"
        "right:\n\t@true\n"
    )

    reached = reach.reachable_commands(makefile, ["make root"], PROFILE, [])

    assert CHECK in reached


def test_a_space_indented_assignment_is_read_like_make() -> None:
    """GNU make ignores leading spaces; an indented reassignment wins."""
    makefile = (
        "LIST := checked\n"
        "  LIST := other\n"
        "root: $(LIST)\n"
        "other:\n\t@true\n"
        f"checked:\n\tpython3 {CHECK}\n"
    )

    reached = reach.reachable_commands(makefile, ["make root"], PROFILE, [])
    assert "true" in reached
    assert CHECK not in reached


def test_a_shell_assignment_invalidates_its_variable() -> None:
    """`LIST != cmd` cannot be evaluated here, so its earlier value is
    dropped instead of certifying a command the build may not run."""
    makefile = (
        "LIST := checked\n"
        "LIST != echo other\n"
        "root: $(LIST)\n"
        "other:\n\t@true\n"
        f"checked:\n\tpython3 {CHECK}\n"
    )

    reached = reach.reachable_commands(makefile, ["make root"], PROFILE, [])
    assert CHECK not in reached
    assert "true" not in reached

    plain = makefile.replace("LIST != echo other\n", "")
    assert CHECK in reach.reachable_commands(plain, ["make root"], PROFILE, [])


def test_a_space_indented_comment_keeps_the_recipe_open() -> None:
    """An indented comment between recipe lines does not end the recipe."""
    makefile = (
        "root:\n"
        f"\tpython3 {CHECK}\n"
        "  # still the same recipe block\n"
        "\t@true\n"
    )

    reached = reach.reachable_commands(makefile, ["make root"], PROFILE, [])
    assert "true" in reached
    assert CHECK in reached
