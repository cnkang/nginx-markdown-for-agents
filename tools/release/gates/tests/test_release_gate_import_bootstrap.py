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


def _is_repo_root_expr(node: ast.AST) -> bool:
    """True for REPO_ROOT, REPO_ROOT / x, and str(...) of either.

    The earlier version only matched a bare Name or BinOp, so the very common
    ``sys.path.insert(0, str(REPO_ROOT))`` -- a Call wrapping the Name -- was
    reported as missing and correct files looked broken.
    """
    if isinstance(node, ast.Call):
        # str(REPO_ROOT) / str(REPO_ROOT / "tools")
        return bool(node.args) and _is_repo_root_expr(node.args[0])
    if isinstance(node, ast.Name):
        return node.id == "REPO_ROOT"
    if isinstance(node, ast.BinOp):
        # REPO_ROOT / "tools"
        return _is_repo_root_expr(node.left)
    return False


def _is_sys_path_insert(node: ast.AST) -> bool:
    """True for a ``sys.path.insert(...)`` call."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    func = node.func
    if func.attr != "insert" or not isinstance(func.value, ast.Attribute):
        return False
    return func.value.attr == "path"


def _mentions_repo_root_in_a_collection(tree: ast.AST) -> bool:
    """True when a candidate tuple/list/set holds ``str(REPO_ROOT)``.

    Covers the loop spelling: ``for _p in (str(REPO_ROOT), ...): sys.path.insert(0, _p)``
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            continue
        if any(_is_repo_root_expr(elt) for elt in node.elts):
            return True
    return False


def _bootstraps_repository_root(source: str) -> bool:
    """Does the file put the repository root on sys.path, in any spelling?

    Parsed rather than grepped: matching the literal ``str(REPO_ROOT))`` missed
    the loop form (``for _p in (str(REPO_ROOT), ...): insert(_p)``) and reported
    correct files as broken.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not _is_sys_path_insert(node):
            continue
        assert isinstance(node, ast.Call)  # narrowed by _is_sys_path_insert
        # sys.path.insert(0, <value>) or sys.path.insert(<value>, 0)
        if any(_is_repo_root_expr(arg) for arg in node.args):
            return True
        # sys.path.insert(0, _p) where _p came from a tuple of candidates.
        if any(isinstance(arg, ast.Name) for arg in node.args):
            if _mentions_repo_root_in_a_collection(tree):
                return True
    return False


def test_the_listed_scripts_really_import_tools_packages():
    """Guard the inventory itself: an entry that stops importing tools.* is dead
    weight, and a missing one would let this file pass while a script breaks."""
    for script in _ABSOLUTE_IMPORT_SCRIPTS:
        assert script.is_file(), f"inventory lists a missing script: {script}"
        assert _imported_tools_modules(script), (
            f"{script.name} no longer imports any tools.* module; drop it from the "
            "inventory rather than asserting a bootstrap it does not need"
        )


def _module_body_imports_cleanly(path: Path) -> bool:
    """Execute the module body from an unrelated cwd and report whether it works.

    A static bootstrap check cannot tell a file that needs the repository root
    from one that happens to be safe because every caller already supplied it.
    Running the body settles it. ``run_name`` is not ``__main__`` so the
    ``if __name__ == ...`` blocks stay out of it -- only the top-level imports
    execute, which is what the bootstrap exists for.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import runpy,sys; runpy.run_path(sys.argv[1], run_name='probe_not_main')", str(path)],
        cwd=REPO_ROOT.parent,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.returncode == 0


def test_every_script_importing_tools_packages_is_covered():
    """Any script importing ``tools.*`` must bootstrap the repository root.

    Scanning the tree rather than trusting the inventory is what keeps a newly
    added script from silently shipping the same defect. The check is the
    bootstrap, not the spelling: a file whose top-level ``try: from tools...``
    falls back to a bare-name import needs the root added before that try, and
    several gate scripts use exactly that shape.
    """
    uncovered = []
    for path in sorted((REPO_ROOT / "tools").rglob("*.py")):
        if "tests" in path.parts or path.name.startswith("test_"):
            continue
        if not _imported_tools_modules(path):
            continue
        source = path.read_text(encoding="utf-8")
        if _bootstraps_repository_root(source):
            continue
        # No bootstrap at all: only safe if the file is never run as a program
        # from outside the root. Run the body to find out rather than guessing.
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
    )
    assert result.returncode == 0, {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "why": "importing the gate by absolute path from an unrelated cwd must work",
    }
    assert "OK" in result.stdout
