#!/usr/bin/env bash
# detect_ngx_log_arg_count.sh — Audit ngx_log_debugN / ngx_log_errorN argument counts
#
# Purpose: Rule 8 enforcement.  The NGINX logging macros ngx_log_debug0
#          through ngx_log_debug8 and ngx_log_error0 through ngx_log_error8
#          encode the variadic argument count in the suffix digit.  A mismatch
#          between the suffix digit and the actual number of format-string
#          arguments causes undefined behaviour (extra args read garbage,
#          missing args read past the va_list).
#
# Arguments: None (scans components/nginx-module/src/ relative to repo root)
#
# Output: Findings to stderr; exit 0 if clean, exit 1 if violations found.
#
# Exit behaviour:
#   0 — all ngx_log_debugN / ngx_log_errorN calls have matching arg counts
#   1 — one or more calls have a suffix-digit / argument-count mismatch

set -euo pipefail

SRC_DIR="components/nginx-module/src"

if [[ ! -d "$SRC_DIR" ]]; then
    echo "  [ngx-log-args] Source directory not found: $SRC_DIR" >&2
    exit 0
fi

VIOLATIONS=0
CHECKED=0

# ── Scanning ──
#
# Strategy: for each line containing ngx_log_debugN or ngx_log_errorN,
# extract the suffix digit and count the format-string arguments in the
# format string.  The format string is the 3rd argument (after the macro
# name, the log level, and the log context).  We count printf-style
# format specifiers (%s, %d, %u, %V, %i, %z, %x, %o, %D, %U, %T, etc.)
# in the format string and compare to the suffix digit.
#
# Multi-line calls are handled by joining continuation lines (backslash-newline)
# before scanning.

