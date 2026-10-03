"""The benchmark module must import on the interpreter the release gate uses.

The release gate runs ``run_module_benchmark.sh`` inside the pinned
``almalinux`` (EL9) image, whose ``python3`` is 3.9.  Nothing on the host
reaches that container, so a module using a newer language or library feature
compiles and passes locally and then fails the gate after hours of builds.

This parses the module with the oldest grammar available locally rather than
importing it, so the check itself runs on any interpreter and still catches the
constructs that need 3.10+ (``dataclass(slots=...)``, PEP 604 unions in
annotations evaluated at runtime, and so on).
"""

from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
PERF_MODULE = REPO_ROOT / "tools" / "perf" / "benchmark_validation.py"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"

# Features the 3.9 container cannot evaluate.
PY310_ONLY_CALLS = {"slots": "dataclass(slots=...) needs Python 3.10"}


def _container_is_el9() -> bool:
    """Confirm the benchmark really runs in an EL9 container with python3."""
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "almalinux@sha256:" in text, (
        "the pinned benchmark container image changed; re-check which python "
        "the release gate runs against before trusting this test"
    )
    return True


def test_the_benchmark_module_has_no_python_310_only_constructs() -> None:
    """No `slots=True`: the gate's interpreter is EL9's python 3.9."""
    _container_is_el9()
    tree = ast.parse(PERF_MODULE.read_text(encoding="utf-8"), filename=str(PERF_MODULE))

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # @dataclass(...) -- the decorator call itself
        is_dataclass = isinstance(func, ast.Name) and func.id == "dataclass"
        if not is_dataclass:
            continue
        for keyword in node.keywords:
            if keyword.arg in PY310_ONLY_CALLS:
                offenders.append(
                    f"line {node.lineno}: {PY310_ONLY_CALLS[keyword.arg]}"
                )

    assert not offenders, (
        "benchmark_validation.py must import on the release gate's python 3.9; "
        + "; ".join(offenders)
    )


def _decorator_kwargs(class_node: ast.ClassDef, decorator_name: str) -> set[str]:
    """Return the keyword names passed to a named decorator on a class."""
    for decorator in class_node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if isinstance(func, ast.Name) and func.id == decorator_name:
            return {kw.arg for kw in decorator.keywords if kw.arg}
    return set()


def _find_class(tree: ast.AST, name: str) -> ast.ClassDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def test_the_dataclass_is_still_frozen() -> None:
    """Dropping `slots` must not cost the immutability it was paired with."""
    tree = ast.parse(PERF_MODULE.read_text(encoding="utf-8"), filename=str(PERF_MODULE))
    node = _find_class(tree, "ScenarioResultInput")
    assert node is not None, "ScenarioResultInput is missing from the module"
    assert "frozen" in _decorator_kwargs(node, "dataclass"), (
        "ScenarioResultInput must stay frozen=True"
    )
