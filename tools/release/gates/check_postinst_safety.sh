#!/bin/bash
# ---------------------------------------------------------------------------
# check_postinst_safety.sh — Static analysis of maintainer scripts for safety
#
# PURPOSE:
#   Verifies that all package maintainer scripts do not contain forbidden
#   operations that could modify NGINX state during package installation,
#   and that each script establishes a trusted PATH before resolving any
#   external command.
#
# USAGE:
#   check_postinst_safety.sh [<file> ...]
#   check_postinst_safety.sh --help
#
# OPTIONS:
#   -h, --help    Show this help message
#
# ARGUMENTS:
#   If no files are provided, defaults to checking:
#     - packaging/nfpm/scripts/preinstall.sh
#     - packaging/nfpm/scripts/postinstall.sh
#     - packaging/nfpm/scripts/preremove.sh
#     - packaging/rpm/SPECS/nginx-module-markdown.spec (%post section)
#
# EXIT CODES:
#   0  No forbidden patterns or trusted-PATH violations found
#   1  One or more violations detected
#   2  Usage error (bad option or file not found)
#
# CHECKS PERFORMED:
#   1. Forbidden patterns (existing):
#     - nginx -s reload / nginx -s restart
#     - systemctl restart nginx / systemctl reload nginx
#     - service nginx restart / service nginx reload
#     - Writing to /etc/nginx/ (cp, mv, tee, > redirects)
#     - Modifying nginx.conf (sed -i, echo to nginx.conf)
#     - Enabling snippets (ln -s to modules-enabled or conf.d)
#   2. Trusted-PATH invariant (structural):
#     - A literal trusted PATH assignment must exist in the top-level prologue
#     - It must precede any external command resolution from a known list
#
# NOTES:
#   - macOS bash 3.2 compatible (no bash 4+ features)
#   - Diagnostic messages go to stderr
#   - Machine-readable results to stdout
#
# SEE ALSO:
#   - .kiro/specs/archive/31-0.7.0-release-package-compatibility/requirements.md §7
#   - .kiro/specs/archive/31-0.7.0-release-package-compatibility/design.md §Components 3
# ---------------------------------------------------------------------------

set -e

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
SCRIPT_NAME="$(basename "$0")"
VIOLATION_COUNT=0
FILE_COUNT=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

usage() {
    printf 'Usage: %s [<file> ...]\n' "$SCRIPT_NAME" >&2
    printf '       %s --help\n' "$SCRIPT_NAME" >&2
    printf '\n' >&2
    printf 'Static analysis of maintainer scripts for forbidden operations\n' >&2
    printf 'and trusted-PATH invariant violations.\n' >&2
    printf '\n' >&2
    printf 'If no files are provided, defaults to checking:\n' >&2
    printf '  packaging/nfpm/scripts/preinstall.sh\n' >&2
    printf '  packaging/nfpm/scripts/postinstall.sh\n' >&2
    printf '  packaging/nfpm/scripts/preremove.sh\n' >&2
    printf '  packaging/rpm/SPECS/nginx-module-markdown.spec (%%post section)\n' >&2
    printf '\n' >&2
    printf 'Options:\n' >&2
    printf '  -h, --help    Show this help message\n' >&2
    return 0
}

log_info() {
    local msg="$1"
    printf '[INFO]  %s\n' "$msg" >&2
    return 0
}

log_warn() {
    local msg="$1"
    printf '[WARN]  %s\n' "$msg" >&2
    return 0
}

log_error() {
    local msg="$1"
    printf '[ERROR] %s\n' "$msg" >&2
    return 0
}

log_violation() {
    local file="$1"
    local line="$2"
    local desc="$3"
    printf '[VIOLATION] %s:%s: %s\n' "$file" "$line" "$desc" >&2
    return 0
}

# ---------------------------------------------------------------------------
# Pattern checking
# ---------------------------------------------------------------------------

