#!/bin/bash
# Regression tests for dynconf_reload_rollback.sh ownership and polling.

set -e

SCRIPT="$(cd "$(dirname "$0")/../../.." && pwd -P)/tests/e2e/dynconf_reload_rollback.sh"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/dynconf-ownership-test.XXXXXX")"

cleanup_test_files() {
    rm -rf -- "$TMP_ROOT"
    return 0
}

trap cleanup_test_files EXIT HUP INT TERM

stat_mode() {
    local path="$1"
    local value=""

    value="$(stat -f '%Lp' "$path" 2>/dev/null || true)"
    if [[ -n "$value" ]]; then
        printf '%s\n' "$value"
        return 0
    fi
    stat -c '%a' "$path"
}

assert_rc() {
    local expected="$1"
    local actual="$2"
    local description="$3"

    if [[ "$actual" -ne "$expected" ]]; then
        echo "FAIL: $description (expected $expected, got $actual)" >&2
        return 1
    fi
    echo "PASS: $description" >&2
}

run_case() {
    local name="$1"
    local expected_rc="$2"
    local code="$3"
    local target_path="${4:-$TMP_ROOT/$name}"
    local private_path="${5:-$TMP_ROOT/$name-private}"
    local log_path="$TMP_ROOT/$name.log"
    local actual_rc=0

    mkdir -p -- "$private_path"
    private_path="$(cd -P -- "$private_path" && pwd -P)"
    if DYNCONF_RELOAD_ROLLBACK_LIBRARY=1 bash -c "$code" bash "$SCRIPT" \
        "$target_path" "$private_path" >"$log_path" 2>&1; then
        actual_rc=0
    else
        actual_rc=$?
    fi
    assert_rc "$expected_rc" "$actual_rc" "$name preserves exit status"
    return 0
}

existing_target="$TMP_ROOT/existing/markdown-dynamic.conf"
mkdir -p -- "${existing_target%/*}"
printf 'caller-owned original\n' > "$existing_target"
chmod 640 "$existing_target"
cp -p -- "$existing_target" "$TMP_ROOT/existing.before"
mode_before="$(stat_mode "$existing_target")"

run_case existing-file 41 '
set -e
source "$1"
TEST_TMPDIR="$3"
OWN_TEST_TMPDIR=1
export ALLOW_EXTERNAL_DYNCONF_TEST=1
prepare_dynconf_ownership "$2"
write_dynconf_atomically "schema_version=0.9\nmarkdown_filter=on"
trap "cleanup 41" EXIT
exit 41
' "$existing_target"

if ! cmp -s "$existing_target" "$TMP_ROOT/existing.before"; then
    echo "FAIL: existing caller-owned dynconf content was not restored" >&2
    exit 1
fi
if [[ "$(stat_mode "$existing_target")" != "$mode_before" ]]; then
    echo "FAIL: existing caller-owned dynconf mode was not restored" >&2
    exit 1
fi
if compgen -G "${existing_target%/*}/.markdown-dynconf-backup.*" \
    >/dev/null 2>&1; then
    echo "FAIL: completed caller-owned backup was left behind" >&2
    exit 1
fi
echo "PASS: existing caller-owned dynconf content and mode restored" >&2

missing_target="$TMP_ROOT/missing/markdown-dynamic.conf"
mkdir -p -- "${missing_target%/*}"
run_case missing-file 42 '
set -e
source "$1"
TEST_TMPDIR="$3"
OWN_TEST_TMPDIR=1
export ALLOW_EXTERNAL_DYNCONF_TEST=1
prepare_dynconf_ownership "$2"
write_dynconf_atomically "schema_version=0.9\nmarkdown_filter=off"
trap "cleanup 42" EXIT
exit 42
' "$missing_target"
if [[ -e "$missing_target" ]]; then
    echo "FAIL: test-created external dynconf file was not removed" >&2
    exit 1
fi
echo "PASS: missing caller dynconf is removed only after test creation" >&2

