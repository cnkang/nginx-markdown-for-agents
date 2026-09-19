#!/bin/bash
#
# test_detect_version_consistency.sh — Unit tests for the version consistency
# detector.
#
# The detector resolves PROJECT_ROOT from its own location and takes no
# arguments, so every fixture is a minimal project tree with a copy of the
# detector planted at <tree>/tools/harness/.  Each tree must also carry a
# tools/harness/check_rust_baseline.py stub: the detector invokes it and any
# non-zero or missing stub fails the run (fail-closed), so the stub is
# mandatory for the clean case.
#
# Covered:
#   - clean tree                                -> exit 0, "All version checks passed"
#   - Chart appVersion + fuzz dependency drift  -> exit 1, both mismatch lines
#   - missing main Cargo.toml                   -> exit 2, "Cannot read version from"
#   - failing baseline stub                     -> exit 1 (fail-closed)
#   - missing baseline stub                     -> exit 1 (fail-closed)
#   - absent Chart.yaml                         -> skipped, still exit 0

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR_SRC="${SCRIPT_DIR}/../detect_version_consistency.sh"

PASS_COUNT=0
FAIL_COUNT=0

pass() {
    local msg="$1"
    PASS_COUNT=$((PASS_COUNT + 1))
    printf '  PASS: %s\n' "${msg}"
    return 0
}

fail() {
    local msg="$1"
    local detail="${2:-}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf '  FAIL: %s\n' "${msg}" >&2
    if [[ -n "${detail}" ]]; then
        printf '        Detail: %s\n' "${detail}" >&2
    fi
    return 0
}

# The detector's PASS/ERROR/INFO lines go to stderr; capture both streams.
run_detector() {
    local project_root="$1"
    local output_file="$2"
    bash "${project_root}/tools/harness/detect_version_consistency.sh" \
        >"${output_file}" 2>&1
    return $?
}

printf 'Unit Tests: detect_version_consistency.sh\n'

if bash -n "${DETECTOR_SRC}" 2>/dev/null; then
    pass "detector has valid bash syntax"
else
    fail "detector has valid bash syntax" "bash -n failed"
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/version-consistency-detector.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT

# ── Fixture builder: minimal consistent project tree ──
make_tree() {
    local base="$1"
    mkdir -p "${base}/tools/harness" \
             "${base}/components/rust-converter/fuzz" \
             "${base}/charts/nginx-markdown" \
             "${base}/tools/corpus/test-corpus-conversion"
    cp "${DETECTOR_SRC}" "${base}/tools/harness/detect_version_consistency.sh"

    cat >"${base}/tools/harness/check_rust_baseline.py" <<'PYEOF'
#!/usr/bin/env python3
raise SystemExit(0)
PYEOF

    cat >"${base}/components/rust-converter/Cargo.toml" <<'TOML'
[package]
name = "nginx-markdown-converter"
version = "1.2.3"
edition = "2021"
TOML

    cat >"${base}/charts/nginx-markdown/Chart.yaml" <<'YAML'
apiVersion: v2
name: nginx-markdown
version: 1.2.3
appVersion: "1.2.3"
YAML

    cat >"${base}/components/rust-converter/fuzz/Cargo.toml" <<'TOML'
[package]
name = "fuzz"
version = "0.0.0"

[dependencies]
nginx-markdown-converter = { version = "1.2.3", path = ".." }
TOML

    cat >"${base}/tools/corpus/test-corpus-conversion/Cargo.toml" <<'TOML'
[package]
name = "test-corpus-conversion"
version = "0.0.0"

[dependencies]
nginx-markdown-converter = { version = "1.2.3", path = "../../../components/rust-converter" }
TOML

    return 0
}

# ── Fixture: clean tree -> exit 0 ──
clean_tree="${tmp_dir}/clean"
make_tree "${clean_tree}"
out="${tmp_dir}/clean.out"
rc=0
run_detector "${clean_tree}" "${out}" || rc=$?
if [[ "${rc}" -eq 0 ]] \
    && grep -q 'PASS.*All version checks passed' "${out}" \
    && grep -q "Main Cargo.toml version: 1.2.3" "${out}"; then
    pass "consistent tree reports All version checks passed (exit 0)"
else
    fail "consistent tree reports All version checks passed (exit 0)" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi

