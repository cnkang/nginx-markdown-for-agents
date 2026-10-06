"""Guards the import bootstrap every release-gate script depends on.

A release-gate script that only puts ``<repo>/tools`` on ``sys.path`` works when
a test imports it from the repository root and dies with
``ModuleNotFoundError: No module named 'tools'`` the moment anything imports it
by absolute path. That is not hypothetical: Python puts the SCRIPT's directory on
``sys.path``, not the working directory, so the release gate -- which runs these
scripts inside a container whose workdir is the repository root -- hit exactly
that failure in ``validate_release_evidence_manifest.py``.

These tests execute the scripts the way the gate does -- absolute path, from an
unrelated working directory -- so the failure surfaces locally instead of hours
later on the runner.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
GATES = REPO_ROOT / "tools" / "release" / "gates"

# Scripts that import ``tools.<package>.<module>`` and therefore need the
# repository root on sys.path, not just ``tools/``. Keep this list to the ones a
# caller can actually run as programs; test modules are collected by pytest,
# which already puts the rootdir on sys.path.
_ABSOLUTE_IMPORT_SCRIPTS = [
    GATES / "validate_release_evidence_manifest.py",
    GATES / "generate_release_gate_manifests.py",
    GATES / "validate_pre_lts_status.py",
    REPO_ROOT / "tools" / "release" / "matrix" / "completeness_check.py",
]


def _imported_tools_modules(path: Path) -> set[str]:
    """Return the ``tools.*`` modules the file imports, lazily or not."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("tools"):
            assert node.module is not None  # narrowed by the startswith check
            found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("tools"):
                    found.add(alias.name)
    return found


def _tools_parent_level() -> int:
    """The ``parents[N]`` index that lands on the repository root.

    Derived from this file's own depth rather than hardcoded, so the guard keeps
    working if the test moves: every ``tools/...`` script is below the root, and
    the root is one level above ``tools/``.
    """
    return len(REPO_ROOT.relative_to(GATES).parts)


def _module_body_imports_cleanly(path: Path) -> bool:
    """Execute the module body from an unrelated cwd and report whether it works.

    This is the ONLY judgement used. Four static approximations were tried first
    -- a literal substring, then AST matchers for the argument shapes -- and each
    one mis-classified correct files in both directions:

    * accepting ``REPO_ROOT / "tools"`` as the root passes the exact shape the
      guard exists to catch;
    * rejecting ``str(Path(__file__).resolve().parents[N])`` flags files that do
      bootstrap, because the root is spelled without the name REPO_ROOT.

    Running the code has no such blind spot. ``run_name`` is not ``__main__`` so
    the ``if __name__ == ...`` blocks stay out of it and only the top-level
    imports run -- which is precisely what the bootstrap exists for. ``__file__``
    is registered in ``sys.modules`` first because a dataclass decorator reaches
    for it; without that, a correct file fails for an unrelated reason.
    """
    # PYTHONPATH must not reach the probe. The suite itself runs with
    # PYTHONPATH=. (that is how these gates are invoked), and an inherited value
    # would let a script with no bootstrap import successfully -- the guard would
    # pass the exact defect it exists to catch.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib.util, sys\n"
            "path = sys.argv[1]\n"
            "spec = importlib.util.spec_from_file_location('probe_target', path)\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            # Register before exec: a dataclass decorator resolves its own
            # module through sys.modules and fails when the name is absent.
            "sys.modules['probe_target'] = mod\n"
            "spec.loader.exec_module(mod)\n"
            "print('PROBE-OK')",
            str(path),
        ],
        cwd=REPO_ROOT.parent,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    return "PROBE-OK" in result.stdout


def _is_invoked_as_a_program(path: Path) -> bool:
    """True when something outside this file runs the script.

    Scans the Makefile and the workflows rather than trusting a hand-kept list,
    so a gate that starts calling a new script is caught by the guard instead of
    by the runner.
    """
    needle = str(path.relative_to(REPO_ROOT))
    callers = [
        REPO_ROOT / "Makefile",
        *sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")),
    ]
    for caller in callers:
        try:
            text = caller.read_text(encoding="utf-8")
        except OSError:
            continue
        if needle in text:
            return True
    return False


def test_every_script_importing_tools_packages_is_covered():
    """Any script importing ``tools.*`` must bootstrap the repository root.

    Scanning the tree rather than trusting the inventory is what keeps a newly
    added script from silently shipping the same defect. Scripts something else
    executes get no exemption: the body probe passes for them too easily, so
    they must carry the bootstrap themselves.
    """
    uncovered = []
    for path in sorted((REPO_ROOT / "tools").rglob("*.py")):
        if "tests" in path.parts or path.name.startswith("test_"):
            continue
        if not _imported_tools_modules(path):
            continue
        # Executed, not inspected: every static approximation of this rule was
        # wrong in at least one direction. A clean body means the imports
        # resolve; that IS the property the gate needs.
        if _module_body_imports_cleanly(path):
            continue
        uncovered.append(path)

    assert not uncovered, {
        "scripts importing tools.* that fail when run from outside the root": [
            str(p.relative_to(REPO_ROOT)) for p in uncovered
        ],
        "hint": "add sys.path.insert(0, str(REPO_ROOT)) before the tools.* import",
    }


def test_the_validator_imports_by_absolute_path_from_another_directory(tmp_path):
    """The reported failure, reproduced: run it the way the release gate does.

    ``--mode real`` is not passed because the manifest is absent here; reaching
    ``_format_checker`` is what matters, and that is the import that failed.
    """
    target = GATES / "validate_release_evidence_manifest.py"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('m', {str(target)!r})\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "assert m._format_checker() is not None\n"
        "print('OK')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(probe)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
    )
    assert result.returncode == 0, {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "why": "importing the gate by absolute path from an unrelated cwd must work",
    }
    assert "OK" in result.stdout