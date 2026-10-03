"""Check the production Brotli configure probe and its selected build flags."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
INCLUDE_FLAG = "-I/fixture/brotli/include"
LIBRARY_FLAG = "-L/fixture/brotli/lib"


def run_probe(tmp_path, mode, flags_available=True, flag_variable="NGX_CC_OPT",
              pkg_config_available=False):
    """Run the configure block with a compiler that needs custom search paths."""
    config = (ROOT / "components/nginx-module/config").read_text()
    block = config.split("ngx_markdown_brotli_mode=", 1)[1]
    block = "ngx_markdown_brotli_mode=" + block.split(
        "# Keep the C compilation surface", 1)[0]
    compiler = tmp_path / "compiler"
    log = tmp_path / "compiler.args"
    compiler.write_text(r'''#!/bin/sh
printf '%s\n' "$@" > "$PROBE_LOG"
include_found=0
library_found=0
for argument in "$@"; do
    case "$argument" in
        -I/fixture/brotli/include) include_found=1 ;;
        -L/fixture/brotli/lib) library_found=1 ;;
        *) ;;
    esac
done
test "$include_found" = 1 && test "$library_found" = 1
''')
    compiler.chmod(0o755)
    pkg_config = tmp_path / "pkg-config"
    pkg_config.write_text("""#!/bin/sh
case "$1" in
    --exists) exit "$PKG_CONFIG_EXIT" ;;
    --cflags) printf '%s\\n' '-I/fixture/brotli/include' ;;
    --libs) printf '%s\\n' '-L/fixture/brotli/lib -lbrotlidec' ;;
    *) exit 1 ;;
esac
""")
    pkg_config.chmod(0o755)
    script = tmp_path / "probe.sh"
    script.write_text(r'''
set -eu
ngx_module_libs=""
''' + block + r'''
printf 'found=%s\ncflags=%s\nlibs=%s\n' \
    "$ngx_markdown_brotli_found" "$CFLAGS" "$ngx_module_libs"
''')
    env = os.environ.copy()
    for name in ("NGX_CC_OPT", "NGX_LD_OPT", "CFLAGS"):
        env.pop(name, None)
    env.update(CC=str(compiler), PROBE_LOG=str(log), CFLAGS="",
               NGX_MARKDOWN_BROTLI_STREAMING=mode,
               PKG_CONFIG_EXIT="0" if pkg_config_available else "1",
               PATH=str(tmp_path) + os.pathsep + env["PATH"])
    if flags_available:
        env[flag_variable] = INCLUDE_FLAG
        env["NGX_LD_OPT"] = LIBRARY_FLAG
    result = subprocess.run(["sh", str(script)], env=env, capture_output=True,
                            text=True, check=False)
    return result, log


@pytest.mark.parametrize("mode", ["on", "auto"])
@pytest.mark.parametrize("flag_variable", ["NGX_CC_OPT", "CFLAGS"])
def test_custom_search_paths_enable_brotli(tmp_path, mode, flag_variable):
    result, log = run_probe(tmp_path, mode, flag_variable=flag_variable)
    assert result.returncode == 0, result.stderr
    assert "found=yes" in result.stdout
    assert "-DNGX_HTTP_BROTLI" in result.stdout
    assert "libs= -lbrotlidec\n" in result.stdout
    arguments = log.read_text().splitlines()
    assert INCLUDE_FLAG in arguments
    assert LIBRARY_FLAG in arguments
    assert arguments.index(LIBRARY_FLAG) < arguments.index("-lbrotlidec")


@pytest.mark.parametrize("mode, expected_code", [("on", 1), ("auto", 0)])
def test_missing_library_preserves_mode_contract(tmp_path, mode, expected_code):
    result, log = run_probe(tmp_path, mode, flags_available=False)
    assert result.returncode == expected_code
    assert log.exists()
    assert "-DNGX_HTTP_BROTLI" not in result.stdout
    if mode == "on":
        assert "configuration error" in result.stderr
    else:
        assert "found=no" in result.stdout
        assert "libs=\n" in result.stdout


def test_off_mode_skips_compiler_and_brotli_linking(tmp_path):
    result, log = run_probe(tmp_path, "off")
    assert result.returncode == 0, result.stderr
    assert not log.exists()
    assert "found=no" in result.stdout
    assert "-DNGX_HTTP_BROTLI" not in result.stdout
    assert "libs=\n" in result.stdout


@pytest.mark.parametrize("mode", ["on", "auto"])
def test_pkg_config_search_paths_still_enable_brotli(tmp_path, mode):
    result, log = run_probe(tmp_path, mode, flags_available=False,
                            pkg_config_available=True)
    assert result.returncode == 0, result.stderr
    assert "found=yes" in result.stdout
    assert "-DNGX_HTTP_BROTLI" in result.stdout
    assert "libs= -L/fixture/brotli/lib -lbrotlidec\n" in result.stdout
    arguments = log.read_text().splitlines()
    assert INCLUDE_FLAG in arguments
    assert LIBRARY_FLAG in arguments
