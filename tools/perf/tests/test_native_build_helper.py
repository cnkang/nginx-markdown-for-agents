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


def test_prepare_runtime_reuse_rejects_unsafe_explicit_module_filename(
    tmp_path: Path,
) -> None:
    """An explicit module override must have a safe NGINX module basename."""
    nginx_root = tmp_path / "nginx"
    nginx_bin = nginx_root / "sbin" / "nginx"
    (nginx_root / "conf").mkdir(parents=True)
    nginx_bin.parent.mkdir(parents=True)
    (nginx_root / "conf" / "mime.types").write_text(
        "types { text/plain txt; }\n", encoding="utf-8"
    )
    nginx_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    nginx_bin.chmod(0o755)

    module_path = tmp_path / "module-runtime" / "unsafe module.so"
    module_path.parent.mkdir()
    module_path.write_bytes(b"module-bytes")
    runtime_dir = tmp_path / "runtime"

    command = f'source "{HELPER}"; markdown_prepare_runtime_reuse "{nginx_bin}" "{runtime_dir}"'
    env = os.environ.copy()
    env["MODULE_SO"] = str(module_path)
    result = subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    assert "unsafe module filename: unsafe module.so" in result.stderr


def test_module_beside_the_binary_is_discovered(tmp_path: Path) -> None:
    """A compiled source tree keeps its dynamic module in objs/."""
    build_root = tmp_path / "nginx-1.30.4"
    objs = build_root / "objs"
    (build_root / "conf").mkdir(parents=True)
    objs.mkdir(parents=True)
    (build_root / "conf" / "mime.types").write_text(
        "types { text/plain txt; }\n", encoding="utf-8"
    )
    nginx_bin = objs / "nginx"
    nginx_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    nginx_bin.chmod(0o755)
    module = objs / "ngx_http_markdown_filter_module.so"
    module.write_bytes(b"module-bytes")
    runtime_dir = tmp_path / "runtime"

    command = (
        f'source "{HELPER}"; '
        f"unset MODULE_SO; "
        f'markdown_prepare_runtime_reuse "{nginx_bin}" "{runtime_dir}"'
    )
    env = os.environ.copy()
    env.pop("MODULE_SO", None)
    result = subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "load_module modules/ngx_http_markdown_filter_module.so;"
    )
    assert (runtime_dir / "modules" / module.name).read_bytes() == b"module-bytes"