# ── Fixture: Chart appVersion + fuzz dependency drift -> exit 1 ──
drift_tree="${tmp_dir}/drift"
make_tree "${drift_tree}"
cat >"${drift_tree}/charts/nginx-markdown/Chart.yaml" <<'YAML'
apiVersion: v2
name: nginx-markdown
version: 1.2.3
appVersion: "9.9.9"
YAML
cat >"${drift_tree}/components/rust-converter/fuzz/Cargo.toml" <<'TOML'
[package]
name = "fuzz"
version = "0.0.0"

[dependencies]
nginx-markdown-converter = { version = "0.0.1", path = ".." }
TOML

out="${tmp_dir}/drift.out"
rc=0
run_detector "${drift_tree}" "${out}" || rc=$?
if [[ "${rc}" -eq 1 ]] \
    && grep -q "Chart.yaml appVersion: '9.9.9' (expected '1.2.3')" "${out}" \
    && grep -q "fuzz/Cargo.toml dep version: '0.0.1' (expected '1.2.3')" "${out}" \
    && grep -q "Found 2 version inconsistency(ies)" "${out}"; then
    pass "appVersion + fuzz dependency drift is reported (exit 1)"
else
    fail "appVersion + fuzz dependency drift is reported (exit 1)" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi

# ── Fixture: missing main Cargo.toml -> exit 2 ──
nocargo_tree="${tmp_dir}/nocargo"
make_tree "${nocargo_tree}"
rm -f "${nocargo_tree}/components/rust-converter/Cargo.toml"

out="${tmp_dir}/nocargo.out"
rc=0
run_detector "${nocargo_tree}" "${out}" || rc=$?
if [[ "${rc}" -eq 2 ]] \
    && grep -q "Cannot read version from" "${out}" \
    && ! grep -q "All version checks passed" "${out}"; then
    pass "missing main Cargo.toml is a script error (exit 2)"
else
    fail "missing main Cargo.toml is a script error (exit 2)" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi

# ── Fixture: failing baseline stub -> exit 1 (fail-closed) ──
badstub_tree="${tmp_dir}/badstub"
make_tree "${badstub_tree}"
cat >"${badstub_tree}/tools/harness/check_rust_baseline.py" <<'PYEOF'
#!/usr/bin/env python3
raise SystemExit(3)
PYEOF

out="${tmp_dir}/badstub.out"
rc=0
run_detector "${badstub_tree}" "${out}" || rc=$?
if [[ "${rc}" -eq 1 ]] \
    && grep -q "Rust compiler/MSRV baseline is inconsistent" "${out}" \
    && ! grep -q "All version checks passed" "${out}"; then
    pass "failing baseline stub fails the run (exit 1, fail-closed)"
else
    fail "failing baseline stub fails the run (exit 1, fail-closed)" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi

# ── Fixture: missing baseline stub -> exit 1 (fail-closed) ──
nostub_tree="${tmp_dir}/nostub"
make_tree "${nostub_tree}"
rm -f "${nostub_tree}/tools/harness/check_rust_baseline.py"

out="${tmp_dir}/nostub.out"
rc=0
run_detector "${nostub_tree}" "${out}" || rc=$?
if [[ "${rc}" -eq 1 ]] \
    && grep -q "Rust compiler/MSRV baseline is inconsistent" "${out}" \
    && ! grep -q "All version checks passed" "${out}"; then
    pass "missing baseline stub fails the run (exit 1, fail-closed)"
else
    fail "missing baseline stub fails the run (exit 1, fail-closed)" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi

# ── Fixture: absent Chart.yaml is skipped, not failed ──
nochart_tree="${tmp_dir}/nochart"
make_tree "${nochart_tree}"
rm -rf "${nochart_tree}/charts"

out="${tmp_dir}/nochart.out"
rc=0
run_detector "${nochart_tree}" "${out}" || rc=$?
if [[ "${rc}" -eq 0 ]] \
    && grep -q "Chart.yaml not found (skipping)" "${out}" \
    && grep -q 'PASS.*All version checks passed' "${out}"; then
    pass "absent Chart.yaml is skipped (exit 0)"
else
    fail "absent Chart.yaml is skipped (exit 0)" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    printf '\nFAIL: %s test(s) failed.\n' "${FAIL_COUNT}" >&2
    exit 1
fi

printf '\nPASS: %s test(s) passed.\n' "${PASS_COUNT}"
exit 0
