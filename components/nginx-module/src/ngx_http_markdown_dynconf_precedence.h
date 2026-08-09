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
 * Location validation index entry.
 *
 * One entry per merged location, built during configuration merge.
 * Used at dynconf reload time to validate streaming_buffer candidates
 * against each location's effective static conversion_memory.
 */
typedef struct ngx_http_markdown_loc_validation_index_entry_s {
    size_t        conversion_memory;    /* effective static conversion_memory */
    ngx_uint_t    block_mask;           /* dynconf block mask for this location */
    ngx_flag_t    applicable;           /* 1 if streaming_buffer not blocked */
} ngx_http_markdown_loc_validation_entry_t;


/*
 * Location validation index.
 *
 * Bounded read-only array built once at configuration time.
 * Contains one entry for every merged location that could consume
 * a dynconf streaming_buffer overlay.
 *
 * The index is bounded by the number of merged locations and is
 * built once at configuration time; reload validation is a bounded
 * scan and does not traverse the live NGINX configuration tree.
 */
typedef struct ngx_http_markdown_loc_validation_index_s {
    ngx_http_markdown_loc_validation_entry_t  *entries;
    ngx_uint_t                                 count;
    ngx_uint_t                                 capacity;
} ngx_http_markdown_loc_validation_index_t;


/*
 * Maximum number of locations in the validation index.
 * Provides an allocation upper bound to prevent unbounded growth.
 */
#define NGX_HTTP_MARKDOWN_LOC_INDEX_MAX  4096


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
 * Validate a dynconf candidate snapshot against the location
 * validation index.
 *
 * Checks that the candidate streaming_buffer value does not exceed
 * the effective static conversion_memory for any applicable
 * (unblocked) location.
 *
 * Parameters:
 *   index             - the location validation index
 *   streaming_buffer  - the candidate streaming_buffer value
 *   log               - NGINX log for error reporting
 *
 * Returns:
 *   NGX_OK if the candidate is valid for all applicable locations
 *   NGX_ERROR if any applicable location would be violated
 */
static ngx_inline ngx_int_t
ngx_http_markdown_validate_snapshot_against_index(
    const ngx_http_markdown_loc_validation_index_t *index,
    size_t streaming_buffer, ngx_log_t *log)
{
    ngx_uint_t  violated_count;

    if (index == NULL || index->entries == NULL || index->count == 0) {
        return NGX_OK;
    }

    violated_count = 0;

    for (ngx_uint_t i = 0; i < index->count; i++) {
        if (!index->entries[i].applicable) {
            continue;
        }

        if (streaming_buffer > index->entries[i].conversion_memory) {
            violated_count++;
        }
    }

    if (violated_count > 0) {
        if (log != NULL) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                "dynconf candidate rejected: streaming_buffer (%uz) "
                "exceeds conversion_memory in %ui applicable location(s)",
                streaming_buffer, violated_count);
        }
        return NGX_ERROR;
    }

    return NGX_OK;
}


/*
 * Initialize the location validation index.
 *
 * Allocates the index entry array from the provided pool.
 * Must be called once at configuration time before any
 * merge operations add entries.
 *
 * Parameters:
 *   index - the index to initialize
 *   pool  - NGINX pool for allocation
 *
 * Returns:
 *   NGX_OK on success, NGX_ERROR on allocation failure
 */
static ngx_inline ngx_int_t
ngx_http_markdown_loc_index_init(
    ngx_http_markdown_loc_validation_index_t *index, ngx_pool_t *pool)
{
    if (index == NULL || pool == NULL) {
        return NGX_ERROR;
    }

    index->entries = ngx_pcalloc(pool,
        NGX_HTTP_MARKDOWN_LOC_INDEX_MAX
        * sizeof(ngx_http_markdown_loc_validation_entry_t));
    if (index->entries == NULL) {
        return NGX_ERROR;
    }

    index->count = 0;
    index->capacity = NGX_HTTP_MARKDOWN_LOC_INDEX_MAX;

    return NGX_OK;
}


/*
 * Add a location entry to the validation index.
 *
 * Called during merge_conf for each location.  Records the
 * location's effective static conversion_memory and whether
 * the streaming_buffer field is blocked from dynconf overlay.
 *
 * Parameters:
 *   index             - the location validation index
 *   conversion_memory - effective static conversion_memory for this location
 *   block_mask        - the location's dynconf block mask
 *
 * Returns:
 *   NGX_OK on success
 *   NGX_ERROR if the index is full (capacity exceeded)
 */
static ngx_inline ngx_int_t
ngx_http_markdown_loc_index_add(
    ngx_http_markdown_loc_validation_index_t *index,
    size_t conversion_memory, ngx_uint_t block_mask)
{
    ngx_http_markdown_loc_validation_entry_t  *entry;

    if (index == NULL || index->entries == NULL) {
        return NGX_ERROR;
    }

    if (index->count >= index->capacity) {
        return NGX_ERROR;
    }

    entry = &index->entries[index->count];
    entry->conversion_memory = conversion_memory;
    entry->block_mask = block_mask;
    entry->applicable = !ngx_http_markdown_field_blocked(
        block_mask, NGX_HTTP_MARKDOWN_BLOCK_STREAMING_BUFFER);
    index->count++;

    return NGX_OK;
}


#endif /* _NGX_HTTP_MARKDOWN_DYNCONF_PRECEDENCE_H_INCLUDED_ */
