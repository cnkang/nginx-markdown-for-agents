#!/usr/bin/env bash
#
# test_detect_e2e_streaming_config.sh - Unit tests for E2E streaming config detector.
#
# Rule 60: Validates that the block-aware Python detector correctly identifies
# contradictory streaming configurations and exempts intentional cases.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="python3 ${SCRIPT_DIR}/../detect_e2e_streaming_config.py"

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

printf 'Unit Tests: detect_e2e_streaming_config.py\n'

# Create temp fixture directory mimicking the expected structure
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/e2e-streaming-config.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT

# Set up the expected directory structure
e2e_dir="${tmp_dir}/tools/e2e"
harness_dir="${tmp_dir}/tools/e2e-harness/src"
mkdir -p "${e2e_dir}" "${harness_dir}"

# ============================================================================
# Test 1: Missing streaming directive in same block -> WARN
# ============================================================================
cat >"${e2e_dir}/test_missing.sh" <<'SCRIPT'
#!/usr/bin/env bash
cat <<'EOF' > /tmp/nginx.conf
http {
    server {
        location /cache-full/ {
            markdown_filter on;
            markdown_cache_validation full;
            markdown_limits memory=10m timeout=120s;
        }
    }
}
EOF
SCRIPT

output_file="${tmp_dir}/test1.out"
${DETECTOR} "${tmp_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]] && grep -q "WARN.*markdown_cache_validation full without explicit markdown_streaming" "${output_file}"; then
    pass "missing streaming directive in block -> advisory WARN"
else
    fail "missing streaming directive in block -> advisory WARN" "exit=${exit_code}"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 2: Adjacent block has streaming, current block does NOT -> WARN
# This is the key false-negative the old detector had with the 15-line window
# ============================================================================
cat >"${e2e_dir}/test_adjacent.sh" <<'SCRIPT'
#!/usr/bin/env bash
cat <<'EOF' > /tmp/nginx.conf
http {
    server {
        location /previous/ {
            markdown_filter on;
            markdown_streaming off;
            markdown_cache_validation off;
        }

        location /broken/ {
            markdown_filter on;
            markdown_cache_validation full;
            markdown_limits memory=10m timeout=120s;
        }
    }
}
EOF
SCRIPT

output_file="${tmp_dir}/test2.out"
${DETECTOR} "${tmp_dir}" >"${output_file}" 2>&1
if grep -q "WARN.*location /broken/" "${output_file}" && ! grep -q "WARN.*location /previous/" "${output_file}"; then
    pass "adjacent block streaming doesn't mask missing directive in current block"
else
    fail "adjacent block streaming doesn't mask missing directive in current block"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 3: Commented-out streaming directive -> still WARN
# ============================================================================
cat >"${e2e_dir}/test_commented.sh" <<'SCRIPT'
#!/usr/bin/env bash
cat <<'EOF' > /tmp/nginx.conf
http {
    server {
        location /commented/ {
            markdown_filter on;
            # markdown_streaming off;
            markdown_cache_validation full;
            markdown_limits memory=10m timeout=120s;
        }
    }
}
EOF
SCRIPT

output_file="${tmp_dir}/test3.out"
${DETECTOR} "${tmp_dir}" >"${output_file}" 2>&1
if grep -q "WARN.*location /commented/" "${output_file}"; then
    pass "commented-out streaming directive not counted as present"
else
    fail "commented-out streaming directive not counted as present"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 4: Legitimate off + full -> PASS (no warnings)
# ============================================================================

