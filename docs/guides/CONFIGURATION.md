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
- If your variable map enables conversion for `Accept: text/*` or `Accept: */*`, also set `markdown_accept wildcard;`.

---

#### markdown_memory_budget

**Syntax:** `markdown_memory_budget <size>;`  
**Default:** unset; path-specific defaults apply
**Context:** http, server, location

Unified memory/resource budget override for conversion paths. If set, it is
used by full-buffer and streaming paths unless a path-specific directive such
as `markdown_max_size` or `markdown_streaming_budget` is explicitly set.
Responses that exceed the effective path limit are not converted (fail-open
behavior).

> **Compatibility:** `markdown_memory_budget` supersedes `markdown_max_size` (deprecated in 0.6.0). `markdown_max_size` is still accepted with a deprecation warning at `info` verbosity.

**Valid Units:** `k` (kilobytes), `m` (megabytes)

**Example:**
```nginx
# Limit to 5 megabytes
markdown_memory_budget 5m;

# Limit to 512 kilobytes
markdown_memory_budget 512k;
```

#### markdown_decompress_max_size

**Syntax:** `markdown_decompress_max_size <size>;`
**Default:** inherits the effective full-buffer size limit (`markdown_max_size`,
after any `markdown_memory_budget` override resolution)
**Context:** http, server, location

Independent budget for decompressed output size. When unset, it follows the
effective full-buffer conversion size limit. Set this explicitly when
compressed upstream responses may expand much larger than the final Markdown
output you are willing to process.

**Example:**
```nginx
# Allow up to 20MB of decompressed content
markdown_decompress_max_size 20m;
```

#### markdown_parse_timeout

**Syntax:** `markdown_parse_timeout <time>;`
**Default:** `30s`
**Context:** http, server, location

Maximum time for the HTML parsing phase. The deadline is checked before and
after parsing. The current parser path is not preemptively interrupted
mid-parse; combine this directive with `markdown_parser_budget`,
`markdown_memory_budget`, and `markdown_decompress_max_size` to bound resource
exposure.

**Example:**
```nginx
markdown_parse_timeout 10s;
```

#### markdown_parser_budget

**Syntax:** `markdown_parser_budget <size>;`
**Default:** `64m`
**Context:** http, server, location

Maximum memory the HTML parser may allocate. If the parser exceeds
this budget, parsing is terminated and the request proceeds according
to the `markdown_on_error` policy.

**Example:**
```nginx
markdown_parser_budget 32m;
```


#### markdown_limits

**Syntax:** `markdown_limits memory=<size> timeout=<time> streaming_buffer=<size> max_inflight=<N>;`  
**Default:** `memory=10m timeout=5s streaming_buffer=2m max_inflight=64`  
**Context:** http, server, location

Unified limits block (Config V2, 0.9.0). Consolidates the removed
`markdown_max_size`, `markdown_timeout`, and `markdown_streaming_budget`
directives. Any subset of keys may be given; unspecified keys inherit
(per-key inheritance).

- `memory=<size>` — maximum response size to attempt conversion (was
  `markdown_max_size`).
- `timeout=<time>` — maximum conversion time (was `markdown_timeout`).
- `streaming_buffer=<size>` — streaming memory budget (was
  `markdown_streaming_budget`).
- `max_inflight=<N>` — maximum concurrent in-flight conversions per worker
  (0.9.0 production-protection default 64; enforced by the worker inflight
  guard).

`nginx -t` rejects duplicate keys, unknown keys, zero values, and malformed
size/time/integer values.

**Example:**
```nginx
markdown_limits memory=8m timeout=2s streaming_buffer=256k max_inflight=64;
```

**Migration:** `markdown_max_size 5m` -> `markdown_limits memory=5m`;
`markdown_timeout 3s` -> `markdown_limits timeout=3s`;
`markdown_streaming_budget 4m` -> `markdown_limits streaming_buffer=4m`.
`markdown_large_body_threshold` is removed with no direct replacement.

---

#### markdown_error_policy

**Syntax:** `markdown_error_policy pass | fail_closed | status <code>;`  
**Default:** `pass`  
**Context:** http, server, location

Unified pre-commit error policy. Consolidates the removed `markdown_on_error`
and `markdown_streaming_on_error` directives (0.9.0 breaking change).

- `pass` (default): return the original content on a pre-commit error
  (fail-open, recommended for production).
- `fail_closed`: return 502 on a pre-commit error.
- `status <code>`: return the specified status code (`429`, `502`, or `503`).

This applies to **pre-commit** errors only (headers not yet sent). Post-commit
streaming errors cannot rewrite the status or pass original content; they stop
output and close the connection (see the streaming sections).

**Example:**
```nginx
# Fail-open (default, recommended)
markdown_error_policy pass;

# Return 503 on conversion failure
markdown_error_policy status 503;
```

**Migration:** `markdown_on_error pass` -> `markdown_error_policy pass`;
`markdown_on_error reject` -> `markdown_error_policy fail_closed`;
`markdown_streaming_on_error pass|reject` ->
`markdown_error_policy pass|fail_closed` (the policy is now unified across the
full-buffer and streaming paths).

---

#### markdown_flavor

**Syntax:** `markdown_flavor commonmark | gfm | mdx | org-mode;`
**Default:** `commonmark`  
**Context:** http, server, location

Markdown flavor to generate:
- `commonmark`: CommonMark specification (standard)
- `gfm`: GitHub Flavored Markdown (includes tables, strikethrough, task lists)
- `mdx`: experimental MDX-oriented selector; output fidelity depends on the
  active converter integration
- `org-mode`: experimental Org-mode-oriented selector; output fidelity depends
  on the active converter integration

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

#### markdown_accept

**Syntax:** `markdown_accept strict | wildcard | force;`  
**Default:** `strict`  
**Context:** http, server, location

Accept-header negotiation policy. Replaces the removed
`markdown_on_wildcard` directive (0.9.0 breaking change).

- `strict` (default): only an explicit `Accept: text/markdown` match triggers
  conversion.
- `wildcard`: additionally convert when the `Accept` header contains wildcards
  (`*/*` or `text/*`). Equivalent to the old `markdown_on_wildcard on`.
- `force`: convert regardless of the `Accept` header, including when no
  `Accept` header is present. Dangerous; use only for closed/internal traffic.

**Example:**
```nginx
# Enable conversion for wildcard Accept headers
markdown_accept wildcard;
```

**Behavior:**
- `strict` (default): Only `Accept: text/markdown` triggers conversion
- `wildcard`: `Accept: */*` or `Accept: text/*` also triggers conversion
- `force`: conversion is attempted regardless of the `Accept` header

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

