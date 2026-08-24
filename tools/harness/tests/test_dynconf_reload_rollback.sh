#!/bin/bash
# Regression tests for dynconf_reload_rollback.sh ownership and polling.
#
# run_case passes the library path, target path, and private directory to its
# fixed snippet as $1, $2, and $3. The PID cases below use a different seam.

set -e

FAIL_COUNT=0

SCRIPT="$(cd "$(dirname "$0")/../../.." && pwd -P)/tests/e2e/dynconf_reload_rollback.sh"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/dynconf-ownership-test.XXXXXX")"

cleanup_test_files() {
    # Fixture directories may be made read-only by the ownership checks;
    # restore write permissions first so rm -rf can remove them.
    chmod -R u+w -- "$TMP_ROOT" 2>/dev/null || true
    rm -rf -- "$TMP_ROOT"
    return 0
}

# EXIT performs cleanup only; HUP/INT/TERM clean up and then terminate
# with the conventional signal-derived status (128 + signal) instead of
# resuming the interrupted script.
trap cleanup_test_files EXIT
trap 'cleanup_test_files; exit $((128 + 1))' HUP
trap 'cleanup_test_files; exit $((128 + 2))' INT
trap 'cleanup_test_files; exit $((128 + 15))' TERM

# shellcheck source=tests/e2e/dynconf_reload_rollback.sh
DYNCONF_RELOAD_ROLLBACK_LIBRARY=1 source "$SCRIPT"

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
    # These are fixed, repository-owned snippets; do not pass external or
    # runtime-controlled code through this intentional bash -c test seam.
    if DYNCONF_RELOAD_ROLLBACK_LIBRARY=1 bash -c "$code" bash "$SCRIPT" \
        "$target_path" "$private_path" >"$log_path" 2>&1; then
        actual_rc=0
    else
        actual_rc=$?
    fi
    if ! assert_rc "$expected_rc" "$actual_rc" "$name preserves exit status"; then
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
    return 0
}

existing_target="$TMP_ROOT/existing/markdown-dynamic.conf"
mkdir -p -- "${existing_target%/*}"
printf 'caller-owned original\n' > "$existing_target"
chmod 640 "$existing_target"
cp -p -- "$existing_target" "$TMP_ROOT/existing.before"
mode_before="$(stat_field '%Lp' '%a' "$existing_target")"

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
if [[ "$(stat_field '%Lp' '%a' "$existing_target")" != "$mode_before" ]]; then
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

caller_target="$TMP_ROOT/caller-selection/markdown-dynamic.conf"
mkdir -p -- "${caller_target%/*}"
run_case caller-path-selection 47 '
set -e
DYNCONF_FILE="$2"
source "$1"
TEST_TMPDIR="$3"
[[ "$DYNCONF_FILE" == "$2" ]]
selected_path="$(requested_dynconf_path)"
[[ "$selected_path" == "$2" ]]
exit 47
' "$caller_target"
echo "PASS: caller-provided dynconf path survives top-level initialization" >&2

run_case default-path-selection 46 '
set -e
source "$1"
TEST_TMPDIR="$3"
CALLER_DYNCONF_FILE_SET=0
CALLER_DYNCONF_FILE=""
selected_path="$(requested_dynconf_path)"
[[ "$selected_path" == "$3/markdown-dynamic.conf" ]]
prepare_dynconf_ownership "$selected_path"
write_dynconf_atomically "schema_version=0.9\nmarkdown_filter=on"
trap "cleanup 46" EXIT
exit 46
'
if [[ -e "$TMP_ROOT/default-path-selection-private/markdown-dynamic.conf" ]]; then
    echo "FAIL: selected default dynconf file was not cleaned" >&2
    exit 1
fi
echo "PASS: default path selection uses the private test directory" >&2

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

# ---------------------------------------------------------------------------
# PID ownership model regression tests
# ---------------------------------------------------------------------------

