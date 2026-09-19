"""Unit tests for detect_script_exec_bits.py.

Each case builds a throwaway git repository, records the script with a
known mode in the index, and runs the detector against it.  The detector
must fail only when an executable-style reference meets a non-executable
index mode.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

DETECTOR = Path(__file__).resolve().parent.parent / "detect_script_exec_bits.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / "tools").mkdir()
    _git(repo, "init", "-q")
    return repo


def _add(repo: Path, rel: str, mode: int, content: str = "#!/usr/bin/env bash\nexit 0\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)
    _git(repo, "add", "--", rel)


def _run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(DETECTOR)],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _workflow(repo: Path, body: str) -> None:
    (repo / ".github" / "workflows" / "w.yml").write_text(
        "jobs:\n  x:\n    steps:\n      - name: t\n        run: |\n" + body,
        encoding="utf-8",
    )


def test_direct_reference_without_exec_bit_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          ./tools/x.sh --flag\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1
    assert "tools/x.sh" in result.stdout
    assert "100644" in result.stdout


def test_direct_reference_with_exec_bit_passes(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          ./tools/x.sh --flag\n")
    _add(repo, "tools/x.sh", 0o700)
    result = _run(repo)
    assert result.returncode == 0
    assert "OK" in result.stdout


def test_interpreter_prefix_does_not_require_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          bash tools/x.sh --flag\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_bare_makefile_recipe_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "Makefile").write_text("t:\n\ttools/z.sh\n", encoding="utf-8")
    _add(repo, "tools/z.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1
    assert "tools/z.sh" in result.stdout


def test_untracked_script_is_skipped(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          ./tools/new.sh\n")
    (repo / "tools" / "new.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    result = _run(repo)
    assert result.returncode == 0


def test_pytest_continuation_argument_does_not_require_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "Makefile").write_text(
        "t:\n\tpython3 -m pytest \\\n\t\ttools/t.py \\\n\t\t-q --tb=short\n",
        encoding="utf-8",
    )
    _add(repo, "tools/t.py", 0o600, content="def test_x():\n    assert True\n")
    result = _run(repo)
    assert result.returncode == 0


def test_bash_prefixed_direct_reference_does_not_require_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          bash ./tools/x.sh --flag\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_env_wrapper_still_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          env ./tools/x.sh --flag\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_inline_run_label_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / ".github" / "workflows" / "w.yml").write_text(
        "jobs:\n  x:\n    steps:\n      - name: t\n        run: tools/x.sh --flag\n",
        encoding="utf-8",
    )
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1
    assert "tools/x.sh" in result.stdout


def test_reference_after_separator_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          cd sub && tools/z.sh\n")
    _add(repo, "tools/z.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_interpreter_argument_is_not_a_command_word(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / ".github" / "workflows" / "w.yml").write_text(
        "jobs:\n  x:\n    steps:\n      - name: t\n        run: python3 tools/y.py\n",
        encoding="utf-8",
    )
    _add(repo, "tools/y.py", 0o600, content="print('x')\n")
    result = _run(repo)
    assert result.returncode == 0


def test_argument_token_is_not_a_command_word(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          echo tools/y.sh\n")
    _add(repo, "tools/y.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_direct_reference_as_argument_is_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          echo ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_direct_reference_after_separator_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          cd sub && ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_command_wrapper_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          command ./tools/x.sh --flag\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_env_with_assignment_still_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          env FOO=1 ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_env_wrapping_interpreter_does_not_require_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          env -i bash ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_command_v_query_does_not_require_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          command -v ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_bare_path_after_env_wrapper_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          env tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_control_prefix_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          if ./tools/x.sh --profile smoke; then\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_assignment_before_wrapper_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          FOO=1 env ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_separator_attached_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          cmd;./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_command_substitution_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          x=$(./tools/x.sh)\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_exec_dash_a_argument_does_not_require_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          exec -a tools/x.sh /bin/sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_env_unset_operand_still_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          env -u FOO ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_env_chdir_operand_still_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          env -C /tmp ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_env_unset_then_interpreter_does_not_require_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          env -u FOO bash ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_env_end_of_options_still_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          env -- ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_env_missing_operand_consumes_the_path(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          env -u ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_env_options_only_then_path_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          env -i ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_case_pattern_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          case x in y) ./tools/x.sh;; esac\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_brace_group_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          { ./tools/x.sh; }\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_quoted_command_word_requires_exec_bit(tmp_path):
    """A quoted command word executes the script after quote removal."""
    repo = _make_repo(tmp_path)
    _workflow(repo, '          "./tools/x.sh"\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_quoted_argument_is_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          echo "./tools/x.sh"\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_quoted_assignment_value_is_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          X="./tools/x.sh"\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_quoted_interpreter_argument_is_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          bash "./tools/x.sh"\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_background_separator_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          cmd & ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_redirect_prefixed_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          2>/dev/null ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_time_keyword_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          time ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_timeout_kill_after_long_option_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          timeout --kill-after 1 2 ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_closed_array_before_command_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          ( arr=(foo) ./tools/x.sh )\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_sudo_wrapper_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          sudo ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_sudo_option_operand_still_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          sudo -u bob ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_timeout_duration_then_script_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          timeout 1 ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_timeout_duration_then_interpreter_does_not_require_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          timeout 1 bash ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_case_pattern_path_is_data(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          case x in foo|tools/x.sh) echo ok;; esac\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_array_assignment_element_is_data(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          arr=(tools/x.sh)\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_pipe_into_script_still_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          a | ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_bash_dash_c_command_string_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          bash -c ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_time_option_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          time -p ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_xargs_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          xargs ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_make_at_prefix_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "Makefile").write_text("t:\n\t@tools/x.sh\n", encoding="utf-8")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_quoted_operator_is_not_a_separator(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          X="a|b" ./tools/x.sh\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_yaml_paths_list_is_not_a_reference(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / ".github" / "workflows" / "w.yml").write_text(
        "on:\n"
        "  push:\n"
        "    paths:\n"
        '      - "tools/x.sh"\n'
        "      - tools/x.sh\n"
        "jobs:\n"
        "  x:\n"
        "    steps:\n"
        "      - name: t\n"
        "        run: echo ok\n",
        encoding="utf-8",
    )
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_escaped_operator_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          FOO=a\\|b ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_eval_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          eval ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_nested_wrapper_with_interpreter_is_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          timeout 1 sudo bash ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_python_dash_c_argument_is_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          python3 -c "print(1)" ./tools/x.sh\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_at_in_assignment_value_is_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          SCRIPT=@tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_make_dash_prefix_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "Makefile").write_text("t:\n\t-tools/x.sh\n", encoding="utf-8")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_inline_step_run_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / ".github" / "workflows" / "w.yml").write_text(
        "jobs:\n"
        "  x:\n"
        "    steps:\n"
        "      - run: tools/x.sh --flag\n",
        encoding="utf-8",
    )
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_env_bash_dash_c_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          env bash -c ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_timeout_signal_option_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          timeout -s KILL 1 ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_xargs_arg_file_option_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          xargs -a list.txt ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_sed_replacement_text_is_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          sed -e "s|./tools/x.sh|dest.sh|" file\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_combined_make_prefixes_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "Makefile").write_text("t:\n\t@-tools/x.sh\n", encoding="utf-8")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_shell_chain_continuation_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "Makefile").write_text(
        "t:\n\ttrap 'rm -f x' EXIT; \\\n\t\ttools/z.sh\n", encoding="utf-8"
    )
    _add(repo, "tools/z.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_interpreter_continuation_arguments_are_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "Makefile").write_text(
        "t:\n\tpython3 -m pytest \\\n\t\ttools/t.py -q\n", encoding="utf-8"
    )
    _add(repo, "tools/t.py", 0o600, content="def test_x():\n    assert True\n")
    result = _run(repo)
    assert result.returncode == 0


def test_assignment_continuation_reference_fails(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          FOO=1 \\\n            tools/z.sh --flag\n')
    _add(repo, "tools/z.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_bash_c_command_string_with_argument_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          bash -c "./tools/x.sh --flag"\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_bash_c_echo_argument_is_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          bash -c "echo ./tools/x.sh --flag"\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_eval_command_string_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          eval "tools/x.sh --flag"\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_escaped_quote_in_assignment_does_not_break_masking(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          X="a\\"; echo" ./tools/x.sh\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_dollar_substitution_in_double_quotes_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          x="$(./tools/x.sh)"\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_backtick_substitution_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          x=`./tools/x.sh`\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_single_quoted_substitution_is_literal(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          x='$(./tools/x.sh)'\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_substitution_echo_argument_is_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          x="$(echo ./tools/x.sh)"\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_echoing_bash_dash_c_is_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          echo bash -c "./tools/x.sh"\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_positional_argument_after_command_string_is_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          bash -c "echo hi" ./tools/x.sh\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_redirect_target_is_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, "          > ./tools/x.sh\n")
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 0


def test_quoted_wrapper_operand_still_requires_exec_bit(tmp_path):
    repo = _make_repo(tmp_path)
    _workflow(repo, '          sudo -u "user" ./tools/x.sh\n')
    _add(repo, "tools/x.sh", 0o600)
    result = _run(repo)
    assert result.returncode == 1


def test_assignment_interpreter_chain_arguments_are_not_flagged(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "Makefile").write_text(
        "t:\n\tFOO=1 python3 -m pytest \\\n\t\ttools/t.py -q\n",
        encoding="utf-8",
    )
    _add(repo, "tools/t.py", 0o600, content="def test_x():\n    assert True\n")
    result = _run(repo)
    assert result.returncode == 0