#### markdown_cache_validation

**Syntax:** `markdown_cache_validation off | ims_only | full;`  
**Default:** `full`  
**Context:** http, server, location

Cache-validation policy. Consolidates the removed `markdown_etag` and
`markdown_conditional_requests` directives (0.9.0 breaking change).

- `off`: no Markdown-variant ETag and no conditional request handling.
- `ims_only`: no module-side ETag; `If-Modified-Since` is handled by standard
  NGINX conditional processing using the preserved upstream `Last-Modified`.
- `full` (default): generate a transformed (Markdown-variant) ETag and support
  both `If-None-Match` and `If-Modified-Since`.

**Example:**
```nginx
# Performance optimization: only support If-Modified-Since
markdown_cache_validation ims_only;
```

**Performance Note:** `full` requires conversion to generate a Markdown-variant
ETag for comparison, which has performance implications.

**Streaming interaction:** streaming responses do not carry an ETag (headers
commit before the transformed body is known). `markdown_cache_validation full`
with `markdown_streaming auto` blocks streaming at runtime and uses full-buffer
so full validation is preserved; with `markdown_streaming force` it is a
configuration error (see `markdown_streaming`).

**Migration:** `markdown_etag off` -> `markdown_cache_validation off`;
`markdown_etag on` -> `markdown_cache_validation full`;
`markdown_conditional_requests if_modified_since_only` ->
`markdown_cache_validation ims_only`;
`markdown_conditional_requests disabled` -> `markdown_cache_validation off`;
`markdown_conditional_requests full_support` -> `markdown_cache_validation full`.

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

#### markdown_content_types

**Syntax:** `markdown_content_types <type> [<type> ...];`
**Default:** `text/html`
**Context:** http, server, location

Content types eligible for Markdown conversion (positive allowlist). Uses prefix + boundary-char matching: `text/html` matches `text/html` and `text/html; charset=utf-8` but not `text/htmlx`.

**Example:**
```nginx
# Convert both HTML and XHTML responses
markdown_content_types text/html application/xhtml+xml;
```

**Notes:**
- This directive controls whether the module processes a response at all (any path: full-buffer, incremental, or streaming).
- `markdown_stream_types` and `markdown_stream_excluded_types` are additional filters that prevent specific types from entering the streaming path.
- A type excluded from streaming may still be converted via full-buffer if it passes the general eligibility checks.

---

### Content Pruning

#### markdown_prune_noise

**Syntax:** `markdown_prune_noise on | off;`
**Default:** `on`
**Context:** http, server, location

Enable or disable noise region pruning at runtime. When enabled, structural HTML regions matching the prune selectors (nav, footer, aside, etc.) are excluded from Markdown output, reducing noise for AI agents.

**Example:**
```nginx
# Disable noise pruning (include all content)
markdown_prune_noise off;
```

---

#### markdown_prune_selectors

**Syntax:** `markdown_prune_selectors "<tag> [<tag> ...]";`
**Default:** `"nav footer aside"` (built-in)
**Context:** http, server, location

Space-separated HTML tag names for regions to prune from the output. Replaces the built-in defaults when set.

**Example:**
```nginx
# Prune navigation, footer, aside, and sidebar elements
markdown_prune_selectors "nav footer aside sidebar";
```

---

#### markdown_prune_protection_selectors

**Syntax:** `markdown_prune_protection_selectors "<tag> [<tag> ...]";`
**Default:** empty (no protection)
**Context:** http, server, location

Space-separated HTML tag names for regions to protect from pruning. Protection wins over prune: an element matching both prune and protection selectors is kept in the output.

**Example:**
```nginx
# Protect nav elements from being pruned
markdown_prune_protection_selectors "nav";
```

---

### Security Directives

#### markdown_trusted_proxies

**Syntax:** `markdown_trusted_proxies <CIDR>... | off;`
**Default:** unset (no forwarded headers honored)
**Context:** http

Defines the CIDR ranges of trusted reverse proxies. Forwarded headers
(`Forwarded` per RFC 7239, `X-Forwarded-Proto`, `X-Forwarded-Host`) are honored
for base-URL construction **only** when the request's direct source IP matches a
configured CIDR. This replaces the removed boolean
`markdown_trust_forwarded_headers` trust model (see
[MIGRATION-0.9.md](MIGRATION-0.9.md)).

Key properties:

1. **http context only.** The directive is not allowed in `server` or
   `location` blocks; a misplaced directive fails `nginx -t` with a migration
   hint. Per-location trust would create a local trust-bypass risk and is harder
   to audit.
2. **IPv4 and IPv6 CIDR** are both supported (`10.0.0.0/8`, `192.168.1.1`,
   `2001:db8::/32`, `::1/128`). A bare address is treated as a host route
   (`/32` or `/128`). CIDRs are parsed and validated at config time; an invalid
   CIDR fails `nginx -t` with the offending value.
3. **`off`** disables trust entirely — no forwarded headers are honored,
   regardless of source IP.
4. **Source IP** is taken from the direct connection peer
   (`r->connection->addr_text`), already resolved by NGINX's `realip` module or
   PROXY protocol when configured. The `X-Forwarded-For` header is **never**
   used as the source IP (spoofing avoidance).
5. **Strict validation.** Even for a trusted source, forwarded host/proto values
   are strictly validated (control characters, comma chains, userinfo `@`, path
   `/`/`?`, port range, IPv6 brackets, raw Unicode IDN). Invalid values fall back
   to the `Host` header or a safe default. The `Forwarded` header takes
   precedence over `X-Forwarded-*`; multi-hop comma chains use the left-most
   (closest-to-client) value.

**Example:**
```nginx
http {
    # Honor forwarded headers only from these proxy ranges.
    markdown_trusted_proxies 10.0.0.0/8 172.16.0.0/12 2001:db8::/32;
}
```

**Deployment guidance:**

- **`realip` module:** keep `markdown_trusted_proxies` consistent with your
  `set_real_ip_from` ranges; the module trusts the realip-resolved source IP.
- **PROXY protocol:** include the load balancer / CDN CIDRs so the
  PROXY-protocol-resolved peer is recognized as trusted.
- **Unix socket peers** are treated as untrusted (a Unix socket cannot match an
  IP CIDR).

**Security Note:** Configure `markdown_trusted_proxies` only with proxy ranges
you control. A source outside every configured CIDR has its forwarded headers
ignored, so a direct client cannot send `X-Forwarded-Host: evil.com` to poison
relative links in the Markdown output.

