#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
MAKEFILE = PROJECT_ROOT / "Makefile"
FFI_CONTRACT_PATH = PROJECT_ROOT / "docs" / "architecture" / "FFI_MIGRATION_CONTRACT.md"
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"
CARGO_TOML_PATH = PROJECT_ROOT / "components" / "rust-converter" / "Cargo.toml"
RUST_TOOLCHAIN_PATH = PROJECT_ROOT / "rust-toolchain.toml"
RELEASE_PACKAGES_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-packages.yml"
RELEASE_DEB_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-deb.yml"
RELEASE_RPM_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "release-rpm.yml"
CONFIG_DIRECTIVES_H = PROJECT_ROOT / "components" / "nginx-module" / "src" / "ngx_http_markdown_config_directives_impl.h"
ERROR_RS = PROJECT_ROOT / "components" / "rust-converter" / "src" / "error.rs"
ABI_RS = PROJECT_ROOT / "components" / "rust-converter" / "src" / "ffi" / "abi.rs"
EXPORTS_RS = PROJECT_ROOT / "components" / "rust-converter" / "src" / "ffi" / "exports.rs"
RELEASE_GATES_MD = PROJECT_ROOT / "docs" / "project" / "release-gates" / "0.7.0-release-gates.md"
VALIDATION_MATRIX_MD = PROJECT_ROOT / "docs" / "project" / "0.7.0-validation-matrix.md"
PAYLOAD_IMPL_H = PROJECT_ROOT / "components" / "nginx-module" / "src" / "ngx_http_markdown_payload_impl.h"
CONVERSION_IMPL_H = PROJECT_ROOT / "components" / "nginx-module" / "src" / "ngx_http_markdown_conversion_impl.h"
DECISION_LOG_IMPL_H = PROJECT_ROOT / "components" / "nginx-module" / "src" / "ngx_http_markdown_decision_log_impl.h"
HEADERS_IMPL_H = PROJECT_ROOT / "components" / "nginx-module" / "src" / "ngx_http_markdown_headers_impl.h"
DECOMPRESSION_C = PROJECT_ROOT / "components" / "nginx-module" / "src" / "ngx_http_markdown_decompression.c"
FILTER_MODULE_H = PROJECT_ROOT / "components" / "nginx-module" / "src" / "ngx_http_markdown_filter_module.h"

GATE_3 = "Gate 3"
GATE_4 = "Gate 4"
GATE_FUTURE = {GATE_3, GATE_4}
GATE_LOCAL_SCRIPTS = {
    GATE_3: "gate3_local_package_smoke.sh",
    GATE_4: "gate4_local_k8s_smoke.sh",
}
RELEASE_GATES_070_DOC_GATE = "release-gates:070-doc"
CARGO_VERSION_070_GATE = "cargo:version-070"


class ValidationResult:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def pass_(self, gate_id: str, message: str) -> None:
        self.results.append(("PASS", gate_id, message))

    def fail(self, gate_id: str, message: str) -> None:
        self.results.append(("FAIL", gate_id, message))

    def skip(self, gate_id: str, message: str) -> None:
        self.results.append(("SKIP", gate_id, message))

    @property
    def has_failures(self) -> bool:
        return any(s == "FAIL" for s, _, _ in self.results)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _toml_document(path: Path) -> dict[str, object]:
    text = read(path)
    if not text:
        return {}
    return tomllib.loads(text)


def _cargo_rust_version() -> str | None:
    package = _toml_document(CARGO_TOML_PATH).get("package")
    if not isinstance(package, dict):
        return None
    rust_version = package.get("rust-version")
    return rust_version if isinstance(rust_version, str) else None


def _rust_toolchain_channel() -> str | None:
    toolchain = _toml_document(RUST_TOOLCHAIN_PATH).get("toolchain")
    if not isinstance(toolchain, dict):
        return None
    channel = toolchain.get("channel")
    return channel if isinstance(channel, str) else None