# strip_heredocs — remove heredoc content from a shell script
# Heredoc bodies contain user-facing text (instructions) that may mention
# commands like "systemctl reload nginx" without actually executing them.
# Arguments: $1 = file path
# Outputs: file content with heredoc bodies replaced by blank lines
#          (preserving line numbers for accurate violation reporting)
strip_heredocs() {
    local file="$1"
    local in_heredoc=0
    local heredoc_delim=""
    local line=""

    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$in_heredoc" -eq 1 ]]; then
            # Check if this line ends the heredoc
            local trimmed
            trimmed="$(printf '%s' "$line" | sed 's/^[[:space:]]*//')"
            if [[ "$trimmed" == "$heredoc_delim" ]]; then
                in_heredoc=0
                heredoc_delim=""
            fi
            # Output blank line to preserve line numbering
            printf '\n'
        else
            # Detect heredoc start: <<'DELIM', <<"DELIM", <<DELIM, <<-'DELIM' etc.
            local delim_match
            delim_match="$(printf '%s' "$line" | sed -n "s/.*<<-*[[:space:]]*['\"]\\{0,1\\}\([A-Za-z_][A-Za-z_0-9]*\)['\"]\\{0,1\\}.*/\1/p")"
            if [[ -n "$delim_match" ]]; then
                in_heredoc=1
                heredoc_delim="$delim_match"
                # Output the heredoc start line itself (it's a command, not content)
                printf '%s\n' "$line"
            else
                printf '%s\n' "$line"
            fi
        fi
    done < "$file"

    return 0
}

# check_pattern — grep stripped content for a forbidden pattern
# Arguments:
#   $1 = file path (original, for reporting)
#   $2 = grep pattern (extended regex)
#   $3 = human-readable description of the violation
#   $4 = stripped content temp file
# Returns: number of matches found (added to VIOLATION_COUNT)
check_pattern() {
    local file="$1"
    local pattern="$2"
    local description="$3"
    local stripped_file="$4"
    local matches=""
    local line_num=""

    # Use grep -nE on stripped content; suppress exit code since no-match is expected
    matches="$(grep -nE "$pattern" "$stripped_file" 2>/dev/null)" || true

    if [[ -n "$matches" ]]; then
        # Report each matching line
        printf '%s\n' "$matches" | while IFS= read -r match_line; do
            line_num="$(printf '%s\n' "$match_line" | cut -d: -f1)"
            log_violation "$file" "$line_num" "$description"
            printf 'VIOLATION %s:%s %s\n' "$file" "$line_num" "$description"
        done
        # Count violations (number of matching lines)
        local count
        count="$(printf '%s\n' "$matches" | wc -l | tr -d ' ')"
        VIOLATION_COUNT=$((VIOLATION_COUNT + count))
    fi

    return 0
}

# is_function_definition — identify a shell function declaration.
# Arguments: $1 = trimmed source line
# Returns: 0 when the line starts a function definition, 1 otherwise
is_function_definition() {
    local line="$1"

    if [[ "$line" =~ ^[a-zA-Z_][a-zA-Z_0-9]*[[:space:]]*\(\)[[:space:]]*(\{.*)?$ ]]; then
        return 0
    fi
    if [[ "$line" =~ ^function[[:space:]]+[a-zA-Z_][a-zA-Z_0-9]*[[:space:]]*(\(\)[[:space:]]*)?(\{.*)?$ ]]; then
        return 0
    fi
    return 1
}

# function_brace_delta — count braces on a function source line.
# This keeps function-body skipping correct for one-line definitions and
# nested brace blocks while preserving the checker's Bash 3.2 portability.
# Arguments: $1 = source line
# Outputs: the net opening-brace delta
function_brace_delta() {
    local line="$1"
    local braces
    local opening
    local closing

    # Strip quoted spans and comments before counting. A brace inside a string
    # literal or a comment is text, not block structure, and counting it would
    # close the surrounding function body early.
    braces="$(printf '%s\n' "$line" \
        | sed -e "s/'[^']*'//g" \
              -e 's/"[^"]*"//g' \
              -e 's/\(^\|[[:space:]]\)#.*$//' \
        | sed 's/[^{}]//g')"
    opening="${braces//\}/}"
    closing="${braces//\{}"
    printf '%s\n' "$(( ${#opening} - ${#closing} ))"
    return 0
}