> **Removed in 0.9.0:** `markdown_trust_forwarded_headers` is a reject-only stub.
> `markdown_trust_forwarded_headers on;` →
> `markdown_trusted_proxies <CIDR>...;` (list your proxy ranges).
> `markdown_trust_forwarded_headers off;` → omit `markdown_trusted_proxies`
> (the default already ignores forwarded headers). See
> [MIGRATION-0.9.md](MIGRATION-0.9.md).

---

### Streaming Directives (v0.8.0 configuration contract)

v0.8.0 uses the unified `conf->stream.*` configuration model. The module no
longer preserves the 0.6.x streaming compatibility bridge. Responses are
automatically routed to streaming or full-buffer based on Content-Length
and `markdown_stream_threshold`. Use `markdown_stream_threshold` for
auto-mode threshold control.

#### markdown_streaming

**Syntax:** `markdown_streaming off | auto | force;`
**Default:** `auto`
**Context:** http, server, location

Streaming *enablement* policy (Config V2, 0.9.0). This is the selector that
decides **whether** streaming is attempted; it is distinct from
`markdown_streaming_engine`, which selects **which** streaming backend
implementation is used. The two are independent and both remain first-class
directives — `markdown_streaming` is not an alias for
`markdown_streaming_engine`.

- `off` — never stream; always use the full-buffer path.
- `auto` — stream large responses (Content-Length at or above
  `markdown_stream_threshold`), full-buffer small ones (default).
- `force` — always stream, subject to runtime hard blocks (`Range`,
  `Cache-Control: no-transform`, `HEAD`, `304`, upstream `Content-Encoding`,
  and `markdown_cache_validation full`).

**Conflict with `markdown_cache_validation`:**

| Combination | `nginx -t` | Runtime |
|-------------|-----------|---------|
| `markdown_cache_validation full` + `markdown_streaming force` | **error** | n/a (config rejected) |
| `markdown_cache_validation full` + `markdown_streaming auto` | **warning** | streaming blocked (`streaming_block_full_cache_validation`); request falls back to full-buffer, preserving full cache validation |

A streaming response never carries an ETag (headers commit before the
transformed body is known), so full cache validation requires the
full-buffer path. Use `markdown_cache_validation ims_only` to allow
streaming alongside `If-Modified-Since` handling.

**Example:**
```nginx
# Large docs stream; small ones buffer (ETag only via ims_only/off)
markdown_cache_validation ims_only;
markdown_streaming auto;
```

#### markdown_streaming_engine

**Syntax:** `markdown_streaming_engine off | on | auto;`
**Default:** `auto`
**Context:** http, server, location

Controls whether the streaming conversion engine is used. When `off`,
all requests use the full-buffer conversion path.
When `auto`, the engine is selected automatically per-request.

- `off`: Disable streaming. All requests use the full-buffer path.
- `on`: Enable streaming for all eligible requests.
- `auto`: Enable streaming with automatic fallback to full-buffer when the streaming
  engine encounters unsupported features (e.g., tables requiring full-buffer processing).

**Note**: `$variable` support was removed in v0.8.0. The directive only accepts
the enum values `off`, `on`, and `auto`. If your configuration uses a variable
(e.g. `markdown_streaming_engine $flag`), `nginx -t` will reject it.

**Example:**
```nginx
# Enable streaming for all requests
markdown_streaming_engine on;

# Auto mode with fallback
markdown_streaming_engine auto;

# Disable streaming
markdown_streaming_engine off;
```

#### markdown_streaming_budget

**Syntax:** `markdown_streaming_budget <size>;`
**Default:** `2m`
**Context:** http, server, location
**Status:** REMOVED in 0.9.0 — use `markdown_limits streaming_buffer=<size>`

Sets the memory budget for streaming conversion, passed to the Rust streaming engine.
The streaming engine enforces this budget to ensure bounded memory usage regardless
of input size. If the budget is exceeded, the streaming engine reports a budget-exceeded
error and the failure is handled according to `markdown_streaming_on_error`.

**Valid Units:** `k` (kilobytes), `m` (megabytes)

**Example:**
```nginx
# Default budget (2 MB)
markdown_streaming_budget 2m;

# Larger budget for complex pages
markdown_streaming_budget 4m;
```

**Behavior:**
- The budget applies to the total working set of the streaming converter, including
  parser state, sanitizer state, and output buffers.
- When the budget is exceeded, the `nginx_markdown_streaming_budget_exceeded_total`
  Prometheus counter increments and the error is classified as a pre-commit failure.
- The budget does not affect the full-buffer path, which uses `markdown_memory_budget` instead.
#### markdown_streaming_on_error

**Syntax:** `markdown_streaming_on_error pass | reject;`
**Default:** `pass`
**Context:** http, server, location
**Status:** REMOVED in 0.9.0 — use `markdown_error_policy pass|fail_closed|status <code>` (now unified across full-buffer and streaming)

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
- When `markdown_streaming_engine` is not set or is `auto` (the default), streaming is enabled for eligible responses. When explicitly set to `off`, `markdown_streaming_on_error`
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
| `precommit_failopen_total` | Pre-commit failures handled by `pass` (fail-open) |
| `precommit_reject_total` | Pre-commit failures handled by `reject` (fail-closed) |
| `postcommit_error_total` | Post-commit failures (always fail-closed) |
| `fallback_total` | Capability fallbacks to full-buffer (always executes) |

If `precommit_failopen_total` or `precommit_reject_total` is
growing, investigate the NGINX error log for the specific error types triggering
the failures.

#### markdown_streaming_shadow

**Syntax:** `markdown_streaming_shadow on | off;`
**Default:** `off`
**Context:** http, server, location

Enables shadow mode for the streaming conversion engine. When enabled, the module
runs both the full-buffer and streaming engines on the same decompressed HTML input
after a successful full-buffer conversion, compares their outputs, and records
differences in metrics and the debug log. The full-buffer result is always used for
the client response — shadow mode never affects what the client receives.

Shadow mode is intended for Phase 0 of a streaming rollout to verify engine output
parity before enabling streaming for live traffic. See the
[Streaming Rollout Cookbook](streaming-rollout-cookbook.md) for phased deployment
guidance.

**Metrics produced:**

| Metric (JSON path) | Prometheus series | Description |
|---------------------|-------------------|-------------|
| `streaming.shadow_total` | `nginx_markdown_streaming_shadow_total` | Shadow comparison attempts, including init/feed/finalize failures |
| `streaming.shadow_diff_total` | `nginx_markdown_streaming_shadow_diff_total` | Comparisons where outputs differed |

**Important caveats:**

- Shadow mode verifies engine output parity on already-decompressed HTML. It does
  **not** exercise chunk boundaries, streaming decompression, backpressure, or
  commit boundaries — those runtime behaviors are covered by integration tests and
  chunk-boundary fuzzing.