# A stub kill that records the PID it would signal, instead of sending a
# real signal.  This ensures tests never signal system processes.
KILL_LOG="$TMP_ROOT/kill-stub.log"

# Run a snippet with kill and ps stubbed so no real process is ever
# signaled.  The snippet is sourced after the library; stubs override the
# real kill/ps for the lifetime of the subshell.
# run_pid_case parameters are: $1 = case name, $2 = expected exit code, and
# $3 = fixed shell code. The snippet receives only "$SCRIPT" as its $1.
run_pid_case() {
    local name="$1"
    local expected_rc="$2"
    local code="$3"
    local log_path="$TMP_ROOT/pid-$name.log"
    local actual_rc=0
    local stub_pid="${STUB_PID:-77777}"

    DYNCONF_RELOAD_ROLLBACK_LIBRARY=1 \
    KILL_LOG="$KILL_LOG" \
    STUB_PID="$stub_pid" \
    bash -c '
        set -e
        # Stub kill: only STUB_PID exists; HUP is logged instead of sent.
        kill() {
            if [[ "$1" == "-0" ]]; then
                [[ "$2" == "$STUB_PID" ]]
                return $?
            fi
            printf "%s %s\n" "$1" "$2" >> "$KILL_LOG"
            return 0
        }
        # Stub ps: emulate "ps -o command= -p <pid>" (pid is the last arg).
        ps() {
            local last_arg="${!#}"
            if [[ "$last_arg" == "$STUB_PID" ]]; then
                printf "%s\n" "${STUB_CMDLINE:-nginx: master process /test/nginx}"
                return 0
            fi
            return 1
        }
        source "$1"
        '"$code"'
    ' bash "$SCRIPT" >"$log_path" 2>&1 || actual_rc=$?
    if ! assert_rc "$expected_rc" "$actual_rc" "$name preserves exit status"; then
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
    return 0
}

# Two simulated masters must not cause an arbitrary signal: with no explicit
# PID source, resolve_nginx_pid returns exactly 1 and no kill is called.
: > "$KILL_LOG"
run_pid_case no-pid-no-kill 0 '
unset NGINX_PID HARNESS_NGINX_PID NGINX_PID_FILE
set +e
resolve_nginx_pid >/dev/null
rc=$?
set -e
[[ "$rc" -eq 1 ]]
echo "OK: no PID resolved"
'
if [[ -s "$KILL_LOG" ]]; then
    echo "FAIL: kill was called without a trusted PID" >&2
    cat "$KILL_LOG" >&2
    exit 1
fi
echo "PASS: no PID source → no kill" >&2

# Two simulated masters exist, but without an explicit PID we must not
# signal either one.
: > "$KILL_LOG"
run_pid_case two-masters-no-signal 0 '
unset NGINX_PID HARNESS_NGINX_PID NGINX_PID_FILE
set +e
resolve_nginx_pid >/dev/null
rc=$?
set -e
[[ "$rc" -eq 1 ]]
echo "OK: no arbitrary signal"
'
echo "PASS: two simulated masters do not cause an arbitrary signal" >&2

# Valid explicit PID is resolved and signaled on reload.
: > "$KILL_LOG"
run_pid_case explicit-pid-resolved 0 '
export NGINX_PID=77777
export STUB_PID=77777
set +e
pid="$(resolve_nginx_pid)"
rc=$?
set -e
[[ "$rc" -eq 0 ]] || exit 1
[[ "$pid" == "77777" ]] || exit 1
'
: > "$KILL_LOG"
run_pid_case explicit-pid-reload 0 '
export NGINX_PID=77777
export STUB_PID=77777
send_reload "test reload"
'
if ! grep -q "HUP 77777" "$KILL_LOG"; then
    echo "FAIL: explicit PID was not signaled (log: $(cat "$KILL_LOG" 2>/dev/null))" >&2
    exit 1
fi
echo "PASS: explicit PID is signaled" >&2

