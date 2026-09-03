#!/bin/bash
#
# detect_ngx_again_call_sites.sh — NGX_AGAIN Call-Site Branch Audit
#                                  (streaming-backpressure NGX_AGAIN call-site rule)
#
# Scans every call site of the module's known NGX_AGAIN-returning APIs and
# requires an explicit branch for NGX_AGAIN that is NOT folded into the
# NGX_ERROR handling path.  A call site that treats NGX_AGAIN like an error
# (return rc / free output / advance without save) silently truncates the
# response under downstream backpressure.
#
# The known-API list is the single registry for "may return NGX_AGAIN"
# functions.  Adding a new API that can return NGX_AGAIN requires registering
# it here (and in the rule document) so every call site is audited.
#
# Heuristic scope: this is an audit-assist heuristic, NOT a
# completeness proof.  Known limitations: (a) a windowed scan can let one
# call site's NGX_AGAIN mention mask a sibling call site in the same
# function; (b) the folded pattern `if (rc == NGX_OK) {...} else {error}`
# (success branch + implicit NGX_AGAIN fall-through) is not matched; (c) only
# a bare `return rc;` with no nearby NGX_AGAIN mention is reported.  Manual
# review of each reported-and-each-missed site remains authoritative.
#
# Usage:
#   bash tools/harness/detect_ngx_again_call_sites.sh [directory]
#     directory defaults to components/nginx-module/src
#
# Exit codes:
#   0 — no violations found
#   1 — one or more violations detected
#   2 — usage/argument error

set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SRC_DIR="${1:-${REPO_ROOT}/components/nginx-module/src}"

if [[ ! -d "$SRC_DIR" ]]; then
    echo "ERROR: Source directory not found: $SRC_DIR" >&2
    exit 2
fi

# Known APIs that may return NGX_AGAIN.  Keep sorted.
NGX_AGAIN_APIS=(
    ngx_http_markdown_forward_headers
    ngx_http_markdown_streaming_commit
    ngx_http_markdown_streaming_send_feed_output
    ngx_http_markdown_streaming_send_failopen_chain
    ngx_http_markdown_streaming_send_pending
    ngx_http_markdown_streaming_resume_pending
)

tmp_violations=$(mktemp)
trap 'rm -f "$tmp_violations"' EXIT

while IFS= read -r -d '' file; do
    for api in "${NGX_AGAIN_APIS[@]}"; do
        # Find every call site of this API (matches "api(" as a call)
        grep -n "${api}[[:space:]]*(" "$file" 2>/dev/null | \
            while IFS=: read -r line_num content; do
            [[ -z "$line_num" ]] && continue
            [[ -z "$content" ]] && continue

            # Skip comment-only lines and documentation
            echo "$content" | grep -qE '^[[:space:]]*//|^[[:space:]]*\*|@return|@param' && continue

            # Skip the API's own definition / declaration (name followed by ( at
            # start or after return type on the same line is ambiguous; the
            # definition normally has the name at line start or after a type).
            # Heuristic: a definition line has no leading whitespace before the
            # name when the return type is on the same line; declarations end
            # with ';' while calls end with ')' or ';' too — so instead skip
            # lines whose function body starts on this line (contains '{' after
            # the name) or that are declarations in headers ending with ';'
            # without an argument expression context.
            if echo "$content" | grep -qE "${api}[[:space:]]*\([^)]*\)[[:space:]]*\{"; then
                continue
            fi

            # Examine a window around the call site (before and after)
            window_start=$((line_num - 25))
            [[ $window_start -lt 1 ]] && window_start=1
            window_end=$((line_num + 12))
            surrounding_code=$(sed -n "${window_start},${window_end}p" "$file")

            # The call must be inside a function (not a header declaration).
            # Header declarations end with ';' immediately after the parameter
            # list.  Calls are followed by more statements.  Skip lines that
            # look like declarations: name(...) followed only by ';' on the
            # same line AND the surrounding window has no assignment/return.
            # Still may be a call statement `rc = api(...);` — allowlist
            # assignment/return forms, skip bare declarations.
            if echo "$content" | grep -qE "${api}[[:space:]]*\([^;]*\)[[:space:]]*;" \
                && ! echo "$content" | grep -qE '(=|return|->|\.)'; then
                continue
            fi

            # Ignore the API definition body (the function that IS the API)
            if echo "$surrounding_code" | grep -qE "^${api}[[:space:]]*\(|^[a-z_]+[[:space:]]*\*?[a-z_]*[[:space:]]*${api}[[:space:]]*\(" \
                && echo "$surrounding_code" | grep -qE "${api}[[:space:]]*\([^)]*\)[[:space:]]*\{"; then
                continue
            fi

            # ===== Violation checks =====

            # 1. NGX_AGAIN must be explicitly branched, not folded into error.
            #    Look at the code after the call site: an explicit branch has
            #    'NGX_AGAIN' within the next ~20 lines.  Only lines strictly
            #    after the call site count — a mention of NGX_AGAIN in the
            #    code before the call (or in the wider surrounding window)
            #    is not a branch on this call's result.
            after_end=$((line_num + 12))
            after_code=$(sed -n "$((line_num + 1)),${after_end}p" "$file")
            has_again_branch=0
            if echo "$after_code" | grep -q 'NGX_AGAIN'; then
                has_again_branch=1
            fi
            # delivery_ok is the module's dedicated NGX_AGAIN-aware result
            # predicate; a call that checks delivery_ok(rc) is treating
            # NGX_AGAIN as an explicit branch and should not be flagged.
            if echo "$after_code" | grep -q 'ngx_http_markdown_streaming_delivery_ok'; then
                has_again_branch=1
            fi

            # 2. If the call result is assigned and the very next statements
            #    treat non-NGX_OK as error (rc != NGX_OK / rc == NGX_ERROR),
            #    that is a violation when NGX_AGAIN is not distinguished.
            fold_pattern=''
            if echo "$after_code" | grep -qE 'rc[[:space:]]*!=[[:space:]]*NGX_OK|rc[[:space:]]*==[[:space:]]*NGX_ERROR|!= NGX_OK'; then
                fold_pattern='error-fold'
            fi

            # 3. A return immediately after the call without NGX_AGAIN mention.
            immediate_return=0
            # Inspect after_code, which holds the lines following the call
            # site, rather than deriving a range from surrounding_code
            # (whose tail may include lines before the call).
            if echo "$after_code" | grep -qE '^[[:space:]]*return[[:space:]]+(rc|[a-z_]+);'; then
                immediate_return=1
            fi

            if [[ "$has_again_branch" -eq 0 && ( -n "$fold_pattern" || "$immediate_return" -eq 1 ) ]]; then
                echo "VIOLATION: $file:$line_num — call to $api() returns NGX_AGAIN but the call site has no explicit NGX_AGAIN branch (folded into error path or immediate return)" >> "$tmp_violations"
            fi
        done
    done
done < <(find "$SRC_DIR" \( -name "*.c" -o -name "*.h" \) -type f -print0)

violations=$(wc -l < "$tmp_violations" | tr -d '[:space:]')

if [[ "$violations" -gt 0 ]]; then
    cat "$tmp_violations" >&2
    echo "ERROR: Found $violations NGX_AGAIN call-site violation(s)" >&2
    exit 1
else
    echo "OK: No NGX_AGAIN call-site violations detected"
    exit 0
fi
