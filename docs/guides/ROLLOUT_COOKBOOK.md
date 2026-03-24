# Rollout Cookbook — Controlled Enablement Guide

## Table of Contents

1. [Overview](#overview)
2. [Before You Start](#before-you-start)
3. [Rollout Stages](#rollout-stages)
   - [Stage 1: Internal/Staging — Single Path](#stage-1-internalstaging--single-path)
   - [Stage 2: Internal/Staging — Multiple Paths](#stage-2-internalstaging--multiple-paths)
   - [Stage 3: Production — Single Low-Traffic Path](#stage-3-production--single-low-traffic-path)
   - [Stage 4: Production — Broader Scope](#stage-4-production--broader-scope)
4. [Selective Enablement Patterns](#selective-enablement-patterns)
   - [Path-Based Enablement](#path-based-enablement)
   - [Host-Based Enablement](#host-based-enablement)
   - [Accept-Header-Based Enablement](#accept-header-based-enablement)
   - [Bot / User-Agent-Based Enablement](#bot--user-agent-based-enablement)
   - [Internal-Only (IP-Range Gating)](#internal-only-ip-range-gating)
   - [Canary (Percentage-Based)](#canary-percentage-based)
   - [Header-Gated (Controlled Testing)](#header-gated-controlled-testing)
5. [Page Types Not Recommended for Initial Enablement](#page-types-not-recommended-for-initial-enablement)
   - [Why These Page Types Are Risky](#why-these-page-types-are-risky)
   - [Recommended Starting Points](#recommended-starting-points)
   - [Excluding Page Types from Conversion Scope](#excluding-page-types-from-conversion-scope)
6. [Conservative Default Configuration](#conservative-default-configuration)
   - [Why These Defaults Matter](#why-these-defaults-matter)
   - [Changing Defaults During Rollout](#changing-defaults-during-rollout)
7. [Observation Guidance](#observation-guidance)
   - [Metrics to Monitor](#metrics-to-monitor)
   - [Log Patterns to Check](#log-patterns-to-check)
   - [Checking the Metrics Endpoint](#checking-the-metrics-endpoint)
   - [Healthy Rollout Indicators](#healthy-rollout-indicators)
   - [Stop and Investigate Triggers](#stop-and-investigate-triggers)

---

## Overview

This cookbook walks you through enabling the Markdown filter module in a "start small, then expand" sequence. Each stage narrows the blast radius so you can observe behavior, confirm safety, and expand with confidence.

The recommended approach:

1. Pick a single, low-traffic, static-content path (e.g., `/docs` or `/help`).
2. Enable on an internal or staging host first.
3. Observe for at least one full traffic cycle before expanding.
4. Expand gradually — more paths, then more hosts.

All patterns in this cookbook use existing NGINX configuration primitives (`map`, `geo`, `split_clients`, `location` blocks) combined with the module's `markdown_filter $variable` capability. No new directives are required.

### Target Audience

- Site Reliability Engineers (SREs)
- DevOps Engineers
- System Administrators

### Related Documents

- [Decision Chain Model](../features/DECISION_CHAIN.md) — check order, reason codes, and outcome determination
- [CONFIGURATION.md](CONFIGURATION.md) — full directive reference
- [DEPLOYMENT_EXAMPLES.md](DEPLOYMENT_EXAMPLES.md) — deployment patterns and verification
- [OPERATIONS.md](OPERATIONS.md) — operational guide and metrics reference

---

## Before You Start

### Verify Module Installation

Confirm the module loads and the converter initializes:

```bash
nginx -t
sudo tail -20 /var/log/nginx/error.log | grep markdown
# Expected: "markdown filter: converter initialized"
```

### Verify Metrics Endpoint

Confirm the metrics endpoint responds:

```bash
curl -s http://localhost/markdown-metrics | head -5
```

### Record Baseline Metrics

Capture current metrics before enabling conversion:

```bash
curl -s -H "Accept: application/json" \
  http://localhost/markdown-metrics > /tmp/baseline-metrics.json
```

### Back Up Configuration

```bash
cp /usr/local/nginx/conf/nginx.conf \
   /usr/local/nginx/conf/nginx.conf.pre-rollout
```

### Recommended Initial Settings

Use these conservative defaults during rollout:

| Directive | Recommended Value | Rationale |
|-----------|-------------------|-----------|
| `markdown_filter` | `off` (global) | No conversion without explicit opt-in |
| `markdown_on_error` | `pass` | Conversion failures serve original HTML |
| `markdown_on_wildcard` | `off` | Only explicit `Accept: text/markdown` triggers conversion |
| `markdown_log_verbosity` | `info` | Decision log entries visible for all outcomes |

Keep `markdown_on_wildcard off` during initial rollout. This limits conversion to clients that explicitly send `Accept: text/markdown`, preventing unexpected conversion for browsers sending `Accept: */*`.

Do not change `markdown_on_error` to `reject` during initial rollout. Fail-open (`pass`) ensures conversion failures never break client responses.

---

## Rollout Stages

### Stage 1: Internal/Staging — Single Path

Enable conversion for one path on an internal or staging host. Pick a low-traffic, static-content path such as `/docs` or `/help`.

#### Configuration

```nginx
load_module modules/ngx_http_markdown_filter_module.so;

http {
    markdown_filter off;
    markdown_on_error pass;
    markdown_on_wildcard off;
    markdown_log_verbosity info;
    markdown_max_size 10m;
    markdown_timeout 5s;

    server {
        listen 80;
        server_name staging.example.com;

        location /docs {
            markdown_filter on;
            proxy_pass http://backend;
        }

        location / {
            proxy_pass http://backend;
        }
    }
}
```

#### Apply

```bash
nginx -t && nginx -s reload
```

#### Observation Checkpoint

Wait at least 30 minutes, then verify:

```bash
# Check for conversion activity
curl -s http://localhost/markdown-metrics | \
  grep -E "conversions_(attempted|succeeded|failed)"

# Check decision log entries
grep "markdown decision:" /var/log/nginx/error.log | tail -10

# Check for failure reason codes
grep "markdown decision:" /var/log/nginx/error.log | \
  grep -c "reason=ELIGIBLE_FAILED"

# Verify a test request converts
curl -sD - -o /dev/null \
  -H "Accept: text/markdown" \
  http://staging.example.com/docs/
# Expected: Content-Type: text/markdown; charset=utf-8
```

#### Safe to Continue

- Conversion success rate > 95% (few or no `ELIGIBLE_FAILED_OPEN` / `ELIGIBLE_FAILED_CLOSED` entries)
- No `FAIL_SYSTEM` category codes in logs
- Conversion latency within the configured `markdown_timeout`
- No upstream error rate increase
- No unexpected `SKIP_*` reason codes for requests you expect to convert

#### Stop and Investigate

- Sudden increase in `ELIGIBLE_FAILED_OPEN` or `ELIGIBLE_FAILED_CLOSED` counts
- Any `FAIL_SYSTEM` category codes
- Conversion latency exceeding `markdown_timeout`
- Upstream error rate increase correlated with module enablement
- Unexpected `Content-Type` in converted responses

---

### Stage 2: Internal/Staging — Multiple Paths

Expand to additional paths on the same staging host.

#### Configuration

```nginx
http {
    markdown_filter off;
    markdown_on_error pass;
    markdown_on_wildcard off;
    markdown_log_verbosity info;
    markdown_max_size 10m;
    markdown_timeout 5s;

    server {
        listen 80;
        server_name staging.example.com;

        location /docs {
            markdown_filter on;
            proxy_pass http://backend;
        }

        location /help {
            markdown_filter on;
            proxy_pass http://backend;
        }

        location /blog {
            markdown_filter on;
            proxy_pass http://backend;
        }

        # Keep API and static assets excluded
        location /api {
            proxy_pass http://backend;
        }

        location / {
            proxy_pass http://backend;
        }
    }
}
```

#### Apply

```bash
nginx -t && nginx -s reload
```

#### Observation Checkpoint

Wait at least 1 hour, then verify:

```bash
# Check overall conversion metrics
curl -s -H "Accept: application/json" \
  http://localhost/markdown-metrics

# Check reason code distribution
grep "markdown decision:" /var/log/nginx/error.log | \
  grep -oP 'reason=\K[A-Z_]+' | sort | uniq -c

# Check for failures across all enabled paths
grep "markdown decision:" /var/log/nginx/error.log | \
  grep "reason=ELIGIBLE_FAILED" | \
  grep -oP 'uri=\K[^ ]+' | sort | uniq -c
```

#### Safe to Continue

- Same criteria as Stage 1, applied across all enabled paths
- No path-specific failure patterns (one path failing more than others)
- Stable or decreasing `conversions_failed` count over the observation period

#### Stop and Investigate

- Same triggers as Stage 1
- One path showing significantly higher failure rate than others
- New `SKIP_CONTENT_TYPE` or `SKIP_SIZE` patterns indicating unexpected upstream responses

---

### Stage 3: Production — Single Low-Traffic Path

Enable on one production path with low traffic. Minimum observation period: 24 hours.

#### Configuration

```nginx
http {
    markdown_filter off;
    markdown_on_error pass;
    markdown_on_wildcard off;
    markdown_log_verbosity info;
    markdown_max_size 10m;
    markdown_timeout 5s;

    # Staging server (already enabled from Stage 2)
    server {
        listen 80;
        server_name staging.example.com;

        location /docs {
            markdown_filter on;
            proxy_pass http://backend;
        }

        location /help {
            markdown_filter on;
            proxy_pass http://backend;
        }

        location /blog {
            markdown_filter on;
            proxy_pass http://backend;
        }

        location / {
            proxy_pass http://backend;
        }
    }

    # Production server — single path enabled
    server {
        listen 80;
        server_name www.example.com;

        location /docs {
            markdown_filter on;
            proxy_pass http://backend;
        }

        location / {
            proxy_pass http://backend;
        }
    }
}
```

#### Apply

```bash
nginx -t && nginx -s reload
```

#### Observation Checkpoint

Wait at least 24 hours to cover a full traffic cycle, then verify:

```bash
# Check production conversion metrics
curl -s http://localhost/markdown-metrics | \
  grep -E "conversions_(attempted|succeeded|failed)"

# Check for failure reason codes in the last 24 hours
grep "markdown decision:" /var/log/nginx/error.log | \
  grep "reason=ELIGIBLE_FAILED" | wc -l

# Check reason code distribution
grep "markdown decision:" /var/log/nginx/error.log | \
  grep -oP 'reason=\K[A-Z_]+' | sort | uniq -c

# Verify conversion latency is within bounds
curl -s -H "Accept: application/json" \
  http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
m = json.load(sys.stdin)
b = m.get('conversion_latency_buckets', {})
total = sum(b.values()) if b else 0
if total > 0:
    for k, v in b.items():
        print(f'{k}: {v} ({v*100//total}%)')
else:
    print('No conversions recorded yet')
"
```

#### Safe to Continue

- All Stage 1 criteria hold over a full 24-hour period
- No increase in `ELIGIBLE_FAILED_OPEN` or `ELIGIBLE_FAILED_CLOSED` counts relative to conversion volume
- No `FAIL_SYSTEM` category codes
- Conversion latency within configured `markdown_timeout`
- Stable or decreasing failure count over the 24-hour observation period
- No upstream error rate increase correlated with module enablement

#### Stop and Investigate

- All Stage 1 triggers apply
- Failure rate exceeding 5% of conversion attempts over any 1-hour window
- Latency spikes correlated with peak traffic periods
- Client reports of unexpected content

---

### Stage 4: Production — Broader Scope

Expand to additional production paths or hosts. Continue using 24-hour observation periods between expansions.

#### Configuration

Use a `map` directive for flexible path-based control instead of per-`location` enablement:

```nginx
http {
    map $uri $markdown_enabled {
        default         off;
        "~^/docs"       on;
        "~^/help"       on;
        "~^/blog"       on;
        "~^/guides"     on;
    }

    markdown_on_error pass;
    markdown_on_wildcard off;
    markdown_log_verbosity info;
    markdown_max_size 10m;
    markdown_timeout 5s;

    server {
        listen 80;
        server_name www.example.com;

        location / {
            markdown_filter $markdown_enabled;
            proxy_pass http://backend;
        }

        # Explicit exclusions for safety
        location /api {
            markdown_filter off;
            proxy_pass http://backend;
        }

        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg)$ {
            markdown_filter off;
            proxy_pass http://backend;
        }
    }
}
```

#### Apply

```bash
nginx -t && nginx -s reload
```

#### Observation Checkpoint

Wait at least 24 hours per expansion step, then verify:

```bash
# Full metrics snapshot
curl -s -H "Accept: application/json" \
  http://localhost/markdown-metrics

# Reason code distribution
grep "markdown decision:" /var/log/nginx/error.log | \
  grep -oP 'reason=\K[A-Z_]+' | sort | uniq -c

# Per-path failure check
grep "markdown decision:" /var/log/nginx/error.log | \
  grep "reason=ELIGIBLE_FAILED" | \
  grep -oP 'uri=\K[^ ]+' | sort | uniq -c

# Verify no FAIL_SYSTEM codes
grep "markdown decision:" /var/log/nginx/error.log | \
  grep -c "category=FAIL_SYSTEM"
```

#### Safe to Continue

- All Stage 3 criteria hold across all enabled paths
- No path-specific failure patterns
- Conversion volume scales proportionally with traffic without latency degradation

#### Stop and Investigate

- All Stage 3 triggers apply
- Any single path showing failure rate above 5%
- Overall conversion latency trending upward over the observation period

---

## Selective Enablement Patterns

These patterns let you target conversion to specific traffic segments using NGINX configuration primitives and the module's `markdown_filter $variable` capability.

### Path-Based Enablement

Enable conversion for specific URL paths using `location` blocks or a `map` on `$uri`.

#### Using Location Blocks

The simplest approach — enable `markdown_filter on` in specific `location` blocks:

```nginx
http {
    markdown_filter off;
    markdown_on_error pass;
    markdown_on_wildcard off;

    server {
        listen 80;
        server_name example.com;

        location /docs {
            markdown_filter on;
            proxy_pass http://backend;
        }

        location /help {
            markdown_filter on;
            proxy_pass http://backend;
        }

        location / {
            proxy_pass http://backend;
        }
    }
}
```

#### Using map $uri

For flexible path patterns without creating many `location` blocks:

```nginx
http {
    map $uri $markdown_by_path {
        default         off;
        "~^/docs/"      on;
        "~^/help/"      on;
        "~^/blog/"      on;
        "~*\.html$"     on;
    }

    markdown_on_error pass;
    markdown_on_wildcard off;

    server {
        listen 80;
        server_name example.com;

        location / {
            markdown_filter $markdown_by_path;
            proxy_pass http://backend;
        }

        location /api {
            markdown_filter off;
            proxy_pass http://backend;
        }
    }
}
```

Start with a single low-traffic static-content path (e.g., `/docs` or `/help`) before expanding the `map` to broader patterns.

---

### Host-Based Enablement

Enable conversion for specific virtual hosts. Use this to test on a staging or internal host before enabling on production hosts.

#### Using Per-Server Blocks

```nginx
http {
    markdown_filter off;
    markdown_on_error pass;
    markdown_on_wildcard off;

    # Staging host — conversion enabled
    server {
        listen 80;
        server_name staging.example.com;

        markdown_filter on;

        location / {
            proxy_pass http://backend;
        }
    }

    # Production host — conversion disabled
    server {
        listen 80;
        server_name www.example.com;

        location / {
            proxy_pass http://backend;
        }
    }
}
```

#### Using map $host

For multi-host control from a single `server` block or shared configuration:

```nginx
http {
    map $host $markdown_by_host {
        default                 off;
        staging.example.com     on;
        internal.example.com    on;
    }

    markdown_on_error pass;
    markdown_on_wildcard off;

    server {
        listen 80;
        server_name staging.example.com www.example.com;

        location / {
            markdown_filter $markdown_by_host;
            proxy_pass http://backend;
        }
    }
}
```

Start with an internal or staging host before adding production hosts to the `map`.

---

### Accept-Header-Based Enablement

Enable conversion only for requests that explicitly include `Accept: text/markdown`. This targets clients that opt in to Markdown content.

#### Configuration

```nginx
http {
    # Parse Accept header — handles multi-value headers with q-factors
    # Matches "text/markdown" anywhere in the Accept value, including
    # comma-separated lists like "text/html, text/markdown;q=0.9"
    map $http_accept $markdown_by_accept {
        default                                     off;
        "~*(^|,)\s*text/markdown(\s*;|,|$)"         on;
    }

    markdown_on_error pass;
    markdown_on_wildcard off;

    server {
        listen 80;
        server_name example.com;

        location / {
            markdown_filter $markdown_by_accept;
            proxy_pass http://backend;
        }
    }
}
```

Keep `markdown_on_wildcard off` (the default) during initial rollout. With `off`, only explicit `text/markdown` in the Accept header triggers conversion. Clients sending `Accept: */*` or `Accept: text/*` receive HTML unchanged.

If you later want wildcard Accept values (e.g., `text/*`) to trigger conversion, set `markdown_on_wildcard on` and expand the `map`:

```nginx
    map $http_accept $markdown_by_accept {
        default                                     off;
        "~*(^|,)\s*text/markdown(\s*;|,|$)"         on;
        "~*(^|,)\s*text/\*(\s*;|,|$)"              on;
    }

    server {
        listen 80;
        server_name example.com;

        location / {
            markdown_filter $markdown_by_accept;
            markdown_on_wildcard on;
            proxy_pass http://backend;
        }
    }
```

---

### Bot / User-Agent-Based Enablement

Enable conversion for specific AI bots or crawlers identified by User-Agent. Combine UA detection with an Accept header override so bots that do not send `Accept: text/markdown` still receive Markdown.

#### Configuration

```nginx
http {
    # Detect known AI bots by User-Agent
    map $http_user_agent $is_ai_bot {
        default         off;
        "~*ClaudeBot"   on;
        "~*GPTBot"      on;
        "~*Googlebot"   on;
    }

    # Override Accept header for detected bots
    map $http_user_agent $bot_accept_override {
        default         "";
        "~*ClaudeBot"   "text/markdown, text/html;q=0.9";
        "~*GPTBot"      "text/markdown, text/html;q=0.9";
        "~*Googlebot"   "text/markdown, text/html;q=0.9";
    }

    # Use the override when present, otherwise keep original Accept
    map $bot_accept_override $final_accept {
        ""      $http_accept;
        default $bot_accept_override;
    }

    markdown_on_error pass;
    markdown_on_wildcard off;

    server {
        listen 80;
        server_name example.com;

        location / {
            markdown_filter $is_ai_bot;
            proxy_set_header Accept $final_accept;
            proxy_pass http://backend;
        }

        location /api {
            markdown_filter off;
            proxy_pass http://backend;
        }
    }
}
```

UA-based targeting depends on clients sending accurate User-Agent strings. It is not a security boundary — any client can spoof a User-Agent. Use this pattern for convenience, not access control.

#### Verification

```bash
# Simulate ClaudeBot — should return Markdown
curl -sD - -o /dev/null -A "ClaudeBot/1.0" \
  http://example.com/docs/
# Expected: Content-Type: text/markdown; charset=utf-8

# Normal browser request — should return HTML
curl -sD - -o /dev/null -H "Accept: text/html" \
  http://example.com/docs/
# Expected: Content-Type: text/html
```

---

### Internal-Only (IP-Range Gating)

Enable conversion only for requests from internal IP ranges. This is the safest pattern for initial testing — external traffic is never affected.

#### Configuration

```nginx
http {
    # Define internal IP ranges
    geo $is_internal {
        default         0;
        10.0.0.0/8      1;
        172.16.0.0/12   1;
        192.168.0.0/16  1;
        127.0.0.1/32    1;
        ::1/128         1;
    }

    # Map internal flag to filter state
    map $is_internal $markdown_internal_only {
        0   off;
        1   on;
    }

    markdown_on_error pass;
    markdown_on_wildcard off;

    server {
        listen 80;
        server_name example.com;

        location /docs {
            markdown_filter $markdown_internal_only;
            proxy_pass http://backend;
        }

        location / {
            proxy_pass http://backend;
        }
    }
}
```

Trade-offs: safest pattern with zero external exposure, but limited to traffic originating from internal networks. Useful for initial validation before any external rollout.

---

### Canary (Percentage-Based)

Enable conversion for a small percentage of traffic using NGINX `split_clients`. This provides broader coverage through statistical sampling without enabling for all requests.

#### Configuration

```nginx
http {
    # Route 5% of traffic to conversion based on remote address
    split_clients $remote_addr $markdown_canary {
        5%      on;
        *       off;
    }

    markdown_on_error pass;
    markdown_on_wildcard off;

    server {
        listen 80;
        server_name example.com;

        location /docs {
            markdown_filter $markdown_canary;
            proxy_pass http://backend;
        }

        location / {
            proxy_pass http://backend;
        }
    }
}
```

Adjust the percentage as confidence grows (e.g., 5% → 25% → 50% → 100%). Each increase should be followed by a 24-hour observation period.

Trade-offs: broader coverage than internal-only, provides statistical sampling of real traffic. However, the same client may see different behavior across requests (conversion is not sticky per client). Use `$remote_addr` for rough client-level consistency, or `$request_id` for per-request randomization.

---

### Header-Gated (Controlled Testing)

Enable conversion only when a specific internal header is present. Use this for precise, on-demand testing — a developer or test harness sends the header to trigger conversion.

#### Configuration

```nginx
http {
    # Enable only when X-Markdown-Enable: true is present
    map $http_x_markdown_enable $markdown_header_gated {
        default     off;
        "true"      on;
        "1"         on;
    }

    markdown_on_error pass;
    markdown_on_wildcard off;

    server {
        listen 80;
        server_name example.com;

        location / {
            markdown_filter $markdown_header_gated;
            proxy_pass http://backend;
        }
    }
}
```

#### Verification

```bash
# With header — should convert
curl -sD - -o /dev/null \
  -H "Accept: text/markdown" \
  -H "X-Markdown-Enable: true" \
  http://example.com/docs/
# Expected: Content-Type: text/markdown; charset=utf-8

# Without header — should return HTML
curl -sD - -o /dev/null \
  -H "Accept: text/markdown" \
  http://example.com/docs/
# Expected: Content-Type: text/html
```

Trade-offs: precise control, ideal for integration testing and QA. Requires client cooperation (the header must be sent explicitly). Not suitable for broad rollout since real clients do not send this header.


---

## Page Types Not Recommended for Initial Enablement

Not all pages are good candidates for Markdown conversion. Some page types produce poor results, trigger eligibility skips, or risk breaking client functionality. Exclude these from your initial rollout scope and expand only after static content paths are stable.

### Why These Page Types Are Risky

| Page Type | Why Not Recommended | Relevant Check |
|-----------|---------------------|----------------|
| Single-Page Applications (SPAs) | SPAs render content via JavaScript after the initial HTML load. The upstream HTML is typically a minimal shell (`<div id="root"></div>`) with no meaningful content to convert. The resulting Markdown is empty or useless. | — (conversion produces poor output) |
| Pages with heavy interactive elements | Forms, dynamic widgets, and JavaScript-driven UI components do not have Markdown equivalents. Conversion strips interactivity and produces a degraded representation that may confuse consuming agents. | — (conversion produces poor output) |
| Authenticated / personalized pages | Pages behind authentication or with per-user content may vary per request, making caching and observation unreliable during rollout. The module detects authentication credentials and adjusts cache-control headers accordingly, but does not currently block eligibility based on authentication status. Exclude these pages from conversion scope using `location` blocks or `map` directives. | — (exclude via configuration) |
| Non-text content pages | Pages serving images, video, downloads, or other binary content return a `Content-Type` other than `text/html`. The module skips these automatically. Enabling conversion scope for paths that serve mixed content types adds noise to your decision logs without producing conversions. | `SKIP_CONTENT_TYPE` — Content-Type not text/html |
| API endpoints (JSON / XML) | API endpoints return `application/json`, `application/xml`, or other non-HTML content types. The module skips these via the content-type eligibility check. Including API paths in your conversion scope produces `SKIP_CONTENT_TYPE` log entries with no benefit. | `SKIP_CONTENT_TYPE` — Content-Type not text/html |
| SSE / streaming endpoints | Server-Sent Events and streaming responses have no `Content-Length` or use chunked transfer with unbounded duration. The module detects these as streaming content and skips them. Attempting conversion on unbounded streams would block resources indefinitely. | `SKIP_STREAMING` — unbounded streaming response |

### Recommended Starting Points

Start your rollout with content-heavy pages that have simple, static HTML structure:

- **Static documentation pages** (`/docs`, `/help`, `/guides`) — predictable HTML, low interactivity, high value for AI agents
- **Blog posts** (`/blog`) — article-style content converts cleanly to Markdown
- **Help articles and FAQs** (`/support`, `/faq`) — structured text content with headings and lists
- **Changelogs and release notes** (`/changelog`, `/releases`) — simple HTML, rarely personalized

These page types share common traits that make them ideal first candidates:
- Content is primarily text with headings, paragraphs, lists, and links
- HTML structure is simple and predictable
- Pages are not personalized or authenticated
- Content-Type is consistently `text/html`
- Response sizes are within typical `markdown_max_size` limits

Once these paths are stable (conversion success rate > 95%, no `FAIL_SYSTEM` codes, latency within `markdown_timeout`), expand to additional content paths following the [Rollout Stages](#rollout-stages) sequence.

### Excluding Page Types from Conversion Scope

Use `location` blocks or `map` directives to keep risky page types out of your conversion scope.

#### Using Location Blocks for Explicit Exclusions

The most direct approach — set `markdown_filter off` in `location` blocks for paths you want to exclude, and enable conversion only in specific content paths:

```nginx
http {
    markdown_filter off;
    markdown_on_error pass;
    markdown_on_wildcard off;

    server {
        listen 80;
        server_name example.com;

        # --- Excluded page types ---

        # API endpoints — return JSON/XML, not text/html
        location /api {
            markdown_filter off;
            proxy_pass http://backend;
        }

        # SPA routes — JavaScript-rendered, minimal HTML shell
        location /app {
            markdown_filter off;
            proxy_pass http://backend;
        }

        # Streaming / SSE endpoints
        location /events {
            markdown_filter off;
            proxy_pass http://backend;
        }

        # Authenticated / personalized pages
        location /account {
            markdown_filter off;
            proxy_pass http://backend;
        }
        location /dashboard {
            markdown_filter off;
            proxy_pass http://backend;
        }

        # Static assets — not text/html
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?|ttf)$ {
            markdown_filter off;
            proxy_pass http://backend;
        }

        # --- Enabled content paths ---

        location /docs {
            markdown_filter on;
            proxy_pass http://backend;
        }

        location /blog {
            markdown_filter on;
            proxy_pass http://backend;
        }

        location /help {
            markdown_filter on;
            proxy_pass http://backend;
        }

        # Default — conversion disabled
        location / {
            proxy_pass http://backend;
        }
    }
}
```

#### Using a map Directive for Pattern-Based Exclusions

For more flexible control, use a `map` on `$uri` to define both inclusions and exclusions in one place:

```nginx
http {
    map $uri $markdown_enabled {
        default         off;

        # Enabled — static content paths
        "~^/docs/"      on;
        "~^/blog/"      on;
        "~^/help/"      on;
        "~^/guides/"    on;
        "~^/faq"        on;

        # Excluded — even if a broader pattern above would match
        # (not needed here since default is off, but shown for
        # cases where you use a broad include pattern)
        "~^/api/"       off;
        "~^/app/"       off;
        "~^/events/"    off;
        "~^/account/"   off;
        "~^/dashboard/" off;
    }

    markdown_on_error pass;
    markdown_on_wildcard off;

    server {
        listen 80;
        server_name example.com;

        location / {
            markdown_filter $markdown_enabled;
            proxy_pass http://backend;
        }

        # Explicit override for API — belt and suspenders
        location /api {
            markdown_filter off;
            proxy_pass http://backend;
        }
    }
}
```

The `map` approach is easier to maintain as your rollout scope grows — add or remove paths in the `map` block without creating new `location` blocks. Combine it with explicit `location` overrides for critical exclusions (like `/api`) as a safety net.

Note: Even if a risky page type is accidentally included in your conversion scope, the module's eligibility checks provide a safety net. API endpoints are skipped via `SKIP_CONTENT_TYPE`, streaming endpoints via `SKIP_STREAMING`. However, relying on eligibility checks alone adds noise to your decision logs and metrics. Explicit exclusions keep your rollout scope clean and your observation data meaningful.

---

## Conservative Default Configuration

The module ships with defaults chosen for production safety. Every capability that changes client-visible behavior requires explicit opt-in. This section explains the rationale behind each default and when (if ever) you should change them.

For a quick-reference table of these defaults, see [Before You Start — Recommended Initial Settings](#before-you-start).

### Why These Defaults Matter

#### `markdown_filter off`

The module performs no conversion unless you explicitly enable it per scope. With `off` as the default at the `http` level, adding the module to your NGINX build changes nothing about your site's behavior. Conversion only activates in `location` or `server` blocks where you set `markdown_filter on` or use a `map` variable.

This is the most important default: it means a module upgrade or installation never introduces conversion as a side effect. You control exactly which traffic segments see Markdown responses.

#### `markdown_on_error pass`

When conversion fails (HTML parse error, timeout, memory limit), the module serves the original HTML response unchanged. The client never sees a 502 or broken response due to a conversion problem.

Fail-open (`pass`) is the safe choice for production because conversion is an enhancement, not a requirement. If the converter encounters HTML it cannot handle, the worst outcome is that the client receives the same HTML it would have received without the module. Metrics and decision logs still record the failure (as `ELIGIBLE_FAILED_OPEN`) so you can investigate, but client experience is unaffected.

#### `markdown_on_wildcard off`

With `off`, only requests containing an explicit `Accept: text/markdown` media type trigger conversion. Wildcard Accept values like `Accept: */*` or `Accept: text/*` — which browsers and many HTTP clients send by default — do not trigger conversion.

This prevents accidental conversion of browser traffic. Without this default, a standard browser request (`Accept: text/html, */*`) could match the wildcard and receive Markdown instead of HTML, breaking the page rendering. Keeping `off` during rollout ensures only clients that specifically request Markdown receive it.

#### `markdown_log_verbosity info`

At `info` level, the module emits a decision log entry for every request that enters the decision chain — conversions, skips, and failures alike. This gives you full visibility into module behavior without requiring `debug` level, which adds extended fields (filter value, Accept header, upstream status) and increases log volume.

During rollout, `info` is the right level: you can see every decision the module makes, correlate with metrics, and diagnose unexpected behavior. After rollout stabilizes, you may raise verbosity to `warn` to reduce log volume — at that level, only failure outcomes (`ELIGIBLE_FAILED_OPEN`, `ELIGIBLE_FAILED_CLOSED`) are logged.

### Changing Defaults During Rollout

Most defaults should remain unchanged throughout your initial rollout. The table below summarizes when it is safe to adjust each setting.

| Directive | Safe to Change During Rollout? | Guidance |
|-----------|-------------------------------|----------|
| `markdown_filter` | Yes — this is how you roll out | Enable per scope following the [Rollout Stages](#rollout-stages) sequence |
| `markdown_on_error` | No — keep `pass` | See warning below |
| `markdown_on_wildcard` | No — keep `off` | Only enable after confirming no browser traffic reaches enabled scopes |
| `markdown_log_verbosity` | Yes — but keep `info` initially | Lower to `warn` only after rollout is stable and you no longer need full decision visibility |

#### Do not change `markdown_on_error` to `reject` during initial rollout

Setting `markdown_on_error reject` causes the module to return a 502 Bad Gateway when conversion fails. During initial rollout, you are still discovering which pages convert cleanly and which trigger edge cases in the converter. A single unexpected HTML structure could cause a conversion failure that, with `reject`, returns a 502 to the client instead of the original HTML.

Keep `markdown_on_error pass` until:

1. Your rollout has been stable for multiple traffic cycles (at least 48 hours in production).
2. Your `ELIGIBLE_FAILED_OPEN` count is zero or near-zero for all enabled scopes.
3. You have reviewed the failure reason codes (`FAIL_CONVERSION`, `FAIL_RESOURCE_LIMIT`, `FAIL_SYSTEM`) and resolved any underlying issues.
4. You have a specific operational reason to reject failed conversions (e.g., you need to guarantee Markdown-only responses for a downstream consumer).

Even then, consider enabling `reject` only in narrow scopes (specific `location` blocks) rather than globally, and monitor closely after the change. If failures appear, switch back to `pass` immediately by setting `markdown_on_error pass` and running `nginx -s reload`.


---

## Observation Guidance

This section is the comprehensive reference for monitoring module behavior during rollout. The [Rollout Stages](#rollout-stages) observation checkpoints provide stage-specific commands — this section explains what to monitor, why, and how to interpret the results.

Use this guidance at every observation checkpoint and whenever you need to assess whether the module is behaving as expected.

### Metrics to Monitor

The module exposes metrics at the `/markdown-metrics` endpoint in JSON (when `Accept: application/json` is sent) or plain-text format. The endpoint is restricted to localhost access.

> **Note on metric names:** The metrics endpoint uses flat counter names (e.g., `conversions_succeeded`). The table below shows the actual endpoint field names.

| Endpoint Field | Type | What It Tells You |
|---------------|------|-------------------|
| `conversions_succeeded` | Counter | Successful HTML-to-Markdown conversions |
| `conversions_failed` | Counter | Conversion attempts that failed |
| `conversions_bypassed` | Counter | Requests where conversion was bypassed (fail-open or ineligible after context creation) |
| `failures_conversion` | Counter | HTML parse or conversion errors |
| `failures_resource_limit` | Counter | Timeout or memory limit failures |
| `failures_system` | Counter | Internal or system errors |
| Latency ≤ 10ms | `conversion_latency_le_10ms` | Counter | Conversions completing in ≤ 10ms |
| Latency ≤ 100ms | `conversion_latency_le_100ms` | Counter | Conversions completing in 10–100ms |
| Latency ≤ 1000ms | `conversion_latency_le_1000ms` | Counter | Conversions completing in 100–1000ms |
| Latency > 1000ms | `conversion_latency_gt_1000ms` | Counter | Conversions completing in > 1000ms |

> **Skip reason codes** (`SKIP_METHOD`, `SKIP_STATUS`, etc.) are not currently exposed as individual metric counters. Use decision log entries (`grep "reason=SKIP_*"`) to determine skip reason distribution. Failure sub-classification is available via the `failures_conversion`, `failures_resource_limit`, and `failures_system` counters.

The key ratio to track is the conversion success rate:

```
success_rate = conversions_succeeded /
               (conversions_succeeded + conversions_failed)
```

A healthy rollout maintains a success rate above 95%.

### Log Patterns to Check

Decision log entries use the format `markdown decision: reason=<REASON_CODE> ...` and appear in the NGINX error log. Use these `grep` patterns to check for specific outcomes:

#### Check for conversion failures

```bash
# Count all conversion failures
grep "markdown decision:" /var/log/nginx/error.log | \
  grep -c "reason=ELIGIBLE_FAILED"

# Show the most recent failures with full context
grep "markdown decision:" /var/log/nginx/error.log | \
  grep "reason=ELIGIBLE_FAILED" | tail -10
```

#### Check for system-level failures

```bash
# FAIL_SYSTEM indicates internal errors — these should never appear
grep "markdown decision:" /var/log/nginx/error.log | \
  grep -c "category=FAIL_SYSTEM"
```

#### Check reason code distribution

```bash
# See the distribution of all reason codes
grep "markdown decision:" /var/log/nginx/error.log | \
  grep -oP 'reason=\K[A-Z_]+' | sort | uniq -c | sort -rn
```

#### Check for unexpected skip reasons

```bash
# Show skip reasons excluding SKIP_CONFIG (expected for disabled scopes)
grep "markdown decision:" /var/log/nginx/error.log | \
  grep "reason=SKIP_" | grep -v "SKIP_CONFIG" | \
  grep -oP 'reason=\K[A-Z_]+' | sort | uniq -c
```

#### Check failure sub-classification

```bash
# Break down failures by category (FAIL_CONVERSION, FAIL_RESOURCE_LIMIT, FAIL_SYSTEM)
# The category= field appears in decision log entries for failure outcomes
grep "markdown decision:" /var/log/nginx/error.log | \
  grep -oP 'category=\K[A-Z_]+' | sort | uniq -c
```

#### Check per-URI failure patterns

```bash
# Identify which URIs are failing most often
grep "markdown decision:" /var/log/nginx/error.log | \
  grep "reason=ELIGIBLE_FAILED" | \
  grep -oP 'uri=\K[^ ]+' | sort | uniq -c | sort -rn | head -10
```

#### Check NGINX error-level messages from the module

```bash
# Look for module-level errors beyond decision log entries
grep -i "markdown" /var/log/nginx/error.log | \
  grep -E "\[(error|crit|alert|emerg)\]" | tail -10
```

### Checking the Metrics Endpoint

Use these `curl` commands to query the metrics endpoint directly. These are copy-pasteable — adjust the hostname and port to match your environment.

#### Quick health check

```bash
# Fetch metrics in JSON format for easy parsing
curl -s -H "Accept: application/json" \
  http://localhost/markdown-metrics | python3 -m json.tool
```

#### Full metrics snapshot (JSON)

```bash
# Save a full snapshot for comparison
curl -s -H "Accept: application/json" \
  http://localhost/markdown-metrics > /tmp/metrics-$(date +%s).json
```

#### Compare metrics over time

```bash
# Take a before snapshot
curl -s -H "Accept: application/json" \
  http://localhost/markdown-metrics > /tmp/metrics-before.json

# ... wait for observation period ...

# Take an after snapshot and compare
curl -s -H "Accept: application/json" \
  http://localhost/markdown-metrics > /tmp/metrics-after.json

diff <(python3 -m json.tool /tmp/metrics-before.json) \
     <(python3 -m json.tool /tmp/metrics-after.json)
```

#### Check skip reason distribution from metrics

```bash
# Show all skip reason codes from decision log
# (skip reasons are not in the metrics endpoint;
# use decision log grep patterns instead)
grep "markdown decision:" /var/log/nginx/error.log | \
  grep "reason=SKIP_" | \
  grep -oP 'reason=\K[A-Z_]+' | sort | uniq -c
```

#### Check failure stage distribution from metrics

```bash
# Show failure counters from the metrics endpoint
curl -s http://localhost/markdown-metrics | \
  grep -E "failures_(conversion|resource_limit|system)"
```

#### Check conversion latency buckets

```bash
# Show latency distribution from the metrics endpoint
curl -s http://localhost/markdown-metrics | \
  grep "conversion_latency"
```

#### Check latency with human-readable summary

```bash
# Parse latency buckets and show percentages
curl -s -H "Accept: application/json" \
  http://localhost/markdown-metrics | \
  python3 -c "
import sys, json
m = json.load(sys.stdin)
b = m.get('conversion_latency_buckets', {})
total = sum(b.values()) if b else 0
if total > 0:
    for k, v in sorted(b.items(), key=lambda x: float(x[0])):
        print(f'  <= {k}s: {v} ({v*100//total}%)')
    print(f'  total: {total}')
else:
    print('  No conversions recorded yet')
"
```

#### Verify a test request converts successfully

```bash
# Send a request with Accept: text/markdown and check the response headers
curl -sD - -o /dev/null \
  -H "Accept: text/markdown" \
  http://localhost/docs/
# Expected: Content-Type: text/markdown; charset=utf-8
```

### Healthy Rollout Indicators

A rollout is healthy when all of the following hold true during the observation period:

| Indicator | Threshold | How to Check |
|-----------|-----------|--------------|
| Conversion success rate | > 95% | `conversions_succeeded / (conversions_succeeded + conversions_failed)` from metrics endpoint |
| `FAIL_SYSTEM` count | 0 | `grep -c "category=FAIL_SYSTEM"` in logs, or `failures_system` in metrics |
| Conversion latency | Within configured `markdown_timeout` | Latency buckets show the vast majority of conversions completing before the timeout threshold |
| `conversions_failed` trend | Stable or decreasing | Compare metrics snapshots over the observation period — failure count should not be climbing |
| Upstream error rate | No increase correlated with enablement | Compare upstream 5xx rates before and after enabling the module |
| Unexpected skip reasons | None for traffic you expect to convert | Check decision log `reason=SKIP_*` — no unexpected `SKIP_CONTENT_TYPE` or `SKIP_SIZE` for enabled paths |

When all indicators are green, it is safe to proceed to the next rollout stage.

### Stop and Investigate Triggers

Stop expanding rollout scope and investigate if any of the following occur:

| Trigger | What It Means | How to Detect |
|---------|---------------|---------------|
| Sudden increase in failure category codes | Conversion failures are spiking — may indicate upstream HTML changes, resource pressure, or a converter bug | `grep "reason=ELIGIBLE_FAILED" /var/log/nginx/error.log \| tail -20` or watch `conversions_failed` in metrics |
| Any `FAIL_SYSTEM` category codes | Internal/system error — this should never happen in normal operation and indicates a bug or severe resource issue | `grep -c "category=FAIL_SYSTEM" /var/log/nginx/error.log` |
| Conversion latency exceeding `markdown_timeout` | Conversions are taking too long — may indicate large pages, resource contention, or converter performance issues | Check latency buckets; look for conversions in the highest `le` bucket or timeouts in logs |
| Upstream error rate increase | The module may be causing upstream issues (unlikely but possible with decompression or buffering interactions) | Compare upstream 5xx rates before and after enablement |
| Unexpected `Content-Type` in responses | Converted responses have wrong Content-Type, or non-HTML responses are being processed | `curl -sD - -H "Accept: text/markdown" http://localhost/your-path/ \| grep Content-Type` |
| One path failing significantly more than others | Path-specific issue — the HTML structure on that path may not convert cleanly | Per-URI failure check: `grep "reason=ELIGIBLE_FAILED" \| grep -oP 'uri=\K[^ ]+' \| sort \| uniq -c` |
| `SKIP_CONTENT_TYPE` or `SKIP_SIZE` for paths you expect to convert | Upstream responses changed — content type is no longer `text/html` or response size exceeds `markdown_max_size` | Check skip reason distribution filtered by URI |

When a trigger fires:

1. Do not expand to the next rollout stage.
2. Check the decision logs and metrics to understand the scope of the issue.
3. If the issue is isolated to a single path, consider narrowing your rollout scope to exclude that path.
4. If the issue is widespread, consider rolling back — see the Rollback Guide (`ROLLBACK_GUIDE.md`) for procedures.
5. Resolve the underlying issue before resuming rollout expansion.
