#!/bin/bash
# Integration tests for install.sh release-tag identity verification.
#
# The default latest-release flow leaves RELEASE_VERSION empty and must skip
# the exact requested-tag identity comparison, while a pinned VERSION must
# still be compared against the resolved release tag (with one leading v
# normalized away on both sides).
#
# The tests source the real installer function block (everything before the
# main execution flow) inside a child shell, override die_with_error to make
# the failure observable, and drive verify_requested_tag_identity directly.
#
# Usage: bash tools/tests/test_install_tag_identity.sh
# Exit 0 if all tests pass, exit 1 if any fail.
# Compatible with bash 3.2+ on macOS and Linux.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_SCRIPT="$REPO_ROOT/tools/install.sh"

PASS_COUNT=0
FAIL_COUNT=0

# Marker line that starts the installer main execution flow; everything
# before it is function and constant definitions that are safe to source.
MAIN_FLOW_MARKER='^# When --json is set, save original stdout'

TRUNCATED_FILE="$(mktemp)"
trap 'rm -f "$TRUNCATED_FILE"' EXIT

# info prints an informational line to stderr.
info() {
  echo "$1" >&2
  return 0
}

# pass records a passing assertion.
pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  echo "  PASS: $1"
  return 0
}

# fail records a failing assertion and prints the detail to stderr.
fail() {
  local msg="$1"
  local detail="${2:-}"
  FAIL_COUNT=$((FAIL_COUNT + 1))
  echo "  FAIL: $msg" >&2
  if [[ -n "$detail" ]]; then
    echo "        Detail: $detail" >&2
  fi
  return 0
}

# build_truncated_source materializes the installer function block (constants
# and functions, without the main flow) into TRUNCATED_FILE.
build_truncated_source() {
  local marker_line
  marker_line="$(grep -n "$MAIN_FLOW_MARKER" "$INSTALL_SCRIPT" | head -1 | cut -d: -f1)"
  if [[ -z "$marker_line" ]]; then
    echo "FAIL: main-flow marker not found in install.sh" >&2
    return 1
  fi
  head -n $((marker_line - 1)) "$INSTALL_SCRIPT" > "$TRUNCATED_FILE"
  return 0
}

# run_identity_check drives the production verify_requested_tag_identity
# function with the requested version and resolved tag, capturing stderr.
# Output: "rc=<code>" on the first line, captured stderr afterwards.
run_identity_check() {
  local requested_version="$1"
  local resolved_tag="$2"
  local stderr_file
  stderr_file="$(mktemp)"
  bash -c '
    source "$1"
    die_with_error() {
      printf "DIE:%s:%s\n" "$1" "$2"
      exit 42
    }
    RELEASE_VERSION="$2"
    RELEASE_TAG="$3"
    verify_requested_tag_identity
  ' _ "$TRUNCATED_FILE" "$requested_version" "$resolved_tag" 2>"$stderr_file"
  local rc=$?
  printf 'rc=%s\n' "$rc"
  cat "$stderr_file"
  rm -f "$stderr_file"
  return 0
}

# expect_no_die asserts the identity check returns 0 and never calls
# die_with_error for the given version pair.
expect_no_die() {
  local label="$1"
  local requested_version="$2"
  local resolved_tag="$3"
  local output
  output="$(run_identity_check "$requested_version" "$resolved_tag")"
  if [[ "$output" == rc=0* ]]; then
    pass "$label — identity check passed without dying"
  else
    fail "$label — expected rc=0 without die_with_error" "$output"
  fi
  return 0
}

# expect_die asserts the identity check aborts with the checksum-category
# mismatch error for the given version pair.
expect_die() {
  local label="$1"
  local requested_version="$2"
  local resolved_tag="$3"
  local output
  output="$(run_identity_check "$requested_version" "$resolved_tag")"
  if [[ "$output" == *rc=42* ]]; then
    pass "$label — identity check aborted on mismatch"
  else
    fail "$label — expected die_with_error exit (42)" "$output"
    return 0
  fi
  if [[ "$output" == *"DIE:checksum:"* ]]; then
    pass "$label — checksum category present"
  else
    fail "$label — expected checksum category" "$output"
  fi
  if [[ "$output" == *"does not match the requested version"* ]]; then
    pass "$label — mismatch message present"
  else
    fail "$label — expected mismatch message" "$output"
  fi
  return 0
}

