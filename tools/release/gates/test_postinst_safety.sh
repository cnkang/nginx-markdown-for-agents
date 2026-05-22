#!/bin/bash
# ---------------------------------------------------------------------------
# test_postinst_safety.sh — Unit tests for check_postinst_safety.sh
#
# PURPOSE:
#   Validates that the postinst safety checker correctly identifies safe
#   scripts (exit 0) and rejects scripts containing forbidden operations
#   (exit 1). Also verifies that heredoc stripping prevents false positives
#   from instructional text.
#
# Validates: Requirements 11.4
#
# USAGE:
#   bash tools/release/gates/test_postinst_safety.sh
#
# EXIT CODES:
#   0  All tests pass
#   1  One or more tests failed
#
# NOTES:
#   - macOS bash 3.2 compatible (no bash 4+ features)
#   - AGENTS.md Rule 14: new helper must have corresponding tests
#   - AGENTS.md Rule 11: macOS bash 3.2 compatible
#   - AGENTS.md Rule 18: case has default; messages to stderr; explicit return
# ---------------------------------------------------------------------------

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK_SCRIPT="$SCRIPT_DIR/check_postinst_safety.sh"

PASS_COUNT=0
FAIL_COUNT=0
TMPDIR_TEST=""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Colors (if terminal supports them)
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    NC='\033[0m'
else
    GREEN=''
    RED=''
    NC=''
fi

pass() {
    local msg="$1"
    PASS_COUNT=$((PASS_COUNT + 1))
    printf "  ${GREEN}PASS${NC}: %s\n" "$msg"
    return 0
}

fail() {
    local msg="$1"
    local detail="${2:-}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf "  ${RED}FAIL${NC}: %s\n" "$msg" >&2
    if [ -n "$detail" ]; then
        printf "        Detail: %s\n" "$detail" >&2
    fi
    return 0
}

cleanup() {
    if [ -n "$TMPDIR_TEST" ] && [ -d "$TMPDIR_TEST" ]; then
        rm -rf "$TMPDIR_TEST"
    fi
    return 0
}

trap cleanup EXIT

# ---------------------------------------------------------------------------
# Pre-flight check
# ---------------------------------------------------------------------------

if [ ! -f "$CHECK_SCRIPT" ]; then
    printf '[ERROR] check_postinst_safety.sh not found at: %s\n' "$CHECK_SCRIPT" >&2
    exit 1
fi

if ! bash -n "$CHECK_SCRIPT" 2>/dev/null; then
    printf '[ERROR] check_postinst_safety.sh has syntax errors\n' >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Setup temp directory and test fixtures
# ---------------------------------------------------------------------------

TMPDIR_TEST="$(mktemp -d)"

# --- Fixture: safe postinst (only echo/printf to stderr in heredocs) ---
cat > "$TMPDIR_TEST/safe_postinst.sh" <<'FIXTURE'
#!/bin/bash
set -e

case "$1" in
    configure)
        cat >&2 <<'EOF'
======================================================================
nginx-markdown-for-agents module installed successfully.

To enable the module:
  1. Add to nginx.conf (top-level, before http block):
     load_module modules/ngx_http_markdown_module.so;

  2. Verify configuration:
     sudo nginx -t

  3. Reload NGINX:
     sudo systemctl reload nginx

For compatibility information, see:
  /usr/share/doc/nginx-markdown-for-agents/COMPATIBILITY.md
======================================================================
EOF
        ;;
    *)
        ;;
esac

exit 0
FIXTURE

# --- Fixture: unsafe postinst with systemctl reload nginx ---
cat > "$TMPDIR_TEST/unsafe_reload.sh" <<'FIXTURE'
#!/bin/bash
set -e

case "$1" in
    configure)
        echo "Enabling module..." >&2
        systemctl reload nginx
        ;;
    *)
        ;;
esac

exit 0
FIXTURE

# --- Fixture: unsafe postinst with sed -i on nginx.conf ---
cat > "$TMPDIR_TEST/unsafe_conf_modify.sh" <<'FIXTURE'
#!/bin/bash
set -e

case "$1" in
    configure)
        sed -i 's/# load_module/load_module/' /etc/nginx/nginx.conf
        ;;
    *)
        ;;
esac

exit 0
FIXTURE

# --- Fixture: forbidden commands INSIDE heredocs (should pass) ---
cat > "$TMPDIR_TEST/heredoc_safe.sh" <<'FIXTURE'
#!/bin/bash
set -e

case "$1" in
    configure)
        cat >&2 <<'EOF'
