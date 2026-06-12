#!/bin/bash
#
# Shell Hygiene Detection Script (SonarCloud S7682, S7688, S131, S1066, Rule 18)
#
# Scans shell scripts for common hygiene violations that repeatedly
# caused fix commits in the 2026-04 to 2026-05 window:
#
#   (a) Functions missing explicit return statements (S7682)
#       Every shell function must end with an explicit 'return' statement
#       so callers do not inherit an accidental exit status from the last
#       command.  Per AGENTS.md Rule 18, this applies to ALL functions,
#       not only those that emit no output.
#
#   (b) Diagnostic/info messages sent to stdout instead of stderr
#       (S1066 / Rule 18)  Lines containing echo/printf with
#       INFO/WARN/DEBUG/ERROR/SUGGEST markers that lack >&2 redirection
#       pollute stdout when scripts are piped or output is captured.
#
#   (c) Single-bracket [ ] instead of [[ ]] (S7688)
#       In bash scripts, [[ ]] is safer (no word splitting, no pathname
#       expansion) and more feature-rich.  Per AGENTS.md Rule 18, all
#       conditional tests in bash scripts must use [[ ]].
#
#   (d) case statements without default *) clause (S131)
#       Every case statement must include a default *) clause, even if
#       it only contains a comment or no-op.  Per AGENTS.md Rule 18.
#
#   (e) curl -X HEAD instead of curl --head (Rule 18 / e0f3948)
#       -X HEAD sends a method override that may not behave identically
#       to --head across all HTTP servers.  Per AGENTS.md Rule 18,
#       prefer --head (-I) for HTTP HEAD validation.
#
# Rationale: 8+ fix commits in the review window addressed these three
# patterns (0a43c15, 6fb0b1a, 698a2cb, 486a97a, f723235, e0f3948,
# 495aa0e, a9ea852).  No prior automated detection existed.
#
# Usage: bash tools/harness/detect_shell_hygiene.sh [directory]
#   directory defaults to tools
#
# Exit codes:
#   0 — no findings (or only allowlisted patterns)
#   1 — one or more non-allowlisted findings requiring fix
#

set -euo pipefail

readonly SCAN_DIR="${1:-tools}"
readonly SCRIPT_NAME="$(basename "$0")"
readonly MSG_NONE_FOUND="  (none found)"

errors=0
warnings=0