# skip_function_line — update function-body state for the trusted-PATH
# passes. Function definitions are declarations; commands in their bodies do
# not resolve or execute until a later top-level call.
# Arguments: $1 = source line; $2 = trimmed source line
# Uses/updates globals FUNCTION_DEPTH and FUNCTION_PENDING.
# Returns: 0 when the current line belongs to a function definition/body
skip_function_line() {
    local line="$1"
    local trimmed="$2"
    local delta=0

    if [[ "$FUNCTION_DEPTH" -gt 0 ]]; then
        delta="$(function_brace_delta "$line")"
        FUNCTION_DEPTH=$((FUNCTION_DEPTH + delta))
        return 0
    fi

    if [[ "$FUNCTION_PENDING" -eq 1 ]]; then
        if [[ "$trimmed" == *"{"* ]]; then
            FUNCTION_DEPTH=0
            delta="$(function_brace_delta "$line")"
            FUNCTION_DEPTH=$((FUNCTION_DEPTH + delta))
            FUNCTION_PENDING=0
        fi
        return 0
    fi

    if is_function_definition "$trimmed"; then
        if [[ "$trimmed" == *"{"* ]]; then
            FUNCTION_DEPTH=0
            delta="$(function_brace_delta "$line")"
            FUNCTION_DEPTH=$((FUNCTION_DEPTH + delta))
        else
            FUNCTION_PENDING=1
        fi
        return 0
    fi

    return 1
}


# mask_command_text — blot out quoted spans and comments for command matching
# Arguments: $1 = line
# Returns: the masked line on stdout
#
# Single-quoted spans never execute, so their content is always masked.
# Double-quoted spans are masked too — EXCEPT when they contain a command
# substitution ($(...)) or a backtick, because those run their command
# before the quoting context matters; hiding them would let the later
# command scan miss an external command that resolves through PATH.
mask_command_text() {
    local text="$1"
    local out=""
    local i=0
    local n=${#text}
    local ch
    local quote=""
    local span=""
    local fused
    local prev_last
    local next_ch
    local sub_depth=0
    local evaluator
    local commandword
    # Command-position separator tail: start of text or a separator followed
    # by whitespace only.  Kept in a variable so the regex never collides
    # with shell tokenization or static analysis.
    local sep_re='(^|[;&|(])[[:space:]]*$'

    while [[ "$i" -lt "$n" ]]; do
        ch="${text:$i:1}"
        if [[ -z "$quote" ]]; then
            if [[ "$ch" == "'" || "$ch" == '"' ]]; then
                quote="$ch"
                span=""
                sub_depth=0
                i=$((i + 1))
                continue
            fi
            out+="$ch"
            i=$((i + 1))
            continue
        fi
        if [[ "$quote" == '"' ]]; then
            # A backslash escapes the next character: an escaped quote is
            # literal text and cannot terminate the span (nor open a
            # substitution).
            if [[ "$ch" == '\' && -n "${text:$((i + 1)):1}" ]]; then
                span+="$ch${text:$((i + 1)):1}"
                i=$((i + 2))
                continue
            fi
            # Track command-substitution nesting inside the double-quoted
            # span: a quote inside $(...) belongs to the substitution's own
            # context and does not terminate the surrounding span.
            if [[ "$ch" == '$' && "${text:$((i + 1)):1}" == '(' ]]; then
                sub_depth=$((sub_depth + 1))
            elif [[ "$ch" == ')' && "$sub_depth" -gt 0 ]]; then
                sub_depth=$((sub_depth - 1))
            fi
            if [[ "$ch" == '"' && "$sub_depth" -eq 0 ]]; then
                if [[ "$out" =~ $sep_re ]] \
                    || [[ "$out" =~ (^|[^A-Za-z0-9_])(sudo|doas|env|command|nohup|time|exec)[[:space:]]+(-{1,2}[A-Za-z0-9_-]+[[:space:]]+)*$ ]]; then
                    # A double-quoted command word (`"sed"`) is executable
                    # text at a command position: keep it visible.
                    out+="$span"
                elif [[ "$out" =~ (^|[^A-Za-z0-9_])(eval|sh|bash|dash|env)[[:space:]]+(-{1,2}[A-Za-z0-9_-]+[[:space:]]+)*$ ]]; then
                    # An evaluator command executes its double-quoted
                    # argument as a command string: keep the span and its
                    # double-quote delimiters visible.
                    out+="\"$span\""
                elif [[ "$span" == *'$('* || "$span" == *'`'* ]]; then
                    # Keep a double-quoted span whose substitutions run.
                    out+='"'"$span"'"'
                else
                    # A span fused to adjacent word characters
                    # (`NOTE="run "sed`) belongs to one shell word:
                    # replacing it with a quote would invent a command
                    # boundary the shell does not have.  A span that
                    # stands alone keeps the old placeholder behavior.
                    fused=0
                    prev_last=""
                    if [[ -n "$out" ]]; then
                        prev_last="${out:${#out}-1:1}"
                    fi
                    next_ch="${text:$((i + 1)):1}"
                    case "$prev_last" in
                        ""|" "|$'\t'|'"'|"'"|'`'|'$'|'('|';'|'|'|'&') ;;
                        *) fused=1 ;;
                    esac
                    case "$next_ch" in
                        ""|" "|$'\t'|'"'|"'"|'`'|'$'|'('|')'|';'|'|'|'&') ;;
                        *) fused=1 ;;
                    esac
                    if [[ "$fused" -eq 1 ]]; then
                        out+="x"
                    else
                        out+='"'
                    fi
                fi
                quote=""
                sub_depth=0
                i=$((i + 1))
                continue
            fi
            span+="$ch"
            i=$((i + 1))
            continue
        fi
        if [[ "$ch" == "$quote" ]]; then
            evaluator=0
            commandword=0
            if [[ "$out" =~ $sep_re ]] \
                || [[ "$out" =~ (^|[^A-Za-z0-9_])(sudo|doas|env|command|nohup|time|exec)[[:space:]]+(-{1,2}[A-Za-z0-9_-]+[[:space:]]+)*$ ]]; then
                # Shell quoting is also valid around a command word
                # (`'sed' -i ...`): the span names the command that runs,
                # so its text must stay visible without the quotes.
                commandword=1
            fi
            if [[ "$quote" == "'" ]] \
                && [[ "$out" =~ (^|[^A-Za-z0-9_])(eval|sh|bash|dash|env)[[:space:]]+(-{1,2}[A-Za-z0-9_-]+[[:space:]]+)*$ ]]; then
                # An evaluator command (eval/sh/bash/dash/env) executes its
                # single-quoted argument as a command string, so the span is
                # executable text and must stay visible with its quotes.
                evaluator=1
            fi
            fused=0
            prev_last=""
            if [[ -n "$out" ]]; then
                prev_last="${out:${#out}-1:1}"
            fi
            next_ch="${text:$((i + 1)):1}"
            case "$prev_last" in
                ""|" "|$'\t'|'"'|"'"|'`'|'$'|'('|';'|'|'|'&') ;;
                *) fused=1 ;;
            esac
            case "$next_ch" in
                ""|" "|$'\t'|'"'|"'"|'`'|'$'|'('|')'|';'|'|'|'&') ;;
                *) fused=1 ;;
            esac
            if [[ "$commandword" -eq 1 ]]; then
                out+="$span"
            elif [[ "$evaluator" -eq 1 ]]; then
                out+="'$span'"
            elif [[ "$fused" -eq 1 ]]; then
                out+="x"
            else
                out+="''"
            fi
            quote=""
            i=$((i + 1))
            continue
        fi
        span+="$ch"
        i=$((i + 1))
    done

    # An unterminated quote keeps its tail verbatim: masking it would hide
    # executable text.
    if [[ -n "$quote" ]]; then
        out+="$quote$span"
    fi

    out="$(printf '%s' "$out" | sed -E 's/(^|[[:space:];|&])#.*$/\1/')"
    printf '%s' "$out"
}