- `streaming.shadow_total` increments at shadow-mode entry so failed comparison
  attempts are visible. Use `streaming.shadow_diff_total` and decision logs to
  distinguish output drift from initialization, feed, or finalize failures.
- Shadow mode adds latency to every converted request (the streaming engine runs
  in addition to the full-buffer engine). Disable it once Phase 0 verification is
  complete.

**Example:**
```nginx
# Phase 0: shadow mode verification
markdown_filter on;
markdown_streaming_engine off;
markdown_streaming_shadow on;

# Phase 1+: disable shadow after verification
markdown_streaming_shadow off;
```

#### Streaming Candidacy Directives (v0.8.0)

The following directives were introduced in v0.8.0 to give operators fine-grained
control over which responses enter the streaming conversion path and how the
streaming engine batches output. They complement `markdown_streaming_engine` and
`markdown_streaming_budget` defined above.

##### markdown_stream_threshold

**Syntax:** `markdown_stream_threshold <size>;`
**Default:** `1m`
**Context:** http, server, location

Minimum response size for streaming candidacy. Only responses whose body size
meets or exceeds this threshold are considered for streaming conversion. Responses
below the threshold always use the full-buffer path.

The value must be greater than zero; `0` is rejected by `nginx -t`.

**Valid Units:** `k` (kilobytes), `m` (megabytes)

**Example:**
```nginx
# Default: 1 megabyte threshold
markdown_stream_threshold 1m;

# Lower threshold for faster streaming engagement
markdown_stream_threshold 512k;

# Higher threshold — only very large responses stream
markdown_stream_threshold 5m;
```

##### markdown_stream_precommit_buffer

**Syntax:** `markdown_stream_precommit_buffer <size>;`
**Default:** `256k`
**Context:** http, server, location

Size of the pre-commit replay buffer. During the streaming pre-commit phase, the
module buffers converted output up to this size. If an error occurs before the
commit boundary is crossed, the buffered content is discarded and the original
HTML is replayed to the client (fail-open behavior controlled by
`markdown_streaming_on_error`).

Setting this to `0` disables the pre-commit HTML fallback capability — errors
during the pre-commit phase immediately trigger the configured error policy
without replay.

**Valid Units:** `k` (kilobytes), `m` (megabytes)

**Example:**
```nginx
# Default: 256 KB replay buffer
markdown_stream_precommit_buffer 256k;

# Larger buffer for more replay safety margin
markdown_stream_precommit_buffer 512k;

# Disable pre-commit replay (zero allowed)
markdown_stream_precommit_buffer 0;
```

##### markdown_stream_flush_min

**Syntax:** `markdown_stream_flush_min <size>;`
**Default:** `16k`
**Context:** http, server, location

Minimum Markdown output batch size before the streaming engine flushes data
downstream. The engine accumulates converted output until at least this many
bytes are ready, then flushes the batch. This reduces per-byte overhead and
backpressure amplification from many small writes.

The value must be greater than zero; `0` is rejected by `nginx -t` to prevent
pathological per-byte flushing.

**Valid Units:** `k` (kilobytes), `m` (megabytes)

**Example:**
```nginx
# Default: 16 KB flush minimum
markdown_stream_flush_min 16k;

# Smaller batches for lower latency
markdown_stream_flush_min 4k;

# Larger batches for higher throughput
markdown_stream_flush_min 64k;
```

##### markdown_stream_excluded_types

**Syntax:** `markdown_stream_excluded_types <type> [<type> ...];`
**Default:** none (only built-in hard exclusions apply)
**Context:** http, server, location

Space-separated list of MIME types to exclude from streaming conversion. These
types are added to the built-in hard exclusions and never enter the streaming
path, regardless of `markdown_streaming_engine` setting.

User-configured exclusions are **additive** to the built-in hard exclusions:

- `text/event-stream`
- `application/x-ndjson`
- `application/stream+json`

The built-in hard exclusions cannot be removed by user configuration. Matching
is case-insensitive and ignores Content-Type parameters (e.g.,
`text/event-stream; charset=utf-8` remains excluded).

**Example:**
```nginx
# Exclude additional types from streaming
markdown_stream_excluded_types text/csv application/xml;

# Multiple custom exclusions
markdown_stream_excluded_types text/csv application/xml application/rss+xml;
```

##### Configuration Examples (v0.8.0 Streaming)

**Basic streaming enablement:**
```nginx
http {
    markdown_filter on;
    markdown_streaming_engine on;
    markdown_stream_threshold 1m;

    server {
        listen 80;
        server_name docs.example.com;

        location / {
            proxy_pass http://backend;
        }
    }
}
```

**Threshold tuning for large responses:**
```nginx
location /large-docs {
    # Only stream responses >= 2 MB
    markdown_streaming_engine auto;
    markdown_stream_threshold 2m;

    # Larger pre-commit buffer for complex pages
    markdown_stream_precommit_buffer 512k;

    # Bigger flush batches for throughput
    markdown_stream_flush_min 32k;

    proxy_pass http://backend;
}
```

**Adding custom excluded types:**
```nginx
location /api {
    markdown_streaming_engine auto;

    # Exclude CSV and XML feeds from streaming
    markdown_stream_excluded_types text/csv application/atom+xml;

    proxy_pass http://backend;
}
```

**Disabling streaming for a specific location:**
```nginx
location /legacy {
    # Force full-buffer path regardless of response size
    markdown_streaming_engine off;
    proxy_pass http://backend;
}
```

##### Reserved Directives (NOT Implemented)

The following directives are defined in design RFCs but are **not implemented**
in the current release. They are **not registered** in the module command table.

> ⚠️ **Do NOT use any reserved directive in your configuration.** Adding a
> reserved directive causes `nginx -t` to fail, preventing NGINX from
> starting or reloading.

---

###### `markdown_stream_flush_interval`

> **RESERVED — Not implemented in v0.8.0**
>
> Using this directive in any configuration context will cause:
> ```text
> nginx: [emerg] unknown directive "markdown_stream_flush_interval"
> nginx -t: configuration file test failed
> ```

| Field | Value |
|-------|-------|
| **Status** | Reserved — not available |
| **Intended purpose** | Time-based flush control for streaming output (flush after N milliseconds even if `markdown_stream_flush_min` bytes have not accumulated) |
| **Planned version** | Future release (pending finalization of time-based flush semantics) |
| **Defined in** | RFC 0008 |
| **Current behavior** | Not registered; NGINX rejects the directive at configuration parse time |

**What operators should know:**