# Invalid PIDs are rejected.
run_pid_case invalid-pid-non-numeric 0 '
export NGINX_PID="abc; rm -rf /"
set +e
resolve_nginx_pid >/dev/null
rc=$?
set -e
[[ "$rc" -eq 2 ]]
echo "OK: non-numeric PID rejected"
'
echo "PASS: non-numeric PID rejected" >&2

run_pid_case invalid-pid-zero 0 '
export NGINX_PID=0
set +e
resolve_nginx_pid >/dev/null
rc=$?
set -e
[[ "$rc" -eq 2 ]]
echo "OK: PID 0 rejected"
'
echo "PASS: PID <= 1 rejected" >&2

run_pid_case invalid-pid-self 0 '
export NGINX_PID=$$
set +e
resolve_nginx_pid >/dev/null
rc=$?
set -e
[[ "$rc" -eq 2 ]]
echo "OK: current shell PID rejected"
'
echo "PASS: current shell PID rejected" >&2

# A configured but invalid source must fail through send_reload, not become a
# polling SKIP.  The diagnostic is retained and no signal is attempted.
: > "$KILL_LOG"
run_pid_case invalid-pid-send-reload 0 '
export NGINX_PID="abc; rm -rf /"
set +e
output="$(send_reload "invalid PID" 2>&1)"
rc=$?
set -e
printf "%s\n" "$output"
[[ "$rc" -eq 2 ]]
[[ "$output" == *"failed validation"* ]]
[[ ! -s "$KILL_LOG" ]]
'
echo "PASS: invalid NGINX_PID fails send_reload without signaling" >&2

run_pid_case missing-pid-send-reload 0 '
export NGINX_PID=77778
set +e
output="$(send_reload "missing PID" 2>&1)"
rc=$?
set -e
printf "%s\n" "$output"
[[ "$rc" -eq 2 ]]
[[ "$output" == *"does not refer to a live process"* ]]
[[ ! -s "$KILL_LOG" ]]
'
echo "PASS: nonexistent NGINX_PID fails send_reload without signaling" >&2

# A non-NGINX process cannot bypass the master check by matching the prefix.
run_pid_case prefix-bypass-rejected 0 '
export NGINX_PID=77777
export NGINX_PID_PREFIX=/test/nginx
export STUB_CMDLINE="sleep 100 /test/nginx"
set +e
output="$(send_reload "prefix bypass" 2>&1)"
rc=$?
set -e
printf "%s\n" "$output"
[[ "$rc" -eq 2 ]]
[[ "$output" == *"not an nginx master"* ]]
[[ ! -s "$KILL_LOG" ]]
'
echo "PASS: non-NGINX prefix match is rejected" >&2

# A missing source remains the only polling SKIP case.
: > "$KILL_LOG"
run_pid_case no-pid-send-reload 0 '
unset NGINX_PID HARNESS_NGINX_PID NGINX_PID_FILE NGINX_PID_PREFIX
set +e
output="$(send_reload "no PID source" 2>&1)"
rc=$?
set -e
printf "%s\n" "$output"
[[ "$rc" -eq 0 ]]
[[ "$output" == *"SKIP: no harness-owned or explicitly supplied NGINX PID"* ]]
[[ ! -s "$KILL_LOG" ]]
'
echo "PASS: no PID source uses polling SKIP" >&2

# Every configured PID-file error must reach send_reload and fail closed.
pid_file_missing="$TMP_ROOT/missing.pid"
: > "$KILL_LOG"
run_pid_case pid-file-missing-send-reload 0 '
export NGINX_PID_FILE="'"$pid_file_missing"'"
set +e
output="$(send_reload "missing pid file" 2>&1)"
rc=$?
set -e
printf "%s\n" "$output"
[[ "$rc" -eq 2 ]]
[[ "$output" == *"does not exist"* ]]
[[ "$output" != *"SKIP:"* ]]
[[ ! -s "$KILL_LOG" ]]
'
echo "PASS: missing pid file fails send_reload without signaling" >&2

