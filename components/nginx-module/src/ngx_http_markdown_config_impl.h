/*
 * Configuration implementation wiring.
 *
 * WARNING: This header is an implementation detail of the main translation unit
 * (ngx_http_markdown_filter_module.c). It must NOT be included from any other
 * .c file or used as a standalone compilation unit.
 *
 * Kept as a small aggregator so the main module file can include one config
 * entrypoint while directive registry, directive handlers, and config core
 * helpers evolve independently.
 */

#include "ngx_http_markdown_config_core_impl.h"
#include "ngx_http_markdown_config_handlers_impl.h"
#include "ngx_http_markdown_config_directives_impl.h"

/*
 * Configuration-unit forward declarations.
 *
 * These declarations are intentionally centralized here so directive tables
 * can reference handlers before the corresponding helper implementation header
 * is included. Keep signatures in sync with *_config_*_impl.h definitions.
 */
/* Allocate and initialize main-level shared state (metrics zone settings). */
static void *ngx_http_markdown_create_main_conf(ngx_conf_t *cf);
/* Finalize main-level defaults and register shared memory zone callbacks. */
static char *ngx_http_markdown_init_main_conf(ngx_conf_t *cf, void *conf);
/* Allocate location-scoped config with NGX_CONF_UNSET sentinel values. */
static void *ngx_http_markdown_create_conf(ngx_conf_t *cf);
/* Merge parent/child location config with deterministic precedence rules. */
static char *ngx_http_markdown_merge_conf(ngx_conf_t *cf, void *parent, void *child);
/* Initialize or attach cross-worker metrics memory zone. */
static ngx_int_t ngx_http_markdown_init_metrics_zone(ngx_shm_zone_t *shm_zone, void *data);
/* Parse markdown_filter (on/off or complex value) directive payload. */
static char *ngx_http_markdown_filter(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
/* Parse markdown_on_error enum and validate accepted values. */
static char *ngx_http_markdown_on_error(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
/* Parse markdown_flavor enum for converter output dialect selection. */
static char *ngx_http_markdown_flavor(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
/* Parse authentication policy controlling conversion on authenticated traffic. */
static char *ngx_http_markdown_auth_policy(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
/* Parse configured auth-cookie patterns used by auth detection. */
static char *ngx_http_markdown_auth_cookies(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
/* Parse conditional-requests mode (off/weak/strong semantics). */
static char *ngx_http_markdown_conditional_requests(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
/* Parse module log verbosity mapping to nginx log levels. */
static char *ngx_http_markdown_log_verbosity(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
/* Parse streaming content types excluded from buffering/conversion. */
static char *ngx_http_markdown_stream_types(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
/* Parse metrics endpoint enablement and URI settings. */
static char *ngx_http_markdown_metrics_directive(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
/* Map module verbosity enum to nginx native log level constants. */
static ngx_uint_t ngx_http_markdown_log_verbosity_to_ngx_level(ngx_uint_t verbosity);
/* Render stable symbolic names for config/diagnostic logging. */
static const ngx_str_t *ngx_http_markdown_on_error_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_flavor_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_auth_policy_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_conditional_requests_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_log_verbosity_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_compression_name(
    ngx_http_markdown_compression_type_e compression_type);
static const ngx_str_t *ngx_http_markdown_enabled_source_name(ngx_uint_t value);
/* ASCII-only whitespace helper used in directive parser hot paths. */
static ngx_uint_t ngx_http_markdown_is_ascii_space(u_char ch);
/* Parse markdown_filter boolean token and reject malformed values. */
static ngx_int_t ngx_http_markdown_parse_filter_flag(ngx_str_t *value, ngx_flag_t *enabled);
/* Resolve runtime markdown_filter state for the current request. */
ngx_flag_t ngx_http_markdown_is_enabled(ngx_http_request_t *r,
    ngx_http_markdown_conf_t *conf);
/* Emit merged-config summary for observability and debugging. */
static void ngx_http_markdown_log_merged_conf(ngx_conf_t *cf,
    ngx_http_markdown_conf_t *conf);
