"""Regression tests for the Rust/C build feature and ABI lifecycle contract."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_CONFIG = REPO_ROOT / "components/nginx-module/config"
CARGO_MANIFEST = REPO_ROOT / "components/rust-converter/Cargo.toml"
LIFECYCLE_IMPL = (
    REPO_ROOT
    / "components/nginx-module/src/ngx_http_markdown_lifecycle_impl.h"
)
MODULE_SOURCE = (
    REPO_ROOT / "components/nginx-module/src/ngx_http_markdown_filter_module.c"
)
NON_STREAMING_VERIFY = (
    REPO_ROOT / "tools/ci/verify_non_streaming_nginx_module.sh"
)


def _run_module_config(
    tmp_path: Path, feature_contract: str | None
) -> subprocess.CompletedProcess[str]:
    addon_dir = tmp_path / "components/nginx-module"
    archive = (
        tmp_path
        / "components/rust-converter/target/test-target/release"
        / "libnginx_markdown_converter.a"
    )
    archive.parent.mkdir(parents=True)
    archive.touch()
    addon_dir.mkdir(parents=True)

    marker = tmp_path / "nm-was-called"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    nm_stub = bin_dir / "nm"
    nm_stub.write_text(
        "#!/bin/sh\n"
        f": > {marker}\n"
        "exit 97\n",
        encoding="utf-8",
    )
    nm_stub.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "RUST_TARGET": "test-target",
            "ngx_addon_dir": str(addon_dir),
            "ngx_module_link": "",
            "CFLAGS": "",
            "NGX_ADDON_SRCS": "",
            "NGX_ADDON_DEPS": "",
            "HTTP_AUX_FILTER_MODULES": "",
        }
    )
    if feature_contract is None:
        env.pop("NGX_MARKDOWN_RUST_FEATURES", None)
    else:
        env["NGX_MARKDOWN_RUST_FEATURES"] = feature_contract

    command = (
        '. "$MODULE_CONFIG"\n'
        'printf "CFLAGS=%s\\n" "$CFLAGS"\n'
        'if [[ -e "$NM_MARKER" ]]; then printf "NM_CALLED=1\\n"; fi\n'
    )
    env["MODULE_CONFIG"] = str(MODULE_CONFIG)
    env["NM_MARKER"] = str(marker)
    return subprocess.run(
        ["bash", "-c", command],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.parametrize("feature_contract", [None, "default"])
def test_default_feature_contract_is_deterministic_without_nm(
    tmp_path: Path, feature_contract: str | None
) -> None:
    result = _run_module_config(tmp_path, feature_contract)

    assert result.returncode == 0, result.stderr
    assert "-DMARKDOWN_STREAMING_ENABLED" in result.stdout
    assert "-DMARKDOWN_INCREMENTAL_ENABLED" in result.stdout
    assert "NM_CALLED=1" not in result.stdout


def test_default_feature_contract_matches_cargo_manifest() -> None:
    manifest = tomllib.loads(CARGO_MANIFEST.read_text(encoding="utf-8"))

    assert set(manifest["features"]["default"]) == {
        "incremental",
        "prune_noise_regions",
        "streaming",
    }


def test_none_feature_contract_disables_optional_c_paths_without_nm(
    tmp_path: Path,
) -> None:
    result = _run_module_config(tmp_path, "none")

    assert result.returncode == 0, result.stderr
    assert "MARKDOWN_STREAMING_ENABLED" not in result.stdout
    assert "MARKDOWN_INCREMENTAL_ENABLED" not in result.stdout
    assert "NM_CALLED=1" not in result.stdout


def test_explicit_feature_contract_enables_only_matching_c_paths(
    tmp_path: Path,
) -> None:
    result = _run_module_config(tmp_path, "streaming,prune_noise_regions")

    assert result.returncode == 0, result.stderr
    assert "-DMARKDOWN_STREAMING_ENABLED" in result.stdout
    assert "MARKDOWN_INCREMENTAL_ENABLED" not in result.stdout
    assert "NM_CALLED=1" not in result.stdout


@pytest.mark.parametrize(
    "feature_contract", ["", "bogus", "none,streaming", "default,incremental"]
)
def test_invalid_or_ambiguous_feature_contract_fails_closed(
    tmp_path: Path, feature_contract: str
) -> None:
    result = _run_module_config(tmp_path, feature_contract)

    assert result.returncode != 0
    assert "NGX_MARKDOWN_RUST_FEATURES" in result.stderr
    assert "NM_CALLED=1" not in result.stdout


def test_no_default_features_build_declares_none_to_nginx_configure() -> None:
    script = NON_STREAMING_VERIFY.read_text(encoding="utf-8")

    assert re.search(
        r"export NGX_MARKDOWN_RUST_FEATURES=none.*?\./configure",
        script,
        flags=re.DOTALL,
    )
    assert not re.search(r"^\s*nm\s+", script, flags=re.MULTILINE)


def _function_body(source: str, function_name: str) -> str:
    match = re.search(
        rf"\n{re.escape(function_name)}\([^)]*\)\n\{{(?P<body>.*?)\n\}}",
        source,
        flags=re.DOTALL,
    )
    assert match is not None, f"missing function {function_name}"
    return match.group("body")


def test_abi_validation_precedes_configuration_and_filter_registration() -> None:
    lifecycle = LIFECYCLE_IMPL.read_text(encoding="utf-8")
    module_source = MODULE_SOURCE.read_text(encoding="utf-8")
    preconfiguration = _function_body(
        lifecycle, "ngx_http_markdown_preconfiguration"
    )
    postconfiguration = _function_body(
        lifecycle, "ngx_http_markdown_filter_init"
    )

    assert "markdown_abi_version()" in preconfiguration
    assert "ngx_http_markdown_ffi_abi_matches" in preconfiguration
    assert "markdown_abi_version()" not in postconfiguration
    assert "ngx_http_top_header_filter" in postconfiguration
    assert "ngx_http_top_body_filter" in postconfiguration
    assert re.search(
        r"ngx_http_markdown_preconfiguration,\s*/\* preconfiguration \*/"
        r"\s*ngx_http_markdown_filter_init,\s*/\* postconfiguration \*/",
        module_source,
    )