# ── Warning Allowlist ──
#
# Format: "filepath:pattern_id:func_or_detail:justification"
#
# Each entry exempts a specific warning from causing a non-zero exit.
# filepath is matched as a substring; pattern_id identifies the check
# (return, stderr, bracket, case, curl_head). Justification is mandatory.
#
# These are functions that are trivial logging wrappers (1-2 lines) or
# test helpers where the implicit return of echo/printf >&2 is always 0.
# They are tracked for future remediation but do not block CI.
readonly WARNING_ALLOWLIST=(
    # ── tools/compat-check/nginx-markdown-compat-check.sh ──
    # Trivial single-statement logging functions; echo return is always 0
    "tools/compat-check/nginx-markdown-compat-check.sh:return:log_info:trivial echo-only logger; implicit return is always 0"
    "tools/compat-check/nginx-markdown-compat-check.sh:return:log_warn:trivial echo-only logger; implicit return is always 0"
    "tools/compat-check/nginx-markdown-compat-check.sh:return:log_error:trivial echo-only logger; implicit return is always 0"
    # ── tools/compat-check/test_compat_check.sh ──
    # Test helper functions under set -e; implicit return safe for test harness
    "tools/compat-check/test_compat_check.sh:return:setup_mock_dir:test setup helper under set -e; mkdir return is meaningful"
    "tools/compat-check/test_compat_check.sh:return:cleanup_mock_dir:test cleanup helper under set -e; rm return is meaningful"
    "tools/compat-check/test_compat_check.sh:return:create_mock_nginx:test fixture creator under set -e; last statement is redirect"
    "tools/compat-check/test_compat_check.sh:return:create_mock_uname:test fixture creator under set -e; last statement is redirect"
    "tools/compat-check/test_compat_check.sh:return:run_helper:test runner under set -e; captures exit code explicitly"
    "tools/compat-check/test_compat_check.sh:return:run_test:test runner under set -e; orchestrates test assertions"
    # ── tools/release/gates/check_artifact_naming.sh ──
    # Single-statement logging functions; echo >&2 always returns 0
    "tools/release/gates/check_artifact_naming.sh:return:log_info:trivial echo-only logger; implicit return is always 0"
    "tools/release/gates/check_artifact_naming.sh:return:log_pass:trivial echo-only logger; implicit return is always 0"
    "tools/release/gates/check_artifact_naming.sh:return:log_fail:trivial echo-only logger; implicit return is always 0"
    "tools/release/gates/check_artifact_naming.sh:return:log_error:trivial echo-only logger; implicit return is always 0"
    # ── tools/release/gates/check_install_layout.sh ──
    # Single-statement logging functions; echo >&2 always returns 0
    "tools/release/gates/check_install_layout.sh:return:log_info:trivial echo-only logger; implicit return is always 0"
    "tools/release/gates/check_install_layout.sh:return:log_pass:trivial echo-only logger; implicit return is always 0"
    "tools/release/gates/check_install_layout.sh:return:log_fail:trivial echo-only logger; implicit return is always 0"
    "tools/release/gates/check_install_layout.sh:return:log_error:trivial echo-only logger; implicit return is always 0"
    # ── tools/release/gates/check_postinst_safety.sh ──
    # Single-statement logging functions; echo >&2 always returns 0
    "tools/release/gates/check_postinst_safety.sh:return:log_info:trivial echo-only logger; implicit return is always 0"
    "tools/release/gates/check_postinst_safety.sh:return:log_warn:trivial echo-only logger; implicit return is always 0"
    "tools/release/gates/check_postinst_safety.sh:return:log_error:trivial echo-only logger; implicit return is always 0"
    "tools/release/gates/check_postinst_safety.sh:return:log_violation:trivial echo-only logger; implicit return is always 0"
    # ── tools/release/gates/gate3_local_package_smoke.sh ──
    # die() calls exit 1 — never actually returns; implicit return is unreachable
    "tools/release/gates/gate3_local_package_smoke.sh:return:die:function calls exit 1; return is unreachable"
    # ── tools/release/gates/gate4_local_k8s_smoke.sh ──
    # die() calls exit 1 — never actually returns; implicit return is unreachable
    "tools/release/gates/gate4_local_k8s_smoke.sh:return:die:function calls exit 1; return is unreachable"
    # ── tools/harness/detect_ci_supply_chain.sh ──
    # awk-based scanner; awk exit code is the meaningful return
    "tools/harness/detect_ci_supply_chain.sh:return:check_network_to_shell:awk-based scanner; awk exit code is the meaningful return"
)

# Files where specific violations are known and accepted.
# Format: "relative/path"
# (Legacy array kept for backward compat with scanning loop)
readonly RETURN_EXEMPT_FILES=(
)

echo "=== Shell Hygiene Detection (S7682 / S7688 / S131 / S1066 / Rule 18) ===" >&2
echo "Scanning: ${SCAN_DIR}" >&2
echo "" >&2

# ── Collector for warning entries (used for allowlist filtering) ──
# Each entry: "filepath:pattern_id:detail"
WARNING_ENTRIES=()
WARNING_ENTRY_COUNT=0

# ── Pattern (a): Functions without explicit return ──
#
# Strategy: for each function (identified by name() {), find the
# closing brace and check whether any 'return' statement exists
# in the function body.  This is a heuristic — nested braces in
# subshells or conditionals may confuse the simple brace-counting.
# For accuracy we count brace depth from the opening brace.
echo "--- Pattern (a): Functions without explicit return statement ---" >&2