# Join continuation lines and process each call
while IFS= read -r line; do
    # Extract the macro name and suffix digit
    if [[ "$line" =~ ngx_log_(debug|error)([0-8]) ]]; then
        digit="${BASH_REMATCH[2]}"
        macro_kind="${BASH_REMATCH[1]}"
        CHECKED=$((CHECKED + 1))

        # Skip macro definitions (#define) and debug wrapper macros
        # — the format string is a macro parameter, not a literal
        if [[ "$line" =~ define.*ngx_log ]] || [[ "$line" =~ LOG_DEBUG[0-9] ]]; then
            continue
        fi

        # Extract the format string (the string literal argument(s))
        # The format string is typically the 4th argument:
        #   ngx_log_debugN(NGX_LOG_DEBUG_HTTP, r->connection->log, 0, "fmt", arg1, ...)
        # We need to count % specifiers in the format string portion.
        #
        # Strategy: find the first complete string literal after the 3rd comma.
        # If the format string spans multiple string literals (string concatenation),
        # count specifiers in all of them.

        # Extract everything after the macro name
        rest="${line#*ngx_log_}"
        # Remove the call prefix up to and including the 3rd comma
        # (level, log, flags/0, format...)
        # Count commas at paren depth 0 to find where format args start
        after_macro="${line#*ngx_log_${macro_kind}${digit}(}"

        # Split into arguments by counting parens; the 4th argument
        # (1-indexed) is the format string.  We capture only that
        # argument so later string literals do not contribute percent
        # signs to the format walk.
        fmt_part=""
        fmt_start=-1
        depth=0
        comma_count=0
        in_string=0
        i=0
        len=${#after_macro}

        # Walk character by character to find the format string
        while [[ $i -lt $len ]]; do
            ch="${after_macro:$i:1}"
            if [[ "$ch" == "\\" ]] && [[ "$in_string" -eq 1 ]]; then
                # Skip escaped character inside string
                i=$((i + 1))
            elif [[ "$ch" == '"' ]]; then
                if [[ "$in_string" -eq 0 ]]; then
                    in_string=1
                else
                    in_string=0
                fi
            elif [[ $in_string -eq 1 ]]; then
                :
            elif [[ "$ch" == "(" ]]; then
                depth=$((depth + 1))
            elif [[ "$ch" == ")" ]] && [[ $depth -eq 0 ]] && \
                [[ $fmt_start -ge 0 ]] && [[ $comma_count -ge 3 ]]; then
                fmt_part="${after_macro:$fmt_start:$((i - fmt_start))}"
                break
            elif [[ "$ch" == ")" ]]; then
                depth=$((depth - 1))
            elif [[ "$ch" == "," ]] && [[ $depth -eq 0 ]]; then
                comma_count=$((comma_count + 1))
                if [[ $comma_count -eq 3 ]]; then
                    fmt_start=$((i + 1))
                elif [[ $comma_count -gt 3 ]] && [[ $fmt_start -ge 0 ]]; then
                    # Stop at the 5th argument boundary so only the format
                    # argument itself is inspected.
                    fmt_part="${after_macro:$fmt_start:$((i - fmt_start))}"
                    break
                fi
            fi
            i=$((i + 1))
        done

        if [[ -z "$fmt_part" ]]; then
            # Could not parse — skip
            continue
        fi

        # If the format argument is not a string literal (e.g. a variable
        # or macro parameter like (fmt)), skip — we can only check literals
        if ! printf '%s' "$fmt_part" | grep -q '"'; then
            continue
        fi

        # Count format specifiers in string literals within fmt_part
        # We count printf-style % specifiers, skipping %% (escaped percent)
        # Extract all string literal content
        fmt_specifiers=0
        in_str=0
        j=0
        flen=${#fmt_part}
        while [[ $j -lt $flen ]]; do
            ch="${fmt_part:$j:1}"
            # Handle escaped characters inside strings (\" \\ etc.)
            if [[ "$ch" == "\\" ]] && [[ "$in_str" -eq 1 ]]; then
                j=$((j + 2))
                continue
            fi
            if [[ "$ch" == '"' ]]; then
                in_str=$((1 - in_str))
                j=$((j + 1))
                continue
            fi
            if [[ "$in_str" -eq 1 ]] && [[ "$ch" == "%" ]]; then
                next_ch="${fmt_part:$((j + 1)):1}"
                if [[ "$next_ch" == "%" ]]; then
                    j=$((j + 2))
                    continue
                fi
                # Count this specifier; skip past it
                fmt_specifiers=$((fmt_specifiers + 1))
                # Skip the specifier: % [flags] [width] [.prec] [length] conversion
                j=$((j + 1))
                # Count any * in the width or precision slots.
                if [[ $j -lt $flen ]] && [[ "${fmt_part:$j:1}" == "*" ]]; then
                    fmt_specifiers=$((fmt_specifiers + 1))
                    j=$((j + 1))
                fi
                # Skip flags, width, precision, length modifiers
                # NGINX-specific: M, V, u, z, T, etc. are conversion chars, not modifiers
                while [[ $j -lt $flen ]]; do
                    c="${fmt_part:$j:1}"
                    if [[ "$c" == "*" ]]; then
                        fmt_specifiers=$((fmt_specifiers + 1))
                        j=$((j + 1))
                        continue
                    fi
                    case "$c" in
                        '-'|'+'|' '|'#'|'0'|'.'|[0-9]|'l'|'h'|'j'|'z'|'t'|'L')
                            j=$((j + 1))
                            ;;
                        *)
                            break
                            ;;
                    esac
                done
                # Skip the conversion character
                j=$((j + 1))
                continue
            fi
            j=$((j + 1))
        done

        # Compare: suffix digit should equal the number of format specifiers
        if [[ "$fmt_specifiers" -ne "$digit" ]]; then
            # Extract file and line number
            file="${line%%:*}"
            lineno="${line#*:}"
            lineno="${lineno%%:*}"
            echo "  [ngx-log-args] VIOLATION: $file:$lineno — ngx_log_${macro_kind}${digit} has $fmt_specifiers format specifier(s) but suffix digit is $digit" >&2
            VIOLATIONS=$((VIOLATIONS + 1))
        fi
    fi
done < <(
    # Join continuation lines and prefix with file:line.
    # Two cases: explicit backslash continuation, and implicit multi-line
    # function calls (open paren without close on same line).
    find "$SRC_DIR" \( -name '*.c' -o -name '*.h' \) -print0 2>/dev/null |
    while IFS= read -r -d '' f; do
        awk '
            /\\$/ {
                if (cont == "") { line_no = NR }
                cont = cont substr($0, 1, length($0) - 1)
                next
            }
            {
                line = $0
                if (cont != "") {
                    full = cont line
                    cont = ""
                } else {
                    full = line
                    line_no = NR
                }
                # Check for unterminated paren balance (multi-line call)
                n = gsub(/\(/, "(", full)
                m = gsub(/\)/, ")", full)
                if (n > m && full ~ /ngx_log_/) {
                    cont = full
                    next
                }
                print FILENAME ":" line_no ":" full
            }
            END {
                if (cont != "") {
                    print FILENAME ":" line_no ":" cont
                }
            }
        ' "$f"
    done |
    grep -E 'ngx_log_(debug|error)[0-8]' || true
)

# ── Verdict ──

if [[ "$VIOLATIONS" -gt 0 ]]; then
    echo "  [ngx-log-args] FAIL: $VIOLATIONS mismatch(es) found in $CHECKED call(s) checked" >&2
    exit 1
fi

echo "  [ngx-log-args] PASS: $CHECKED ngx_log_debugN/errorN call(s) checked, all arg counts match" >&2
exit 0
