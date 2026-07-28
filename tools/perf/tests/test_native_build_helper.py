"""Regression tests for reusable native NGINX runtime setup."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "tools" / "lib" / "nginx_markdown_native_build.sh"


def test_prepare_runtime_reuse_honors_explicit_module_path(tmp_path: Path) -> None:
    """A workflow-provided module path must be loaded when nginx cannot discover it."""
    nginx_root = tmp_path / "nginx"
    nginx_bin = nginx_root / "sbin" / "nginx"
    (nginx_root / "conf").mkdir(parents=True)
    nginx_bin.parent.mkdir(parents=True)
    (nginx_root / "conf" / "mime.types").write_text(
        "types { text/plain txt; }\n", encoding="utf-8"
    )
    nginx_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    nginx_bin.chmod(0o755)

    module_path = tmp_path / "module-runtime" / "ngx_http_markdown_filter_module.so"
    module_path.parent.mkdir()
    module_path.write_bytes(b"module-bytes")
    runtime_dir = tmp_path / "runtime"

    command = (
        f'source "{HELPER}"; '
        f'MODULE_SO="{module_path}" '
        f'markdown_prepare_runtime_reuse "{nginx_bin}" "{runtime_dir}"'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "load_module modules/ngx_http_markdown_filter_module.so;"
    )
    copied_module = (
        runtime_dir / "modules" / "ngx_http_markdown_filter_module.so"
    )
    assert copied_module.read_bytes() == b"module-bytes"
