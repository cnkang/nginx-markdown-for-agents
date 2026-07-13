# NGINX Markdown Filter Module Configuration Structure

## Overview

This document describes the configuration structure used by the NGINX Markdown filter module.

It is an implementation reference for the merged configuration object in code. For public directive syntax and examples, use `docs/guides/CONFIGURATION.md`.

## Configuration Structure

The `ngx_http_markdown_conf_t` structure holds all configuration directives for the Markdown filter module. It supports NGINX's configuration inheritance model (http, server, location contexts).

### Structure Definition

```c
typedef struct {
    ngx_flag_t   enabled;              /* markdown_filter on|off */
    ngx_uint_t   enabled_source;       /* markdown_filter source: static/complex/unset */
    ngx_http_complex_value_t *enabled_complex; /* markdown_filter variable expression */
    size_t       max_size;             /* markdown_limits memory (default: 10MB) */
    ngx_msec_t   timeout;              /* markdown_limits timeout (default: 5000ms) */
    ngx_uint_t   on_error;             /* markdown_error_policy pass|fail_closed|status (default: pass) */
    ngx_uint_t   error_status;         /* markdown_error_policy status <code> (default: 502) */
    ngx_uint_t   flavor;               /* markdown_flavor commonmark|gfm (default: commonmark) */
    ngx_flag_t   token_estimate;       /* markdown_token_estimate on|off (default: off) */
    ngx_flag_t   front_matter;         /* markdown_front_matter on|off (default: off) */
    ngx_uint_t   accept_policy;        /* markdown_accept strict|wildcard|force (default: strict) */
    ngx_http_markdown_policy_cfg_t policy;  /* auth_policy, auth_cookies */
    ngx_flag_t   buffer_chunked;       /* markdown_buffer_chunked on|off (default: on) */

    struct {
        ngx_array_t *stream_types;         /* markdown_stream_types exclusion list */
        ngx_array_t *content_types;        /* markdown_content_types allowlist */
        size_t       large_body_threshold; /* markdown_large_body_threshold (retired in 0.9.0) */
        ngx_uint_t   max_inflight;         /* markdown_limits max_inflight */
    } routing;

    struct {
        ngx_flag_t   auto_decompress;      /* internal decompression toggle (default: on) */
        size_t       max_size;             /* markdown_decompress_max_size */
        ngx_msec_t   parse_timeout;        /* markdown_parse_timeout (default: 30000ms) */
        size_t       parser_budget;        /* markdown_parser_budget (default: 64MB) */
        ngx_flag_t   max_size_explicit;    /* 1 if operator set markdown_limits memory */
    } decompress;

    /* ... streaming, ops, metrics sub-structs ... */
} ngx_http_markdown_conf_t;
```

## Configuration Fields

### Core Settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `ngx_flag_t` | `0` (off) | Static fallback for Markdown conversion |
| `enabled_source` | `ngx_uint_t` | `STATIC` (after merge) | Tracks whether markdown_filter is static or variable-driven |
| `enabled_complex` | `ngx_http_complex_value_t*` | `NULL` | Optional compiled variable/complex expression for per-request toggle |
| `max_size` | `size_t` | `10MB` | Maximum response size to convert |
| `timeout` | `ngx_msec_t` | `5000ms` | Conversion timeout |

### Failure Handling

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `on_error` | `ngx_uint_t` | `PASS` | Failure strategy: pass (fail-open) or fail_closed (fail-closed) |

### Markdown Generation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `flavor` | `ngx_uint_t` | `COMMONMARK` | Markdown flavor: CommonMark or GFM |
| `token_estimate` | `ngx_flag_t` | `0` (off) | Include X-Markdown-Tokens header |
| `front_matter` | `ngx_flag_t` | `0` (off) | Include YAML front matter |

### Content Negotiation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `accept_mode` | `ngx_uint_t` | `strict` | Accept negotiation: strict (text/markdown only) or wildcard (also text/*, */*) |

### Authentication & Security

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `auth_policy` | `ngx_uint_t` | `ALLOW` | Convert authenticated requests: allow or deny |
| `auth_cookies` | `ngx_array_t*` | `NULL` | Cookie name patterns for authentication detection |

