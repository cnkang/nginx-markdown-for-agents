"""The benchmark module must import on the interpreter the release gate uses.

The release gate runs ``run_module_benchmark.sh`` inside the pinned
``almalinux`` (EL9) image, whose ``python3`` is 3.9.  Nothing on the host
reaches that container, so a module using a newer language or library feature
compiles and passes locally and then fails the gate after hours of builds.

It parses the module with ``ast`` rather than importing it, so the check itself
runs on any interpreter and needs no 3.9 to exist locally.

Scope, stated exactly: this enforces the ONE construct that actually broke the
gate -- ``@dataclass(slots=...)``. It does not attempt to police every 3.10+
feature. PEP 604 unions need no check because the module carries
``from __future__ import annotations``, which defers all annotations; a future
``match``, ``itertools.pairwise`` or ``zip(strict=)`` would slip past. Widening
the coverage is a judgement call for whoever adds such a construct, not
something this test pretends to do.
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import Any, TypeGuard

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
PERF_MODULE = REPO_ROOT / "tools" / "perf" / "benchmark_validation.py"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"

# Features the 3.9 container cannot evaluate.
PY310_ONLY_CALLS = {"slots": "dataclass(slots=...) needs Python 3.10"}


def _is_named_call(node: ast.AST, name: str) -> TypeGuard[ast.Call]:
    """True when `node` is a call to `name`, bare or attribute-qualified.

    Both `@dataclass(...)` and `@dataclasses.dataclass(...)` name the same
    thing. Matching only the bare form would let a later `import dataclasses`
    refactor walk straight past every guard in this file.

    Declared as a TypeGuard so callers can reach `.keywords` and `.lineno`.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == name
    if isinstance(func, ast.Attribute):
        return func.attr == name
    return False


# The digest whose `python3` this whole file exists for: EL9 ships 3.9, which is
# why `@dataclass(slots=...)` could not be used. Matching the exact digest rather
# than the `almalinux@sha256:` prefix means an image bump FAILS this test instead
# of silently keeping it green while the 3.9 constraint no longer holds.
KNOWN_EL9_DIGEST = "d2515c769e7b73f95c4fde38c0a505336ff38f14990c0b7253b77060a049a743"


def _container_is_el9() -> None:
    """Confirm the benchmark still runs in the exact EL9 image this pins."""
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r"almalinux@sha256:([0-9a-f]{64})", text)
    assert match is not None, (
        "no pinned almalinux image found in the release workflow; the benchmark "
        "may have moved off the EL9 image this guard assumes"
    )
    assert match.group(1) == KNOWN_EL9_DIGEST, (
        f"the benchmark image is now {match.group(1)[:12]}, but this guard is "
        f"written for {KNOWN_EL9_DIGEST[:12]} (EL9, python3 3.9). Check that "
        "image's python version, then update this constant and whatever the new "
        "interpreter allows or forbids."
    )


def test_the_benchmark_module_has_no_python_310_only_constructs() -> None:
    """No `slots=True`: the gate's interpreter is EL9's python 3.9."""
    _container_is_el9()
    tree = ast.parse(PERF_MODULE.read_text(encoding="utf-8"), filename=str(PERF_MODULE))

    offenders: list[str] = []
    for node in ast.walk(tree):
        # @dataclass(...) -- the decorator call itself
        if not _is_named_call(node, "dataclass"):
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


def _decorator_kwargs(class_node: ast.ClassDef, decorator_name: str) -> dict[str, Any]:
    """Return each keyword passed to a named decorator, mapped to its value.

    Literals are unwrapped to Python values so a caller can assert the VALUE,
    not merely that the keyword is present: `frozen=False` must not satisfy a
    test that means to require `frozen=True`.
    """
    for decorator in class_node.decorator_list:
        if _is_named_call(decorator, decorator_name):
            return {
                kw.arg: _literal(kw.value)
                for kw in decorator.keywords
                if kw.arg is not None
            }
    return {}


def _literal(node: ast.expr) -> Any:
    """Best-effort unwrap of a decorator keyword value."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return node.id
    return None


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
    assert _decorator_kwargs(node, "dataclass").get("frozen") is True, (
        "ScenarioResultInput must stay frozen=True, not merely mention the "
        "keyword: frozen=False would satisfy a presence-only check"
    )
