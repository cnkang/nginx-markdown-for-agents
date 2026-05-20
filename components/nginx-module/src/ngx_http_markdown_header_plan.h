/*
 * NGINX Markdown Filter Module - Header Plan Atomic Application
 *
 * Provides atomic application of Rust-built header plans to NGINX
 * response headers.  Either all header changes succeed or all are
 * rolled back (no partial application).
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
 * Iterates through all entries in the plan and applies each operation
 * (set/delete/modify) to r->headers_out.  If any operation fails
 * (e.g., allocation failure), all previously applied changes are
 * rolled back to restore the original header state.
 *
 * On success, frees the Rust-owned plan via markdown_header_plan_free().
 * On failure (with rollback completed), also frees the plan.
 *
 * NULL plan or plan with 0 entries is a no-op returning NGX_OK.
 *
 * r    - current HTTP request
 * plan - Rust-owned header plan (freed on both success and failure)
 *
 * Returns:
 *   NGX_OK    all entries applied successfully (plan freed)
 *   NGX_ERROR one or more entries failed; all changes rolled back
 *             (plan freed)
 */
ngx_int_t ngx_http_markdown_apply_header_plan(ngx_http_request_t *r,
    struct FFIHeaderPlan *plan);

#endif /* NGX_HTTP_MARKDOWN_HEADER_PLAN_H */