### HTTP Semantics

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `generate_etag` | `ngx_flag_t` | `1` (on) | Generate ETags for Markdown responses |
| `conditional_requests` | `ngx_uint_t` | `FULL_SUPPORT` | Conditional request mode: full_support, if_modified_since_only, or disabled |

### Observability

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `log_verbosity` | `ngx_uint_t` | `INFO` | Module-local log threshold for module-generated messages |

### Streaming & Buffering

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `buffer_chunked` | `ngx_flag_t` | `1` (on) | Buffer and convert chunked responses |
| `stream_types` | `ngx_array_t*` | `NULL` | Content types to exclude from conversion (e.g., text/event-stream) |

### Internal Runtime Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `auto_decompress` | `ngx_flag_t` | `1` (on) | Internal merged flag used by the decompression path |

## Configuration Constants

### on_error Values

```c
#define NGX_HTTP_MARKDOWN_ON_ERROR_PASS    0  /* fail-open: return original HTML */
#define NGX_HTTP_MARKDOWN_ON_ERROR_REJECT  1  /* fail-closed: return 502 error */
```

### flavor Values

```c
#define NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK  0  /* CommonMark flavor */
#define NGX_HTTP_MARKDOWN_FLAVOR_GFM         1  /* GitHub Flavored Markdown */
```

### auth_policy Values

```c
#define NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW  0  /* Allow conversion of authenticated requests */
#define NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY   1  /* Deny conversion of authenticated requests */
```

### conditional_requests Values

```c
#define NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT         0  /* Full If-None-Match support */
#define NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE    1  /* If-Modified-Since only */
#define NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED             2  /* No conditional request support */
```

### log_verbosity Values

```c
#define NGX_HTTP_MARKDOWN_LOG_ERROR  0  /* Only error/critical */
#define NGX_HTTP_MARKDOWN_LOG_WARN   1  /* Warnings and above */
#define NGX_HTTP_MARKDOWN_LOG_INFO   2  /* Informational and above */
#define NGX_HTTP_MARKDOWN_LOG_DEBUG  3  /* Debug and above */
```

## Configuration Lifecycle

### Creation (`ngx_http_markdown_create_conf`)

1. Allocates configuration structure from NGINX memory pool
2. Initializes all fields to `NGX_CONF_UNSET*` values
3. Enables proper configuration inheritance

### Merging (`ngx_http_markdown_merge_conf`)

1. Implements inheritance: location > server > http
2. Child values override parent values if set
3. Unset child values inherit from parent
4. Applies default values when both parent and child are unset

## Default Values

All default values are applied during configuration merging:

- **enabled**: `0` (off) - Module disabled by default
- **max_size**: `10MB` - Reasonable limit for most web pages
- **timeout**: `5000ms` (5 seconds) - Prevents long-running conversions
- **on_error**: `PASS` (fail-open) - Maintains availability on errors
- **flavor**: `COMMONMARK` - Well-specified baseline
- **token_estimate**: `0` (off) - Optional feature
- **front_matter**: `0` (off) - Optional feature
- **on_wildcard**: `0` (off) - Conservative default
- **auth_policy**: `ALLOW` - Allow conversion of authenticated requests
- **auth_cookies**: `NULL` - No patterns configured
- **generate_etag**: `1` (on) - Enable proper caching
- **conditional_requests**: `FULL_SUPPORT` - Full HTTP semantics
- **log_verbosity**: `INFO` - Informational module logs by default
- **buffer_chunked**: `1` (on) - Handle chunked responses
- **stream_types**: `NULL` - No exclusions by default
- **auto_decompress**: `1` (on) - Internal decompression path enabled by default

## Requirements Satisfied

This implementation satisfies the following requirements:

- **FR-12.1**: Configuration directives for all module features
- **FR-12.2**: Secure defaults for all configuration parameters
- **FR-12.6**: Configuration inheritance and override support

## Notes

- `auto_decompress` is part of the merged runtime configuration object, but public docs currently describe decompression as built-in behavior rather than as a standalone documented directive.
- This page should track the current structure in `components/nginx-module/src/ngx_http_markdown_filter_module.h` rather than older task-plan wording.

## Files Modified

- `components/nginx-module/src/ngx_http_markdown_filter_module.h` - Configuration structure and constants
- `components/nginx-module/src/ngx_http_markdown_filter_module.c` - Configuration creation and merging functions


## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