- This directive does **not exist** in the v0.8.0 module binary.
- There is no workaround or feature flag to enable it.
- Size-based flushing (`markdown_stream_flush_min`) is the only flush control
  available in v0.8.0.
- Do not include `markdown_stream_flush_interval` in configuration files,
  templates, or automation scripts targeting v0.8.0 deployments.
- When this directive becomes available in a future release, it will be
  documented in the implemented streaming directives section above and
  removed from this reserved section.

##### Directive Interactions

Streaming directives interact with other module directives in ways that may
not be immediately obvious. This section documents the key interactions to
help operators understand the full picture when multiple directives are
configured together.

| Directive Pair | Interaction Summary |
|----------------|---------------------|
| `markdown_streaming_engine` + `markdown_filter` | Streaming only applies when `markdown_filter` is `on` (or resolves to a truthy value). If the filter is disabled, the streaming engine setting is irrelevant — no conversion of any kind occurs. |
| `markdown_streaming_engine` + `markdown_memory_budget` | The unified `markdown_memory_budget` sets the ceiling for both engines unless a path-specific directive overrides it. For streaming, `markdown_streaming_budget` takes precedence when explicitly set; otherwise the unified budget applies. |
| `markdown_streaming_engine` + `markdown_on_error` | These error policies are **independent**. `markdown_on_error` governs full-buffer failures; `markdown_streaming_on_error` governs streaming pre-commit failures. Changing one never affects the other. See the [error policy table](#relationship-between-markdown_on_error-and-markdown_streaming_on_error) above. |
| `markdown_streaming_engine` + `markdown_etag` | ETags are generated from converted output. For full-buffer responses, the complete Markdown is available before headers are sent, so ETag generation works normally. For streaming responses that have crossed the commit boundary, response headers (including `Content-Type: text/markdown`) are already sent — ETag cannot be retroactively added. Streaming responses do **not** carry an ETag header. |
| `markdown_streaming_engine` + `markdown_conditional_requests` | Conditional request handling (`If-None-Match` / 304) works for full-buffer responses where an ETag is available. Streaming responses bypass 304 logic because no ETag is produced. **Important:** when `markdown_conditional_requests` is `full_support` (the default), the streaming selector always selects full-buffer because full ETag support requires the complete converted output before headers. To actually activate streaming in `auto` mode, set `markdown_conditional_requests if_modified_since_only` or `disabled`. |
| `markdown_stream_threshold` + `Content-Length` | When the upstream response includes a `Content-Length` header and the value is below `markdown_stream_threshold`, the response always uses the full-buffer path regardless of `markdown_streaming_engine on`. This is a size-based eligibility gate, not an error. |
| `markdown_stream_excluded_types` + `markdown_content_types` | Both must permit the content type for streaming conversion. `markdown_content_types` controls whether the module processes a response at all (any path). `markdown_stream_excluded_types` is an additional filter that prevents specific types from entering the streaming path. A type excluded from streaming may still be converted via full-buffer if it passes the general eligibility checks. |
| `markdown_stream_precommit_buffer` + `markdown_streaming_on_error` | The pre-commit buffer enables fail-open replay: if an error occurs before the commit boundary, the buffered original HTML is replayed to the client. When `markdown_stream_precommit_buffer 0` (replay disabled), any pre-commit error immediately triggers the `markdown_streaming_on_error` policy without replay capability. |
| `markdown_streaming_engine auto` + explicit value | When `markdown_streaming_engine` is set to a fixed value (`off`, `on`, or `auto`), it applies for the entire request lifecycle. Use different values at different configuration levels (http/server/location) for per-path control. |

**Non-obvious behaviors to watch for:**

1. **No ETag on streaming responses.** If your caching layer depends on ETags
   for revalidation, streaming responses will not benefit from 304 short-circuits.
   Consider setting `markdown_stream_threshold` high enough that commonly cached
   small responses stay on the full-buffer path.

2. **Unified budget resolution order.** The effective streaming budget is:
   `markdown_streaming_budget` (if set) → `markdown_memory_budget` (if set) →
   built-in default (`2m`). An operator who sets only `markdown_memory_budget 1m`
   may inadvertently lower the streaming budget below the `2m` default.

3. **Excluded types are additive.** `markdown_stream_excluded_types` adds to
   the built-in hard exclusions (`text/event-stream`, `application/x-ndjson`,
   `application/stream+json`). You cannot remove the built-in exclusions.

4. **Pre-commit buffer size vs. streaming budget.** The pre-commit buffer is
   drawn from the overall streaming budget. Setting `markdown_stream_precommit_buffer`
   close to `markdown_streaming_budget` leaves little room for converter working
   memory and may trigger budget-exceeded errors.

5. **Streaming engine configuration inheritance.** A `markdown_streaming_engine`
   directive at the `http` level is inherited by all `server`/`location` blocks.
   Override with an explicit value (`off`, `on`, `auto`) in specific locations to
   exclude them from the inherited setting.

6. **Default `auto` mode does not activate streaming.** When `markdown_conditional_requests`
   is `full_support` (the default), the streaming selector always routes to full-buffer
   because full ETag support requires the complete output before sending headers. This
   means the default 0.8.0 configuration (`streaming_engine auto` + `conditional_requests
   full_support`) never selects streaming. To activate streaming for large responses, set
   `markdown_conditional_requests if_modified_since_only` or `disabled`.

**Example — interactions in practice:**

```nginx
http {
    markdown_filter on;
    markdown_memory_budget 4m;           # Applies to both paths
    markdown_streaming_engine auto;
    markdown_stream_threshold 1m;
    markdown_etag on;                    # Works for full-buffer only

    server {
        listen 80;

        location /docs {
            # Responses < 1m: full-buffer path, ETag generated, 304 works
            # Responses >= 1m: streaming path, no ETag, no 304
            markdown_streaming_budget 3m;   # Overrides unified budget for streaming
            markdown_streaming_on_error pass;
            proxy_pass http://backend;
        }

        location /api-reference {
            # Exclude XML from streaming but still convert via full-buffer
            markdown_stream_excluded_types application/xml;
            proxy_pass http://backend;
        }
    }
}
```

> **Inheritance note:** All streaming directives follow standard NGINX inheritance:
> `http` → `server` → `location`. A value set at the `http` level applies to all
> enclosed contexts unless explicitly overridden. When troubleshooting unexpected
> streaming behavior, check for inherited values from outer scopes.

---

### Dynamic Configuration (0.6.1+)

Dynamic configuration (dynconf) enables runtime modification of select
module parameters without restarting NGINX.  A periodic timer (1s
interval) polls a configuration file for changes and applies them
using a two-phase, staged-commit model.

**Scope:** Dynconf supports a **single global watcher per worker
process**.  If multiple location blocks configure different
`markdown_dynamic_config_path` values, only the first one that
initializes takes effect; subsequent attempts are logged as warnings
and ignored.  Configure dynconf at the `http` or `server` level to
avoid ambiguity.

**Atomicity:** The entire configuration file is parsed into a staging
snapshot.  Only if **every** line parses and validates successfully
is the staging snapshot atomically promoted to the active snapshot.
On any parse error, the staging is discarded and the active snapshot
remains unchanged — partial updates are never applied.

**Request Consistency:** Each request binds a pointer to the active
snapshot at the time it enters the header filter.  The body filter,
conversion, and logging all read from this bound snapshot for the
lifetime of the request, even if a concurrent timer reload swaps the
global active snapshot.  This guarantees that a single request never
observes a mix of old and new configuration values.

**No File I/O on the Request Path:** The timer handler performs all
file reading and parsing.  The request path never opens or reads the
dynconf file, eliminating blocking I/O from the request latency path.

#### markdown_dynamic_config

**Syntax:** `markdown_dynamic_config on | off;`  
**Default:** `off`  
**Context:** http, server, location

> **Single-instance scope:** Only the first-initialized watcher wins; subsequent
> `markdown_dynamic_config on` directives in other location blocks are ignored
> with a warning. See Scope above.

Enables or disables the dynamic configuration watcher.  When `on`,
the worker process watches the file specified by
`markdown_dynamic_config_path` for modification-time changes.

#### markdown_dynamic_config_path

**Syntax:** `markdown_dynamic_config_path <path>;`  
**Default:** (none)  
**Context:** http, server, location

Path to the dynamic configuration file.  Only effective when
`markdown_dynamic_config on` is set.

**Supported runtime keys** (no NGINX restart required):

| Key | Value | Maps to |
|-----|-------|---------|
| `markdown_filter` | `on` \| `off` | `conf->enabled`, overrides complex values |
| `prune_noise` | `on` \| `off` | `conf->advanced.prune_noise` |
| `log_verbosity` | `error` \| `warn` \| `info` \| `debug` | `conf->policy.log_verbosity` (module enum) |
| `streaming_budget` | `<size>` (e.g. `64k`, `4m`) | `conf->stream.budget` |
| `memory_budget` | `<size>` (e.g. `128k`) | `conf->advanced.memory_budget` |

**Not supported via dynconf** (require `nginx -s reload`):
`markdown_content_types`, `markdown_stream_types`, auth policy,
conditional requests, and all other structural directives.

**Example dynconf file:**
```ini
# Runtime-safe overrides (no NGINX restart)
markdown_filter=on
prune_noise=off
log_verbosity=warn
streaming_budget=4m
memory_budget=128k
```

**Safety Recommendations:**
- Dynconf is off by default.  Enable it only when operational
  workflows require hot-reload without restart.
- Place the dynconf file on a local filesystem (not NFS) to ensure
  reliable mtime detection.
- Validate the file contents before writing (e.g. via `make
  test-nginx-unit`) — an invalid file is rejected atomically but
  the error is only visible in the worker error log.
- Do not configure different `markdown_dynamic_config_path` values
  in multiple location blocks; the global single-watcher constraint
  means only one will take effect.

#### markdown_dynconf_dry_run

**Syntax:** `markdown_dynconf_dry_run on | off;`
**Default:** `off`
**Context:** http, server, location

Validates a pending dynconf change without applying it to the active
snapshot. When enabled, the dynconf timer parses and validates the
configuration file normally but does not promote the staging snapshot
to active. Validation results (success or per-line errors) are logged
at `info` level.

Use dry-run mode to pre-flight configuration changes in production
before committing them.

**Example:**
```nginx
# Enable dry-run validation (changes are checked but not applied)
markdown_dynconf_dry_run on;
```

**Workflow:**
1. Set `markdown_dynconf_dry_run on;` and reload NGINX.
2. Write the new dynconf file.
3. Wait for the timer cycle (1s) and check the error log for
   validation results.
4. If validation passes, set `markdown_dynconf_dry_run off;` and
   reload to apply the change.

---

### LLM Token Estimation

#### markdown_llm_provider

**Syntax:** `markdown_llm_provider default | openai-gpt | anthropic-claude | google-gemini | meta-llama;`
**Default:** `default`
**Context:** http, server, location

LLM provider for token estimation. Each provider has a characteristic chars-per-token ratio that improves estimate accuracy for that provider's tokenizer family.

| Provider | Chars/Token | Context Window |
|----------|-------------|----------------|
| `default` | 4.0 | not enforced |
| `openai-gpt` | 3.8 | 128,000 |
| `anthropic-claude` | 3.6 | 200,000 |
| `google-gemini` | 4.2 | 1,000,000 |
| `meta-llama` | 3.8 | 128,000 |

Requires `markdown_token_estimate on;` to have any effect.

**Example:**
```nginx
markdown_token_estimate on;
markdown_llm_provider openai-gpt;
```

---

#### markdown_chars_per_token

**Syntax:** `markdown_chars_per_token <number>;`
**Default:** `0` (use provider default)
**Context:** http, server, location

Explicit chars-per-token ratio for token estimation, stored as an integer
fixed-point value multiplied by 10 (e.g., `38` = 3.8 chars/token). Overrides
both the default (4.0) and the provider-specific ratio. Set to `0` to use the
provider's default.

**Range:** 0-255. Non-zero values encode raw ratios from 0.1 to 25.5
chars/token; decoded values below 1.0 are normalized to the effective minimum
1.0 before token estimation. Practical values are usually 20-60 (2.0-6.0
chars/token).

**Example:**
```nginx
markdown_token_estimate on;
markdown_chars_per_token 38;
```

---

### OpenTelemetry Integration

#### markdown_otel

**Syntax:** `markdown_otel on | off;`
**Default:** `off`
**Context:** http, server, location

Enable OpenTelemetry span creation for conversion requests. When enabled, each conversion creates a span with attributes for flavor, engine, content_type, input/output bytes, and reason code.

**Example:**
```nginx
markdown_otel on;
markdown_otel_endpoint /_otel_export;
```

---

#### markdown_otel_endpoint

**Syntax:** `markdown_otel_endpoint <uri>;`
**Default:** empty (no endpoint configured)
**Context:** http, server, location

Internal NGINX URI for OTel span export via subrequest. The module issues an HTTP POST to this URI using `ngx_http_subrequest()`, sending the OTLP JSON payload as the request body.

This URI must map to an `internal` location block in `nginx.conf` that proxy_passes to the OTel collector:

```nginx
location = /_otel_export {
    internal;
    proxy_pass http://collector:4318/v1/traces;
}

markdown_otel on;
markdown_otel_endpoint /_otel_export;
```

---

#### markdown_otel_tracing

**Syntax:** `markdown_otel_tracing on | off;`
**Default:** `off`
**Context:** http, server, location

Enable OTel span creation for conversion request tracing. When enabled, each conversion creates a span with trace context propagation and conversion attributes.

---

#### markdown_otel_metrics

**Syntax:** `markdown_otel_metrics on | off;`
**Default:** `off`
**Context:** http, server, location

Enable OTel metrics export via OTLP protocol.

---

#### markdown_otel_service_name

**Syntax:** `markdown_otel_service_name <name>;`
**Default:** `nginx-markdown`
**Context:** http, server, location

Service name label for OTel resource attributes.

**Example:**
```nginx
markdown_otel_service_name my-nginx-markdown;
```

---

#### markdown_otel_span_buffer_size

**Syntax:** `markdown_otel_span_buffer_size <number>;`
**Default:** `1024`
**Context:** http, server, location

Buffer size for spans when the collector is unreachable. Buffered spans are retried on the next export window.

---

#### markdown_otel_export_timeout

**Syntax:** `markdown_otel_export_timeout <time>;`
**Default:** `5s`
**Context:** http, server, location

Timeout for OTLP HTTP export requests.

**Example:**
```nginx
markdown_otel_export_timeout 10s;
```

---

### Large Response Processing

#### markdown_large_body_threshold

**Syntax:** `markdown_large_body_threshold off | <size>;`  
**Default:** `off`  
**Context:** http, server, location
**Status:** REMOVED in 0.9.0 — no direct replacement

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

**Security:** The module handler enforces localhost-only access (`127.0.0.1`,
`::1`). NGINX `allow`/`deny` rules can further restrict access, but they do
not broaden access beyond localhost.

This differs from `markdown_diagnostics`: diagnostics defaults to loopback but
can be exposed to selected CIDRs with `markdown_diagnostics_allow`.

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
- `requests_entered`: Requests that reached the module decision chain
- `conversions_attempted`: Total conversion attempts
- `conversions_succeeded`: Successful conversions
- `conversions_failed`: Engine-level failures (includes fail-open errors; for response-level failure count, subtract `precommit_failopen_total` or use `failures_conversion` + `failures_resource_limit` + `failures_system`)
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
- `skips`: Object containing per-reason skip counters (`config`, `method`,
  `status`, `content_type`, `size`, `streaming`, `auth`, `range`, `accept`)
- `failopen_count`: Fail-open responses where original HTML was served
- `estimated_token_savings`: Cumulative token-savings estimate
- `streaming_path_hits`: Feature-gated requests routed to the streaming path
- `streaming`: Feature-gated streaming object with outcome, fallback,
  shadow, TTFB, and peak-memory counters


#### markdown_metrics_format

**Syntax:** `markdown_metrics_format auto | prometheus;`
**Default:** `auto`
**Context:** http, server, location

Controls the output format of the `markdown_metrics` endpoint for non-JSON requests.

- `auto`: JSON for `Accept: application/json`, plain text for `Accept: text/plain`,
  and plain text for Prometheus-style Accept values (default).
- `prometheus`: Prometheus text exposition format for explicit Prometheus Accept
  values (`text/plain; version=0.0.4` or `application/openmetrics-text`). JSON
  output remains available regardless of this setting.

When `markdown_metrics_format prometheus` is configured, Prometheus scrapes should
send an explicit Prometheus Accept header. Plain `Accept: text/plain` requests keep
the human-readable text format.

**Example:**
```nginx
location /markdown-metrics {
    markdown_metrics;
    markdown_metrics_format prometheus;
}
```

See [Prometheus Metrics Guide](prometheus-metrics.md) for scrape configuration
and metric catalog.

#### markdown_metrics_shm_size

**Syntax:** `markdown_metrics_shm_size <size>;`
**Default:** `8 * ngx_pagesize` (typically 32k or 64k depending on platform)
**Context:** http

Sets the size of the shared-memory zone used to aggregate metrics across NGINX worker
processes. The shared-memory zone holds the metrics struct that all workers update
atomically.

This directive must appear in the `http` context, not in `server` or `location`.

**Valid Units:** `k` (kilobytes), `m` (megabytes)

**Example:**
```nginx
http {
    markdown_metrics_shm_size 128k;
}
```

**Notes:**
- The default size is sufficient for typical deployments. Increase only if the
  metrics struct grows due to new fields in a future release.
- When the SHM-backed metrics struct layout changes between releases, the module
  bumps the SHM zone name to prevent hot-reload layout mismatch. A full restart
  is recommended after upgrading.

#### markdown_metrics_per_path

**Syntax:** `markdown_metrics_per_path on | off;`
**Default:** `off`
**Context:** http, server, location

Enable per-URL-path metrics tracking. When enabled, the top-N most-hit URI paths are tracked individually alongside global aggregates. Per-path data is exposed in the metrics endpoint under the `per_path` key.

**Example:**
```nginx
markdown_metrics_per_path on;
markdown_metrics_per_path_cardinality 200;
```

---

#### markdown_metrics_per_path_cardinality

**Syntax:** `markdown_metrics_per_path_cardinality <number>;`
**Default:** `100`
**Context:** http only

Maximum number of distinct URI paths tracked individually in the per-path RB-tree. When this limit is reached, further unique paths are counted in the overflow aggregate and appear under the `__other__` pseudo-path in output.

This is a global (http-level) setting because the per-path limit is stored in shared memory and applies across all server and location blocks.

**Example:**
```nginx
http {
    markdown_metrics_per_path on;
    markdown_metrics_per_path_cardinality 200;
}
```

---

#### markdown_diagnostics

**Syntax:** `markdown_diagnostics on | off;`
**Default:** `off`
**Context:** http, server, location

Enables the runtime diagnostics endpoint at `/nginx-markdown/diagnostics`.
When enabled, the endpoint exposes a JSON object containing:

- `config_snapshot`: current active dynconf values
- `recent_decisions`: ring buffer of recent request decision summaries
  (reason code, duration)
- `metrics_snapshot`: key counters (conversions, deliveries, errors)
- `dynconf_state`: active mtime, config version, LKG mtime

Access is restricted by default: only loopback addresses (127.0.0.1, ::1)
are permitted unless `markdown_diagnostics_allow` is configured. Unlike
`markdown_metrics`, diagnostics can be exposed to selected non-loopback CIDRs.
Treat diagnostics output as sensitive operational data and prefer `internal`,
VPN CIDRs, or management-network CIDRs instead of public exposure.

#### markdown_diagnostics_allow

**Syntax:** `markdown_diagnostics_allow <CIDR>;`
**Default:** none (loopback-only when empty)
**Context:** http, server, location

Adds a CIDR address to the diagnostics endpoint allow list. Multiple
directives can be specified to allow multiple networks. When at least one
`markdown_diagnostics_allow` directive is present, only matching client
addresses are permitted; the loopback fallback is replaced by the explicit
list.

**Example:**
```nginx
location /nginx-markdown/diagnostics {
    markdown_diagnostics on;
    markdown_diagnostics_allow 127.0.0.1/32;
    markdown_diagnostics_allow ::1/128;
    markdown_diagnostics_allow 10.0.0.0/8;
}
```

**Notes:**
- The diagnostics endpoint is read-only and does not modify module state.
- The `recent_decisions` ring buffer holds up to 100 entries by default.
- When no `markdown_diagnostics_allow` is configured, only loopback
  addresses are permitted (built-in default-deny).
- For additional access control, NGINX's native `allow`/`deny` directives
  can be used alongside `markdown_diagnostics_allow`.
- Disable in production if the endpoint is not needed to avoid exposing
  internal state.

---

### Deprecated Directives

The following directives are deprecated and will be removed in a future major release. They are still accepted with info-level warnings to allow gradual migration.

#### markdown_max_size

**Syntax:** `markdown_max_size <size>;`
**Default:** `10m`
**Context:** http, server, location
**Status:** REMOVED in 0.9.0 — use `markdown_limits memory=<size>`

Maximum response size to attempt conversion. Responses larger than this will not be converted.

**Migration:**
```nginx
# Before (removed in 0.9.0)
markdown_max_size 5m;

# After
markdown_limits memory=5m;
```

---

### Removed Directives

The following directives have been removed in v0.8.0 and are no longer accepted. `nginx -t` will fail with "unknown directive" if any of these appear in configuration.

#### markdown_streaming_auto_threshold — REMOVED in 0.8.0

This directive has been **removed** in v0.8.0. It is no longer registered.
Use `markdown_stream_threshold` instead.

**Migration:**
```nginx
# Before (0.6.x/0.7.x) — NO LONGER ACCEPTED IN 0.8.0
markdown_streaming_auto_threshold 64k;

# After (0.8.0) — required
markdown_stream_threshold 64k;
```

---

## Configuration Examples

### Basic Setup

Enable conversion globally for all content:

```nginx
http {
    # Enable markdown filter globally
    markdown_filter on;
    
    # Configure resource limits
    markdown_memory_budget 10m;
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
            markdown_memory_budget 5m;  # Smaller limit for blog posts
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
        markdown_accept wildcard;
        markdown_filter $convert_html;
    }
}
```

**Why this pattern is recommended:**
- `map $http_accept` with exact string values is brittle; many clients send `Accept` with multiple values.
- `$request_uri` includes query strings (for example `/index.html?x=1`) and can break extension matching.
- `markdown_accept wildcard` is required if you intentionally treat `text/*` as convertible.

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
    markdown_memory_budget 5m;
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
    markdown_memory_budget 2m;
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
    markdown_memory_budget 10m;
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
            markdown_memory_budget 5m;  # Smaller limit
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
    markdown_memory_budget 10m;        # Inherited by all servers/locations
    
    server {
        location /docs {
            # Inherits: markdown_filter on, markdown_memory_budget 10m
        }
    }
}
```

**Result:** `/docs` has `markdown_filter on` and `markdown_memory_budget 10m`

---

#### Example 2: Override at Location Level

```nginx
http {
    markdown_filter on;
    markdown_memory_budget 10m;
    
    server {
        location /docs {
            markdown_memory_budget 5m;  # Override
            # Inherits: markdown_filter on
        }
    }
}
```

**Result:** `/docs` has `markdown_filter on` and `markdown_memory_budget 5m` (overridden)

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
    markdown_memory_budget 10m;
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
            # Inherits: markdown_memory_budget 10m (from http)
        }
    }
}
```

