#!/bin/bash
# Integration tests for install.sh structured error message format.
# Validates: Requirements 12.4
#
# Property 9: Install script error message format consistency
# — All error exit paths SHALL produce [ERROR] <category>: <description>
#   followed by at least one [SUGGEST] <suggestion> on stderr.
#
# Usage: bash tools/tests/test_install_error_format.sh
# Exit 0 if all tests pass, exit 1 if any fail.
# Compatible with bash 4.0+ on macOS and Linux.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_SCRIPT="$REPO_ROOT/tools/install.sh"

PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

# Repeated separator line
SEPARATOR_LINE="========================================================================"

# Colors (if terminal supports them)
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'
  RED='\033[0;31m'
  YELLOW='\033[0;33m'
  NC='\033[0m'
else
  GREEN=''
  RED=''
  YELLOW=''
  NC=''
fi

pass() {
  local msg="$1"
  PASS_COUNT=$((PASS_COUNT + 1))
  echo -e "  ${GREEN}PASS${NC}: ${msg}"
  return 0
}

fail() {
  local msg="$1"
  local detail="${2:-}"
  FAIL_COUNT=$((FAIL_COUNT + 1))
  echo -e "  ${RED}FAIL${NC}: ${msg}" >&2
  if [[ -n "${detail}" ]]; then
    echo "        Detail: ${detail}" >&2
  fi
  return 0
}

skip() {
  local msg="$1"
  SKIP_COUNT=$((SKIP_COUNT + 1))
  echo -e "  ${YELLOW}SKIP${NC}: ${msg}"
  return 0
}

# Verify stderr contains at least one [ERROR] <category>: <message> line.
# Returns 0 if found, 1 otherwise.
assert_has_error_format() {
  local stderr_output="$1"
  echo "$stderr_output" | grep -qE '^\[ERROR\] [a-z_]+: .+'
  return $?
}

# Verify stderr contains at least one [SUGGEST] <suggestion> line.
# Returns 0 if found, 1 otherwise.
assert_has_suggest_format() {
  local stderr_output="$1"
  echo "$stderr_output" | grep -qE '^\[SUGGEST\] .+'
  return $?
}