# check_file — run all forbidden pattern checks against a single file
# Arguments: $1 = file path
# Returns: 0 always (violations tracked in VIOLATION_COUNT), 2 on file error
check_file() {
    local file="$1"

    if [[ ! -f "$file" ]]; then
        log_error "File not found: $file"
        return 2
    fi

    log_info "Checking: $file"
    FILE_COUNT=$((FILE_COUNT + 1))

    # Strip heredoc bodies so instructional text is not flagged
    local stripped_tmp
    stripped_tmp="$(mktemp)"
    strip_heredocs "$file" > "$stripped_tmp"

    # --- nginx reload/restart commands ---
    check_pattern "$file" \
        'nginx[[:space:]]+-s[[:space:]]+(reload|restart)' \
        "Forbidden: nginx -s reload/restart" \
        "$stripped_tmp"

    # --- systemctl restart/reload nginx ---
    check_pattern "$file" \
        'systemctl[[:space:]]+(restart|reload)[[:space:]]+nginx' \
        "Forbidden: systemctl restart/reload nginx" \
        "$stripped_tmp"

    # --- service nginx restart/reload ---
    check_pattern "$file" \
        'service[[:space:]]+nginx[[:space:]]+(restart|reload)' \
        "Forbidden: service nginx restart/reload" \
        "$stripped_tmp"

    # --- Writing to /etc/nginx/ (cp, mv, tee, redirect) ---
    check_pattern "$file" \
        '(cp|mv|tee|install)[[:space:]]+.*(/etc/nginx/|/etc/nginx[[:space:]])' \
        "Forbidden: writing to /etc/nginx/" \
        "$stripped_tmp"

    check_pattern "$file" \
        '>[[:space:]]*/etc/nginx/' \
        "Forbidden: redirect to /etc/nginx/" \
        "$stripped_tmp"

    # --- Modifying nginx.conf ---
    check_pattern "$file" \
        'sed[[:space:]]+-i.*nginx\.conf' \
        "Forbidden: in-place edit of nginx.conf" \
        "$stripped_tmp"

    check_pattern "$file" \
        'echo[[:space:]]+.*>.*nginx\.conf' \
        "Forbidden: echo redirect to nginx.conf" \
        "$stripped_tmp"

    check_pattern "$file" \
        'printf[[:space:]]+.*>.*nginx\.conf' \
        "Forbidden: printf redirect to nginx.conf" \
        "$stripped_tmp"

    check_pattern "$file" \
        'tee[[:space:]]+.*nginx\.conf' \
        "Forbidden: tee to nginx.conf" \
        "$stripped_tmp"

    # --- Enabling snippets (ln -s to modules-enabled or conf.d) ---
    check_pattern "$file" \
        'ln[[:space:]]+-s.*modules-enabled' \
        "Forbidden: enabling snippet via symlink to modules-enabled" \
        "$stripped_tmp"

    check_pattern "$file" \
        'ln[[:space:]]+-s.*conf\.d' \
        "Forbidden: enabling snippet via symlink to conf.d" \
        "$stripped_tmp"

    check_pattern "$file" \
        'ln[[:space:]]+-s.*sites-enabled' \
        "Forbidden: enabling snippet via symlink to sites-enabled" \
        "$stripped_tmp"

    rm -f "$stripped_tmp"
    return 0
}