info ""
info " Integration Tests: install.sh release-tag identity verification"
info ""

if ! build_truncated_source; then
  echo "FAIL: could not build truncated installer source" >&2
  exit 1
fi

# -----------------------------------------------------------------------
# Test 1: latest-release mode (empty RELEASE_VERSION) skips the check
# -----------------------------------------------------------------------
info "Test 1: latest mode (empty RELEASE_VERSION) skips the identity check"
expect_no_die "latest mode" "" "v0.9.2"
echo ""

# -----------------------------------------------------------------------
# Test 2: pinned version matching the resolved tag passes
# -----------------------------------------------------------------------
info "Test 2: pinned version matches resolved tag"
expect_no_die "pinned exact" "v0.9.2" "v0.9.2"
echo ""

# -----------------------------------------------------------------------
# Test 3: leading-v normalization on one side still matches
# -----------------------------------------------------------------------
info "Test 3: leading-v normalization in both directions"
expect_no_die "pinned unpinned-spelling" "0.9.2" "v0.9.2"
expect_no_die "pinned tag-unprefixed" "v0.9.2" "0.9.2"
echo ""

# -----------------------------------------------------------------------
# Test 4: pinned version mismatching the resolved tag dies
# -----------------------------------------------------------------------
info "Test 4: pinned version mismatch dies with checksum error"
expect_die "pinned mismatch" "v0.9.1" "v0.9.2"
echo ""

# -----------------------------------------------------------------------
# Test 5: static wiring — the main flow calls the helper
# -----------------------------------------------------------------------
info "Test 5: static wiring"
if grep -q '^verify_requested_tag_identity()' "$INSTALL_SCRIPT"; then
  pass "helper definition present"
else
  fail "helper definition missing from install.sh"
fi
if grep -qE '^  verify_requested_tag_identity$' "$INSTALL_SCRIPT"; then
  pass "main flow calls verify_requested_tag_identity"
else
  fail "main flow does not call verify_requested_tag_identity"
fi
if grep -q 'REQUESTED_TAG_NORM=' "$INSTALL_SCRIPT"; then
  fail "stale inline tag normalization still present in install.sh"
else
  pass "no stale inline tag normalization"
fi
echo ""

# -----------------------------------------------------------------------
# Test 6: fetch_release_json retries the equivalent tag spelling
# -----------------------------------------------------------------------
info "Test 6: fetch_release_json retries the equivalent tag spelling"

FAKE_CURL_DIR="$(mktemp -d)"
trap 'rm -rf "${TRUNCATED_FILE}" "${FAKE_CURL_DIR}"' EXIT
cat > "${FAKE_CURL_DIR}/fake-curl" << 'FAKE'
#!/bin/bash
# Succeed only for the v-prefixed tag; every other URL fails like a 404.
for argument in "$@"; do
    if [[ "$argument" == *"/releases/tags/v0.9.2" ]]; then
        printf '{"tag_name": "v0.9.2", "assets": []}'
        exit 0
    fi
done
exit 22
FAKE
chmod +x "${FAKE_CURL_DIR}/fake-curl"

fetch_output="$(bash -c '
    source "$1"
    CURL_BIN="$2"
    RELEASE_VERSION="0.9.2"
    fetch_release_json
' _ "${TRUNCATED_FILE}" "${FAKE_CURL_DIR}/fake-curl")"
fetch_rc=$?
if [[ "$fetch_rc" -eq 0 && "$fetch_output" == *'"tag_name": "v0.9.2"'* ]]; then
    pass "unpinned-spelling VERSION resolves the v-prefixed release"
else
    fail "unpinned-spelling VERSION should retry with the v prefix" \
         "rc=$fetch_rc output=$fetch_output"
fi

fetch_output="$(bash -c '
    source "$1"
    CURL_BIN="$2"
    RELEASE_VERSION="v0.9.3"
    fetch_release_json
' _ "${TRUNCATED_FILE}" "${FAKE_CURL_DIR}/fake-curl")"
fetch_rc=$?
if [[ "$fetch_rc" -ne 0 ]]; then
    pass "a version matching no release spelling returns an error"
else
    fail "unknown version should fail both release lookups" \
         "rc=$fetch_rc output=$fetch_output"
fi
echo ""

info "Summary: $PASS_COUNT passed, $FAIL_COUNT failed"
if [[ "$FAIL_COUNT" -gt 0 ]]; then
  exit 1
fi
exit 0
