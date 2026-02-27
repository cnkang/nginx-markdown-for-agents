# NGINX Markdown Filter Module Configuration Structure

## Overview

This document describes the configuration structure used by the NGINX Markdown filter module.

## Configuration Structure

The `ngx_http_markdown_conf_t` structure holds all configuration directives for the Markdown filter module. It supports NGINX's configuration inheritance model (http, server, location contexts).

### Structure Definition

```c
typedef struct {
    ngx_flag_t   enabled;              /* markdown_filter on|off */
    size_t       max_size;             /* markdown_max_size (default: 10MB) */
    ngx_msec_t   timeout;              /* markdown_timeout (default: 5000ms) */
    ngx_uint_t   on_error;             /* markdown_on_error pass|reject (default: pass) */
    ngx_uint_t   flavor;               /* markdown_flavor commonmark|gfm (default: commonmark) */
    ngx_flag_t   token_estimate;       /* markdown_token_estimate on|off (default: off) */
    ngx_flag_t   front_matter;         /* markdown_front_matter on|off (default: off) */
    ngx_flag_t   on_wildcard;          /* markdown_on_wildcard on|off (default: off) */
    ngx_uint_t   auth_policy;          /* markdown_auth_policy allow|deny (default: allow) */
    ngx_array_t *auth_cookies;         /* markdown_auth_cookies patterns (default: NULL) */
    ngx_flag_t   generate_etag;        /* markdown_etag on|off (default: on) */
    ngx_uint_t   conditional_requests; /* markdown_conditional_requests mode (default: full_support) */
    ngx_flag_t   buffer_chunked;       /* markdown_buffer_chunked on|off (default: on) */
    ngx_array_t *stream_types;         /* markdown_stream_types exclusion list (default: NULL) */
} ngx_http_markdown_conf_t;
```

## Configuration Fields

### Core Settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `ngx_flag_t` | `0` (off) | Enable/disable Markdown conversion |
| `max_size` | `size_t` | `10MB` | Maximum response size to convert |
| `timeout` | `ngx_msec_t` | `5000ms` | Conversion timeout |

### Failure Handling

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `on_error` | `ngx_uint_t` | `PASS` | Failure strategy: pass (fail-open) or reject (fail-closed) |

### Markdown Generation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `flavor` | `ngx_uint_t` | `COMMONMARK` | Markdown flavor: CommonMark or GFM |
| `token_estimate` | `ngx_flag_t` | `0` (off) | Include X-Markdown-Tokens header |
| `front_matter` | `ngx_flag_t` | `0` (off) | Include YAML front matter |

### Content Negotiation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `on_wildcard` | `ngx_flag_t` | `0` (off) | Convert on Accept: */* |

### Authentication & Security

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `auth_policy` | `ngx_uint_t` | `ALLOW` | Convert authenticated requests: allow or deny |
| `auth_cookies` | `ngx_array_t*` | `NULL` | Cookie name patterns for authentication detection |

### HTTP Semantics

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `generate_etag` | `ngx_flag_t` | `1` (on) | Generate ETags for Markdown variants |
| `conditional_requests` | `ngx_uint_t` | `FULL_SUPPORT` | Conditional request mode: full_support, if_modified_since_only, or disabled |

### Streaming & Buffering

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `buffer_chunked` | `ngx_flag_t` | `1` (on) | Buffer and convert chunked responses |
| `stream_types` | `ngx_array_t*` | `NULL` | Content types to exclude from conversion (e.g., text/event-stream) |

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
- **auth_policy**: `ALLOW` - Allow authenticated content conversion
- **auth_cookies**: `NULL` - No patterns configured
- **generate_etag**: `1` (on) - Enable proper caching
- **conditional_requests**: `FULL_SUPPORT` - Full HTTP semantics
- **buffer_chunked**: `1` (on) - Handle chunked responses
- **stream_types**: `NULL` - No exclusions by default

## Requirements Satisfied

This implementation satisfies the following requirements:

- **FR-12.1**: Configuration directives for all module features
- **FR-12.2**: Secure defaults for all configuration parameters
- **FR-12.6**: Configuration inheritance and override support

## Next Steps

Task 12.2 will implement the directive handlers that parse and validate these configuration values from the NGINX configuration file.

## Files Modified

- `components/nginx-module/src/ngx_http_markdown_filter_module.h` - Configuration structure and constants
- `components/nginx-module/src/ngx_http_markdown_filter_module.c` - Configuration creation and merging functions