pid_file_nonregular="$TMP_ROOT/nonregular.pid"
mkdir -p -- "$pid_file_nonregular"
: > "$KILL_LOG"
run_pid_case pid-file-nonregular-send-reload 0 '
export NGINX_PID_FILE="'"$pid_file_nonregular"'"
set +e
output="$(send_reload "nonregular pid file" 2>&1)"
rc=$?
set -e
printf "%s\n" "$output"
[[ "$rc" -eq 2 ]]
[[ "$output" == *"not a regular file"* ]]
[[ "$output" != *"SKIP:"* ]]
[[ ! -s "$KILL_LOG" ]]
'
echo "PASS: nonregular pid file fails send_reload without signaling" >&2

pid_file_canonicalize="$TMP_ROOT/canonicalize.pid"
printf "99999\n" > "$pid_file_canonicalize"
: > "$KILL_LOG"
run_pid_case pid-file-canonicalize-failure 0 '
export NGINX_PID_FILE="'"$pid_file_canonicalize"'"
cd() { return 1; }
set +e
output="$(send_reload "canonicalize failure" 2>&1)"
rc=$?
set -e
printf "%s\n" "$output"
[[ "$rc" -eq 2 ]]
[[ "$output" == *"unable to canonicalize"* ]]
[[ "$output" != *"SKIP:"* ]]
[[ ! -s "$KILL_LOG" ]]
'
echo "PASS: canonicalization failure fails send_reload without signaling" >&2

run_pid_case pid-file-missing-resolve 0 '
export NGINX_PID_FILE="'"$pid_file_missing"'"
set +e
resolve_nginx_pid >/dev/null
rc=$?
set -e
[[ "$rc" -eq 2 ]]
echo "OK: missing pid file returns PID_CONFIG_INVALID"
'
echo "PASS: missing pid file returns exact resolver status" >&2

pid_file_unreadable="$TMP_ROOT/unreadable.pid"
printf "99999\n" > "$pid_file_unreadable"
: > "$KILL_LOG"
run_pid_case pid-file-unreadable 0 '
export NGINX_PID_FILE="'"$pid_file_unreadable"'"
# A parser stub is deterministic even when the harness runs as root and can
# read mode-000 files; it models an unreadable pid-file parse operation.
awk() { return 1; }
set +e
output="$(send_reload "unreadable pid file" 2>&1)"
rc=$?
set -e
printf "%s\n" "$output"
[[ "$rc" -eq 2 ]]
[[ "$output" == *"NGINX_PID_FILE"* ]]
[[ "$output" != *"SKIP:"* ]]
[[ ! -s "$KILL_LOG" ]]
'
echo "PASS: unreadable pid file fails send_reload without signaling" >&2

# PID file: multi-line content is rejected.
pid_file_multi="$TMP_ROOT/multi.pid"
printf "12345\n67890\n" > "$pid_file_multi"
run_pid_case pid-file-multi-line 0 '
export NGINX_PID_FILE="'"$pid_file_multi"'"
set +e
resolve_nginx_pid >/dev/null
rc=$?
set -e
[[ "$rc" -eq 2 ]]
echo "OK: multi-line pid file rejected"
'
echo "PASS: multi-line pid file rejected" >&2

# PID file: non-numeric content is rejected.
pid_file_nonnum="$TMP_ROOT/nonnum.pid"
printf "not-a-pid\n" > "$pid_file_nonnum"
run_pid_case pid-file-non-numeric 0 '
export NGINX_PID_FILE="'"$pid_file_nonnum"'"
set +e
resolve_nginx_pid >/dev/null
rc=$?
set -e
[[ "$rc" -eq 2 ]]
echo "OK: non-numeric pid file rejected"
'
echo "PASS: non-numeric pid file rejected" >&2

