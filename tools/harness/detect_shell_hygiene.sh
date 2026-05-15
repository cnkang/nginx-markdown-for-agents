#!/bin/bash
#
# Shell Hygiene Detection Script (SonarCloud S7682, S1066, Rule 18)
#
# Scans shell scripts for three common hygiene violations that repeatedly
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
#   (c) curl -X HEAD instead of curl --head (Rule 18 / e0f3948)
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
#   1 — one or more findings requiring review
#

set -euo pipefail

readonly SCAN_DIR="${1:-tools}"
readonly SCRIPT_NAME="$(basename "$0")"

errors=0
warnings=0

# Files where specific violations are known and accepted.
# Format: "relative/path:check_id:line_or_pattern:description"
#
# (a) return-statement exemptions: one-liner functions where the
#     implicit return of the sole command is intentional and documented.
# (b) stdout-echo exemptions: progress output that is intentionally
#     on stdout (e.g. --json output mode).
# (c) -X HEAD exemptions: none expected; --head is always preferred.
readonly RETURN_EXEMPT_FILES=(
)

# Some scripts use set -e which makes implicit return safe in
# trivial functions.  Still flag them as warnings per AGENTS.md
# Rule 18 strict requirement.

echo "=== Shell Hygiene Detection (S7682 / S1066 / Rule 18) ===" >&2
echo "Scanning: ${SCAN_DIR}" >&2
echo "" >&2

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
    for exempt in "${RETURN_EXEMPT_FILES[@]}"; do
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
    echo "  (none found)" >&2
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
done < <(grep -rnE '(echo|printf)[[:space:]].*\b(INFO|WARN|WARNING|DEBUG|ERROR|SUGGEST)\b' "$SCAN_DIR" --include='*.sh' 2>/dev/null | grep -vE '>&2' || true)

if [[ "$stderr_hits" -eq 0 ]]; then
    echo "  (none found)" >&2
fi
echo "" >&2

# ── Pattern (c): curl -X HEAD instead of --head / -I ──
#
# -X HEAD sends a method override; --head (-I) is the standard way
# to perform an HTTP HEAD request.  Per AGENTS.md Rule 18 and the
# fix in commit e0f3948, prefer --head.
echo "--- Pattern (c): curl -X HEAD instead of --head / -I ---" >&2

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
    echo "  (none found)" >&2
fi
echo "" >&2

# ── Summary ──
echo "=== Summary ===" >&2
echo "  Errors:   ${errors}" >&2
echo "  Warnings: ${warnings}" >&2
echo "" >&2

if [[ "$errors" -gt 0 ]]; then
    echo "FAIL: ${errors} error(s) found — fix before merge" >&2
    exit 1
fi

if [[ "$warnings" -gt 0 ]]; then
    echo "PASS with warnings: ${warnings} warning(s) — review recommended" >&2
    exit 0
fi

echo "PASS: no shell hygiene findings" >&2
exit 0
