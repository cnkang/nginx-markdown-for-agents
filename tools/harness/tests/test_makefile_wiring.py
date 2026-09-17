"""Makefile housekeeping: phony coverage and wired test entry points."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")


def _phony_names(text: str) -> set[str]:
    """Return every name declared by any `.PHONY:` list in the Makefile."""
    names: set[str] = set()
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith(".PHONY:"):
            index += 1
            continue
        content = line[len(".PHONY:"):]
        while True:
            stripped = content.strip()
            continued = stripped.endswith("\\")
            if continued:
                stripped = stripped[:-1]
            names.update(stripped.split())
            index += 1
            if not continued or index >= len(lines):
                break
            content = lines[index]
    return names


def _target_recipe(text: str, target: str) -> str:
    """Return the tab-indented recipe lines that belong to one target."""
    recipe: list[str] = []
    collecting = False
    for line in text.splitlines():
        if line.startswith(target + ":"):
            collecting = True
            continue
        if not collecting:
            continue
        if line.startswith("\t"):
            recipe.append(line)
        elif line.strip():
            break
    return "\n".join(recipe)


def test_recursively_invoked_targets_are_phony() -> None:
    """Targets invoked through `$(MAKE)` stay phony so no file can shadow them."""
    names = _phony_names(MAKEFILE)

    for target in (
        "ci-local-check",
        "release-gates-check-070-strict",
        "verify-real-nginx-ims-e2e",
    ):
        assert target in names, target
    # Positive control: the parser really reads the list.
    assert "test-all" in names


def test_docs_and_sonar_suites_stay_wired_into_test_harness() -> None:
    """The docs and sonar pytest suites are executed by `make test-harness`."""
    recipe = _target_recipe(MAKEFILE, "test-harness")

    assert "tools/docs/tests/" in recipe
    assert "tools/sonar/tests/" in recipe


def test_every_shell_harness_test_is_registered() -> None:
    """No `test_*.sh` in tools/harness/tests may be left unwired.

    The pytest line auto-collects Python tests, but a shell test has to be
    listed explicitly: an unlisted one is a dead test that every entry point
    silently skips.
    """
    tests_dir = REPO_ROOT / "tools/harness/tests"
    on_disk = sorted(
        path.name for path in tests_dir.glob("test_*.sh")
    )
    recipe = _target_recipe(MAKEFILE, "test-harness")
    unregistered = [name for name in on_disk if f"tests/{name}" not in recipe]

    assert not unregistered, unregistered


def test_streaming_test_target_keeps_its_compile_guard() -> None:
    """`streaming_test.c` must be compiled with the streaming macro.

    Without `-DMARKDOWN_STREAMING_ENABLED` the unit binary builds its stub
    `main`, which prints "SKIPPED" and exits 0 — a silent green.  The macro
    therefore has to stay on the `streaming`) case of the per-target cflags
    switch in components/nginx-module/tests/Makefile.
    """
    makefile = (
        REPO_ROOT / "components/nginx-module/tests/Makefile"
    ).read_text(encoding="utf-8")
    case_lines = [
        line
        for line in makefile.splitlines()
        if line.strip().startswith("streaming)")
    ]

    assert len(case_lines) == 1, case_lines
    assert "-DMARKDOWN_STREAMING_ENABLED" in case_lines[0], case_lines[0]