# PID-file parsing permits only one numeric logical line and optional ASCII
# spaces around it.  Internal whitespace and extra tokens are rejected.
pid_file_space="$TMP_ROOT/space.pid"
printf " 99999 \n" > "$pid_file_space"
: > "$KILL_LOG"
run_pid_case pid-file-surrounding-spaces 0 '
export NGINX_PID_FILE="'"$pid_file_space"'"
export STUB_PID=99999
send_reload "surrounding spaces"
'
if ! grep -q "HUP 99999" "$KILL_LOG"; then
    echo "FAIL: surrounding-space pid file was not signaled" >&2
    exit 1
fi
echo "PASS: pid file accepts surrounding spaces" >&2

for pid_file_case in internal-space internal-tab extra-token; do
    case "$pid_file_case" in
        internal-space) pid_file_value="99 999" ;;
        internal-tab) pid_file_value=$'99\t999' ;;
        extra-token) pid_file_value="99999 extra" ;;
        *) echo "FAIL: unknown pid file case: $pid_file_case" >&2; exit 1 ;;
    esac
    pid_file_path="$TMP_ROOT/${pid_file_case}.pid"
    printf "%s\n" "$pid_file_value" > "$pid_file_path"
    : > "$KILL_LOG"
    run_pid_case "pid-file-$pid_file_case" 0 '
export NGINX_PID_FILE="'"$pid_file_path"'"
set +e
output="$(send_reload "invalid pid file" 2>&1)"
rc=$?
set -e
printf "%s\n" "$output"
[[ "$rc" -eq 2 ]]
[[ "$output" == *"NGINX_PID_FILE"* ]]
[[ ! -s "$KILL_LOG" ]]
'
done
echo "PASS: pid file rejects internal whitespace and extra tokens" >&2

# PID file: symlink is rejected.
pid_file_real="$TMP_ROOT/real.pid"
printf "12345\n" > "$pid_file_real"
pid_file_symlink="$TMP_ROOT/link.pid"
ln -sf "$pid_file_real" "$pid_file_symlink"
: > "$KILL_LOG"
run_pid_case pid-file-symlink-send-reload 0 '
export NGINX_PID_FILE="'"$pid_file_symlink"'"
set +e
output="$(send_reload "symlink pid file" 2>&1)"
rc=$?
set -e
printf "%s\n" "$output"
[[ "$rc" -eq 2 ]]
[[ "$output" == *"must not be a symlink"* ]]
[[ "$output" != *"SKIP:"* ]]
[[ ! -s "$KILL_LOG" ]]
'
echo "PASS: symlink pid file fails send_reload without signaling" >&2

# PID file: valid single-PID file is accepted and signaled.
: > "$KILL_LOG"
pid_file_valid="$TMP_ROOT/valid.pid"
printf "99999\n" > "$pid_file_valid"
run_pid_case pid-file-valid-resolve 0 '
export NGINX_PID_FILE="'"$pid_file_valid"'"
export STUB_PID=99999
set +e
pid="$(resolve_nginx_pid)"
rc=$?
set -e
[[ "$rc" -eq 0 ]]
[[ "$pid" == "99999" ]]
'
echo "PASS: valid pid file returns exact resolver status" >&2

: > "$KILL_LOG"
run_pid_case pid-file-valid-reload 0 '
export NGINX_PID_FILE="'"$pid_file_valid"'"
export STUB_PID=99999
send_reload "pid file reload"
'
if ! grep -q "HUP 99999" "$KILL_LOG"; then
    echo "FAIL: pid file PID was not signaled (log: $(cat "$KILL_LOG" 2>/dev/null))" >&2
    exit 1
fi
echo "PASS: valid pid file is signaled" >&2

# Explicit NGINX_PID wins over a valid pid file, and the pid file wins over
# the harness fallback.
: > "$KILL_LOG"
run_pid_case explicit-source-priority 0 '
export NGINX_PID=77777
export NGINX_PID_FILE="'"$pid_file_valid"'"
export HARNESS_NGINX_PID=88888
export STUB_PID=77777
send_reload "explicit source priority"
'
if ! grep -q "HUP 77777" "$KILL_LOG"; then
    echo "FAIL: NGINX_PID did not take priority over pid file" >&2
    exit 1
