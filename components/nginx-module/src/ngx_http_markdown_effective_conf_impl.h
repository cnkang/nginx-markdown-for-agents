/*
 * NGINX Markdown Filter Module - Effective Configuration Projection
 *
 * Static-only effective-configuration projection, request-lifecycle
 * binding seam, per-request accessor helpers, and the static block-mask
 * precedence primitives.
 *
 * This translation unit is the retained home for the shared static
 * configuration projection. The former runtime overlay and its snapshot
 * capture, JSON reload, and two-phase apply paths were removed.
 * The request path continues to read all mutable configuration through a
 * single pool-owned effective view bound once at header_filter time
 * (bind-once invariant, AGENTS.md Rules 34/35/45), so a request never
 * reads live conf-> fields directly.
 *
 * The block-mask bit constants, provenance constants,
 * ngx_http_markdown_effective_conf_t, and
 * ngx_http_markdown_loc_validation_summary_t all live in the public
 * ngx_http_markdown_filter_module.h header (retained).  They name the
 * set of explicitly-set static fields whose per-level inheritance the
 * static block-mask (Rule 71) protects.
 *
 * WARNING: This header is an implementation detail of the main
 * translation unit (ngx_http_markdown_filter_module.c).  It must NOT be
 * included from any other .c file or used as a standalone compilation
 * unit.
 */

#ifndef _NGX_HTTP_MARKDOWN_EFFECTIVE_CONF_IMPL_H_INCLUDED_
#define _NGX_HTTP_MARKDOWN_EFFECTIVE_CONF_IMPL_H_INCLUDED_

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

#include "ngx_http_markdown_filter_module.h"


/*
 * Check whether a specific field's block bit is set.
 *
 * A set bit marks a field that a server or location block configured
 * explicitly; per-level inheritance carries the bit parent to child via
 * OR during merge so a child's explicit value is not silently masked.
 *
 * Parameters:
 *   mask      - the static explicit block mask
 *   field_bit - the bit constant for the field to check
 *
 * Returns:
 *   1 if the field is explicitly set (blocked), 0 otherwise
 */
static ngx_inline ngx_flag_t
ngx_http_markdown_field_blocked(ngx_uint_t mask, ngx_uint_t field_bit)
{
    return (mask & field_bit) ? 1 : 0;
}


/*
 * Update the location validation summary with one merged location.
 *
 * If streaming_buffer is NOT explicitly set for this location, its
 * conversion_memory participates in the minimum.  The block mask is
 * always OR'd into the union regardless of applicability.
 *
 * Parameters:
 *   summary           - the validation summary (must be non-NULL)
 *   conversion_memory - effective static conversion_memory for this location
 *   block_mask        - the location's static explicit block mask
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
     * value may become a false minimum that rejects a valid buffer. */
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


/**
 * Build the effective configuration view from the live static conf.
 *
 * With the dynconf overlay removed, every field is projected from the
 * static (merged/inherited) configuration and its provenance is STATIC,
 * except the filter-enable field, whose provenance is REQUEST_VARIABLE
 * when the enable state is driven by a complex value.  The request
 * variable itself is evaluated later, at is_enabled time.
 *
 * The block mask is copied into the effective view for diagnostics; it
 * records which fields a server/location block set explicitly.
 *
 * @param eff  Target effective config view to populate; must be non-NULL.
 * @param conf Live module configuration to project; must be non-NULL.
 */
static void
ngx_http_markdown_build_effective_conf(
    ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff == NULL || conf == NULL) {
        return;
    }

    /* Copy block mask into effective_conf for diagnostics. */
    eff->block_mask = conf->advanced.static_block_mask;

    if (conf->enabled_source == NGX_HTTP_MARKDOWN_ENABLED_COMPLEX) {
        eff->enabled = conf->enabled;
        eff->enabled_source = conf->enabled_source;
        eff->filter_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_REQUEST_VARIABLE;
    } else {
        eff->enabled = conf->enabled;
        eff->enabled_source = conf->enabled_source;
        eff->filter_provenance = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    }

    eff->prune_noise = conf->advanced.prune_noise;
    eff->prune_noise_provenance = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;

    eff->log_verbosity = conf->policy.log_verbosity;
    eff->log_verbosity_provenance = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;

    eff->error_policy = conf->on_error;
    eff->error_status = conf->error_status;
    eff->error_policy_provenance = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;

    /* memory_budget carries the frozen conversion_memory limit so request
     * paths read one consistent value. */
    eff->memory_budget = conf->limits.conversion_memory;

