#!/bin/bash
# ---------------------------------------------------------------------------
# check_postinst_safety.sh — Static analysis of postinst scripts for safety
#
# PURPOSE:
#   Verifies that postinst (DEB) and %post (RPM) scripts do not contain
#   forbidden operations that could modify NGINX state during package
#   installation. The package installation MUST be non-invasive: no
#   reload/restart, no config modification, no snippet enablement.
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
#     - packaging/debian/postinst
#     - packaging/rpm/SPECS/nginx-module-markdown.spec (%post section)
#
# EXIT CODES:
#   0  No forbidden patterns found
#   1  One or more forbidden patterns detected
#   2  Usage error (bad option or file not found)
#
# FORBIDDEN PATTERNS:
#   - nginx -s reload / nginx -s restart
#   - systemctl restart nginx / systemctl reload nginx
#   - service nginx restart / service nginx reload
#   - Writing to /etc/nginx/ (cp, mv, tee, > redirects)
#   - Modifying nginx.conf (sed -i, echo to nginx.conf)
#   - Enabling snippets (ln -s to modules-enabled or conf.d)
#
# NOTES:
#   - macOS bash 3.2 compatible (no bash 4+ features)
#   - Diagnostic messages go to stderr
#   - Machine-readable results to stdout
#
# SEE ALSO:
#   - .kiro/specs/31-0.7.0-release-package-compatibility/requirements.md §7
#   - .kiro/specs/31-0.7.0-release-package-compatibility/design.md §Components 3
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
    printf 'Static analysis of postinst scripts for forbidden operations.\n' >&2
    printf '\n' >&2
    printf 'If no files are provided, defaults to checking:\n' >&2
    printf '  packaging/debian/postinst\n' >&2
    printf '  packaging/rpm/SPECS/nginx-module-markdown.spec\n' >&2
    printf '\n' >&2
    printf 'Options:\n' >&2
    printf '  -h, --help    Show this help message\n' >&2
    return 0
}

log_info() {
    printf '[INFO]  %s\n' "$1" >&2
}

log_warn() {
    printf '[WARN]  %s\n' "$1" >&2
}

log_error() {
    printf '[ERROR] %s\n' "$1" >&2
}

log_violation() {
    printf '[VIOLATION] %s:%s: %s\n' "$1" "$2" "$3" >&2
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

    while IFS= read -r line || [ -n "$line" ]; do
        if [ "$in_heredoc" -eq 1 ]; then
            # Check if this line ends the heredoc
            local trimmed
            trimmed="$(printf '%s' "$line" | sed 's/^[[:space:]]*//')"
            if [ "$trimmed" = "$heredoc_delim" ]; then
                in_heredoc=0
                heredoc_delim=""
            fi
            # Output blank line to preserve line numbering
            printf '\n'
        else
            # Detect heredoc start: <<'DELIM', <<"DELIM", <<DELIM, <<-'DELIM' etc.
            local delim_match
            delim_match="$(printf '%s' "$line" | sed -n "s/.*<<-*[[:space:]]*['\"]\\{0,1\\}\([A-Za-z_][A-Za-z_0-9]*\)['\"]\\{0,1\\}.*/\1/p")"
            if [ -n "$delim_match" ]; then
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

    if [ -n "$matches" ]; then
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

# check_file — run all forbidden pattern checks against a single file
# Arguments: $1 = file path
# Returns: 0 always (violations tracked in VIOLATION_COUNT), 2 on file error
check_file() {
    local file="$1"

    if [ ! -f "$file" ]; then
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

# extract_rpm_post — extract %post section from RPM spec for analysis
# Arguments: $1 = RPM spec file path
# Outputs: extracted %post content to a temp file, prints temp file path
extract_rpm_post() {
    local spec_file="$1"
    local tmp_file
    tmp_file="$(mktemp)"
    local in_post=0

    while IFS= read -r line; do
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
    case "${1:-}" in
        -h|--help)
            usage
            return 0
            ;;
        -*)
            log_error "Unknown option: $1"
            usage
            return 2
            ;;
        *)
            ;;
    esac

    local files_to_check=""
    local had_error=0

    if [ $# -eq 0 ]; then
        # Default: check known postinst locations
        log_info "No files specified; using defaults"

        if [ -f "packaging/debian/postinst" ]; then
            files_to_check="packaging/debian/postinst"
        else
            log_warn "Default file not found: packaging/debian/postinst"
        fi

        if [ -f "packaging/rpm/SPECS/nginx-module-markdown.spec" ]; then
            # Extract %post section to a temp file for analysis
            local rpm_post_tmp
            rpm_post_tmp="$(extract_rpm_post "packaging/rpm/SPECS/nginx-module-markdown.spec")"
            if [ -s "$rpm_post_tmp" ]; then
                log_info "Extracted %%post section from RPM spec"
                check_file "$rpm_post_tmp" || had_error=1
                rm -f "$rpm_post_tmp"
            else
                log_info "No %%post section found in RPM spec (or section is empty)"
                rm -f "$rpm_post_tmp"
            fi
        else
            log_warn "Default file not found: packaging/rpm/SPECS/nginx-module-markdown.spec"
        fi

        # Check the DEB postinst if found
        if [ -n "$files_to_check" ]; then
            check_file "$files_to_check" || had_error=1
        fi
    else
        # Check each provided file
        for file in "$@"; do
            check_file "$file" || had_error=1
        done
    fi

    # Summary
    printf '\n' >&2
    log_info "Files checked: ${FILE_COUNT}"
    log_info "Violations found: ${VIOLATION_COUNT}"

    if [ "$had_error" -ne 0 ]; then
        return 2
    fi

    if [ "$VIOLATION_COUNT" -gt 0 ]; then
        log_error "Safety check FAILED — postinst contains forbidden operations"
        return 1
    fi

    log_info "Safety check PASSED — no forbidden operations detected"
    return 0
}

main "$@"