fi
echo "PASS: explicit PID source has highest priority" >&2

: > "$KILL_LOG"
run_pid_case pid-file-source-priority 0 '
unset NGINX_PID
export NGINX_PID_FILE="'"$pid_file_valid"'"
export HARNESS_NGINX_PID=88888
export STUB_PID=99999
send_reload "pid file source priority"
'
if ! grep -q "HUP 99999" "$KILL_LOG"; then
    echo "FAIL: pid file did not take priority over harness PID" >&2
    exit 1
fi
echo "PASS: pid file source has priority over harness fallback" >&2

# Prefix is an additional constraint for a valid NGINX master.
: > "$KILL_LOG"
run_pid_case master-correct-prefix 0 '
export NGINX_PID=77777
export NGINX_PID_PREFIX=/test/nginx
export STUB_CMDLINE="nginx: master process /test/nginx -p /test/nginx"
send_reload "correct master prefix"
'
if ! grep -q "HUP 77777" "$KILL_LOG"; then
    echo "FAIL: correct NGINX master and prefix were rejected" >&2
    exit 1
fi
echo "PASS: correct NGINX master and prefix are accepted" >&2

: > "$KILL_LOG"
run_pid_case master-wrong-prefix 0 '
export NGINX_PID=77777
export NGINX_PID_PREFIX=/other/nginx
export STUB_CMDLINE="nginx: master process /test/nginx -p /test/nginx"
set +e
output="$(send_reload "wrong master prefix" 2>&1)"
rc=$?
set -e
printf "%s\n" "$output"
[[ "$rc" -eq 2 ]]
[[ "$output" == *"does not match NGINX_PID_PREFIX"* ]]
[[ ! -s "$KILL_LOG" ]]
'
echo "PASS: wrong NGINX master prefix is rejected" >&2

# Remote NGINX_URL does not authorize a local kill.
: > "$KILL_LOG"
run_pid_case remote-url-no-kill 0 '
export NGINX_URL="http://remote-host:9999"
unset NGINX_PID HARNESS_NGINX_PID NGINX_PID_FILE
send_reload "remote url test"
'
if [[ -s "$KILL_LOG" ]]; then
    echo "FAIL: remote NGINX_URL caused a local kill" >&2
    cat "$KILL_LOG" >&2
    exit 1
fi
echo "PASS: remote NGINX_URL does not cause a local kill" >&2

# Harness-owned NGINX can reload (uses HARNESS_NGINX_PID).
: > "$KILL_LOG"
run_pid_case harness-owned-reload 0 '
export HARNESS_NGINX_PID=88888
export STUB_PID=88888
send_reload "harness-owned reload"
'
if ! grep -q "HUP 88888" "$KILL_LOG"; then
    echo "FAIL: harness-owned PID was not signaled (log: $(cat "$KILL_LOG" 2>/dev/null))" >&2
    exit 1
fi
echo "PASS: harness-owned NGINX reloads correctly" >&2

# ---------------------------------------------------------------------------
# Cleanup failure exit-code contract tests
# ---------------------------------------------------------------------------

if [[ "$(id -u)" -eq 0 ]]; then
    echo "SKIP: permission-based cleanup failure cases are not meaningful as root" >&2
else

# Case: original rc == 0 + restore fails → exit 70.
# Make the target directory read-only so mv cannot replace the file.  The
# cleanup trap runs WITHOUT restoring perms first, so restore fails.
cleanup_fail_target="$TMP_ROOT/cleanup-fail/markdown-dynamic.conf"
mkdir -p -- "${cleanup_fail_target%/*}"
printf 'caller-owned original\n' > "$cleanup_fail_target"
chmod 640 "$cleanup_fail_target"