#ifdef MARKDOWN_STREAMING_ENABLED
    eff->streaming_budget = conf->stream.budget;
#endif
    eff->streaming_buffer = conf->stream.budget;
    eff->streaming_buffer_provenance = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
}


/*
 * Bind the effective view into request storage (bind-once seam).
 *
 * Production request code and conformance tests use this same seam so the
 * request-lifetime configuration view cannot silently diverge.  The
 * effective view is copied by value into `eff_storage` (owned by the
 * caller, typically the request context); there is no pool allocation and
 * no failure path, so the bind-once invariant always holds.
 *
 * Parameters:
 *   r             - NGINX request structure (for pool and logging guards)
 *   conf          - module location configuration (retained for signature
 *                   stability; unused in the static-only projection)
 *   early_eff     - effective view built at header_filter entry
 *   eff_storage   - caller-owned storage for the effective view copy
 *   effective_slot- caller pointer set to eff_storage on success
 */
static void
ngx_http_markdown_bind_request_snapshot(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *early_eff,
    ngx_http_markdown_effective_conf_t *eff_storage,
    ngx_http_markdown_effective_conf_t **effective_slot)
{
    (void) conf;

    if (r == NULL || effective_slot == NULL || eff_storage == NULL
        || early_eff == NULL)
    {
        return;
    }

    /* By-value copy: no allocation, no failure path, bind-once preserved. */
    *eff_storage = *early_eff;
    *effective_slot = eff_storage;
}


/**
 * Read effective log_verbosity for a request.
 *
 * Prefers the effective_conf view bound to ctx; falls back to live conf
 * if the view is unavailable.
 */
static ngx_uint_t
ngx_http_markdown_effective_log_verbosity(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff != NULL) {
        return eff->log_verbosity;
    }
    if (conf == NULL) {
        return NGX_HTTP_MARKDOWN_LOG_ERROR;
    }
    return conf->policy.log_verbosity;
}


/**
 * Read effective prune_noise for a request.
 */
static ngx_flag_t
ngx_http_markdown_effective_prune_noise(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff != NULL) {
        return eff->prune_noise;
    }
    if (conf == NULL) {
        return 0;
    }
    return conf->advanced.prune_noise;
}


/**
 * Read effective memory_budget for a request.
 *
 * The full-buffer conversion memory bound is the public
 * `markdown_limits conversion_memory` limit.  `eff->memory_budget` carries
 * that value, and the static fallback reads `conf->limits.conversion_memory`
 * directly.
 */
static size_t
ngx_http_markdown_effective_memory_budget(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff != NULL) {
        return eff->memory_budget;
    }
    if (conf == NULL) {
        return 0;
    }
    return conf->limits.conversion_memory;
}


#ifdef MARKDOWN_STREAMING_ENABLED
/**
 * Read effective streaming_budget for a request.
 */
static size_t
ngx_http_markdown_effective_streaming_budget(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff != NULL) {
        return eff->streaming_budget;
    }
    if (conf == NULL) {
        return 0;
    }
    return conf->stream.budget;
}
#endif


/**
 * Read effective enabled flag for a request.
 */
static ngx_flag_t
ngx_http_markdown_effective_enabled(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff != NULL) {
        return eff->enabled;
    }
    if (conf == NULL) {
        return 0;
    }
    return conf->enabled;
}


/**
 * Read effective enabled_source for a request.
 */
static ngx_uint_t
ngx_http_markdown_effective_enabled_source(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff != NULL) {
        return eff->enabled_source;
    }
    if (conf == NULL) {
        return NGX_HTTP_MARKDOWN_ENABLED_STATIC;
    }
    return conf->enabled_source;
}


#endif /* _NGX_HTTP_MARKDOWN_EFFECTIVE_CONF_IMPL_H_INCLUDED_ */
