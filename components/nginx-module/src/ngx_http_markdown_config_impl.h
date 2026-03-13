/*
 * Configuration implementation wiring.
 *
 * Kept as a small aggregator so the main module file can include one config
 * entrypoint while directive registry, directive handlers, and config core
 * helpers evolve independently.
 */

/* Configuration-unit forward declarations. */
static void *ngx_http_markdown_create_main_conf(ngx_conf_t *cf);
static void *ngx_http_markdown_create_conf(ngx_conf_t *cf);
static char *ngx_http_markdown_merge_conf(ngx_conf_t *cf, void *parent, void *child);
static ngx_int_t ngx_http_markdown_init_metrics_zone(ngx_shm_zone_t *shm_zone, void *data);
static char *ngx_http_markdown_filter(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_on_error(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_flavor(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_auth_policy(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_auth_cookies(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_conditional_requests(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_log_verbosity(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_stream_types(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_metrics_directive(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static ngx_uint_t ngx_http_markdown_log_verbosity_to_ngx_level(ngx_uint_t verbosity);
static const ngx_str_t *ngx_http_markdown_on_error_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_flavor_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_auth_policy_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_conditional_requests_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_log_verbosity_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_compression_name(
    ngx_http_markdown_compression_type_e compression_type);
static const ngx_str_t *ngx_http_markdown_enabled_source_name(ngx_uint_t value);
static ngx_uint_t ngx_http_markdown_is_ascii_space(u_char ch);
static ngx_int_t ngx_http_markdown_parse_filter_flag(ngx_str_t *value, ngx_flag_t *enabled);
ngx_flag_t ngx_http_markdown_is_enabled(ngx_http_request_t *r,
    ngx_http_markdown_conf_t *conf);
static void ngx_http_markdown_log_merged_conf(ngx_conf_t *cf,
    ngx_http_markdown_conf_t *conf);

#include "ngx_http_markdown_config_core_impl.h"

#include "ngx_http_markdown_config_handlers_impl.h"

#include "ngx_http_markdown_config_directives_impl.h"