# Build a PATH that contains all current PATH directories but hides a
# specific binary by placing a shim directory first.
# Usage: build_path_hiding <binary_name>
# Prints the new PATH value to stdout.
build_path_hiding() {
  local binary_name="$1"
  local shim_dir
  shim_dir="$(mktemp -d)"

  # Create shim scripts for common utilities so they remain accessible,
  # but do NOT create one for the binary we want to hide.
  # The approach: put an empty shim dir at the front and rely on the
  # real PATH for everything except the hidden binary.
  # We simply remove directories containing the target binary.
  local new_path=""
  local dir
  while IFS= read -r -d ':' dir || [[ -n "$dir" ]]; do
    if [[ -x "$dir/$binary_name" ]]; then
      # Skip this directory only if it contains the binary to hide
      # But we need the other binaries from this dir, so symlink them
      if [[ -d "$dir" ]]; then
        for bin in "$dir"/*; do
          local bname
          bname="$(basename "$bin")"
          if [[ "$bname" != "$binary_name" && -x "$bin" && ! -e "$shim_dir/$bname" ]]; then
            ln -sf "$bin" "$shim_dir/$bname" 2>/dev/null || true
          fi
        done
      fi
    else
      if [[ -n "$new_path" ]]; then
        new_path="${new_path}:${dir}"
      else
        new_path="$dir"
      fi
    fi
  done <<< "$PATH"

  # Put shim dir first so its symlinks take priority
  echo "${shim_dir}:${new_path}"
  # Caller is responsible for cleaning up shim_dir
  echo "$shim_dir" >&2
  return 0
}

# Run install.sh with a modified environment that hides nginx from PATH.
# Captures stderr and validates [ERROR]/[SUGGEST] format.
# Usage: run_hiding_nginx <test_name> [extra_args...]
run_hiding_nginx() {
  local test_name="$1"
  shift

  local stderr_file stdout_file shim_info
  stderr_file="$(mktemp)"
  stdout_file="$(mktemp)"
  shim_info="$(mktemp)"

  # Build restricted PATH (hide nginx); shim dir path comes on stderr
  local restricted_path
  restricted_path="$(build_path_hiding nginx 2>"$shim_info")"
  local shim_dir
  shim_dir="$(cat "$shim_info")"
  rm -f "$shim_info"

  # Run install.sh with restricted PATH
  SKIP_ROOT_CHECK=1 PATH="$restricted_path" \
    bash "$INSTALL_SCRIPT" "$@" >"$stdout_file" 2>"$stderr_file" || true

  local stderr_content stdout_content
  stderr_content="$(cat "$stderr_file")"
  stdout_content="$(cat "$stdout_file")"
  rm -f "$stderr_file" "$stdout_file"
  rm -rf "$shim_dir"

  if [[ -z "$stderr_content" ]]; then
    fail "$test_name — no stderr output captured"
    echo ""
    return 1
  fi

  if assert_has_error_format "$stderr_content"; then
    pass "$test_name — [ERROR] format present"
  else
    fail "$test_name — missing [ERROR] <category>: <message>" \
         "stderr: $(echo "$stderr_content" | head -5)"
  fi

  if assert_has_suggest_format "$stderr_content"; then
    pass "$test_name — [SUGGEST] format present"
  else
    fail "$test_name — missing [SUGGEST] <suggestion>" \
         "stderr: $(echo "$stderr_content" | head -5)"
  fi

  # Return stdout via a global variable for callers that need it
  _LAST_STDOUT="$stdout_content"
  _LAST_STDERR="$stderr_content"
  return 0
}

echo "$SEPARATOR_LINE"
echo " Integration Tests: install.sh error message format (Property 9)"
echo " Validates: Requirements 12.4"
echo "$SEPARATOR_LINE"
echo ""

# -----------------------------------------------------------------------
# Test 1: nginx not found (hide nginx from PATH)
# -----------------------------------------------------------------------
echo "Test 1: nginx not found"
run_hiding_nginx "nginx not found"
echo ""

# -----------------------------------------------------------------------
# Test 2: Root check failure (non-root without SKIP_ROOT_CHECK)
# -----------------------------------------------------------------------
echo "Test 2: Root check failure"
if [[ "$EUID" -ne 0 ]]; then
  # We are not root, so unsetting SKIP_ROOT_CHECK should trigger the error
  STDERR_FILE="$(mktemp)"
  SKIP_ROOT_CHECK=0 bash "$INSTALL_SCRIPT" 2>"$STDERR_FILE" >/dev/null || true
  STDERR_CONTENT="$(cat "$STDERR_FILE")"
  rm -f "$STDERR_FILE"

  if [[ -z "$STDERR_CONTENT" ]]; then
    fail "root check — no stderr output"
  else
    if assert_has_error_format "$STDERR_CONTENT"; then
      pass "root check — [ERROR] format present"
    else
      fail "root check — missing [ERROR] format"
    fi
    if assert_has_suggest_format "$STDERR_CONTENT"; then
      pass "root check — [SUGGEST] format present"
    else
      fail "root check — missing [SUGGEST] format"
    fi
  fi
else
  skip "root check — running as root, cannot trigger this error"
fi
echo ""

# -----------------------------------------------------------------------
# Test 3: --json flag produces valid JSON on error (nginx not found)
# -----------------------------------------------------------------------
echo "Test 3: --json flag produces valid JSON on error"
run_hiding_nginx "--json error" --json

JSON_OUT="$_LAST_STDOUT"

# The install script prints a banner to stdout before the JSON line.
# Extract only the JSON line (starts with '{').
JSON_LINE=""
if [[ -n "$JSON_OUT" ]]; then
  JSON_LINE="$(echo "$JSON_OUT" | grep '^{' | tail -1)"
fi

# stdout should contain valid JSON with success=false
if [[ -n "$JSON_LINE" ]]; then
  # Validate JSON structure using python3 if available, otherwise basic check
  if command -v python3 >/dev/null 2>&1; then
    if echo "$JSON_LINE" | python3 -c "
import json, sys
data = json.load(sys.stdin)
assert data.get('success') == False, 'success should be false'
assert 'error' in data, 'error field missing'
assert data['error'] is not None, 'error should not be null'
assert 'category' in data['error'], 'error.category missing'
assert 'message' in data['error'], 'error.message missing'
assert isinstance(data.get('suggestions', []), list), 'suggestions should be a list'
" 2>/dev/null; then
      pass "--json error: stdout is valid JSON with expected schema"
    else
      fail "--json error: stdout JSON validation failed" \
           "json line: $(echo "$JSON_LINE" | head -1)"
    fi
  else
    # Fallback: basic string checks
    if echo "$JSON_LINE" | grep -q '"success":false'; then
      pass "--json error: stdout contains success:false (basic check)"
    else
      fail "--json error: stdout missing success:false" \
           "json line: $(echo "$JSON_LINE" | head -1)"
    fi
  fi
else
  fail "--json error: no JSON line found in stdout (expected line starting with '{')"
fi
echo ""

# -----------------------------------------------------------------------
# Test 4: Static analysis — every exit 1 preceded by emit_error/die_with_error
# -----------------------------------------------------------------------
echo "Test 4: Static analysis — exit 1 paths use structured error helpers"

# Strategy: find all lines with 'exit 1' in install.sh, excluding:
#   - Lines inside awk/sed/python heredocs (between <<'...' and the delimiter)
#   - The die_with_error function definition itself
#   - Function definitions of helpers (emit_error, emit_suggest, etc.)
# For each remaining exit 1, verify the surrounding context includes
# emit_error or die_with_error.

STATIC_PASS=0
STATIC_FAIL=0

# We use a python3 script for reliable multi-line analysis if available,
# otherwise fall back to a simpler grep-based approach.
if command -v python3 >/dev/null 2>&1; then
  STATIC_RESULT="$(python3 - "$INSTALL_SCRIPT" <<'PYEOF'
import re
import sys

filepath = sys.argv[1]
with open(filepath, "r") as f:
    lines = f.readlines()

# Track whether we are inside a heredoc or awk/python block
in_heredoc = False
heredoc_end = ""
in_function_def = False
function_name = ""

# Regions to skip: awk inline scripts, python heredocs, function defs
# We'll mark each line as "shell code" or "skip"
line_types = []

i = 0
while i < len(lines):
    line = lines[i].rstrip("\n")
    stripped = line.strip()

    if in_heredoc:
        if stripped == heredoc_end:
            in_heredoc = False
            heredoc_end = ""
        line_types.append("heredoc")
        i += 1
        continue

    # Detect heredoc start: <<'DELIM' or <<DELIM or <<"DELIM"
    heredoc_match = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
    if heredoc_match:
        heredoc_end = heredoc_match.group(1)
        line_types.append("shell")
        in_heredoc = True
        i += 1
        continue

    line_types.append("shell")
    i += 1

# Now find all 'exit 1' in shell lines
issues = []
for i, (line, ltype) in enumerate(zip(lines, line_types)):
    if ltype != "shell":
        continue
    stripped = line.strip()
    if not re.search(r'\bexit\s+1\b', stripped):
        continue

    lineno = i + 1

    # Skip if this line is inside the die_with_error function body
    # (the function itself calls exit 1, that's expected)
    # Look backwards to find if we're in die_with_error() { ... }
    in_die_func = False
    in_awk_block = False
    brace_depth = 0
    for j in range(i, -1, -1):
        prev = lines[j].strip()
        # Check if we're inside an awk script (single-quoted block)
        if "awk " in prev and "'" in prev:
            in_awk_block = True
            break
        if re.match(r'^die_with_error\s*\(\)', prev):
            in_die_func = True
            break
        if re.match(r'^(emit_error|emit_suggest|json_output|semver_lt|sha256_file)\s*\(\)', prev):
            in_die_func = True  # Also skip helper function internals
            break
        # Don't look back more than 30 lines
        if i - j > 30:
            break

    if in_die_func or in_awk_block:
        continue

    # Check if the preceding ~10 lines contain emit_error or die_with_error
    context_start = max(0, i - 10)
    context = "".join(lines[context_start:i + 1])
    if "emit_error" in context or "die_with_error" in context:
        continue

    issues.append(f"Line {lineno}: 'exit 1' without preceding emit_error/die_with_error")

if issues:
    for issue in issues:
        print(f"FAIL: {issue}")
else:
    print("PASS: All exit 1 paths use structured error helpers")
PYEOF
  )"

  while IFS= read -r result_line; do
    if [[ "$result_line" == PASS:* ]]; then
      pass "${result_line#PASS: }"
      STATIC_PASS=$((STATIC_PASS + 1))
    elif [[ "$result_line" == FAIL:* ]]; then
      fail "${result_line#FAIL: }"
      STATIC_FAIL=$((STATIC_FAIL + 1))
    fi
  done <<< "$STATIC_RESULT"

  if [[ "$STATIC_FAIL" -eq 0 && "$STATIC_PASS" -eq 0 ]]; then
    # No output means no issues found
    pass "static analysis — all exit 1 paths use structured error helpers"
  fi
else
  # Fallback: simple grep-based check
  # Find exit 1 lines and check if nearby lines have emit_error or die_with_error
  EXIT_LINES="$(grep -n 'exit 1' "$INSTALL_SCRIPT" | grep -v '^\s*#' || true)"
  ALL_OK=1
  while IFS= read -r eline; do
    [[ -z "$eline" ]] && continue
    LINENO_NUM="$(echo "$eline" | cut -d: -f1)"
    # Check 10 lines before for emit_error or die_with_error
    START=$((LINENO_NUM - 10))
    [[ "$START" -lt 1 ]] && START=1
    CONTEXT="$(sed -n "${START},${LINENO_NUM}p" "$INSTALL_SCRIPT")"
    if echo "$CONTEXT" | grep -qE '(emit_error|die_with_error)'; then
      continue
    fi
    # Check if inside awk block or function definition
    if echo "$CONTEXT" | grep -qE "(awk |die_with_error\s*\(\)|emit_error\s*\(\)|semver_lt\s*\(\)|sha256_file\s*\(\))"; then
      continue
    fi
    fail "static analysis — line $LINENO_NUM: exit 1 without structured error"
    ALL_OK=0
  done <<< "$EXIT_LINES"

  if [[ "$ALL_OK" -eq 1 ]]; then
    pass "static analysis — all exit 1 paths use structured error helpers (grep fallback)"
  fi
fi
echo ""

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------
echo "$SEPARATOR_LINE"
echo " Results: $PASS_COUNT passed, $FAIL_COUNT failed, $SKIP_COUNT skipped"
echo "$SEPARATOR_LINE"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
  exit 1
fi

exit 0
