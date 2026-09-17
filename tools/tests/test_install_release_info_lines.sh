#!/bin/bash
# Contract test: the embedded release-info resolver keeps its six-line
# output contract on the early-exit (missing inputs) path, so the
# positional shell reader (RELEASE_INFO[0..5]) stays in lockstep with the
# resolver's documented six-field interface.
#
# Usage: bash tools/tests/test_install_release_info_lines.sh
# Exit 0 if all tests pass, exit 1 if any fail.
# Compatible with bash 3.2+ on macOS and Linux.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_SCRIPT="$REPO_ROOT/tools/install.sh"

PASS_COUNT=0
FAIL_COUNT=0

pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    printf '  PASS: %s\n' "$1"
    return 0
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf '  FAIL: %s\n' "$1" >&2
    return 0
}

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/release-info-guard.XXXXXX")" || {
    printf 'FAIL: cannot create a scratch directory\n' >&2
    exit 1
}
trap 'rm -rf "$work_dir"' EXIT

guard_py="$work_dir/guard.py"
guard_out="$work_dir/guard.out"

# Extract the embedded resolver program: everything between the first
# `<<'PY'` heredoc marker and its closing line in install.sh.
start_line="$(grep -n "<<'PY'" "$INSTALL_SCRIPT" | head -1 | cut -d: -f1)"
if [[ -z "$start_line" ]]; then
    fail "embedded resolver heredoc found in install.sh"
    printf '\nFAIL: %d test(s) failed.\n' "$FAIL_COUNT" >&2
    exit 1
fi
pass "embedded resolver heredoc found in install.sh"

awk -v start="$((start_line + 1))" 'NR >= start && $0 == "PY" { exit } NR >= start { print }' \
    "$INSTALL_SCRIPT" > "$guard_py"

if [[ -s "$guard_py" ]]; then
    pass "embedded resolver program extracted"
else
    fail "embedded resolver program extracted"
fi

# Run the resolver with no release inputs: the early exit must still
# publish exactly six (empty) lines.
env -i PATH="${PATH}" python3 "$guard_py" > "$guard_out" 2>"$work_dir/guard.err"
guard_rc=$?
if [[ "$guard_rc" -eq 0 ]]; then
    pass "early-exit resolver exits 0"
else
    fail "early-exit resolver exits 0" "exit=${guard_rc}; stderr=$(tr '\n' ' ' <"$work_dir/guard.err" | head -c 120)"
fi
line_count="$(wc -l < "$guard_out" | tr -d ' ')"
if [[ "$line_count" -eq 6 ]]; then
    pass "early-exit resolver output is exactly six lines"
else
    fail "early-exit resolver output is exactly six lines" \
        "expected 6 lines, got ${line_count}"
fi

non_empty="$(grep -c '[^[:space:]]' "$guard_out")"
if [[ "$non_empty" -eq 0 ]]; then
    pass "early-exit resolver output lines are empty"
else
    fail "early-exit resolver output lines are empty" \
        "found ${non_empty} non-empty line(s)"
fi

printf '\n'
if [[ "$FAIL_COUNT" -gt 0 ]]; then
    printf 'FAIL: %d test(s) failed.\n' "$FAIL_COUNT" >&2
    exit 1
fi
printf 'PASS: %d test(s) passed.\n' "$PASS_COUNT"
exit 0
