# NGINX Markdown Filter Module - Configuration Guide

## Table of Contents

1. [Overview](#overview)
2. [Configuration Directives Reference](#configuration-directives-reference)
3. [Configuration Examples](#configuration-examples)
4. [Configuration Inheritance](#configuration-inheritance)
5. [Security Best Practices](#security-best-practices)
6. [Performance Tuning](#performance-tuning)
7. [Configuration Templates](#configuration-templates)

---

## Overview

The NGINX Markdown filter module provides fine-grained control over HTML-to-Markdown conversion through configuration directives. This guide documents the available directives, provides examples for common scenarios, and explains configuration inheritance rules.

### Maintenance and Scope Notes

- Directive names and accepted values in this guide are intended to match the module implementation in `components/nginx-module/src/ngx_http_markdown_filter_module.c`.
- Example configurations are operational templates, not guaranteed drop-in configs for every deployment.
- For build/runtime preparation steps, use `BUILD_INSTRUCTIONS.md` and `INSTALLATION.md`.
- Metrics endpoint examples use `/markdown-metrics` as a placeholder path. Use your configured `location` path if different.
- For “which directive changes which runtime branch,” use [../architecture/CONFIG_BEHAVIOR_MAP.md](../architecture/CONFIG_BEHAVIOR_MAP.md) instead of duplicating that rationale here.

### Configuration Contexts

Directives can be used in different NGINX configuration contexts:

- **http**: Global settings for all servers
- **server**: Settings for a specific virtual host
- **location**: Settings for a specific URL path

Configuration follows NGINX's standard inheritance model where inner scopes inherit from outer scopes and can override specific settings.

---

## Configuration Directives Reference

### Core Directives

#### markdown_filter

**Syntax:** `markdown_filter on | off | $variable;`  
**Default:** `off`  
**Context:** http, server, location

Enables or disables Markdown conversion for the current context.
You can also use an NGINX variable/complex value for per-request control.
Resolved values accept `1/0`, `on/off`, `true/false`, `yes/no` (case-insensitive).

**Example:**
```nginx
location /docs {
    markdown_filter on;
}
```

**Dynamic Example:**
```nginx
map $http_user_agent $markdown_enabled {
    default 1;
    "~*curl" 0;
}

location /docs {
    markdown_filter $markdown_enabled;
}
```

**Bot-Targeted Example:**

To enable Markdown conversion only for specific AI bots, combine a variable-driven `markdown_filter` with an Accept header override so the module's content negotiation sees `text/markdown`:

```nginx
map $http_user_agent $is_ai_bot {
    default         0;
    "~*ClaudeBot"   1;
    "~*GPTBot"      1;
}

map $http_user_agent $bot_accept_override {
    default         "";
    "~*ClaudeBot"   "text/markdown, text/html;q=0.9";
    "~*GPTBot"      "text/markdown, text/html;q=0.9";
}

map $bot_accept_override $final_accept {
    ""      $http_accept;
    default $bot_accept_override;
}

location /docs {
    markdown_filter $is_ai_bot;
    proxy_set_header Accept $final_accept;
    proxy_pass http://backend;
}
```

See [DEPLOYMENT_EXAMPLES.md](DEPLOYMENT_EXAMPLES.md#bot-targeted-conversion-user-agent-based) for a complete walkthrough.

**Best Practices for Variable-Driven `markdown_filter`:**
- Prefer regex matching for `Accept` header maps because real clients often send comma-separated values and q-factors.
- Prefer `$uri` (normalized path without query string) over `$request_uri` when matching file extensions.
- If your variable map enables conversion for `Accept: text/*` or `Accept: */*`, also set `markdown_on_wildcard on;`.

---

#### markdown_max_size

**Syntax:** `markdown_max_size <size>;`  
**Default:** `10m`  
**Context:** http, server, location

Maximum response size to attempt conversion. Responses larger than this limit will not be converted (fail-open behavior).

**Valid Units:** `k` (kilobytes), `m` (megabytes)

**Example:**
```nginx
# Limit to 5 megabytes
markdown_max_size 5m;

# Limit to 512 kilobytes
markdown_max_size 512k;
```


#### markdown_timeout

**Syntax:** `markdown_timeout <time>;`  
**Default:** `5s`  
**Context:** http, server, location

Maximum time to spend on conversion. Conversions exceeding this timeout will be aborted and the configured failure strategy will be applied.

**Valid Units:** `ms` (milliseconds), `s` (seconds), `m` (minutes)

**Example:**
```nginx
# 3 second timeout
markdown_timeout 3s;

# 500 millisecond timeout
markdown_timeout 500ms;
```

---

#### markdown_on_error

**Syntax:** `markdown_on_error pass | reject;`  
**Default:** `pass`  
**Context:** http, server, location

Failure strategy when conversion fails:
- `pass`: Return original HTML (fail-open, recommended for production)
- `reject`: Return 502 Bad Gateway (fail-closed)

**Example:**
```nginx
# Fail-open (default, recommended)
markdown_on_error pass;

# Fail-closed (strict mode)
markdown_on_error reject;
```

---

#### markdown_flavor

**Syntax:** `markdown_flavor commonmark | gfm;`  
**Default:** `commonmark`  
**Context:** http, server, location

Markdown flavor to generate:
- `commonmark`: CommonMark specification (standard)
- `gfm`: GitHub Flavored Markdown (includes tables, strikethrough, task lists)

**Example:**
```nginx
# Use GitHub Flavored Markdown for better table support
markdown_flavor gfm;
```

---

### Agent-Friendly Extensions

#### markdown_token_estimate

**Syntax:** `markdown_token_estimate on | off;`  
**Default:** `off`  
**Context:** http, server, location

Include `X-Markdown-Tokens` response header with estimated token count. Useful for AI agents to manage context windows.

**Example:**
```nginx
markdown_token_estimate on;
```

**Response Header:**
```
X-Markdown-Tokens: 1250
```

---

#### markdown_front_matter

**Syntax:** `markdown_front_matter on | off;`  
**Default:** `off`  
**Context:** http, server, location

Include YAML front matter with metadata (title, description, URL, etc.) at the beginning of Markdown output.

**Example:**
```nginx
markdown_front_matter on;
```

**Output:**
```markdown
---
title: "Page Title"
description: "Page description"
url: "https://example.com/page"
---

# Page Content
...
```


### Content Negotiation

#### markdown_on_wildcard

**Syntax:** `markdown_on_wildcard on | off;`  
**Default:** `off`  
**Context:** http, server, location

Convert when Accept header contains wildcards (`*/*` or `text/*`). By default, only explicit `text/markdown` triggers conversion.

**Example:**
```nginx
# Enable conversion for wildcard Accept headers
markdown_on_wildcard on;
```

**Behavior:**
- `off` (default): Only `Accept: text/markdown` triggers conversion
- `on`: `Accept: */*` or `Accept: text/*` also triggers conversion

---

### Authentication and Security

#### markdown_auth_policy

**Syntax:** `markdown_auth_policy allow | deny;`  
**Default:** `allow`  
**Context:** http, server, location

Policy for converting authenticated requests:
- `allow`: Convert authenticated requests (adds `Cache-Control: private`)
- `deny`: Skip conversion for authenticated requests

**Example:**
```nginx
# Deny conversion for authenticated requests
markdown_auth_policy deny;
```

**Authentication Detection:**
- Presence of `Authorization` header
- Presence of configured authentication cookies

---

#### markdown_auth_cookies

**Syntax:** `markdown_auth_cookies <pattern> [<pattern> ...];`  
**Default:** none  
**Context:** http, server, location

Cookie name patterns to identify authenticated requests. Supports exact match, prefix match (pattern*), and wildcards.

**Example:**
```nginx
# Detect common authentication cookies
markdown_auth_cookies session* auth_token PHPSESSID wordpress_logged_in_*;
```

**Pattern Matching:**
- `session*`: Matches any cookie starting with "session"
- `PHPSESSID`: Exact match
- `wordpress_logged_in_*`: Matches WordPress authentication cookies

---

### Caching and Conditional Requests

#### markdown_etag

**Syntax:** `markdown_etag on | off;`  
**Default:** `on`  
**Context:** http, server, location

Generate ETag header for Markdown variants. ETags are computed from the Markdown output for proper caching.

**Example:**
```nginx
# Disable ETag generation
markdown_etag off;
```

**Response Header:**
```
ETag: "a1b2c3d4e5f6"
```

---

#### markdown_conditional_requests

**Syntax:** `markdown_conditional_requests full_support | if_modified_since_only | disabled;`  
**Default:** `full_support`  
**Context:** http, server, location

Conditional request support mode:
- `full_support`: Support Markdown-variant `If-None-Match` (ETag) and preserve compatibility with upstream `Last-Modified` / `If-Modified-Since`
- `if_modified_since_only`: Skip module-side `If-None-Match` processing (performance optimization); `If-Modified-Since` remains handled by standard NGINX conditional request processing
- `disabled`: No conditional request support for Markdown variants

**Example:**
```nginx
# Performance optimization: only support If-Modified-Since
markdown_conditional_requests if_modified_since_only;
```

**Performance Note:** `full_support` requires conversion to generate a Markdown-variant ETag for comparison, which has performance implications.

**Implementation Note (Current Design):** `If-Modified-Since` evaluation is delegated to NGINX core (using preserved upstream `Last-Modified` semantics). The module focuses on Markdown-variant ETag handling.

---

#### markdown_log_verbosity

**Syntax:** `markdown_log_verbosity error | warn | info | debug;`  
**Default:** `info`  
**Context:** http, server, location

Sets the module-local verbosity threshold for module-generated logs. This is an additional filter for module messages.

- `error`: Emit only error/critical module logs
- `warn`: Emit warnings and errors
- `info`: Emit informational, warning, and error logs (default)
- `debug`: Emit all module logs, including debug-level configuration logs

**Important:** NGINX's global `error_log` level still applies. A message is emitted only if it passes both the module verbosity filter and the NGINX log level.

**Example:**
```nginx
# Reduce module log volume in production
markdown_log_verbosity warn;
```


### Transfer Encoding

#### markdown_buffer_chunked

**Syntax:** `markdown_buffer_chunked on | off;`  
**Default:** `on`  
**Context:** http, server, location

Buffer and convert chunked Transfer-Encoding responses. When off, chunked responses are passed through without conversion.

**Example:**
```nginx
# Disable buffering of chunked responses
markdown_buffer_chunked off;
```

**Behavior:**
- `on` (default): Buffer all chunks and convert complete response
- `off`: Pass through chunked responses without conversion

---

#### markdown_stream_types

**Syntax:** `markdown_stream_types <type> [<type> ...];`  
**Default:** none  
**Context:** http, server, location

Content types to exclude from conversion (streaming responses). These content types will never be converted, even if eligible.

**Example:**
```nginx
# Exclude Server-Sent Events and NDJSON streams
markdown_stream_types text/event-stream application/x-ndjson;
```

**Common Streaming Types:**
- `text/event-stream`: Server-Sent Events
- `application/x-ndjson`: Newline-delimited JSON
- `application/stream+json`: JSON streams

---

### Security Directives

#### markdown_trust_forwarded_headers

**Syntax:** `markdown_trust_forwarded_headers on | off;`  
**Default:** `off`  
**Context:** http, server, location

Controls whether `X-Forwarded-Proto` and `X-Forwarded-Host` request headers are used for base URL construction in the Markdown output.

When `off` (the default), only the NGINX request schema and server header are used. This prevents client-supplied header injection that could redirect all relative URLs in the Markdown output to an attacker-controlled domain.

**Example:**
```nginx
# Only enable behind a trusted reverse proxy that sets/overwrites
# X-Forwarded-Proto and X-Forwarded-Host from untrusted clients
markdown_trust_forwarded_headers on;
```

**Security Note:** Enable this directive only when NGINX sits behind a trusted reverse proxy. The proxy must strip or overwrite `X-Forwarded-*` headers from untrusted clients. Without this protection, a direct client can send `X-Forwarded-Host: evil.com` to poison all relative links in the Markdown output.

---

### Streaming Directives (0.5.0+)

These directives control the streaming conversion path introduced in 0.5.0. They
are only effective when `markdown_streaming_engine` is enabled. When
`markdown_streaming_engine off` (the default), all streaming directives are ignored
and behavior is identical to 0.4.0.

#### markdown_streaming_on_error

**Syntax:** `markdown_streaming_on_error pass | reject;`
**Default:** `pass`
**Context:** http, server, location

Controls the failure behavior during the streaming Pre-Commit Phase — the window
between when streaming conversion starts and when the first Markdown chunk is sent
to the client.

- `pass`: Fail-open. Abort the streaming conversion and serve the original HTML
  response to the client. The client is unaware that streaming was attempted.
- `reject`: Fail-closed. Abort the streaming conversion and return an HTTP error
  to the client.

This directive has no effect on Post-Commit Phase failures. Once the first Markdown
chunk has been sent to the client (the Commit Boundary has been crossed), failures
always result in fail-closed behavior: the response is truncated by sending an empty
`last_buf`. This is not configurable because the response headers (including
`Content-Type: text/markdown`) have already been sent and cannot be changed.

**Example:**
```nginx
# Fail-open (default, recommended for production)
markdown_streaming_on_error pass;

# Fail-closed (strict mode)
markdown_streaming_on_error reject;
```

#### Relationship Between `markdown_on_error` and `markdown_streaming_on_error`

These two directives have completely independent scopes. Changing one never affects
the behavior of the other.

| Directive | Scope | Controls |
|-----------|-------|----------|
| `markdown_on_error` | Full-buffer path and incremental path | Failure behavior when full-buffer conversion fails |
| `markdown_streaming_on_error` | Streaming path, Pre-Commit Phase only | Failure behavior when streaming pre-commit conversion fails |
| *(not configurable)* | Streaming path, Post-Commit Phase | Always fail-closed (response truncated) |

Key points for operators:

- You can set `markdown_on_error reject` and `markdown_streaming_on_error pass`
  (or any other combination) without conflict.
- When `markdown_streaming_engine off` (the default), `markdown_streaming_on_error`
  is ignored entirely. All failure handling follows `markdown_on_error`.
- Neither directive controls the `ERROR_STREAMING_FALLBACK` signal. When the Rust
  engine determines that a capability requires full-buffer processing (e.g., table
  conversion), the fallback to full-buffer always executes regardless of either
  directive's value.

**Configuration Examples:**

```nginx
# Example 1: Production — fail-open everywhere
# Both full-buffer and streaming failures serve original HTML
markdown_on_error pass;
markdown_streaming_on_error pass;
```

```nginx
# Example 2: Strict mode — fail-closed everywhere
# Both full-buffer and streaming failures return errors
markdown_on_error reject;
markdown_streaming_on_error reject;
```

```nginx
# Example 3: Mixed — strict full-buffer, lenient streaming
# Full-buffer failures return errors; streaming pre-commit failures
# serve original HTML (streaming is newer, so be more lenient)
markdown_on_error reject;
markdown_streaming_on_error pass;
```

```nginx
# Example 4: Full streaming setup with independent error policies
http {
    markdown_filter on;
    markdown_streaming_engine on;
    markdown_on_error pass;
    markdown_streaming_on_error pass;

    server {
        listen 80;
        server_name docs.example.com;

        location /api-docs {
            # Strict mode for API documentation
            markdown_on_error reject;
            markdown_streaming_on_error reject;
            proxy_pass http://backend;
        }

        location /blog {
            # Lenient mode for blog content
            markdown_on_error pass;
            markdown_streaming_on_error pass;
            proxy_pass http://backend;
        }
    }
}
```

**Monitoring streaming failures:**

Use the metrics endpoint to monitor streaming-specific failure counters:

| Metric | Meaning |
|--------|---------|
| `streaming_precommit_failopen_total` | Pre-commit failures handled by `pass` (fail-open) |
| `streaming_precommit_reject_total` | Pre-commit failures handled by `reject` (fail-closed) |
| `streaming_postcommit_error_total` | Post-commit failures (always fail-closed) |
| `streaming_fallback_total` | Capability fallbacks to full-buffer (always executes) |

If `streaming_precommit_failopen_total` or `streaming_precommit_reject_total` is
growing, investigate the NGINX error log for the specific error types triggering
the failures.

---

### Large Response Processing

#### markdown_large_body_threshold

**Syntax:** `markdown_large_body_threshold off | <size>;`  
**Default:** `off`  
**Context:** http, server, location

Routes responses whose body size is at or above the configured threshold to the incremental processing path. When set to `off` (the default), all responses use the existing full-buffer path and behavior is identical to a build without this feature.

**Valid Units:** `k` (kilobytes), `m` (megabytes)

**Example:**
```nginx
# Route responses >= 512KB to incremental path
markdown_large_body_threshold 512k;

# Disable incremental path (default)
markdown_large_body_threshold off;
```

**Behavior:**
- `off` (default): All responses use the full-buffer conversion path
- `<size>`: Responses at or above the threshold use the incremental conversion path

**Notes:**
- The incremental path requires the Rust converter to be built with the `incremental` feature (`cargo build --release --features incremental`). If the feature is not compiled but a threshold is configured, the module logs a warning and falls back to the full-buffer path.
- HEAD requests, 304 responses, and fail-open replays always use the full-buffer path regardless of the threshold setting.
- Path selection is based on `Content-Length` when available; for chunked responses without `Content-Length`, the module buffers first and evaluates the threshold against the buffered size.
- Path hit counters (`fullbuffer_path_hits`, `incremental_path_hits`) are exposed through the `markdown_metrics` endpoint.
- **Hard Limit**: The Rust incremental converter enforces a strict 64 MiB (`64 * 1024 * 1024` bytes) maximum buffer limit. Responses exceeding this size will trigger a `MemoryLimit` error, resulting in the `markdown_on_error` policy being applied.


For the design rationale and rollout guidance, see [LARGE_RESPONSE_DESIGN.md](../architecture/LARGE_RESPONSE_DESIGN.md) and [LARGE_RESPONSE_ROLLOUT.md](LARGE_RESPONSE_ROLLOUT.md).

---

### Metrics and Monitoring

#### markdown_metrics

**Syntax:** `markdown_metrics;`  
**Default:** none  
**Context:** location only

Enables a metrics endpoint at this location. The endpoint returns either a human-readable plain-text report or a JSON object, depending on the request `Accept` header.

The endpoint reads shared-memory counters, so values are aggregated across workers rather than scoped to the worker that handled the metrics request.

**Security:** The module handler enforces localhost-only access (`127.0.0.1`, `::1`). NGINX `allow`/`deny` rules can further restrict access, but they do not broaden access beyond localhost.

**Example:**
```nginx
location /markdown-metrics {
    markdown_metrics;
    
    # Restrict access to localhost only
    allow 127.0.0.1;
    allow ::1;
    deny all;
}
```

**Response Formats:**
- Plain text: `Accept: text/plain` or no Accept header
- JSON: `Accept: application/json`

**JSON Fields Exposed (current implementation):**
- `conversions_attempted`: Total conversion attempts
- `conversions_succeeded`: Successful conversions
- `conversions_failed`: Failed conversions
- `conversions_bypassed`: Requests bypassed (ineligible/passthrough)
- `conversion_completed`: Completed conversions (`conversions_succeeded + conversions_failed`)
- `failures_conversion`: Conversion failures
- `failures_resource_limit`: Resource-limit failures (size/timeout)
- `failures_system`: System failures
- `conversion_time_sum_ms`: Total conversion time (sum)
- `conversion_time_avg_ms`: Average conversion time across completed conversions
- `input_bytes`: Total input bytes processed
- `input_bytes_avg`: Average input bytes across successful conversions
- `output_bytes`: Total output bytes generated
- `output_bytes_avg`: Average output bytes across successful conversions
- `conversion_latency_buckets`: Bucketed counts for `<=10ms`, `<=100ms`, `<=1000ms`, and `>1000ms`
- `decompressions_attempted`: Responses that required decompression before conversion
- `decompressions_succeeded`: Successful decompressions
- `decompressions_failed`: Failed decompressions
- `decompressions_gzip`: Successful gzip decompressions
- `decompressions_deflate`: Successful deflate decompressions
- `decompressions_brotli`: Successful brotli decompressions
- `fullbuffer_path_hits`: Requests routed to the full-buffer conversion path
- `incremental_path_hits`: Requests routed to the incremental conversion path

---

## Configuration Examples

### Basic Setup

Enable conversion globally for all content:

```nginx
http {
    # Enable markdown filter globally
    markdown_filter on;
    
    # Configure resource limits
    markdown_max_size 10m;
    markdown_timeout 5s;
    
    # Use fail-open strategy (recommended)
    markdown_on_error pass;
    
    server {
        listen 80;
        server_name example.com;
        
        location / {
            proxy_pass http://backend;
        }
    }
}
```


### Selective Conversion

Enable conversion only for specific paths:

```nginx
http {
    # Disable globally
    markdown_filter off;
    
    server {
        listen 80;
        server_name example.com;
        
        # Enable for documentation
        location /docs {
            markdown_filter on;
            markdown_flavor gfm;  # Use GFM for better table support
            markdown_front_matter on;
            markdown_token_estimate on;
            proxy_pass http://backend;
        }
        
        # Enable for blog content
        location /blog {
            markdown_filter on;
            markdown_max_size 5m;  # Smaller limit for blog posts
            proxy_pass http://backend;
        }
        
        # Disable for API endpoints
        location /api {
            markdown_filter off;
            proxy_pass http://backend;
        }
        
        # Disable for static assets
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg)$ {
            markdown_filter off;
            proxy_pass http://backend;
        }
    }
}
```

---

### Variable-Driven Conversion (Accept + Path Aware)

Use `markdown_filter` with NGINX variables when you want conversion only for specific request patterns (for example, `.html` pages requested as text/markdown or text wildcard clients).

```nginx
# Parse Accept robustly (supports multiple media types and q-values)
map $http_accept $markdown_accept {
    default 0;
    "~*(^|,)\\s*text/markdown(\\s*;|,|$)" 1;
    "~*(^|,)\\s*text/\\*(\\s*;|,|$)" 1;
}

# Match path on normalized URI (query string excluded)
map $uri $convert_html {
    default 0;
    "~*\\.html$" $markdown_accept;
}

server {
    listen 80;
    server_name example.com;

    location / {
        proxy_pass http://backend;
        markdown_on_wildcard on;
        markdown_filter $convert_html;
    }
}
```

**Why this pattern is recommended:**
- `map $http_accept` with exact string values is brittle; many clients send `Accept` with multiple values.
- `$request_uri` includes query strings (for example `/index.html?x=1`) and can break extension matching.
- `markdown_on_wildcard on` is required if you intentionally treat `text/*` as convertible.

---

### Upstream/CDN Compression Handling

The module automatically detects and decompresses upstream compressed content (`Content-Encoding: gzip`, `br`, `deflate`), so conversion works even when upstream/CDN forces compression.

**No special configuration needed** - the module handles this transparently.

**Optional optimization**: You can still conditionally disable upstream compression for Markdown requests to save decompression overhead:

```nginx
# Detect markdown requests (matches explicit text/markdown in Accept)
map $http_accept $markdown_requested {
    default 0;
    "~*(^|,)\\s*text/markdown(\\s*;|,|$)" 1;
}

# Preserve upstream compression for normal traffic; disable it for markdown requests only
map $markdown_requested $upstream_accept_encoding {
    0 $http_accept_encoding;
    1 "";
}

http {
    gzip on;
    gzip_types text/markdown text/plain text/css application/javascript application/json;

    markdown_filter on;
    markdown_on_error pass;

    server {
        listen 80;
        server_name example.com;

        location /api/ {
            markdown_filter off;
            proxy_pass http://backend;
        }

        location ~* \.md$ {
            markdown_filter off;
            proxy_pass http://backend;
        }

        location / {
            proxy_set_header Accept-Encoding $upstream_accept_encoding;
            proxy_pass http://backend;
        }
    }
}
```

**Performance note**: Automatic decompression adds minimal overhead (typically < 5ms). Disabling upstream compression for Markdown requests eliminates this overhead but requires additional configuration.

---

### Security Hardening

Configuration with enhanced controls for authenticated requests:

```nginx
http {
    markdown_filter on;
    
    # Deny conversion for authenticated requests
    markdown_auth_policy deny;
    
    # Detect authentication cookies
    markdown_auth_cookies session* auth_token PHPSESSID wordpress_logged_in_*;
    
    # Conservative resource limits
    markdown_max_size 5m;
    markdown_timeout 3s;
    
    # Fail-closed for critical applications
    markdown_on_error reject;
    
    # Exclude streaming content
    markdown_stream_types text/event-stream application/x-ndjson;
    
    server {
        listen 443 ssl;
        server_name secure.example.com;
        
        ssl_certificate /path/to/cert.pem;
        ssl_certificate_key /path/to/key.pem;
        
        location / {
            proxy_pass http://backend;
            
            # Add security headers
            add_header X-Content-Type-Options nosniff;
            add_header X-Frame-Options DENY;
        }
        
        # Metrics endpoint (localhost only)
        location /markdown-metrics {
            markdown_metrics;
            allow 127.0.0.1;
            allow ::1;
            deny all;
        }
    }
}
```

---

### Performance Tuning

Configuration optimized for high-performance scenarios:

```nginx
http {
    markdown_filter on;
    
    # Aggressive resource limits
    markdown_max_size 2m;
    markdown_timeout 1s;
    
    # Fail-open for availability
    markdown_on_error pass;
    
    # Disable optional features for performance
    markdown_token_estimate off;
    markdown_front_matter off;
    
    # Optimize conditional requests
    markdown_conditional_requests if_modified_since_only;
    
    # Use CommonMark (faster than GFM)
    markdown_flavor commonmark;
    
    # Disable chunked buffering for streaming
    markdown_buffer_chunked off;
    
    server {
        listen 80;
        server_name fast.example.com;
        
        # Increase worker connections
        worker_connections 2048;
        
        location / {
            proxy_pass http://backend;
            
            # Enable upstream caching
            proxy_cache my_cache;
            proxy_cache_valid 200 10m;
            proxy_cache_key "$scheme$request_method$host$request_uri$http_accept";
        }
    }
}
```


### Multi-Tenant Configuration

Configuration for multiple virtual hosts with different settings:

```nginx
http {
    # Global defaults
    markdown_filter on;
    markdown_max_size 10m;
    markdown_timeout 5s;
    markdown_on_error pass;
    
    # Tenant A: Public documentation site
    server {
        listen 80;
        server_name docs-a.example.com;
        
        location / {
            markdown_flavor gfm;
            markdown_front_matter on;
            markdown_token_estimate on;
            proxy_pass http://backend-a;
        }
    }
    
    # Tenant B: Internal wiki (authenticated)
    server {
        listen 80;
        server_name wiki-b.example.com;
        
        location / {
            markdown_auth_policy allow;  # Allow conversion
            markdown_auth_cookies session_id;
            markdown_flavor gfm;
            proxy_pass http://backend-b;
        }
    }
    
    # Tenant C: High-traffic blog
    server {
        listen 80;
        server_name blog-c.example.com;
        
        location / {
            markdown_max_size 5m;  # Smaller limit
            markdown_timeout 2s;   # Faster timeout
            markdown_conditional_requests if_modified_since_only;
            proxy_pass http://backend-c;
            
            # Aggressive caching
            proxy_cache blog_cache;
            proxy_cache_valid 200 1h;
        }
    }
}
```

---

## Configuration Inheritance

### Inheritance Rules

Configuration follows NGINX's standard inheritance model:

1. **Inner scopes inherit from outer scopes**: location → server → http
2. **Inner scopes can override outer scopes**: Explicit values override inherited values
3. **Unset values use defaults**: If not set anywhere, use built-in defaults

### Precedence Order

**Most Specific → Least Specific:**

1. `location` block
2. `server` block
3. `http` block
4. Built-in defaults

### Inheritance Examples

#### Example 1: Simple Inheritance

```nginx
http {
    markdown_filter on;           # Inherited by all servers/locations
    markdown_max_size 10m;        # Inherited by all servers/locations
    
    server {
        location /docs {
            # Inherits: markdown_filter on, markdown_max_size 10m
        }
    }
}
```

**Result:** `/docs` has `markdown_filter on` and `markdown_max_size 10m`

---

#### Example 2: Override at Location Level

```nginx
http {
    markdown_filter on;
    markdown_max_size 10m;
    
    server {
        location /docs {
            markdown_max_size 5m;  # Override
            # Inherits: markdown_filter on
        }
    }
}
```

**Result:** `/docs` has `markdown_filter on` and `markdown_max_size 5m` (overridden)

---

#### Example 3: Disable at Location Level

```nginx
http {
    markdown_filter on;
    
    server {
        location /api {
            markdown_filter off;  # Override
        }
        
        location /docs {
            # Inherits: markdown_filter on
        }
    }
}
```

**Result:** 
- `/api` has `markdown_filter off` (disabled)
- `/docs` has `markdown_filter on` (inherited)


#### Example 4: Server-Level Override

```nginx
http {
    markdown_filter on;
    markdown_max_size 10m;
    markdown_timeout 5s;
    
    server {
        server_name api.example.com;
        markdown_filter off;  # Disable for entire server
        
        location / {
            # Inherits: markdown_filter off (from server)
        }
    }
    
    server {
        server_name docs.example.com;
        markdown_timeout 3s;  # Override timeout
        
        location / {
            # Inherits: markdown_filter on (from http)
            # Inherits: markdown_timeout 3s (from server)
            # Inherits: markdown_max_size 10m (from http)
        }
    }
}
```

---

#### Example 5: Complex Inheritance

```nginx
http {
    markdown_filter on;
    markdown_max_size 10m;
    markdown_timeout 5s;
    markdown_flavor commonmark;
    
    server {
        markdown_timeout 3s;  # Override
        
        location /docs {
            markdown_flavor gfm;  # Override
            markdown_max_size 5m; # Override
            # Inherits: markdown_filter on (from http)
            # Inherits: markdown_timeout 3s (from server)
        }
        
        location /api {
            markdown_filter off;  # Override
            # Other settings don't matter (filter disabled)
        }
    }
}
```

**Result:**
- `/docs`: enabled, 5m limit, 3s timeout, GFM flavor
- `/api`: disabled

---

### Inheritance Best Practices

1. **Set global defaults at http level**: Common settings that apply to most locations
2. **Override at server level for virtual hosts**: Host-specific settings
3. **Override at location level for specific paths**: Path-specific settings
4. **Use explicit values for clarity**: Don't rely on implicit inheritance for critical settings
5. **Document overrides**: Comment why specific locations override defaults

**Example:**
```nginx
http {
    # Global defaults for all sites
    markdown_filter on;
    markdown_max_size 10m;
    markdown_timeout 5s;
    markdown_on_error pass;
    
    server {
        server_name example.com;
        
        location /docs {
            # Use GFM for better table support in documentation
            markdown_flavor gfm;
        }
        
        location /api {
            # API endpoints should not be converted
            markdown_filter off;
        }
        
        location /blog {
            # Blog posts are typically smaller
            markdown_max_size 5m;
        }
    }
}
```

---

## Security Best Practices

### 1. Resource Limits

Always configure resource limits to prevent resource exhaustion:

```nginx
# Conservative limits for production
markdown_max_size 5m;      # Limit response size
markdown_timeout 3s;       # Limit conversion time
```

**Recommendations:**
- Start with conservative limits (5m, 3s)
- Monitor actual usage and adjust as needed
- Set alerts for resource limit violations

---

### 2. Authenticated Content

Protect responses from authenticated requests from public caching:

```nginx
# Deny conversion for authenticated requests
markdown_auth_policy deny;

# Or allow with private caching
markdown_auth_policy allow;
markdown_auth_cookies session* auth_token;
```

**Recommendations:**
- Use `deny` for highly sensitive content
- Use `allow` with cookie detection for personalized content
- Always verify `Cache-Control: private` is set for authenticated responses

---

### 3. Metrics Endpoint Security

Restrict metrics endpoint to localhost only:

```nginx
location /markdown-metrics {
    markdown_metrics;
    
    # Localhost only
    allow 127.0.0.1;
    allow ::1;
    deny all;
}
```

**Recommendations:**
- Never expose metrics publicly
- Use firewall rules as additional protection
- Consider authentication for metrics access


### 4. Failure Strategy

Choose appropriate failure strategy based on requirements:

```nginx
# Fail-open (recommended for production)
markdown_on_error pass;

# Fail-closed (strict mode)
markdown_on_error reject;
```

**Recommendations:**
- Use `pass` (fail-open) for production to maintain availability
- Use `reject` (fail-closed) for testing or strict compliance requirements
- Monitor failure rates and investigate causes

---

### 5. Streaming Content Exclusion

Exclude streaming content types to prevent buffering issues:

```nginx
markdown_stream_types text/event-stream application/x-ndjson;
```

**Recommendations:**
- Always exclude Server-Sent Events (`text/event-stream`)
- Exclude any unbounded streaming content types
- Test with actual streaming endpoints

---

### 6. HTTPS and Security Headers

Always use HTTPS and security headers:

```nginx
server {
    listen 443 ssl http2;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    
    # Security headers
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;
    add_header X-XSS-Protection "1; mode=block";
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains";
    
    location / {
        markdown_filter on;
        proxy_pass http://backend;
    }
}
```

---

## Performance Tuning

### 1. Optimize Resource Limits

Balance between functionality and performance:

```nginx
# Aggressive limits for high-traffic sites
markdown_max_size 2m;      # Smaller limit
markdown_timeout 1s;       # Faster timeout
```

**Tuning Guidelines:**
- Monitor 95th percentile conversion time
- Set timeout to 2x 95th percentile
- Set size limit based on actual content size distribution

---

### 2. Disable Optional Features

Disable features not needed for your use case:

```nginx
# Minimal configuration for performance
markdown_token_estimate off;
markdown_front_matter off;
markdown_conditional_requests if_modified_since_only;
```

**Performance Impact:**
- `token_estimate off`: Saves ~1-2ms per request
- `front_matter off`: Saves ~2-5ms per request
- `if_modified_since_only`: Avoids conversion for conditional requests

---

### 3. Use Upstream Caching

Cache converted responses to avoid repeated conversions:

```nginx
http {
    # Define cache
    proxy_cache_path /var/cache/nginx/markdown
                     levels=1:2
                     keys_zone=markdown_cache:10m
                     max_size=1g
                     inactive=60m;
    
    server {
        location / {
            markdown_filter on;
            proxy_pass http://backend;
            
            # Enable caching
            proxy_cache markdown_cache;
            proxy_cache_valid 200 10m;
            
            # Include Accept header in cache key
            proxy_cache_key "$scheme$request_method$host$request_uri$http_accept";
            
            # Add cache status header
            add_header X-Cache-Status $upstream_cache_status;
        }
    }
}
```

**Caching Best Practices:**
- Include `$http_accept` in cache key to separate HTML and Markdown variants
- Set appropriate cache duration based on content update frequency
- Monitor cache hit rate
- Use `proxy_cache_bypass` for authenticated requests

---

### 4. Worker Process Tuning

Optimize NGINX worker processes:

```nginx
# Match number of CPU cores
worker_processes auto;

# Increase worker connections
events {
    worker_connections 2048;
    use epoll;  # Linux
}

http {
    # Increase buffer sizes
    client_body_buffer_size 128k;
    proxy_buffer_size 4k;
    proxy_buffers 8 4k;
    proxy_busy_buffers_size 8k;
}
```


### 5. Benchmarking and Monitoring

Establish performance baselines and monitor:

```bash
# Benchmark with Apache Bench
ab -n 1000 -c 10 -H "Accept: text/markdown" http://localhost/

# Monitor conversion metrics
curl http://localhost/markdown-metrics

# Watch NGINX error log
tail -f /var/log/nginx/error.log | grep markdown
```

**Key Metrics to Monitor:**
- Conversion latency (95th percentile)
- Throughput (requests per second)
- Failure rate
- Memory usage per worker
- Cache hit rate

---

## Configuration Templates

### Template 1: Production-Ready Configuration

Complete configuration for production deployment:

```nginx
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 2048;
    use epoll;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for"';
    
    access_log /var/log/nginx/access.log main;
    
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    types_hash_max_size 2048;
    
    # Markdown filter global settings
    markdown_filter on;
    markdown_max_size 10m;
    markdown_timeout 5s;
    markdown_on_error pass;
    markdown_flavor commonmark;
    markdown_auth_policy allow;
    markdown_auth_cookies session* auth_token;
    
    # Upstream cache
    proxy_cache_path /var/cache/nginx/proxy
                     levels=1:2
                     keys_zone=proxy_cache:10m
                     max_size=1g
                     inactive=60m;
    
    upstream backend {
        server backend1.example.com:8080;
        server backend2.example.com:8080;
        keepalive 32;
    }
    
    server {
        listen 80;
        server_name example.com;
        return 301 https://$server_name$request_uri;
    }
    
    server {
        listen 443 ssl http2;
        server_name example.com;
        
        ssl_certificate /etc/nginx/ssl/cert.pem;
        ssl_certificate_key /etc/nginx/ssl/key.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers HIGH:!aNULL:!MD5;
        ssl_prefer_server_ciphers on;
        
        # Security headers
        add_header X-Content-Type-Options nosniff;
        add_header X-Frame-Options DENY;
        add_header X-XSS-Protection "1; mode=block";
        add_header Strict-Transport-Security "max-age=31536000";
        
        location / {
            proxy_pass http://backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            
            proxy_cache proxy_cache;
            proxy_cache_valid 200 10m;
            proxy_cache_key "$scheme$request_method$host$request_uri$http_accept";
            add_header X-Cache-Status $upstream_cache_status;
        }
        
        location /api {
            markdown_filter off;
            proxy_pass http://backend;
            proxy_set_header Host $host;
        }
        
        location /markdown-metrics {
            markdown_metrics;
            allow 127.0.0.1;
            allow ::1;
            deny all;
        }
    }
}
```


### Template 2: Development/Testing Configuration

Simplified configuration for development and testing:

```nginx
worker_processes 1;
error_log /tmp/nginx-error.log debug;
pid /tmp/nginx.pid;

events {
    worker_connections 1024;
}

http {
    access_log /tmp/nginx-access.log;
    
    # Markdown filter with verbose logging
    markdown_filter on;
    markdown_max_size 10m;
    markdown_timeout 10s;  # Longer timeout for debugging
    markdown_on_error pass;
    markdown_flavor gfm;
    markdown_token_estimate on;
    markdown_front_matter on;
    
    server {
        listen 8080;
        server_name localhost;
        
        location / {
            proxy_pass http://127.0.0.1:9000;
        }
        
        location /markdown-metrics {
            markdown_metrics;
        }
    }
}
```

---

### Template 3: High-Performance Configuration

Optimized for maximum throughput:

```nginx
user nginx;
worker_processes auto;
worker_rlimit_nofile 65535;
error_log /var/log/nginx/error.log error;
pid /var/run/nginx.pid;

events {
    worker_connections 4096;
    use epoll;
    multi_accept on;
}

http {
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 30;
    keepalive_requests 100;
    
    # Aggressive markdown filter settings
    markdown_filter on;
    markdown_max_size 2m;
    markdown_timeout 1s;
    markdown_on_error pass;
    markdown_flavor commonmark;
    markdown_token_estimate off;
    markdown_front_matter off;
    markdown_conditional_requests if_modified_since_only;
    markdown_buffer_chunked off;
    
    # Large cache
    proxy_cache_path /var/cache/nginx/proxy
                     levels=1:2
                     keys_zone=proxy_cache:100m
                     max_size=10g
                     inactive=120m
                     use_temp_path=off;
    
    upstream backend {
        server backend1:8080 max_fails=3 fail_timeout=30s;
        server backend2:8080 max_fails=3 fail_timeout=30s;
        keepalive 64;
    }
    
    server {
        listen 80 reuseport;
        server_name example.com;
        
        location / {
            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $host;
            
            proxy_cache proxy_cache;
            proxy_cache_valid 200 30m;
            proxy_cache_key "$scheme$request_method$host$request_uri$http_accept";
            proxy_cache_use_stale error timeout updating http_500 http_502 http_503;
            proxy_cache_background_update on;
            proxy_cache_lock on;
        }
    }
}
```

---

## Validation and Testing

### Configuration Syntax Check

Always test configuration before reloading:

```bash
# Test configuration syntax
nginx -t

# Test with specific config file
nginx -t -c /path/to/nginx.conf
```

**Expected Output:**
```
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

---

### Functional Testing

Test conversion with curl:

```bash
# Test Markdown conversion
curl -v -H "Accept: text/markdown" http://localhost/

# Test HTML passthrough
curl -v -H "Accept: text/html" http://localhost/

# Test metrics endpoint
curl http://localhost/markdown-metrics

# Test with authentication
curl -v -H "Accept: text/markdown" -H "Authorization: Bearer token" http://localhost/
```

---

### Performance Testing

Benchmark with Apache Bench:

```bash
# Markdown conversion
ab -n 1000 -c 10 -H "Accept: text/markdown" http://localhost/

# HTML passthrough (baseline)
ab -n 1000 -c 10 -H "Accept: text/html" http://localhost/

# Compare results
```

---

## Troubleshooting Configuration Issues

### Issue: Conversion Not Occurring

**Check:**
1. `markdown_filter on` is set
2. Request has `Accept: text/markdown` header
3. Response is eligible (GET/HEAD, 200, text/html)
4. Response size within `markdown_max_size` limit

**Debug:**
```bash
# Check NGINX error log
tail -f /var/log/nginx/error.log | grep markdown

# Test with verbose curl
curl -v -H "Accept: text/markdown" http://localhost/
```

---

### Issue: Configuration Not Inherited

**Check:**
1. Directive is valid in the context (http/server/location)
2. No typos in directive names
3. Values are properly quoted if needed

**Debug:**
```bash
# Test configuration
nginx -t

# Check effective configuration
nginx -T | grep markdown
```

---

### Issue: Performance Degradation

**Check:**
1. `markdown_max_size` and `markdown_timeout` are appropriate
2. Caching is enabled
3. Optional features are disabled if not needed

**Debug:**
```bash
# Check metrics
curl http://localhost/markdown-metrics

# Monitor conversion time
tail -f /var/log/nginx/error.log | grep "conversion time"
```

---

## References

- **Installation Guide:** [INSTALLATION.md](INSTALLATION.md)
- **Operational Guide:** [OPERATIONS.md](OPERATIONS.md)
- **Project README:** [../../README.md](../../README.md)
- **Requirements Traceability:** [../project/PROJECT_STATUS.md](../project/PROJECT_STATUS.md)
- **Architecture Index:** [../architecture/README.md](../architecture/README.md)
- **Configuration to Behavior Map:** [../architecture/CONFIG_BEHAVIOR_MAP.md](../architecture/CONFIG_BEHAVIOR_MAP.md)
- **Streaming Compatibility Matrix:** [../project/compatibility-matrix-0-5-0.md](../project/compatibility-matrix-0-5-0.md)
- **NGINX Documentation:** https://nginx.org/en/docs/