run_case default-file 43 '
set -e
source "$1"
TEST_TMPDIR="$3"
OWN_TEST_TMPDIR=1
DYNCONF_FILE="$3/markdown-dynamic.conf"
prepare_dynconf_ownership "$DYNCONF_FILE"
write_dynconf_atomically "schema_version=0.9\nmarkdown_filter=on"
trap "cleanup 43" EXIT
exit 43
'
if [[ -e "$TMP_ROOT/default-file-private" ]]; then
    echo "FAIL: private default dynconf directory was not cleaned" >&2
    exit 1
fi
echo "PASS: default dynconf directory and file are private and cleaned" >&2

external_target="$TMP_ROOT/refused/markdown-dynamic.conf"
mkdir -p -- "${external_target%/*}"
run_case external-without-opt-in 44 '
set -e
source "$1"
TEST_TMPDIR="$3"
OWN_TEST_TMPDIR=1
if prepare_dynconf_ownership "$2"; then
    exit 1
fi
exit 44
' "$external_target"
if ! grep -q 'ALLOW_EXTERNAL_DYNCONF_TEST=1' \
    "$TMP_ROOT/external-without-opt-in.log"; then
    echo "FAIL: external path refusal did not explain the required opt-in" >&2
    exit 1
fi
echo "PASS: external path without opt-in is rejected" >&2

symlink_target="$TMP_ROOT/symlink/markdown-dynamic.conf"
mkdir -p -- "${symlink_target%/*}"
printf 'symlink target\n' > "$TMP_ROOT/symlink/real.conf"
ln -s real.conf "$symlink_target"
run_case symlink 45 '
set -e
source "$1"
TEST_TMPDIR="$3"
OWN_TEST_TMPDIR=1
export ALLOW_EXTERNAL_DYNCONF_TEST=1
if prepare_dynconf_ownership "$2"; then
    exit 1
fi
exit 45
' "$symlink_target"
if [[ ! -L "$symlink_target" ]]; then
    echo "FAIL: symlink target was followed or replaced" >&2
    exit 1
fi
echo "PASS: symlink dynconf path is rejected" >&2

signal_target="$TMP_ROOT/signal/markdown-dynamic.conf"
mkdir -p -- "${signal_target%/*}"
printf 'signal original\n' > "$signal_target"
cp -p -- "$signal_target" "$TMP_ROOT/signal.before"
run_case signal 129 '
set -e
source "$1"
TEST_TMPDIR="$3"
OWN_TEST_TMPDIR=1
export ALLOW_EXTERNAL_DYNCONF_TEST=1
prepare_dynconf_ownership "$2"
write_dynconf_atomically "schema_version=0.9\nmarkdown_filter=on"
trap "cleanup 129" HUP
kill -HUP $$
' "$signal_target"
if ! cmp -s "$signal_target" "$TMP_ROOT/signal.before"; then
    echo "FAIL: signal cleanup did not restore caller dynconf" >&2
    exit 1
fi
echo "PASS: signal cleanup restores caller file and preserves 129" >&2

run_case same-second-mtime 0 '
set -e
source "$1"
DIAG_MARKER="$3/diagnostics-seen"
get_diagnostics() {
    if [[ -e "$DIAG_MARKER" ]]; then
        printf "%s\n" "{\"config_version\":2,\"active_mtime\":100}"
    else
        : > "$DIAG_MARKER"
        printf "%s\n" "{\"config_version\":1,\"active_mtime\":100}"
    fi
    return 0
}
wait_for_diagnostics_field config_version changed_from:1 3
[[ "$(diagnostics_field active_mtime "$LAST_DIAG")" == "100" ]]
[[ "$(diagnostics_field config_version "$LAST_DIAG")" == "2" ]]
'
echo "PASS: config_version polling succeeds when mtime is unchanged" >&2

echo "PASS: dynconf ownership and polling regression tests" >&2
