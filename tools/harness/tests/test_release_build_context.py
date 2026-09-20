"""Each release build image must contain every tool script the in-container
``make build`` chain invokes.

The musl and glibc release tarballs are produced by
``tools/build_release/Dockerfile.musl`` / ``Dockerfile.glibc``, which copy only
an explicit allowlist of repository files into ``/src`` and then run
``make build``.  When a Makefile recipe gains a new ``tools/...`` script and
the Dockerfile allowlist is not extended, every build fails inside the
container with "No such file or directory" — and the musl path only runs from
the release workflow, so the drift stays latent until a release.

This test walks the recursive prerequisite closure of the ``build`` target,
collects the ``tools/`` scripts those recipes execute (plus their in-repo
import dependencies), and asserts each one is covered by a ``COPY`` source in
both Dockerfiles.  A negative fixture proves the checker fails on a missing
copy.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = REPO_ROOT / "Makefile"
# Both images share the same in-container build chain, so both must carry the
# same tool scripts.
DOCKERFILES = (
    REPO_ROOT / "tools/build_release/Dockerfile.musl",
    REPO_ROOT / "tools/build_release/Dockerfile.glibc",
)

_TARGET_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:(?![:=])(.*)$")
_SCRIPT_RE = re.compile(r"(tools/[A-Za-z0-9_./-]+\.py)")
_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+(tools(?:\.[A-Za-z0-9_]+)+)", re.M)
_COPY_RE = re.compile(r"^COPY\s+(.+)$", re.M)


def _target_recipes(text: str) -> dict[str, str]:
    """Map each make target to its recipe body (continuations included)."""
    recipes: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = _TARGET_RE.match(line)
        if match:
            name = match.group(1)
            current = name
            recipes.setdefault(name, []).append(match.group(2))
            continue
        if current is not None and line.startswith("\t"):
            recipes[current].append(line)
            continue
        if not line.startswith(("#", "\t")):
            current = None
    return {name: "\n".join(body) for name, body in recipes.items()}


def _prereq_closure(recipes: dict[str, str], target: str) -> list[str]:
    seen: list[str] = []
    queue = [target]
    while queue:
        name = queue.pop(0)
        if name in seen or name not in recipes:
            continue
        seen.append(name)
        header = recipes[name].splitlines()[0] if recipes[name] else ""
        for token in header.split():
            if token in recipes:
                queue.append(token)
    return seen


def _module_candidates(dotted: str) -> list[str]:
    """Candidate repo paths that satisfy importing ``tools.a.b``."""
    base = Path(*dotted.split("."))
    return [str(base) + ".py", str(base), str(base.parent)]


def _script_requirements(repo_root: Path) -> set[str]:
    recipes = _target_recipes((repo_root / "Makefile").read_text(encoding="utf-8"))
    closure = _prereq_closure(recipes, "build")
    required: set[str] = set()
    for name in closure:
        for script in _SCRIPT_RE.findall(recipes[name]):
            required.add(script)
    # In-repo imports of each required script (one level; enough for the
    # tools.lib helpers the gates import).
    for script in sorted(required):
        script_path = repo_root / script
        if not script_path.is_file():
            continue
        for dotted in _IMPORT_RE.findall(script_path.read_text(encoding="utf-8")):
            for candidate in _module_candidates(dotted):
                if (repo_root / candidate).exists():
                    required.add(candidate)
                    break
    return required


def _copy_sources(dockerfile: Path) -> list[str]:
    sources: list[str] = []
    for match in _COPY_RE.finditer(dockerfile.read_text(encoding="utf-8")):
        parts = match.group(1).split()
        # The final token is the destination; everything before it is a source.
        for token in parts[:-1]:
            if not token.startswith("--"):
                sources.append(token)
    return sources


def _uncovered(repo_root: Path, dockerfile: Path) -> list[str]:
    sources = _copy_sources(dockerfile)
    missing = []
    for required in sorted(_script_requirements(repo_root)):
        covered = any(
            required == source or required.startswith(source.rstrip("/") + "/")
            for source in sources
        )
        if not covered:
            missing.append(required)
    return missing


def test_release_build_images_cover_make_targets() -> None:
    for dockerfile in DOCKERFILES:
        missing = _uncovered(REPO_ROOT, dockerfile)
        assert missing == [], (
            f"make build invokes scripts missing from {dockerfile.name}'s "
            f"COPY allowlist: {missing}"
        )


def test_uncovered_reports_a_missing_script(tmp_path: Path) -> None:
    (tmp_path / "tools/missing").mkdir(parents=True)
    (tmp_path / "tools/missing/check.py").write_text(
        "print('x')\n", encoding="utf-8"
    )
    (tmp_path / "Makefile").write_text(
        "build: capability-check\n"
        "\t@echo done\n"
        "\n"
        "capability-check:\n"
        "\t@python3 tools/missing/check.py --source-root .\n",
        encoding="utf-8",
    )
    docker = tmp_path / "tools/build_release/Dockerfile.musl"
    docker.parent.mkdir(parents=True)
    docker.write_text("FROM alpine\nCOPY Makefile /src/Makefile\n", encoding="utf-8")
    assert _uncovered(tmp_path, docker) == ["tools/missing/check.py"]


if __name__ == "__main__":
    failed = False
    for dockerfile in DOCKERFILES:
        missing = _uncovered(REPO_ROOT, dockerfile)
        if missing:
            failed = True
            print(f"missing from {dockerfile.name}: " + ", ".join(missing))
    if failed:
        sys.exit(1)
    print("release build images cover the make build chain")