# prologue_line_is_assignment_only — true when a prologue line stores a value
# without running a command.
#
# An env-prefixed command ("LC_ALL=C sed -n ...") has the NAME=value shape but
# resolves its command word through PATH, so it must end the prologue scan
# exactly like any other statement.  A real assignment is NAME=value followed
# by nothing, or by one or more further NAME=value pairs.
#
# Arguments: $1 = source line (top-level, already trimmed)
# Outputs:   none
# Returns:   0 when the line is assignment-only, 1 otherwise
prologue_line_is_assignment_only() {
    local text="$1"
    local name_pattern='^[A-Za-z_][A-Za-z0-9_]*='
    local value="" rest="" quote="" index=0 char=""

    while [[ "$text" =~ $name_pattern ]]; do
        rest="${text#*=}"
        if [[ -z "$rest" ]]; then
            return 0
        fi
        quote="${rest:0:1}"
        if [[ "$quote" == '"' || "$quote" == "'" ]]; then
            index=1
            while [[ "$index" -lt "${#rest}" ]]; do
                char="${rest:$index:1}"
                if [[ "$char" == "$quote" ]]; then
                    break
                fi
                index=$((index + 1))
            done
            if [[ "$index" -ge "${#rest}" ]]; then
                # Unterminated quote: cannot prove this is assignment-only.
                return 1
            fi
            text="${rest:$((index + 1))}"
        else
            # Process substitution executes a command: ``OUTPUT=<(hostname)``
            # resolves hostname through PATH even though the line starts as
            # an assignment, so it ends the prologue like other command
            # execution.
            if [[ "$rest" == *'<('* || "$rest" == *'>('* ]]; then
                return 1
            fi
            # Unquoted value runs to the first whitespace.
            value="${rest%%[[:space:]]*}"
            text="${rest#"$value"}"
        fi
        # Only whitespace may remain before the next NAME=value pair (or the
        # end of the line).
        text="${text#"${text%%[![:space:]]*}"}"
        if [[ -z "$text" ]]; then
            return 0
        fi
    done

    return 1
}