def test_module_is_found_when_the_prefix_modules_dir_is_empty(
    tmp_path: Path,
) -> None:
    """An empty <prefix>/modules must not stop the search for the module."""
    build_root = tmp_path / "nginx-1.30.4"
    objs = build_root / "objs"
    (build_root / "conf").mkdir(parents=True)
    (build_root / "modules").mkdir()
    objs.mkdir(parents=True)
    (build_root / "conf" / "mime.types").write_text(
        "types { text/plain txt; }\n", encoding="utf-8"
    )
    nginx_bin = objs / "nginx"
    nginx_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    nginx_bin.chmod(0o755)
    module = objs / "ngx_http_markdown_filter_module.so"
    module.write_bytes(b"module-bytes")

    command = (
        f'source "{HELPER}"; '
        f'markdown_find_dynamic_markdown_module "{nginx_bin}"'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(module)


def test_unrelated_markdown_library_is_not_selected(tmp_path: Path) -> None:
    """Only the NGINX module counts, not any .so with markdown in its name."""
    build_root = tmp_path / "nginx-1.30.4"
    objs = build_root / "objs"
    (build_root / "conf").mkdir(parents=True)
    objs.mkdir(parents=True)
    (build_root / "conf" / "mime.types").write_text(
        "types { text/plain txt; }\n", encoding="utf-8"
    )
    nginx_bin = objs / "nginx"
    nginx_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    nginx_bin.chmod(0o755)
    (objs / "libmarkdown-parser.so").write_bytes(b"not-the-module")
    module = objs / "ngx_http_markdown_filter_module.so"
    module.write_bytes(b"module-bytes")

    command = (
        f'source "{HELPER}"; '
        f'markdown_find_dynamic_markdown_module "{nginx_bin}"'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(module)


def test_discovered_module_with_unsafe_name_is_rejected(tmp_path: Path) -> None:
    """A discovered basename never reaches the generated load_module line."""
    build_root = tmp_path / "nginx-1.30.4"
    objs = build_root / "objs"
    (build_root / "conf").mkdir(parents=True)
    objs.mkdir(parents=True)
    (build_root / "conf" / "mime.types").write_text(
        "types { text/plain txt; }\n", encoding="utf-8"
    )
    nginx_bin = objs / "nginx"
    nginx_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    nginx_bin.chmod(0o755)
    (objs / "ngx_http_markdown bad.so").write_bytes(b"module-bytes")

    command = (
        f'source "{HELPER}"; '
        f"unset MODULE_SO; "
        f'markdown_prepare_runtime_reuse "{nginx_bin}" "{tmp_path / "runtime"}"'
    )
    env = os.environ.copy()
    env.pop("MODULE_SO", None)
    result = subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    assert "unsafe module filename" in result.stderr
    assert "load_module" not in result.stdout


def test_relative_modules_path_is_resolved_against_the_prefix(
    tmp_path: Path,
) -> None:
    """A relative --modules-path is relative to the prefix, not the caller."""
    prefix = tmp_path / "pfx"
    (prefix / "sbin").mkdir(parents=True)
    (prefix / "conf").mkdir()
    (prefix / "conf" / "mime.types").write_text(
        "types { text/plain txt; }\n", encoding="utf-8"
    )
    nginx_bin = prefix / "sbin" / "nginx"
    nginx_bin.write_text(
        "#!/bin/sh\n"
        'echo "nginx version: nginx/1.30.4"\n'
        'echo "configure arguments: --modules-path=lib/nginx/modules"\n',
        encoding="utf-8",
    )
    nginx_bin.chmod(0o755)
    modules = prefix / "lib" / "nginx" / "modules"
    modules.mkdir(parents=True)
    module = modules / "ngx_http_markdown_filter_module.so"
    module.write_bytes(b"module-bytes")

    command = (
        f'source "{HELPER}"; '
        f'markdown_find_dynamic_markdown_module "{nginx_bin}"'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(module)


def test_module_name_wins_over_a_similar_name(tmp_path: Path) -> None:
    """A differently suffixed module must not win because it sorts first."""
    build_root = tmp_path / "nginx-1.30.4"
    objs = build_root / "objs"
    (build_root / "conf").mkdir(parents=True)
    objs.mkdir(parents=True)
    (build_root / "conf" / "mime.types").write_text(
        "types { text/plain txt; }\n", encoding="utf-8"
    )
    nginx_bin = objs / "nginx"
    nginx_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    nginx_bin.chmod(0o755)
    (objs / "ngx_http_markdown-rogue.so").write_bytes(b"not-the-module")
    module = objs / "ngx_http_markdown_filter_module.so"
    module.write_bytes(b"module-bytes")

    command = (
        f'source "{HELPER}"; '
        f'markdown_find_dynamic_markdown_module "{nginx_bin}"'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(module)


def test_modules_path_is_relative_to_the_reported_prefix(tmp_path: Path) -> None:
    """A binary outside its prefix still finds a prefix-relative modules path."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    prefix = tmp_path / "prefix"
    (prefix / "conf").mkdir(parents=True)
    (prefix / "conf" / "mime.types").write_text(
        "types { text/plain txt; }\n", encoding="utf-8"
    )
    nginx_bin = bin_dir / "nginx"
    nginx_bin.write_text(
        "#!/bin/sh\n"
        'echo "configure arguments: '
        f'--prefix={prefix} --modules-path=lib/nginx/modules"\n',
        encoding="utf-8",
    )
    nginx_bin.chmod(0o755)
    modules = prefix / "lib" / "nginx" / "modules"
    modules.mkdir(parents=True)
    module = modules / "ngx_http_markdown_filter_module.so"
    module.write_bytes(b"module-bytes")

    command = (
        f'source "{HELPER}"; '
        f'markdown_find_dynamic_markdown_module "{nginx_bin}"'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(module)