return_hits=0
while IFS= read -r script_file; do
    # Skip exempt files
    skip=0
    for exempt in ${RETURN_EXEMPT_FILES[@]+"${RETURN_EXEMPT_FILES[@]}"}; do
        if [[ "$script_file" == *"$exempt"* ]]; then
            skip=1
            break
        fi
    done
    if [[ "$skip" -eq 1 ]]; then
        continue
    fi

    # Use awk to find function definitions and check for return statements
    while IFS=: read -r func_name func_line has_return; do
        if [[ -z "$func_name" ]]; then
            continue
        fi
        if [[ "$has_return" == "0" ]]; then
            echo "  WARNING ${script_file}:${func_line} — function '${func_name}' has no explicit return statement" >&2
            warnings=$((warnings + 1))
            return_hits=$((return_hits + 1))
            WARNING_ENTRIES+=("${script_file}:return:${func_name}")
            WARNING_ENTRY_COUNT=$((WARNING_ENTRY_COUNT + 1))
        fi
    done < <(awk '
        /^[a-zA-Z_][a-zA-Z0-9_]*\(\)[[:space:]]*\{/ {
            func_name = $1
            sub(/\(\).*/, "", func_name)
            func_line = NR
            depth = 0
            has_return = 0
            # Count braces on the function definition line
            for (i = 1; i <= length($0); i++) {
                c = substr($0, i, 1)
                if (c == "{") depth++
                if (c == "}") depth--
            }
            if (depth == 0) {
                # Single-line function: check for return on this line
                if ($0 ~ /[[:space:]]return[[:space:];]/ || $0 ~ /[[:space:]]return[[:space:]]*[0-9]/) {
                    has_return = 1
                }
                print func_name ":" func_line ":" has_return
                next
            }
            # Multi-line function: scan until closing brace
            while (depth > 0) {
                if ((getline) <= 0) break
                if ($0 ~ /[[:space:]]return[[:space:];]/ || $0 ~ /[[:space:]]return[[:space:]]*[0-9]/) {
                    has_return = 1
                }
                for (i = 1; i <= length($0); i++) {
                    c = substr($0, i, 1)
                    if (c == "{") depth++
                    if (c == "}") depth--
                }
            }
            print func_name ":" func_line ":" has_return
        }
    ' "$script_file" 2>/dev/null || true)
done < <(find "$SCAN_DIR" -name '*.sh' -type f 2>/dev/null | sort)

if [[ "$return_hits" -eq 0 ]]; then
    echo "$MSG_NONE_FOUND" >&2
fi
echo "" >&2

# ── Pattern (b): Diagnostic messages on stdout instead of stderr ──
#
# Look for echo/printf lines containing INFO/WARN/WARNING/DEBUG/ERROR/
# SUGGEST markers that lack >&2 redirection.
echo "--- Pattern (b): Diagnostic messages missing stderr redirection ---" >&2

stderr_hits=0
while IFS= read -r match; do
    if [[ -z "$match" ]]; then
        continue
    fi
    file="$(echo "$match" | cut -d: -f1)"
    line="$(echo "$match" | cut -d: -f2)"
    content="$(echo "$match" | cut -d: -f3-)"
    # Skip comment lines
    if echo "$content" | grep -qE '^\s*#'; then
        continue
    fi
    # Skip grep/test assertions that match the pattern but are not output
    if echo "$content" | grep -qE 'grep.*\[ERROR\]|grep.*\[WARN\]|grep.*\[SUGGEST\]|grep.*\[INFO\]|grep.*\[DEBUG\]'; then
        continue
    fi
    # Skip grep pattern definitions (not actual output)
    if echo "$content" | grep -qE "grep.*-qE|grep.*-qF"; then
        continue
    fi
    # Skip lines that already redirect to stderr
    if echo "$content" | grep -qE '>&2|>>&2'; then
        continue
    fi
    # Skip lines inside here-docs (<<EOF ... EOF blocks)
    # Heuristic: if the line starts with significant whitespace and
    # contains a marker, it may be inside a here-doc.  Skip if
    # the preceding line contains << or <<-.
    prev_line=$((line - 1))
    if [[ "$prev_line" -gt 0 ]] && \
       sed -n "${prev_line}p" "$file" 2>/dev/null | \
       grep -qE '<<-?[[:space:]]*(EOF|HEREDOC)'; then
        continue
    fi
    echo "  WARNING ${file}:${line} — diagnostic message on stdout, add >&2: ${content}" >&2
    warnings=$((warnings + 1))
    stderr_hits=$((stderr_hits + 1))
    WARNING_ENTRIES+=("${file}:stderr:line${line}")
    WARNING_ENTRY_COUNT=$((WARNING_ENTRY_COUNT + 1))
done < <(grep -rnE '(echo|printf)[[:space:]].*\b(INFO|WARN|WARNING|DEBUG|ERROR|SUGGEST)\b' "$SCAN_DIR" --include='*.sh' 2>/dev/null | grep -vE '>&2' || true)

if [[ "$stderr_hits" -eq 0 ]]; then
    echo "$MSG_NONE_FOUND" >&2
fi
echo "" >&2

# ── Pattern (c): Single-bracket [ ] instead of [[ ]] (S7688) ──
#
# In bash scripts, [[ ]] is safer (no word splitting, no pathname
# expansion) and more feature-rich.  Per AGENTS.md Rule 18, all
# conditional tests in bash scripts must use [[ ]].
echo "--- Pattern (c): Single-bracket [ ] instead of [[ ]] (S7688) ---" >&2

bracket_hits=0
while IFS= read -r match; do
    if [[ -z "$match" ]]; then
        continue
    fi
    file="$(echo "$match" | cut -d: -f1)"
    line="$(echo "$match" | cut -d: -f2)"
    content="$(echo "$match" | cut -d: -f3-)"
    # Skip comment lines
    if echo "$content" | grep -qE '^\s*#'; then
        continue
    fi
    # Skip lines inside echo/printf (reporting the pattern itself)
    if echo "$content" | grep -qE '^\s*(echo|printf)'; then
        continue
    fi
    # Skip test assertions that grep for bracket patterns
    if echo "$content" | grep -qE 'grep|awk|sed'; then
        continue
    fi
    # Skip lines inside heredocs (embedded POSIX sh scripts use [ ] legitimately)
    # Check if this line falls between a <<'EOF' and EOF marker
    local_line="$line"
    in_heredoc=0
    heredoc_start=$(awk -v target="$local_line" '
        /<<-?[[:space:]]*'\''?EOF'\''?/ || /<<-?[[:space:]]*'\''?SCRIPT'\''?/ {
            hd_start = NR
        }
        /^EOF$/ || /^SCRIPT$/ {
            if (hd_start > 0 && NR >= target) {
                if (target > hd_start && target < NR) {
                    print "yes"
                    exit
                }
            }
            hd_start = 0
        }
    ' "$file" 2>/dev/null)
    if [[ "$heredoc_start" == "yes" ]]; then
        continue
    fi
    echo "  ERROR   ${file}:${line} — use '[[ ]]' instead of '[ ]': ${content}" >&2
    errors=$((errors + 1))
    bracket_hits=$((bracket_hits + 1))
done < <(grep -rnE '(^|[[:space:];])(if|while|until|elif)[[:space:]]+\[[[:space:]][^[]' "$SCAN_DIR" --include='*.sh' 2>/dev/null || true)

if [[ "$bracket_hits" -eq 0 ]]; then
    echo "$MSG_NONE_FOUND" >&2
fi
echo "" >&2

# ── Pattern (d): case statements without default *) clause (S131) ──
#
# Every case statement must include a default *) clause, even if it
# only contains a comment or no-op.  Per AGENTS.md Rule 18.
echo "--- Pattern (d): case statements without default *) clause (S131) ---" >&2

case_hits=0
while IFS= read -r script_file; do
    # Use awk to find case/esac blocks and check for *) default
    while IFS=: read -r case_line has_default; do
        if [[ -z "$case_line" ]]; then
            continue
        fi
        if [[ "$has_default" == "0" ]]; then
            echo "  ERROR   ${script_file}:${case_line} — case statement missing default *) clause" >&2
            errors=$((errors + 1))
            case_hits=$((case_hits + 1))
        fi
    done < <(awk '
        /^[[:space:]]*case[[:space:]]/ {
            case_line = NR
            has_default = 0
            depth = 1
            while (depth > 0) {
                if ((getline) <= 0) break
                if ($0 ~ /^[[:space:]]*case[[:space:]]/) depth++
                if ($0 ~ /^[[:space:]]*esac/) {
                    depth--
                    if (depth == 0) break
                }
                if (depth == 1 && $0 ~ /^[[:space:]]*\*\)/) has_default = 1
            }
            print case_line ":" has_default
        }
    ' "$script_file" 2>/dev/null || true)
done < <(find "$SCAN_DIR" -name '*.sh' -type f 2>/dev/null | sort)

if [[ "$case_hits" -eq 0 ]]; then
    echo "$MSG_NONE_FOUND" >&2
fi
echo "" >&2

# ── Pattern (e): curl -X HEAD instead of --head / -I ──
#
# -X HEAD sends a method override; --head (-I) is the standard way
# to perform an HTTP HEAD request.  Per AGENTS.md Rule 18 and the
# fix in commit e0f3948, prefer --head.
echo "--- Pattern (e): curl -X HEAD instead of --head / -I ---" >&2

curl_head_hits=0
while IFS= read -r match; do
    if [[ -z "$match" ]]; then
        continue
    fi
    file="$(echo "$match" | cut -d: -f1)"
    line="$(echo "$match" | cut -d: -f2)"
    content="$(echo "$match" | cut -d: -f3-)"
    # Skip comment lines (lines starting with # or echo of description strings)
    if echo "$content" | grep -qE '^\s*#'; then
        continue
    fi
    # Skip echo statements that are reporting the pattern itself (self-reference)
    if echo "$content" | grep -qE 'echo.*Pattern.*curl'; then
        continue
    fi
    # Skip error/warning emission lines that mention the pattern
    if echo "$content" | grep -qE "echo.*ERROR.*curl --head|echo.*WARNING.*curl --head"; then
        continue
    fi
    echo "  ERROR   ${file}:${line} — use 'curl --head' or 'curl -I' instead of 'curl -X HEAD': ${content}" >&2
    errors=$((errors + 1))
    curl_head_hits=$((curl_head_hits + 1))
done < <(grep -rnE 'curl[[:space:]].*-X[[:space:]]+HEAD' "$SCAN_DIR" --include='*.sh' 2>/dev/null || true)

if [[ "$curl_head_hits" -eq 0 ]]; then
    echo "$MSG_NONE_FOUND" >&2
fi
echo "" >&2

# ── Allowlist filtering ──
#
# Filter collected warning entries against the allowlist. A warning is
# exempt if its filepath, pattern_id, and detail all match an allowlist
# entry (substring match on filepath, exact match on pattern_id,
# substring match on detail/function name).

non_exempt_warnings=0

for entry in ${WARNING_ENTRIES[@]+"${WARNING_ENTRIES[@]}"}; do
    # Parse entry: "filepath:pattern_id:detail"
    entry_file="${entry%%:*}"
    entry_rest="${entry#*:}"
    entry_pattern="${entry_rest%%:*}"
    entry_detail="${entry_rest#*:}"

    exempt=0
    for allow in ${WARNING_ALLOWLIST[@]+"${WARNING_ALLOWLIST[@]}"}; do
        # Parse allowlist: "filepath:pattern_id:func_or_detail:justification"
        allow_file="${allow%%:*}"
        allow_rest="${allow#*:}"
        allow_pattern="${allow_rest%%:*}"
        allow_rest2="${allow_rest#*:}"
        allow_detail="${allow_rest2%%:*}"

        # Match: filepath substring, pattern_id exact, detail substring
        if [[ "$entry_file" == *"$allow_file"* ]] \
            && [[ "$entry_pattern" == "$allow_pattern" ]] \
            && [[ "$entry_detail" == *"$allow_detail"* ]]; then
            exempt=1
            break
        fi
    done
    if [[ "$exempt" -eq 0 ]]; then
        non_exempt_warnings=$((non_exempt_warnings + 1))
    fi
done

# ── Summary ──
echo "=== Summary ===" >&2
echo "  Errors:   ${errors}" >&2
echo "  Warnings: ${warnings} (${non_exempt_warnings} non-allowlisted)" >&2
echo "" >&2

if [[ "$errors" -gt 0 ]]; then
    echo "FAIL: ${errors} error(s) found — fix before merge" >&2
    exit 1
fi

if [[ "$non_exempt_warnings" -gt 0 ]]; then
    echo "FAIL: ${non_exempt_warnings} non-allowlisted warning(s) — fix or add to allowlist with justification" >&2
    exit 1
fi

if [[ "$warnings" -gt 0 ]]; then
    echo "PASS: ${warnings} warning(s) all allowlisted" >&2
    exit 0
fi

echo "PASS: no shell hygiene findings" >&2
exit 0