---

#### Example 5: Complex Inheritance

```nginx
http {
    markdown_filter on;
    markdown_memory_budget 10m;
    markdown_timeout 5s;
    markdown_flavor commonmark;
    
    server {
        markdown_timeout 3s;  # Override
         
        location /docs {
            markdown_flavor gfm;  # Override
            markdown_memory_budget 5m; # Override
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
    markdown_memory_budget 10m;
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
            markdown_memory_budget 5m;
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
markdown_memory_budget 5m;      # Limit response size
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
markdown_memory_budget 2m;      # Smaller limit
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
curl -H "Accept: text/plain" http://localhost/markdown-metrics

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
    markdown_memory_budget 10m;
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
    markdown_memory_budget 10m;
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
    markdown_memory_budget 2m;
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
curl -H "Accept: text/plain" http://localhost/markdown-metrics

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
4. Response size within `markdown_memory_budget` limit

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
1. `markdown_memory_budget` and `markdown_timeout` are appropriate
2. Caching is enabled
3. Optional features are disabled if not needed

**Debug:**
```bash
# Check metrics
curl -H "Accept: text/plain" http://localhost/markdown-metrics

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

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Added `markdown_streaming_engine`, `markdown_streaming_budget`, `markdown_metrics_format`, and `markdown_metrics_shm_size` directive documentation previously missing from this guide; added update tracking section |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.7.0 | 2026-05-17 | Kang | Added `markdown_dynconf_dry_run` directive documentation |
| 0.7.0 | 2026-05-18 | Kang | Added `markdown_diagnostics` directive documentation |
| 0.8.0 | 2026-06-16 | Kang | Cross-reference audit: fixed stale `markdown_streaming_auto_threshold` reference in streaming section intro to use `markdown_stream_threshold` |
| 0.8.0 | 2026-06-16 | Codex | Added missing directive documentation: `markdown_content_types`, `markdown_prune_noise`, `markdown_prune_selectors`, `markdown_prune_protection_selectors`, `markdown_llm_provider`, `markdown_chars_per_token`, OpenTelemetry family (`markdown_otel`, `markdown_otel_endpoint`, `markdown_otel_tracing`, `markdown_otel_metrics`, `markdown_otel_service_name`, `markdown_otel_span_buffer_size`, `markdown_otel_export_timeout`), `markdown_metrics_per_path`, `markdown_metrics_per_path_cardinality`; added deprecated directives section for `markdown_max_size` and `markdown_streaming_auto_threshold` |
| 0.8.3 | 2026-06-26 | Kang | No configuration changes; version alignment with 0.8.3 release |
