"""Behaviour tests for detect_unused_imports.py.

Each case is a whole file written to a temporary tree, so the detector is
exercised the way it runs in the repository -- including the cases that must
*not* be reported. A detector that only ever says "yes" would pass a suite of
positives.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.harness.detect_unused_imports import collect_errors

# name -> (source, should_be_reported, why)
CASES: dict[str, tuple[str, bool, str]] = {
    "unused_module_import": (
        "import os\n\nprint('hi')\n",
        True,
        "rule 1: nothing loads os",
    ),
    "shadow_outer_unused": (
        "import sys\n\n\ndef f():\n    import sys\n    return sys.argv\n",
        True,
        "rule 2: nothing outside f() resolves to the module-level sys",
    ),
    "shadow_outer_used_elsewhere": (
        "import sys\n\n\ndef f():\n    import sys\n    return sys.argv\n"
        "\n\ndef g():\n    return sys.executable\n",
        False,
        "g() resolves to the module-level import, so it is not dead",
    ),
    "annotation_only_use": (
        "from pathlib import Path\n\n\ndef f(p: Path) -> None:\n    print(p)\n",
        False,
        "a parameter annotation evaluates in the enclosing scope",
    ),
    "string_annotation_use": (
        "from pathlib import Path\n\n\ndef f(p: \"Path\") -> None:\n    print(p)\n",
        False,
        "a name used only in a string annotation is still a use",
    ),
    "return_annotation_use": (
        "from typing import Optional\n\n\ndef f() -> Optional[int]:\n    return None\n",
        False,
        "the return annotation evaluates outside the function",
    ),
    "decorator_use": (
        "import pytest\n\n\n@pytest.mark.parametrize('x', [1])\ndef f(x):\n    return x\n",
        False,
        "the decorator is evaluated outside the function",
    ),
    "default_value_use": (
        "import re\n\n\ndef f(pattern=re.compile):\n    return pattern\n",
        False,
        "a default value is evaluated outside the function",
    ),
    "alias_use": (
        "import os.path as osp\n\nprint(osp.join('a'))\n",
        False,
        "the alias binds and is loaded",
    ),
    "dotted_attribute_root": (
        "import os\n\nprint(os.sep)\n",
        False,
        "os is loaded through attribute access",
    ),
    "future_import": (
        "from __future__ import annotations\n\nprint('hi')\n",
        False,
        "__future__ binds no runtime name",
    ),
    "nested_shadow_outer_dead": (
        "import sys\n\n\ndef outer():\n    def inner():\n        import sys\n"
        "        return sys.argv\n    return inner\n",
        True,
        "only inner() imports sys, so the module-level binding is unreachable",
    ),
    "nested_shadow_outer_alive": (
        "import sys\n\nprint(sys.executable)\n\n\ndef outer():\n"
        "    def inner():\n        import sys\n        return sys.argv\n    return inner\n",
        False,
        "module scope uses the module-level sys",
    ),
    "class_body_use": (
        "from pathlib import Path\n\n\nclass C:\n    root = Path('.')\n",
        False,
        "a class body is still module scope for import resolution",
    ),
    "lambda_body_use": (
        "import sys\n\nf = lambda: sys.executable\n",
        False,
        "a lambda body resolves to the module-level import",
    ),
    "subscripted_annotation_use": (
        "from typing import List\n\nitems: List[str] = []\n",
        False,
        "List is loaded inside a subscripted annotation",
    ),
    "class_attribute_annotation_use": (
        "from typing import Any\n\n\nclass C:\n    value: Any = None\n",
        False,
        "an annotated class attribute is a type reference",
    ),
    "string_literal_is_not_a_use": (
        "import sys\n\nmsg = 'sys'\nprint(msg)\n",
        True,
        "an ordinary string is data, not a reference to the binding",
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_detector_classifies_case(tmp_path: Path, name: str) -> None:
    source, should_report, why = CASES[name]
    (tmp_path / "sample.py").write_text(source, encoding="utf-8")

    errors = collect_errors(tmp_path)

    reported = [e for e in errors if "sample.py" in e]
    assert bool(reported) is should_report, (
        f"{name}: expected report={should_report}, got {reported} ({why})"
    )


def test_detector_ignores_a_file_it_cannot_parse(tmp_path: Path) -> None:
    """A syntax error is another gate's problem; this one must not mask it."""
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    (tmp_path / "fine.py").write_text("import os\n", encoding="utf-8")

    errors = collect_errors(tmp_path)

    assert not any("broken.py" in e for e in errors)
    assert any("fine.py" in e and "os" in e for e in errors)


def test_detector_skips_build_and_vendor_trees(tmp_path: Path) -> None:
    for noisy in ("build", "target", ".venv", "node_modules", "__pycache__"):
        directory = tmp_path / noisy
        directory.mkdir()
        (directory / "sample.py").write_text("import os\n", encoding="utf-8")

    assert collect_errors(tmp_path) == []
