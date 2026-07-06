/*
 * NGINX Markdown Filter Module - Header Plan Atomic Application
 *
 * Provides atomic application of Rust-built header plans to NGINX
 * response headers using a two-phase prepare/commit model (spec 48):
 * the prepare phase performs every fallible step (allocation,
 * validation, lookup) without mutating any pre-existing header, and the
 * commit phase performs assignment-only mutations that cannot fail.
 * Either all header changes are applied (commit) or none are (prepare
 * aborted) -- there is no partial application.
 *
 * REQ-0700-RUST-004: Header plan atomicity.
 */

#ifndef NGX_HTTP_MARKDOWN_HEADER_PLAN_H
#define NGX_HTTP_MARKDOWN_HEADER_PLAN_H

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

struct FFIHeaderPlan;

/*
 * Apply a header plan atomically to the response headers.
 *
 * Two-phase (spec 48):
 *   prepare - allocate, copy strings, look up existing headers, and
 *             validate every operation.  No pre-existing r->headers_out
 *             field is mutated.  On any failure the plan is aborted with
 *             r->headers_out unchanged.
 *   commit  - apply the already-prepared mutations via assignment only
 *             (no allocation, no failure path).
 *
 * On success, frees the Rust-owned plan via markdown_header_plan_free().
 * On a prepare failure (no mutation applied), also frees the plan.
 *
 * NULL plan is a no-op returning NGX_OK (nothing to free).  A plan with 0
 * entries frees the plan and returns NGX_OK.
 *
 * r    - current HTTP request
 * plan - Rust-owned header plan (freed on both success and failure)
 *
 * Returns:
 *   NGX_OK    all entries committed successfully (plan freed)
 *   NGX_ERROR prepare failed; no header changes applied (plan freed)
 */
ngx_int_t ngx_http_markdown_apply_header_plan(ngx_http_request_t *r,
    struct FFIHeaderPlan *plan);

#endif /* NGX_HTTP_MARKDOWN_HEADER_PLAN_H */