# check_trusted_path — verify that a top-level trusted PATH assignment
# precedes any external command resolution in a maintainer script.
#
# This is a STRUCTURAL check: it validates that:
#   1. A trusted PATH= assignment exists in the top-level script prologue
#   2. No external command from a known list is resolved before that assignment
#
# Arguments: $1 = file path (original, for reporting)
# Returns: 0 always (violations tracked in VIOLATION_COUNT)
check_trusted_path() {
    local file="$1"

    # Known external commands that resolve from PATH in maintainer scripts
    local -a external_cmds=(
        "command -v"
        "cat"
        "readlink"
        "rm"
        "rmdir"
        "sed"
        "ln"
        "cp"
        "mv"
        "tee"
        "install"
        "nginx"
    )

    # Strip heredocs to avoid false positives from instructional text
    local stripped_tmp
    stripped_tmp="$(mktemp)"
    strip_heredocs "$file" > "$stripped_tmp"

    local first_path_line=0
    local extra_path_line=0
    local trusted_path_seen=0
    local first_cmd_line=0
    local line_num=0
    local line=""
    local trimmed=""
    local trusted_path_root_initialized=0
    FUNCTION_DEPTH=0
    FUNCTION_PENDING=0

    # Pass 1: find a literal trusted PATH assignment in the top-level
    # prologue.  Do not accept assignments inside functions or control-flow
    # blocks: they can leave later command resolution under caller PATH.
    line_num=0
    while IFS= read -r line || [[ -n "$line" ]]; do
        line_num=$((line_num + 1))

        trimmed="${line#"${line%%[![:space:]]*}"}"
        if [[ -z "$trimmed" || "$trimmed" == "#"* ]]; then
            continue
        fi

        if skip_function_line "$line" "$trimmed"; then
            continue
        fi

        # A top-level assignment is never indented.  Once the prologue has
        # entered a block or executed another statement, a later PATH= is not
        # unconditional for this check.
        if [[ "$line" != "$trimmed" ]]; then
            break
        fi

        case "$line" in
            'PATH=/usr/sbin:/usr/bin:/sbin:/bin'|\
            'PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH')
                if [[ "$trusted_path_seen" -eq 1 ]]; then
                    extra_path_line=$line_num
                    break
                fi
                first_path_line=$line_num
                trusted_path_seen=1
                ;;
            'PATH="${TRUSTED_PATH_ROOT}/usr/sbin:${TRUSTED_PATH_ROOT}/usr/bin:${TRUSTED_PATH_ROOT}/sbin:${TRUSTED_PATH_ROOT}/bin"')
                if [[ "$trusted_path_root_initialized" -eq 1 ]]; then
                    if [[ "$trusted_path_seen" -eq 1 ]]; then
                        extra_path_line=$line_num
                        break
                    fi
                    first_path_line=$line_num
                    trusted_path_seen=1
                fi
                ;;
            'TRUSTED_PATH_ROOT=""')
                trusted_path_root_initialized=1
                ;;
            PATH=*)
                if [[ "$trusted_path_seen" -eq 1 ]]; then
                    # The trusted assignment is established, so this later
                    # top-level assignment replaces it and leaves command
                    # resolution under a value the check never validated.
                    extra_path_line=$line_num
                    break
                fi
                # A non-literal or self-referencing PATH must not be used as
                # a precursor to a later trusted assignment.
                break
                ;;
            set[[:space:]]*)
                # Option-only set statements are safe in the prologue.  A
                # separator or command substitution would let additional
                # commands run before the trusted PATH assignment.
                if [[ "$line" == *';'* || "$line" == *'&'* || "$line" == *'|'* \
                    || "$line" == *'$('* || "$line" == *'`'* ]]; then
                    break
                fi
                ;;
            *)
                # A plain variable assignment (NAME=value) is safe in the
                # prologue and does not resolve commands through PATH.  Case
                # globs cannot express "name characters immediately followed
                # by =" without letting * span spaces, so anchor the check
                # with a regex; conditional statements fall through to break.
                # Command substitution, backticks, or command separators in
                # the line execute commands before the trusted PATH
                # assignment, so they end the prologue.
                #
                # The assignment must also be assignment-ONLY: an env-prefixed
                # command such as "LC_ALL=C sed ..." starts with a NAME=value
                # shape but runs a PATH-resolved command, so it ends the
                # prologue like any other statement.
                if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] \
                    && [[ "$line" != *'$('* && "$line" != *'`'* \
                    && "$line" != *';'* && "$line" != *'&'* \
                    && "$line" != *'|'* ]] \
                    && prologue_line_is_assignment_only "$line"; then
                    :
                else
                    break
                fi
                ;;
        esac
    done < "$stripped_tmp"

    # Pass 1b: a later top-level PATH assignment replaces the trusted value.
    # The prologue scan stops at the first statement, so this pass looks at
    # every later top-level line: any of them re-points command resolution
    # after the check has committed to the trusted value.
    if [[ "$first_path_line" -ne 0 ]]; then
        line_num=0
        while IFS= read -r line || [[ -n "$line" ]]; do
            line_num=$((line_num + 1))
            if [[ "$line_num" -le "$first_path_line" ]]; then
                continue
            fi
            trimmed="${line#"${line%%[![:space:]]*}"}"
            if [[ -z "$trimmed" || "$trimmed" == "#"* ]]; then
                continue
            fi
            # Track function scope before the indentation check: an
            # indented closing brace must still decrement the depth, or a
            # later top-level PATH line reads as still inside a function.
            if skip_function_line "$line" "$trimmed"; then
                continue
            fi
            if [[ "$line" != "$trimmed" ]]; then
                # An indented line that is not part of a function body
                # belongs to a block.
                continue
            fi
            case "$line" in
                PATH=*)
                    extra_path_line=$line_num
                    break
                    ;;
                *)
                    # Any other bare line is not the PATH export this pass looks
                    # for; keep scanning.
                    ;;
            esac
        done < "$stripped_tmp"
    fi

    # Pass 2: find first external command usage
    FUNCTION_DEPTH=0
    FUNCTION_PENDING=0
    line_num=0
    while IFS= read -r line || [[ -n "$line" ]]; do
        line_num=$((line_num + 1))

        # Skip comment lines
        local trimmed
        trimmed="${line#"${line%%[![:space:]]*}"}"
        if [[ "$trimmed" == "#"* ]]; then
            continue
        fi
        # Skip empty lines
        if [[ -z "$trimmed" ]]; then
            continue
        fi

        if skip_function_line "$line" "$trimmed"; then
            continue
        fi

        # Blot out quoted spans and trailing comments: prose inside a
        # string or a comment must not read as an executed command.
        local match_line
        match_line="$(mask_command_text "$line")"

        # Check each known external command
        local cmd=""
        for cmd in "${external_cmds[@]}"; do
            # Build a pattern that matches the command as a word boundary
            # For "command -v", match literally
            # For single-word commands, match as standalone token
            case "$cmd" in
                "command -v")
                    if [[ "$match_line" =~ (^|[[:space:]\"\'\(;|&])command[[:space:]]+-v($|[[:space:]]) ]]; then
                        first_cmd_line=$line_num
                        break 2
                    fi
                    ;;
                *)
                    # Match command at: start of line (with optional whitespace),
                    # after $( ), after ` `, after pipe, after semicolon, after &&/||
                    if [[ "$match_line" =~ (^|[[:space:]\"\'\`\$\(;|&])${cmd}($|[[:space:];|&\)\>]) ]]; then
                        first_cmd_line=$line_num
                        break 2
                    fi
                    ;;
            esac
        done
    done < "$stripped_tmp"

    rm -f "$stripped_tmp"

    # Decision logic
    if [[ "$first_path_line" -eq 0 ]]; then
        log_violation "$file" "0" "missing unconditional trusted PATH assignment"
        printf 'VIOLATION %s:0 missing unconditional trusted PATH assignment\n' "$file"
        VIOLATION_COUNT=$((VIOLATION_COUNT + 1))
    elif [[ "$extra_path_line" -ne 0 ]]; then
        log_violation "$file" "$extra_path_line" "a later top-level PATH assignment replaces the trusted PATH established at line $first_path_line"
        printf 'VIOLATION %s:%s a later top-level PATH assignment replaces the trusted PATH\n' "$file" "$extra_path_line"
        VIOLATION_COUNT=$((VIOLATION_COUNT + 1))
    elif [[ "$first_cmd_line" -ne 0 ]] && [[ "$first_cmd_line" -lt "$first_path_line" ]]; then
        log_violation "$file" "$first_cmd_line" "external command resolved before trusted PATH is established (PATH set at line $first_path_line)"
        printf 'VIOLATION %s:%s external command resolved before trusted PATH is established\n' "$file" "$first_cmd_line"
        VIOLATION_COUNT=$((VIOLATION_COUNT + 1))
    fi

    return 0
}

# extract_rpm_post — extract %post section from RPM spec for analysis
# Arguments: $1 = RPM spec file path
# Outputs: extracted %post content to a temp file, prints temp file path
extract_rpm_post() {
    local spec_file="$1"
    local tmp_file
    tmp_file="$(mktemp)"
    local in_post=0

    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$in_post" in
            0)
                # Look for %post (not %postun, %posttrans)
                case "$line" in
                    %post|%post\ *)
                        in_post=1
                        ;;
                    *)
                        ;;
                esac
                ;;
            1)
                # End of %post section: next section directive
                case "$line" in
                    %files*|%pre*|%post*|%install*|%build*|%changelog*|%clean*|%check*)
                        in_post=0
                        ;;
                    *)
                        printf '%s\n' "$line" >> "$tmp_file"
                        ;;
                esac
                ;;
            *)
                ;;
        esac
    done < "$spec_file"

    printf '%s' "$tmp_file"
    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    # Handle help flag
    local first_arg="${1:-}"
    case "$first_arg" in
        -h|--help)
            usage
            return 0
            ;;
        -*)
            log_error "Unknown option: $first_arg"
            usage
            return 2
            ;;
        *)
            ;;
    esac

    local had_error=0

    if [[ $# -eq 0 ]]; then
        # Default: check all known maintainer script locations
        log_info "No files specified; using defaults"

        # --- nFPM maintainer scripts ---
        if [[ -f "packaging/nfpm/scripts/preinstall.sh" ]]; then
            if check_file "packaging/nfpm/scripts/preinstall.sh"; then
                check_trusted_path "packaging/nfpm/scripts/preinstall.sh"
            else
                had_error=1
            fi
        else
            log_warn "Default file not found: packaging/nfpm/scripts/preinstall.sh"
        fi

        if [[ -f "packaging/nfpm/scripts/postinstall.sh" ]]; then
            if check_file "packaging/nfpm/scripts/postinstall.sh"; then
                check_trusted_path "packaging/nfpm/scripts/postinstall.sh"
            else
                had_error=1
            fi
        else
            log_warn "Default file not found: packaging/nfpm/scripts/postinstall.sh"
        fi

        if [[ -f "packaging/nfpm/scripts/preremove.sh" ]]; then
            if check_file "packaging/nfpm/scripts/preremove.sh"; then
                check_trusted_path "packaging/nfpm/scripts/preremove.sh"
            else
                had_error=1
            fi
        else
            log_warn "Default file not found: packaging/nfpm/scripts/preremove.sh"
        fi

        # --- RPM spec %post section ---
        if [[ -f "packaging/rpm/SPECS/nginx-module-markdown.spec" ]]; then
            # Extract %post section to a temp file for analysis
            local rpm_post_tmp
            rpm_post_tmp="$(extract_rpm_post "packaging/rpm/SPECS/nginx-module-markdown.spec")"
            if [[ -s "$rpm_post_tmp" ]]; then
                log_info "Extracted %%post section from RPM spec"
                if check_file "$rpm_post_tmp"; then
                    check_trusted_path "$rpm_post_tmp"
                else
                    had_error=1
                fi
                rm -f "$rpm_post_tmp"
            else
                log_info "No %%post section found in RPM spec (or section is empty)"
                rm -f "$rpm_post_tmp"
            fi
        else
            log_warn "Default file not found: packaging/rpm/SPECS/nginx-module-markdown.spec"
        fi
    else
        # Check each provided file
        for file in "$@"; do
            if check_file "$file"; then
                check_trusted_path "$file"
            else
                had_error=1
            fi
        done
    fi

    # Summary
    printf '\n' >&2
    log_info "Files checked: ${FILE_COUNT}"
    log_info "Violations found: ${VIOLATION_COUNT}"

    if [[ "$had_error" -ne 0 ]]; then
        return 2
    fi

    if [[ "$VIOLATION_COUNT" -gt 0 ]]; then
        log_error "Safety check FAILED — postinst contains forbidden operations"
        return 1
    fi

    log_info "Safety check PASSED — no forbidden operations detected"
    return 0
}

main "$@"
