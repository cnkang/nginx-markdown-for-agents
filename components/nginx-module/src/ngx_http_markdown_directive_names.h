#ifndef NGX_HTTP_MARKDOWN_DIRECTIVE_NAMES_H
#define NGX_HTTP_MARKDOWN_DIRECTIVE_NAMES_H

/*
 * Canonical names for the frozen public directive registry.  The command
 * table and contract tests include this inventory so a renamed directive
 * cannot silently make the test's local copy stale.
 */
#define NGX_HTTP_MARKDOWN_DIRECTIVE_FILTER \
    "markdown_filter"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_LIMITS \
    "markdown_limits"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_ERROR_POLICY \
    "markdown_error_policy"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_FLAVOR \
    "markdown_flavor"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_TOKEN_ESTIMATE \
    "markdown_token_estimate"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_FRONT_MATTER \
    "markdown_front_matter"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_ACCEPT \
    "markdown_accept"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_AUTH_POLICY \
    "markdown_auth_policy"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_AUTH_COOKIES \
    "markdown_auth_cookies"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_CACHE_VALIDATION \
    "markdown_cache_validation"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_STREAMING \
    "markdown_streaming"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_LOG_VERBOSITY \
    "markdown_log_verbosity"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_CONTENT_TYPES \
    "markdown_content_types"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_TRUSTED_PROXIES \
    "markdown_trusted_proxies"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_METRICS_SHM_SIZE \
    "markdown_metrics_shm_size"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_METRICS \
    "markdown_metrics"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_PRUNE_NOISE \
    "markdown_prune_noise"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_PRUNE_SELECTORS \
    "markdown_prune_selectors"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_PRUNE_PROTECTION_SELECTORS \
    "markdown_prune_protection_selectors"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_AUTO_DECOMPRESS \
    "markdown_auto_decompress"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_DYNAMIC_CONFIG \
    "markdown_dynamic_config"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_DYNAMIC_CONFIG_PATH \
    "markdown_dynamic_config_path"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_DYNCONF_DRY_RUN \
    "markdown_dynconf_dry_run"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_DIAGNOSTICS \
    "markdown_diagnostics"
#define NGX_HTTP_MARKDOWN_DIRECTIVE_STREAM_EXCLUDED_TYPES \
    "markdown_stream_excluded_types"

#endif /* NGX_HTTP_MARKDOWN_DIRECTIVE_NAMES_H */
