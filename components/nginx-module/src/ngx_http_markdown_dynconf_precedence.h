/*
 * NGINX Markdown Filter Module - Dynconf Precedence Model (0.9.2)
 *
 * Implements the five-tier precedence hierarchy for dynconf-mutable
 * fields and the per-field block mask that controls which fields
 * the dynconf overlay may override at runtime.
 *
 * Precedence hierarchy (highest to lowest):
 *   1. NGINX request variable evaluation (e.g., markdown_filter $var)
 *   2. Server/location explicit static configuration (block bit set)
 *   3. Dynconf runtime override (block bit NOT set)
 *   4. Inherited http baseline (http-block merged value)
 *   5. Built-in default (compile-time default)
 *
 * Block mask semantics:
 *   - One bit per dynconf-mutable field (5 bits total)
 *   - Bit is set when a server or location block explicitly
 *     configures the field
 *   - An explicit setting in the http block does NOT set the bit
 *   - Block bits propagate from parent to child via OR during merge
 *   - A child that explicitly sets a field keeps the bit set with
 *     its own value
 *   - At header_filter time, dynconf values are overlaid only where
 *     the block bit is NOT set
 *
 * Requirements: 3.13, 3.14, 3.15, 4.12
 */

#ifndef _NGX_HTTP_MARKDOWN_DYNCONF_PRECEDENCE_H_INCLUDED_
#define _NGX_HTTP_MARKDOWN_DYNCONF_PRECEDENCE_H_INCLUDED_

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>


/*
 * Dynconf block mask bit definitions.
 *
 * Each bit corresponds to one dynconf-mutable field.  When set,
 * the dynconf overlay for that field is blocked — the static
 * server/location value takes precedence.
 */
#ifndef NGX_HTTP_MARKDOWN_BLOCK_FILTER
#define NGX_HTTP_MARKDOWN_BLOCK_FILTER           (1 << 0)
#define NGX_HTTP_MARKDOWN_BLOCK_PRUNE_NOISE      (1 << 1)
#define NGX_HTTP_MARKDOWN_BLOCK_LOG_VERBOSITY    (1 << 2)
#define NGX_HTTP_MARKDOWN_BLOCK_ERROR_POLICY     (1 << 3)
#define NGX_HTTP_MARKDOWN_BLOCK_STREAMING_BUFFER (1 << 4)
#endif

/*
 * Total number of dynconf-mutable fields (block mask width).
 */
#ifndef NGX_HTTP_MARKDOWN_DYNCONF_FIELD_COUNT
#define NGX_HTTP_MARKDOWN_DYNCONF_FIELD_COUNT    5
#endif


/*
 * Field provenance enum.
 *
 * Records the source of each dynconf-mutable field's effective
 * value after precedence resolution.
 */
/*
 * Location validation summary (0.9.2 pre-freeze).
 *
 * Aggregates the minimum applicable conversion_memory and the union
 * of all block masks across merged locations.  Replaces the former
 * bounded 4096-entry index: the validation invariant
 *   streaming_buffer <= every applicable conversion_memory
 * is equivalent to
 *   streaming_buffer <= min(applicable conversion_memory)
 * and the aggregate has no capacity limit.
 *
 * One instance is allocated from the main configuration pool and shared
 * (by pointer) across the watcher and snapshots — no per-location
 * allocation, no capacity cap.
 */
/* Type defined in ngx_http_markdown_filter_module.h */


/*
 * Propagate dynconf block mask from parent to child during merge.
 *
 * Implements NGINX merge semantics: child inherits parent block
 * bits via OR.  The child's own explicitly-set bits (set during
 * config parsing) are preserved — they were already set in the
 * child's mask before merge.
 *
 * An explicit setting in the http block does NOT set the bit
 * (handled at parse time by checking the configuration context).
 *
 * Parameters:
 *   child_mask  - pointer to the child location's block mask
 *   parent_mask - the parent location/server's block mask
 */
static ngx_inline void
ngx_http_markdown_propagate_block_mask(ngx_uint_t *child_mask,
    ngx_uint_t parent_mask)
{
    if (child_mask == NULL) {
        return;
    }

    *child_mask |= parent_mask;
}


/*
 * Check whether a specific field's block bit is set.
 *
 * Parameters:
 *   mask     - the dynconf block mask
 *   field_bit - the bit constant for the field to check
 *
 * Returns:
 *   1 if the field is blocked from dynconf override, 0 otherwise
 */
static ngx_inline ngx_flag_t
ngx_http_markdown_field_blocked(ngx_uint_t mask, ngx_uint_t field_bit)
{
    return (mask & field_bit) ? 1 : 0;
}


/*
 * Update the location validation summary with one merged location.
 *
 * If streaming_buffer is NOT blocked for this location, its
 * conversion_memory participates in the minimum.  The block mask
 * is always OR'd into the union regardless of applicability.
 *
 * Parameters:
 *   summary           - the validation summary (must be non-NULL)
 *   conversion_memory - effective static conversion_memory for this location
 *   block_mask        - the location's dynconf block mask
 */
static ngx_inline void
ngx_http_markdown_loc_validation_update(
    ngx_http_markdown_loc_validation_summary_t *summary,
    size_t conversion_memory, ngx_uint_t block_mask)
{
    if (summary == NULL) {
        return;
    }

    summary->block_mask_union |= block_mask;

    if (ngx_http_markdown_field_blocked(
            block_mask, NGX_HTTP_MARKDOWN_BLOCK_STREAMING_BUFFER)) {
        return;
    }

    /* Zero and UNSET mean "no conversion-memory constraint" here; neither
     * value may become a false minimum that rejects a valid dynconf buffer. */
    if (conversion_memory == 0
        || conversion_memory == (size_t) -1) {
        return;
    }

    if (!summary->min_applicable_set
        || conversion_memory < summary->min_applicable_conversion_memory)
    {
        summary->min_applicable_conversion_memory = conversion_memory;
        summary->min_applicable_set = 1;
    }
}


/*
 * Validate a dynconf candidate streaming_buffer against the
 * location validation summary.
 *
 * Returns NGX_OK when the summary has no applicable minimum
 * (caller falls back to conversion_memory/memory_budget checks)
 * or when streaming_buffer is within bounds.
 *
 * Parameters:
 *   summary           - the validation summary
 *   streaming_buffer  - the candidate streaming_buffer value
 *   log               - NGINX log for error reporting
 *
 * Returns:
 *   NGX_OK if the candidate is valid or no applicable minimum exists
 *   NGX_ERROR if streaming_buffer exceeds the minimum
 */
static ngx_inline ngx_int_t
ngx_http_markdown_validate_snapshot_against_summary(
    const ngx_http_markdown_loc_validation_summary_t *summary,
    size_t streaming_buffer, ngx_log_t *log)
{
    if (summary == NULL || !summary->min_applicable_set) {
        return NGX_OK;
    }

    if (streaming_buffer > summary->min_applicable_conversion_memory) {
        if (log != NULL) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                "dynconf candidate rejected: streaming_buffer (%uz) "
                "exceeds the minimum applicable conversion_memory (%uz); "
                "at least one location would be violated",
                streaming_buffer,
                summary->min_applicable_conversion_memory);
        }
        return NGX_ERROR;
    }

    return NGX_OK;
}


#endif /* _NGX_HTTP_MARKDOWN_DYNCONF_PRECEDENCE_H_INCLUDED_ */