def _toolchain_matches_cargo() -> bool:
    rust_version = _cargo_rust_version()
    channel = _rust_toolchain_channel()
    if rust_version is None or channel is None:
        return False
    return channel == rust_version or channel.startswith(f"{rust_version}.")


def _workflow_uses_pinned_toolchain(workflow: str) -> bool:
    rust_version = _cargo_rust_version()
    channel = _rust_toolchain_channel()
    if rust_version is None or channel is None:
        return False
    return (
        "RUST_TOOLCHAIN" in workflow
        and channel in workflow
        and "--default-toolchain \"${RUST_TOOLCHAIN}\"" in workflow
        and "--default-toolchain stable" not in workflow
    )


def _workflow_has_release_build_invariants(workflow: str) -> bool:
    return (
        'rustup target add "${RUST_TARGET}"' in workflow
        and 'cargo build --release --locked --target "${RUST_TARGET}" --features "${RUST_FEATURES}"'
        in workflow
        and "target/${RUST_TARGET}/release" in workflow
    )


def _workflow_identifies_release_path(workflow: str, *, official: bool) -> bool:
    marker = "release-packages.yml is the canonical release package workflow"
    if official:
        return marker in workflow and "official release path" in workflow
    return marker in workflow and "legacy compatibility workflow" in workflow


def _check_gate_prerequisites(
    result: ValidationResult, gate: str
) -> tuple[Path | None, str | None]:
    """Check prerequisites for running a local gate script.

    Returns (script_path, docker_bin) if all prerequisites are met,
    or (None, None) after recording a skip result.
    """
    script_name = GATE_LOCAL_SCRIPTS.get(gate)
    if not script_name:
        result.skip(f"{gate}:scope", "no local script configured")
        return None, None

    script_path = Path(__file__).resolve().parent / script_name
    if not script_path.is_file():
        result.skip(f"{gate}:scope", f"{script_name} not found")
        return None, None

    docker_bin = shutil.which("docker") or shutil.which("podman")
    if not docker_bin:
        result.skip(
            f"{gate}:scope",
            f"Docker/Podman not found; install to run {gate} locally",
        )
        return None, None

    if gate == GATE_4:
        missing = [
            t for t in ("kind", "kubectl", "helm")
            if not shutil.which(t)
        ]
        if missing:
            result.skip(
                f"{gate}:scope",
                f"missing tools for {gate}: {', '.join(missing)}; "
                f"install via: brew install {' '.join(missing)}",
            )
            return None, None

    return script_path, docker_bin


def _handle_gate_result(
    result: ValidationResult, gate: str, proc: subprocess.CompletedProcess[str]
) -> None:
    """Record the result of a local gate script execution."""
    if proc.returncode == 0:
        result.pass_(f"{gate}:local", f"{gate} local Docker validation passed")
    elif proc.returncode == 3:
        result.skip(
            f"{gate}:scope",
            f"{gate} prerequisites not met (see output above for install instructions)",
        )
        if proc.stdout:
            sys.stderr.write(proc.stdout)
    else:
        result.fail(f"{gate}:local", f"{gate} local validation failed (rc={proc.returncode})")
        if proc.stdout:
            lines = proc.stdout.splitlines()
            for line in lines[-30:]:
                sys.stderr.write(f"  {line}\n")