# Remove previous test files
rm -f "${e2e_dir}"/*.sh
cat >"${e2e_dir}/test_clean.sh" <<'SCRIPT'
#!/usr/bin/env bash
cat <<'EOF' > /tmp/nginx.conf
http {
    server {
        location /cache-full/ {
            markdown_filter on;
            markdown_streaming off;
            markdown_cache_validation full;
            markdown_limits memory=10m timeout=120s;
        }
    }
}
EOF
SCRIPT

output_file="${tmp_dir}/test4.out"
${DETECTOR} "${tmp_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]] && grep -q "OK: no contradictory" "${output_file}"; then
    pass "legitimate off + full -> no findings"
else
    fail "legitimate off + full -> no findings" "exit=${exit_code}"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 5: Intentional auto + full with comment -> exempted (no warning)
# ============================================================================
rm -f "${e2e_dir}"/*.sh
cat >"${e2e_dir}/test_intentional.sh" <<'SCRIPT'
#!/usr/bin/env bash
cat <<'EOF' > /tmp/nginx.conf
http {
    server {
        # 10.5: full validation selects the full-buffer path in auto mode
        location /t05/ {
            markdown_filter on;
            markdown_cache_validation full;
            markdown_streaming auto;
            markdown_limits memory=10m timeout=120s;
        }
    }
}
EOF
SCRIPT

output_file="${tmp_dir}/test5.out"
${DETECTOR} "${tmp_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]] && grep -q "OK: no contradictory" "${output_file}"; then
    pass "intentional auto + full with preamble comment -> exempted"
else
    fail "intentional auto + full with preamble comment -> exempted" "exit=${exit_code}"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 5b: Intentional auto + full with inline comment -> exempted
# ============================================================================
rm -f "${e2e_dir}"/*.sh
cat >"${e2e_dir}/test_intentional_inline.sh" <<'SCRIPT'
#!/usr/bin/env bash
cat <<'EOF' > /tmp/nginx.conf
http {
    server {
        location /t05/ {
            markdown_filter on;
            markdown_cache_validation full;
            markdown_streaming auto;  # intentional: validates runtime-block mechanism
            markdown_limits memory=10m timeout=120s;
        }
    }
}
EOF
SCRIPT

output_file="${tmp_dir}/test5b.out"
${DETECTOR} "${tmp_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]] && grep -q "OK: no contradictory" "${output_file}"; then
    pass "intentional auto + full with inline comment -> exempted"
else
    fail "intentional auto + full with inline comment -> exempted" "exit=${exit_code}"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 5c: Intentional with 'out of the streaming path' comment -> exempted
# ============================================================================
rm -f "${e2e_dir}"/*.sh
cat >"${e2e_dir}/test_intentional_oosp.sh" <<'SCRIPT'
#!/usr/bin/env bash
cat <<'EOF' > /tmp/nginx.conf
http {
    server {
        # keeps this request out of the streaming path
        location /t11/ {
            markdown_filter on;
            markdown_cache_validation full;
            markdown_streaming auto;
            markdown_limits memory=10m timeout=120s;
        }
    }
}
EOF
SCRIPT

output_file="${tmp_dir}/test5c.out"
${DETECTOR} "${tmp_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]] && grep -q "OK: no contradictory" "${output_file}"; then
    pass "intentional auto + full with 'out of the streaming path' comment -> exempted"
else
    fail "intentional auto + full with 'out of the streaming path' comment -> exempted" "exit=${exit_code}"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 6: Rust escaped config string -> detects missing streaming
# ============================================================================
rm -f "${e2e_dir}"/*.sh
cat >"${harness_dir}/test_rust.rs" <<'RUST'
fn build_config() -> String {
    let config = "\
        http {\n\
            server {\n\
                location /cache-full/ {\n\
                    markdown_filter on;\n\
                    markdown_cache_validation full;\n\
                    markdown_limits memory=10m timeout=120s;\n\
                }\n\
            }\n\
        }\n\
    ";
    config.to_string()
}
RUST

output_file="${tmp_dir}/test6.out"
${DETECTOR} "${tmp_dir}" >"${output_file}" 2>&1
if grep -q "WARN.*markdown_cache_validation full without explicit markdown_streaming" "${output_file}"; then
    pass "Rust escaped config string -> detects missing streaming"
else
    fail "Rust escaped config string -> detects missing streaming"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 6b: Rust escaped config with explicit off -> PASS
# ============================================================================
cat >"${harness_dir}/test_rust_clean.rs" <<'RUST'
fn build_config() -> String {
    let config = "\
        http {\n\
            server {\n\
                location /cache-full/ {\n\
                    markdown_filter on;\n\
                    markdown_streaming off;\n\
                    markdown_cache_validation full;\n\
                    markdown_limits memory=10m timeout=120s;\n\
                }\n\
            }\n\
        }\n\
    ";
    config.to_string()
}
RUST

# Remove the failing test file to test clean case
rm -f "${harness_dir}/test_rust.rs"

output_file="${tmp_dir}/test6b.out"
${DETECTOR} "${tmp_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]] && grep -q "OK: no contradictory" "${output_file}"; then
    pass "Rust escaped config with explicit off -> no findings"
else
    fail "Rust escaped config with explicit off -> no findings" "exit=${exit_code}"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 7: --strict mode exits 1 on findings
# ============================================================================
rm -f "${e2e_dir}"/*.sh "${harness_dir}"/*.rs
cat >"${e2e_dir}/test_strict.sh" <<'SCRIPT'
#!/usr/bin/env bash
cat <<'EOF' > /tmp/nginx.conf
http {
    server {
        location /broken/ {
            markdown_filter on;
            markdown_cache_validation full;
        }
    }
}
EOF
SCRIPT

output_file="${tmp_dir}/test7.out"
${DETECTOR} "${tmp_dir}" --strict >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] && grep -q "FAIL: found" "${output_file}"; then
    pass "--strict mode exits 1 on findings"
else
    fail "--strict mode exits 1 on findings" "exit=${exit_code}"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 7b: --strict mode exits 0 when no findings
# ============================================================================
rm -f "${e2e_dir}"/*.sh
cat >"${e2e_dir}/test_strict_clean.sh" <<'SCRIPT'
#!/usr/bin/env bash
cat <<'EOF' > /tmp/nginx.conf
http {
    server {
        location /ok/ {
            markdown_filter on;
            markdown_streaming off;
            markdown_cache_validation full;
        }
    }
}
EOF
SCRIPT

output_file="${tmp_dir}/test7b.out"
${DETECTOR} "${tmp_dir}" --strict >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]] && grep -q "OK: no contradictory" "${output_file}"; then
    pass "--strict mode exits 0 when no findings"
else
    fail "--strict mode exits 0 when no findings" "exit=${exit_code}"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 8: auto + full WITHOUT intentional comment -> WARN
# ============================================================================
rm -f "${e2e_dir}"/*.sh
cat >"${e2e_dir}/test_auto_no_comment.sh" <<'SCRIPT'
#!/usr/bin/env bash
cat <<'EOF' > /tmp/nginx.conf
http {
    server {
        location /bad-auto/ {
            markdown_filter on;
            markdown_streaming auto;
            markdown_cache_validation full;
            markdown_limits memory=10m timeout=120s;
        }
    }
}
EOF
SCRIPT

output_file="${tmp_dir}/test8.out"
${DETECTOR} "${tmp_dir}" >"${output_file}" 2>&1
if grep -q "WARN.*location /bad-auto/.*markdown_streaming auto" "${output_file}"; then
    pass "auto + full without intentional comment -> WARN"
else
    fail "auto + full without intentional comment -> WARN"
    cat "${output_file}" >&2
fi

# ============================================================================
# Summary
# ============================================================================
printf '\n'
total=$((PASS_COUNT + FAIL_COUNT))
printf 'Results: %d/%d passed\n' "${PASS_COUNT}" "${total}"
if [[ ${FAIL_COUNT} -gt 0 ]]; then
    printf 'FAIL: %d test(s) failed\n' "${FAIL_COUNT}" >&2
    exit 1
fi
printf 'All tests passed.\n'
exit 0
