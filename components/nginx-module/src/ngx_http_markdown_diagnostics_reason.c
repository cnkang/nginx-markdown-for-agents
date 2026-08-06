/*
 * NGINX Markdown Filter Module - Diagnostics Reason Mapping
 *
 * Keeps the canonical and legacy diagnostics reason-name mapping in a small
 * standalone translation unit so it can be exercised without the HTTP
 * diagnostics handler's request-path dependencies.
 */

#include <ngx_config.h>
#include <ngx_core.h>

#include "ngx_http_markdown_diagnostics.h"

#ifndef ngx_strcmp
#include <string.h>
#define ngx_strcmp(s1, s2) \
    strcmp((const char *) (s1), (const char *) (s2))
#endif

/*
 * Convert a diagnostics reason string to its canonical
 * ReasonCode discriminant (decision/reason_code.rs is the source of truth).
 *
 * The decision path carries pre-formatted reason strings; the ring stores a
 * compact numeric code. Unknown/legacy strings map to -1 so the consumer can
 * render them distinctly without guessing.
 */
ngx_int_t
ngx_http_markdown_diagnostics_reason_to_code(const char *reason)
{
    static const struct {
        const char  *name;
        ngx_int_t    code;
    } map[] = {
        /* New lowercase snake_case names (schema v1) */
        { "converted",                     0 },
        { "skipped_accept",                1 },
        { "skipped_no_accept",             2 },
        { "skipped_conditional",           3 },
        { "decompression_error",           4 },
        { "decompression_budget_exceeded", 5 },
        { "decompression_format_error",    6 },
        { "decompression_truncated_input", 7 },
        { "decompression_io_error",        8 },
        { "timeout",                       9 },
        { "budget_exceeded",              10 },
        { "replay_error",                 11 },
        { "skipped_accept_reject",        12 },
        { "ffi_panic",                    13 },
        { "not_eligible",                 14 },
        { "disabled",                     15 },
        { "failed_open",                  16 },
        { "failed_closed",                17 },
        { "conversion_error",             18 },
        { "memory_budget_exceeded",       19 },
        /* Production reason codes (indices 20-26) */
        { "overload",                     20 },
        { "invalid_dynconf",              21 },
        { "degraded_snapshot",            22 },
        { "header_plan_apply_error",      23 },
        { "streaming_mid_flight_error",   24 },
        { "bypass_no_transform",          25 },
        { "encoding_header_invalid",      26 },
        /* Legacy uppercase names (backward compatibility) */
        { "CONVERTED",                     0 },
        { "ELIGIBLE_CONVERTED",            0 },
        { "SKIPPED_ACCEPT",                1 },
        { "SKIP_ACCEPT",                   1 },
        { "SKIPPED_NO_ACCEPT",             2 },
        { "SKIPPED_CONDITIONAL",           3 },
        { "FAILED_DECOMPRESSION",          4 },
        { "DECOMPRESSION_BUDGET_EXCEEDED", 5 },
        { "DECOMPRESSION_FORMAT_ERROR",    6 },
        { "DECOMPRESSION_TRUNCATED_INPUT", 7 },
        { "DECOMPRESSION_IO_ERROR",        8 },
        { "PARSE_TIMEOUT",                 9 },
        { "PARSE_BUDGET_EXCEEDED",        10 },
        { "REPLAY_BUFFER_ERROR",          11 },
        { "SKIPPED_ACCEPT_REJECT",        12 },
        { "FFI_CALL_ERROR",               13 },
        { "NOT_ELIGIBLE",                 14 },
        { "DISABLED",                     15 },
        { "FAILED_OPEN",                  16 },
        { "ELIGIBLE_FAILED_OPEN",         16 },
        { "FAILED_CLOSED",                17 },
        { "ELIGIBLE_FAILED_CLOSED",       17 },
        { "FAIL_CONVERSION",              18 },
        { "FAIL_RESOURCE_LIMIT",          19 },
        { "FAIL_SYSTEM",                  13 },
        { "BYPASS_NO_TRANSFORM",          25 },
    };
    if (reason == NULL) {
        return -1;
    }

    for (size_t i = 0; i < sizeof(map) / sizeof(map[0]); i++) {
        if (ngx_strcmp(reason, map[i].name) == 0) {
            return map[i].code;
        }
    }

    return -1;
}