After installation, run:
  systemctl reload nginx
  sed -i 's/old/new/' /etc/nginx/nginx.conf
  nginx -s reload
  service nginx restart
EOF
        ;;
    *)
        ;;
esac

exit 0
FIXTURE

# --- Fixture: unsafe postinst with nginx -s reload ---
cat > "$TMPDIR_TEST/unsafe_nginx_signal.sh" <<'FIXTURE'
#!/bin/bash
set -e

case "$1" in
    configure)
        nginx -s reload
        ;;
    *)
        ;;
esac

exit 0
FIXTURE

# --- Fixture: unsafe postinst writing to /etc/nginx/ ---
cat > "$TMPDIR_TEST/unsafe_etc_nginx.sh" <<'FIXTURE'
#!/bin/bash
set -e

case "$1" in
    configure)
        cp /tmp/snippet.conf /etc/nginx/conf.d/markdown.conf
        ;;
    *)
        ;;
esac

exit 0
FIXTURE

# --- Fixture: unsafe postinst enabling snippet via symlink ---
cat > "$TMPDIR_TEST/unsafe_snippet_enable.sh" <<'FIXTURE'
#!/bin/bash
set -e

case "$1" in
    configure)
        ln -s /etc/nginx/modules-available/markdown.conf /etc/nginx/modules-enabled/markdown.conf
        ;;
    *)
        ;;
esac

exit 0
FIXTURE

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

printf '========================================================================\n' >&2
printf ' Unit Tests: check_postinst_safety.sh (postinst safety checker)\n' >&2
printf ' Validates: Requirements 11.4\n' >&2
printf '========================================================================\n' >&2
printf '\n'

# --- Safe scripts (should exit 0) ---

printf 'Test group: Safe scripts (expect exit 0)\n'

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/safe_postinst.sh" >/dev/null 2>/dev/null || local_exit=$?
if [ "$local_exit" -eq 0 ]; then
    pass "safe postinst (only heredoc instructions) exits 0"
else
    fail "safe postinst (only heredoc instructions) exits 0" "expected exit 0, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/heredoc_safe.sh" >/dev/null 2>/dev/null || local_exit=$?
if [ "$local_exit" -eq 0 ]; then
    pass "forbidden commands inside heredocs exits 0 (heredoc stripping works)"
else
    fail "forbidden commands inside heredocs exits 0 (heredoc stripping works)" "expected exit 0, got exit $local_exit"
fi

printf '\n'

# --- Unsafe scripts (should exit 1) ---

printf 'Test group: Unsafe scripts (expect exit 1)\n'

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/unsafe_reload.sh" >/dev/null 2>/dev/null || local_exit=$?
if [ "$local_exit" -eq 1 ]; then
    pass "script with systemctl reload nginx exits 1"
else
    fail "script with systemctl reload nginx exits 1" "expected exit 1, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/unsafe_conf_modify.sh" >/dev/null 2>/dev/null || local_exit=$?
if [ "$local_exit" -eq 1 ]; then
    pass "script with sed -i nginx.conf exits 1"
else
    fail "script with sed -i nginx.conf exits 1" "expected exit 1, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/unsafe_nginx_signal.sh" >/dev/null 2>/dev/null || local_exit=$?
if [ "$local_exit" -eq 1 ]; then
    pass "script with nginx -s reload exits 1"
else
    fail "script with nginx -s reload exits 1" "expected exit 1, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/unsafe_etc_nginx.sh" >/dev/null 2>/dev/null || local_exit=$?
if [ "$local_exit" -eq 1 ]; then
    pass "script writing to /etc/nginx/ exits 1"
else
    fail "script writing to /etc/nginx/ exits 1" "expected exit 1, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/unsafe_snippet_enable.sh" >/dev/null 2>/dev/null || local_exit=$?
if [ "$local_exit" -eq 1 ]; then
    pass "script enabling snippet via symlink exits 1"
else
    fail "script enabling snippet via symlink exits 1" "expected exit 1, got exit $local_exit"
fi

printf '\n'

# --- Help flag (should exit 0) ---

printf 'Test group: Usage and help\n'

local_exit=0
bash "$CHECK_SCRIPT" --help >/dev/null 2>/dev/null || local_exit=$?
if [ "$local_exit" -eq 0 ]; then
    pass "--help flag exits 0"
else
    fail "--help flag exits 0" "expected exit 0, got exit $local_exit"
fi

printf '\n'

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf '========================================================================\n'
printf ' Results: %d passed, %d failed\n' "$PASS_COUNT" "$FAIL_COUNT"
printf '========================================================================\n'

if [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
fi

exit 0