def _run_local_gate(result: ValidationResult, gate: str) -> None:
    """Run a local Docker-based gate script if prerequisites are available."""
    script_path, _docker_bin = _check_gate_prerequisites(result, gate)
    if script_path is None:
        return

    timeout = 900 if gate == GATE_3 else 300  # 15 min for builds, 5 min for K8s
    try:
        proc = subprocess.run(
            ["bash", str(script_path)],
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result.fail(f"{gate}:local", f"{gate} local validation timed out")
        return

    _handle_gate_result(result, gate, proc)


def _record_blocking_item(
    result: ValidationResult,
    gate: str,
    item: str,
    ok: bool,
    report: str | None,
) -> None:
    gate_item = f"{gate}:{item}"
    if report is not None:
        if ok and item in report:
            result.pass_(gate_item, "evidence trace found in validation matrix")
        elif ok:
            result.fail(gate_item, "implementation exists but evidence trace missing in matrix")
        else:
            result.fail(gate_item, "blocking item validation failed")
        return

    if ok:
        result.pass_(gate_item, "check passed")
    else:
        result.fail(gate_item, "check failed")


def _record_blocking_items(
    result: ValidationResult,
    blocking_items: dict[str, list[tuple[str, bool]]],
    report: str | None,
) -> None:
    for gate, checks in blocking_items.items():
        for item, ok in checks:
            _record_blocking_item(result, gate, item, ok, report)


def check_structure(result: ValidationResult) -> None:
    gates = read(RELEASE_GATES_MD)
    if not gates:
        result.fail(RELEASE_GATES_070_DOC_GATE, "missing release gate doc")
        return
    if "Gate 1" in gates and "Gate 2" in gates and "Gate 3" in gates and "Gate 4" in gates and "Gate 5" in gates:
        result.pass_(RELEASE_GATES_070_DOC_GATE, "gate doc includes Gate 1..5")
    else:
        result.fail(RELEASE_GATES_070_DOC_GATE, "gate definitions incomplete")

    if FFI_CONTRACT_PATH.is_file():
        result.pass_("ffi-contract:exists", "FFI migration contract exists")
    else:
        result.fail("ffi-contract:exists", "missing FFI migration contract")

    if cargo_txt := read(CARGO_TOML_PATH):
        try:
            version = tomllib.loads(cargo_txt).get("package", {}).get("version", "")
        except tomllib.TOMLDecodeError as exc:
            result.fail(CARGO_VERSION_070_GATE, f"Cargo.toml parse error: {exc}")
        else:
            if version == "0.7.0":
                result.pass_(CARGO_VERSION_070_GATE, "Cargo version is 0.7.0")
            else:
                result.fail(CARGO_VERSION_070_GATE, f"version is {version}")

    else:
        result.fail(CARGO_VERSION_070_GATE, "Cargo.toml missing")


def _build_blocking_items() -> dict[str, list[tuple[str, bool]]]:
    """Build the blocking items dictionary from source file contents."""
    gates = read(RELEASE_GATES_MD)
    mk = read(MAKEFILE)
    docs = read(VALIDATION_MATRIX_MD)
    cfg = read(CONFIG_DIRECTIVES_H)
    err = read(ERROR_RS)
    abi = read(ABI_RS)
    exports = read(EXPORTS_RS)
    payload = read(PAYLOAD_IMPL_H)
    conversion = read(CONVERSION_IMPL_H)
    decision_log = read(DECISION_LOG_IMPL_H)
    headers = read(HEADERS_IMPL_H)
    decomp = read(DECOMPRESSION_C)
    filter_h = read(FILTER_MODULE_H)
    release_packages = read(RELEASE_PACKAGES_WORKFLOW)
    release_deb = read(RELEASE_DEB_WORKFLOW)
    release_rpm = read(RELEASE_RPM_WORKFLOW)

    unit_test_files = "\n".join(
        str(p) for p in (PROJECT_ROOT / "components" / "nginx-module" / "tests" / "unit").glob("*.c")
    )

    return {
        "Gate 1": [
            ("make test-nginx-unit-streaming", "`make test-nginx-unit-streaming`" in gates and "test-nginx-unit-streaming" in mk),
            ("make test-rust", "`make test-rust`" in gates and re.search(r"\btest-rust:", mk) is not None),
            ("make build && make check-headers", "check-headers" in mk and "make check-headers" in docs),
            ("bounded decompression", "markdown_decompress_max_size" in cfg and "DecompressionBudgetExceeded" in err),
            ("accept negotiation", "FFIAcceptResult" in abi and "markdown_negotiate_accept" in exports),
            ("decomp budget exceeded metric write", "NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.budget_exceeded_total)" in payload),
            ("decomp budget exceeded return code", "NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED" in filter_h and "NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED" in decomp),
            ("decomp budget exceeded resource_limit path", "NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT" in payload and "DECOMP_BUDGET_EXCEEDED" in payload),
        ],
        "Gate 2": [
            ("ffi boundary tests", "make test-nginx-unit" in docs and "make test-rust" in docs),
            ("layout tests", "test_ffi_header_plan_layout" in abi and "test_ffi_accept_result_layout" in abi),
            ("reason code source", "reason code" in read(FFI_CONTRACT_PATH).lower()),
            ("delivery semantics tests", "delivery_counter_test.c" in unit_test_files),
            ("delivery_count metric write", "NGX_HTTP_MARKDOWN_METRIC_INC(results.delivery_count)" in conversion or "NGX_HTTP_MARKDOWN_METRIC_INC(results.delivery_count)" in payload),
            ("decision_count metric write", "NGX_HTTP_MARKDOWN_METRIC_INC(results.decision_count)" in decision_log),
            ("parse_timeouts_total metric write", "parse_interrupts.parse_timeouts_total" in conversion),
            ("parse_budget_exceeded_total metric write", "parse_interrupts.parse_budget_exceeded_total" in conversion),
            ("header plan ffi integration", "markdown_build_header_plan" in headers and "ngx_http_markdown_apply_header_plan" in headers),
        ],
        "Gate 5": [
            ("make harness-check-full", "harness-check-full" in mk and "make harness-check-full" in gates),
            ("make docs-check", "docs-check" in mk and "make docs-check" in gates),
            ("release gates strict", "release-gates-check-strict" in mk),
            ("release packages has nm preflight", "binutils" in release_packages and "command -v nm" in release_packages),
            ("rust toolchain pinned", _toolchain_matches_cargo()),
            ("release-packages pinned rust", _workflow_uses_pinned_toolchain(release_packages)),
            ("release-deb pinned rust", _workflow_uses_pinned_toolchain(release_deb)),
            ("release-rpm pinned rust", _workflow_uses_pinned_toolchain(release_rpm)),
            ("release-packages build invariants", _workflow_has_release_build_invariants(release_packages)),
            ("release-deb build invariants", _workflow_has_release_build_invariants(release_deb)),
            ("release-rpm build invariants", _workflow_has_release_build_invariants(release_rpm)),
            ("release-packages official marker", _workflow_identifies_release_path(release_packages, official=True)),
            ("release-deb legacy marker", _workflow_identifies_release_path(release_deb, official=False)),
            ("release-rpm legacy marker", _workflow_identifies_release_path(release_rpm, official=False)),
        ],
    }


def check_blocking_items(result: ValidationResult, mode: str) -> None:
    blocking_items = _build_blocking_items()
    report = read(VALIDATION_MATRIX_MD) if mode == "evidence" else None
    _record_blocking_items(result, blocking_items, report)

    for gate in sorted(GATE_FUTURE):
        if os.environ.get("RELEASE_GATE_LOCAL_DOCKER") == "1":
            _run_local_gate(result, gate)
        else:
            result.skip(
                f"{gate}:scope",
                "future/feasibility gate; set RELEASE_GATE_LOCAL_DOCKER=1 to run locally via Docker",
            )


def print_report(result: ValidationResult) -> None:
    print("0.7.0 Release Gate Validation Report")
    print("=" * 60)
    for status, gate_id, message in result.results:
        print(f"  {status:4s}  {gate_id:44s}  {message}")
    print()
    p = sum(s == "PASS" for s, _, _ in result.results)
    f = sum(s == "FAIL" for s, _, _ in result.results)
    k = sum(s == "SKIP" for s, _, _ in result.results)
    print(f"Summary: {p} passed, {f} failed, {k} skipped")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["basic", "strict", "evidence"], default="basic")
    args = parser.parse_args()

    result = ValidationResult()
    check_structure(result)
    if args.mode in {"strict", "evidence"}:
        check_blocking_items(result, args.mode)

    print_report(result)
    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