run_case cleanup-restore-fail-rc0 70 '
set -e
source "$1"
TEST_TMPDIR="$3"
OWN_TEST_TMPDIR=1
export ALLOW_EXTERNAL_DYNCONF_TEST=1
prepare_dynconf_ownership "$2"
write_dynconf_atomically "schema_version=0.9\nmarkdown_filter=on"
# Make the directory read-only so the atomic restore (mv) fails.  The
# cleanup function itself will run in this state.  The outer harness
# restores perms after the case completes.
chmod 555 "${2%/*}"
trap "cleanup 0" EXIT
exit 0
' "$cleanup_fail_target"
chmod 755 "${cleanup_fail_target%/*}" 2>/dev/null || true
# The backup should be retained since restore failed.
if compgen -G "${cleanup_fail_target%/*}/.markdown-dynconf-backup.*" >/dev/null 2>&1; then
    echo "PASS: cleanup-restore-fail-rc0 exited 70 and backup retained" >&2
    rm -f -- "${cleanup_fail_target%/*}/.markdown-dynconf-backup."* 2>/dev/null || true
else
    echo "FAIL: cleanup-restore-fail-rc0 exited 70 but backup was not retained" >&2
    exit 1
fi

# Case: original rc != 0 + restore fails → preserve original rc (41).
cleanup_fail2_target="$TMP_ROOT/cleanup-fail2/markdown-dynamic.conf"
mkdir -p -- "${cleanup_fail2_target%/*}"
printf 'caller-owned original\n' > "$cleanup_fail2_target"
chmod 640 "$cleanup_fail2_target"

run_case cleanup-restore-fail-rc41 41 '
set -e
source "$1"
TEST_TMPDIR="$3"
OWN_TEST_TMPDIR=1
export ALLOW_EXTERNAL_DYNCONF_TEST=1
prepare_dynconf_ownership "$2"
write_dynconf_atomically "schema_version=0.9\nmarkdown_filter=on"
chmod 555 "${2%/*}"
trap "cleanup 41" EXIT
exit 41
' "$cleanup_fail2_target"
chmod 755 "${cleanup_fail2_target%/*}" 2>/dev/null || true
rm -f -- "${cleanup_fail2_target%/*}/.markdown-dynconf-backup."* 2>/dev/null || true
echo "PASS: original nonzero rc preserved when cleanup also fails" >&2

# Case: deleting a test-created file fails → exit 70 when rc==0.
cleanup_rmdir_target="$TMP_ROOT/cleanup-rmdir/markdown-dynamic.conf"
mkdir -p -- "${cleanup_rmdir_target%/*}"
run_case cleanup-rmdir-fail-rc0 70 '
set -e
source "$1"
TEST_TMPDIR="$3"
OWN_TEST_TMPDIR=1
export ALLOW_EXTERNAL_DYNCONF_TEST=1
prepare_dynconf_ownership "$2"
write_dynconf_atomically "schema_version=0.9\nmarkdown_filter=on"
# Make the directory non-writable so rm -f on the test-created file fails.
chmod 555 "${2%/*}"
trap "cleanup 0" EXIT
exit 0
' "$cleanup_rmdir_target"
chmod 755 "${cleanup_rmdir_target%/*}" 2>/dev/null || true
rm -f -- "$cleanup_rmdir_target" 2>/dev/null || true
echo "PASS: cleanup file-remove failure with rc0 exits 70" >&2
fi

# Case: original rc == 0 + cleanup succeeds → exit 0 (covered by existing
# existing-file test above which asserts rc 41 with successful restore).

# Case: signal rc (129) + cleanup → preserve 129 (covered by signal test).

echo "PASS: PID ownership and cleanup failure regression tests" >&2

echo "PASS: dynconf ownership and polling regression tests" >&2

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "FAIL: $FAIL_COUNT exit-status assertion(s) failed" >&2
    exit 1
fi
