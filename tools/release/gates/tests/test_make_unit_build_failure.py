"""Regression test for unit-build compile failure propagation."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
UNIT_TEST_ROOT = REPOSITORY_ROOT / "components" / "nginx-module" / "tests"


def test_unit_build_stops_after_compile_failure(tmp_path: Path) -> None:
    """A failed compile must prevent linking stale or partial objects."""
    fixture_root = tmp_path / "unit-tests"
    (fixture_root / "make").mkdir(parents=True)
    (fixture_root / "unit").mkdir()
    (fixture_root / "include").mkdir()
    (fixture_root / "helpers").mkdir()
    shutil.copy2(UNIT_TEST_ROOT / "Makefile", fixture_root / "Makefile")
    shutil.copy2(
        UNIT_TEST_ROOT / "make" / "targets.mk",
        fixture_root / "make" / "targets.mk",
    )
    for relative_path in (
        "unit/headers_test.c",
        "include/test_common.h",
        "helpers/headers_standalone.c",
    ):
        (fixture_root / relative_path).touch()

    compiler = tmp_path / "fake-cc.sh"
    compiler.write_text(
        """#!/bin/sh
set -eu

is_compile=0
is_probe=0
output=""
previous=""
for arg in "$@"; do
    [ "$arg" = "-c" ] && is_compile=1
    [ "$arg" = "-x" ] && is_probe=1
    if [ "$previous" = "-o" ]; then
        output="$arg"
    fi
    previous="$arg"
done

if [ "$is_probe" -eq 1 ]; then
    exit 0
fi

if [ "$is_compile" -eq 1 ]; then
    count=0
    if [ -f "$FAKE_CC_STATE" ]; then
        count="$(sed -n '1p' "$FAKE_CC_STATE")"
    fi
    count=$((count + 1))
    printf '%s\\n' "$count" > "$FAKE_CC_STATE"
    if [ "$count" -eq 1 ]; then
        printf 'intentional compile failure\\n' >&2
        exit 1
    fi
    : > "$output"
    exit 0
fi

printf 'link invoked\\n' >> "$FAKE_CC_LINK_MARKER"
: > "$output"
""",
        encoding="utf-8",
    )
    compiler.chmod(0o755)
    state_file = tmp_path / "compiler-state"
    link_marker = tmp_path / "link-marker"
    environment = os.environ.copy()
    environment["FAKE_CC_STATE"] = str(state_file)
    environment["FAKE_CC_LINK_MARKER"] = str(link_marker)

    completed = subprocess.run(
        ["make", "-B", "build/unit/headers", f"CC={compiler}"],
        cwd=fixture_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert not link_marker.exists()
    assert state_file.read_text(encoding="utf-8") == "1\n"
    assert "failed to compile unit source unit/headers_test.c" in completed.stderr
