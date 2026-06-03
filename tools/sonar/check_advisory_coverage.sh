#!/usr/bin/env bash
#
# Advisory per-file coverage threshold check (combined e2e + unit report).
#
# This script checks the COMBINED coverage report (e2e + unit tests merged)
# against advisory per-file thresholds and an 80% aggregate minimum.
# Warnings are advisory (non-blocking) — the lcov report is always valid
# regardless of coverage level.
#
# Usage:
#   tools/sonar/check_advisory_coverage.sh <lcov-file>
#
# Exit: always 0 (advisory only).
#
set -euo pipefail

LCOV_FILE="${1:-}"

if [[ -z "${LCOV_FILE}" ]]; then
    echo "Usage: $0 <lcov-file>" >&2
    exit 1
fi

if [[ ! -f "${LCOV_FILE}" ]]; then
    echo "ERROR: lcov file not found: ${LCOV_FILE}" >&2
    exit 1
fi

LCOV_IGNORE=""
if lcov --help 2>&1 | grep -q -- '--ignore-errors'; then
    LCOV_IGNORE="--ignore-errors inconsistent,inconsistent --ignore-errors unsupported,unsupported"
fi

echo "==> Checking advisory coverage thresholds (combined report)"

# shellcheck disable=SC2086
lcov --list "${LCOV_FILE}" --rc branch_coverage=1 \
  ${LCOV_IGNORE} 2>/dev/null | while IFS= read -r line; do
    # Extract file path and line coverage percentage.
    file="$(echo "${line}" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $1); print $1}')"
    pct="$(echo "${line}" | awk -F'|' '{gsub(/%/, "", $2); gsub(/^[ \t]+/, "", $2); split($2, a, " "); print a[1]}')"
    pct="${pct%\%}"

    # Skip non-data lines
    case "${file}" in
        ngx_http_markdown_*) ;;
        *) continue ;;
    esac

    # Advisory thresholds per file
    threshold=""
    case "${file}" in
        ngx_http_markdown_auth.c)                 threshold=60 ;;
        ngx_http_markdown_conditional.c)          threshold=40 ;;
        ngx_http_markdown_error.c)                threshold=50 ;;
        ngx_http_markdown_prometheus_impl.h)      threshold=50 ;;
        ngx_http_markdown_reason.c)               threshold=50 ;;
        ngx_http_markdown_accept.c)               threshold=70 ;;
        ngx_http_markdown_config_handlers_impl.h) threshold=40 ;;
        *) ;;
    esac

    if [[ -n "${threshold}" ]] && [[ -n "${pct}" ]]; then
        pct_int="${pct%%.*}"
        if [[ -n "${pct_int}" ]] && [[ "${pct_int}" =~ ^[0-9]+$ ]] \
           && [[ "${pct_int}" -lt "${threshold}" ]]; then
            echo "  WARNING: ${file} line coverage ${pct}% below advisory threshold ${threshold}%" >&2
        fi
    fi
done || true

# Aggregate line coverage advisory check (80% minimum)
# shellcheck disable=SC2086
aggregate="$(lcov --summary "${LCOV_FILE}" --rc branch_coverage=1 \
  ${LCOV_IGNORE} 2>&1 | awk '/lines\.\.\.\./{gsub(/%/,"",$2); print $2}' || true)"
if [[ -n "${aggregate}" ]]; then
    aggregate_int="${aggregate%%.*}"
    if [[ -n "${aggregate_int}" ]] && [[ "${aggregate_int}" =~ ^[0-9]+$ ]] \
       && [[ "${aggregate_int}" -lt 80 ]]; then
        echo "  WARNING: Aggregate line coverage ${aggregate}% below 80% minimum" >&2
    else
        echo "  Aggregate line coverage: ${aggregate}% (meets 80% minimum)"
    fi
fi

exit 0
