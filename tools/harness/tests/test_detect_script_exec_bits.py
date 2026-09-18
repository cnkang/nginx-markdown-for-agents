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
