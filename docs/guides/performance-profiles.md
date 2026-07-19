# Performance Profile Comparison and Tuning Guide

## Table of Contents

1. [Overview](#overview)
2. [Profile Comparison](#profile-comparison)
3. [Capability Boundary](#capability-boundary)
4. [Tuning Guidance](#tuning-guidance)
5. [Deployment Pattern Recommendations](#deployment-pattern-recommendations)
6. [Production Example Configurations](#production-example-configurations)
7. [Cross-References](#cross-references)

---

## Overview

The NGINX Markdown filter module provides three performance profiles that
control the tradeoff between cache correctness, streaming TTFB, and memory
usage. This guide documents the performance characteristics of each profile,
provides tuning guidance for performance-relevant directives, and offers
production-ready configurations for common deployment patterns.

All performance claims in this document are qualified with benchmark evidence
references or marked as pending validation through the module-level benchmark
harness.

### Profiles at a Glance

- **`strict_cache`** — prioritizes cache correctness (full ETag/304 support);
  full-buffer only
- **`balanced`** — moderate tradeoff; full-buffer default with auto-streaming
  for large responses
- **`streaming_first`** — prioritizes TTFB; streaming enabled by default with
  0.9.1 performance optimizations active

---

## Profile Comparison

### Performance Characteristics Table

| Characteristic | `strict_cache` | `balanced` | `streaming_first` |
|----------------|:-:|:-:|:-:|
| **Streaming capability** | None (forced off) | Auto (threshold-gated) | Always (forced on) |
| **TTFB characteristics** | Higher — full response buffered before conversion | Lower for large responses via auto-streaming; full-buffer for small | Lower in the validated 1 MiB comparison — streaming begins on first chunk arrival |
| **Peak memory** | Response size × ~2 (buffer + converted output) | Same as strict_cache for small responses; bounded by `streaming_buffer` for large | Lower in the validated 1 MiB comparison; avoids full-response accumulation |
| **Cache correctness** | Full — ETag generation, If-None-Match, If-Modified-Since | Partial — If-Modified-Since only (IMS via upstream Last-Modified) | None — no conditional request support |
| **Zero-copy output (0.9.1)** | Not applicable (no streaming) | Available when streaming active + opt-in | Available + opt-in via `markdown_streaming_zero_copy on` |
| **Streaming decompression (0.9.1)** | Not applicable (no streaming) | Not active (requires `streaming_first` profile) | Active for gzip, deflate (zlib-wrapped + raw), and Brotli only when `markdown_auto_decompress on`, `markdown_cache_validation` is not `full`, and the codec was compiled in |
| **Full-buffer copy reduction (0.9.1)** | Active (internal) | Active (internal) | Active (internal) |

### Profile Selection Guide

| Deployment Need | Recommended Profile | Rationale |
|-----------------|--------------------:|-----------|
| CDN origin with downstream caches | `strict_cache` | Full ETag/304 support enables cache validation across CDN layers |
| General-purpose reverse proxy | `balanced` | Auto-streaming handles large responses while small responses get fast full-buffer conversion |
| AI agent gateway with large documents | `streaming_first` | Minimizes TTFB for large HTML→Markdown conversions; agents benefit from early token delivery |
| Mixed traffic with authenticated users | `balanced` | Works with `markdown_auth_policy allow` and IMS-only caching |

### Rollout State in 0.9.1

| Optimization | Default | Activation | Profile Dependency |
|--------------|---------|------------|-------------------|
| Zero-copy output | OFF | `markdown_streaming_zero_copy on` | Requires streaming path active |
| Streaming decompression | OFF | `streaming_first` profile + `auto_decompress on` | Profile-gated |
| Full-buffer copy reduction | ON | Internal (no directive) | None — always active |
| Performance metrics | ON | Always collected | None |

---

## Capability Boundary

### Conversion Cannot Achieve sendfile Zero-Copy

Unlike static file serving where NGINX uses `sendfile(2)` for kernel-to-network
zero-copy transfer, HTML-to-Markdown conversion requires user-space parsing and
cannot achieve NGINX `sendfile` zero-copy.

The conversion process necessarily:

1. **Reads** upstream response bytes into user space
2. **Parses** HTML structure (DOM construction, sanitization)
3. **Transforms** the parsed structure into Markdown output
4. **Writes** the converted bytes to the downstream output filter chain

Each of these steps executes in user space within the NGINX worker process.
The fundamental processing cost of parsing and transformation cannot be
eliminated regardless of profile or optimization settings.

### What the 0.9.1 Optimizations Achieve

The 0.9.1 optimizations reduce unnecessary copies and improve streaming TTFB
within the user-space constraint:

| Optimization | What It Eliminates | What Remains |
|--------------|-------------------|--------------|
| Zero-copy output | Pool-copy of Rust-produced Markdown chunks into NGINX pool buffers | Rust parsing + conversion + output assembly |
| Streaming decompression | Full-body buffering before gzip/deflate decompression starts | Per-chunk decompression + conversion |
| Full-buffer copy reduction | Extra apply-back copy after Rust FFI decompression output is copied into an `ngx_alloc` buffer | Decompression itself + one FFI-output copy + conversion |

### Practical Implications

- **Do not expect** latency parity with `sendfile`-served static files
- **Do expect** measurable TTFB improvements for large responses under
  `streaming_first`; the checked-in 1 MiB module evidence recorded 6.665 ms
  streaming-first p50 TTFB versus 35.627 ms for full-buffer
- **Do expect** reduced peak memory for streaming paths; the same evidence
  recorded 21,127,168 bytes versus 33,632,256 bytes for full-buffer
- **Do expect** reduced CPU cost when large gzip responses are intentionally
  routed through full-buffer (for example full cache validation), by removing
  the extra apply-back copy after the required FFI-output copy (pending
  benchmark validation)

---

## Tuning Guidance

### Performance-Relevant Directives

#### markdown_streaming_zero_copy

**Syntax:** `markdown_streaming_zero_copy on | off;`
**Default:** `off`
**Context:** location
**0.9.1 Stage:** Stage 1 (opt-in)

Controls whether streaming output chunks reference Rust-owned memory directly
(zero-copy) or are copied into NGINX pool buffers (pool-copy).

| Setting | When to Use | Tradeoff |
|---------|-------------|----------|
| `off` (default) | Conservative deployments; initial 0.9.1 rollout | Safe — no lifecycle complexity |
| `on` | After validating streaming stability; high-throughput AI agent workloads | Reduces per-chunk memcpy; adds pool cleanup lifecycle management |

**Recommended ranges:**
- AI agent gateway: `on` (after Phase 1 validation via streaming rollout)
- Mixed traffic proxy: `off` (keep default until streaming is proven stable)
- CDN origin: not applicable (streaming not active under `strict_cache`)

#### auto_decompress

**Syntax:** `markdown_auto_decompress on | off;`
**Default:** `on`
**Context:** http, server, location

Controls whether the module decompresses gzip/deflate responses before
conversion. When combined with `streaming_first` profile, enables streaming
decompression for supported encodings.

| Setting | When to Use |
|---------|-------------|
| `on` (default) | Standard operation; compressed upstream responses common |
| `off` | Upstream already sends uncompressed to the module; or to disable streaming decompression as a rollback |

#### markdown_limits memory

**Syntax:** `markdown_limits memory=<size>;`
**Default:** `10m` (no profile) / `8m` (all profiles)
**Context:** http, server, location

Maximum response size for conversion attempt. Responses larger than this value
bypass conversion (fail-open).

| Deployment Pattern | Recommended Range | Rationale |
|-------------------|-------------------|-----------|
| AI agent gateway | `8m` – `32m` | Large documentation pages common; agents can handle long context |
| Mixed traffic proxy | `4m` – `8m` | Balance between coverage and resource protection |
| CDN origin | `4m` – `8m` | Full-buffer mode means memory = response size; keep bounded |

#### markdown_decompress_max_size

**Syntax:** `markdown_decompress_max_size <size>;`
**Default:** inherits `markdown_limits memory`
**Context:** http, server, location

Independent budget for decompressed output. Set explicitly when compressed
responses may expand significantly beyond the conversion size limit.

| Deployment Pattern | Recommended Range | Rationale |
|-------------------|-------------------|-----------|
| AI agent gateway | `16m` – `64m` | Large gzip responses from documentation backends |
| Mixed traffic proxy | `8m` – `16m` | Moderate expansion ratio expected |
| CDN origin | `8m` – `16m` | Match conversion budget or slightly above |

**Tuning signal:** If `decompression_budget_exceeded_total` is incrementing
frequently, increase this value. Use the
[doctor advice tool](doctor.md) rule D05 for automated detection.

#### markdown_parser_budget

**Syntax:** `markdown_parser_budget <size>;`
**Default:** `64m`
**Context:** http, server, location

Maximum memory the HTML parser may allocate. Protects against pathological
HTML that produces deeply nested DOM structures.

| Deployment Pattern | Recommended Range | Rationale |
|-------------------|-------------------|-----------|
| AI agent gateway | `32m` – `64m` | Large complex pages with many nested structures |
| Mixed traffic proxy | `16m` – `32m` | Moderate page complexity |
| CDN origin | `16m` – `32m` | Known content sources; complexity bounded |

#### markdown_parse_timeout

**Syntax:** `markdown_parse_timeout <time>;`
**Default:** `30s`
**Context:** http, server, location

Maximum duration for the parsing phase. Combined with parser_budget, provides
dual protection against resource exhaustion.

| Deployment Pattern | Recommended Range | Rationale |
|-------------------|-------------------|-----------|
| AI agent gateway | `5s` – `15s` | Agents tolerate some latency for large conversions |
| Mixed traffic proxy | `2s` – `5s` | User-facing; tighter latency budget |
| CDN origin | `2s` – `5s` | Cached responses should convert quickly |

#### markdown_limits streaming_buffer

**Syntax:** `markdown_limits streaming_buffer=<size>;`
**Default:** `2m` (no profile) / `256k` (all profiles)
**Context:** http, server, location

Streaming memory budget — bounds the memory used by in-flight streaming
conversion. Only relevant when streaming is active.

| Deployment Pattern | Recommended Range | Rationale |
|-------------------|-------------------|-----------|
| AI agent gateway (`streaming_first`) | `256k` – `1m` | Balances memory per-connection with conversion throughput |
| Mixed traffic proxy (`balanced`) | `256k` – `512k` | Auto-streaming threshold already limits which responses stream |
| CDN origin (`strict_cache`) | N/A | Streaming disabled |

**Tuning signal:** If `pending_output_high_watermark_bytes` is consistently
near the configured streaming_buffer, increase the value. Use doctor advice
rule D06 for automated detection.

#### cache_validation

**Syntax:** `markdown_cache_validation off | ims_only | full;`
**Default:** `ims_only`
**Context:** http, server, location

Cache validation mode. Directly affects whether streaming is possible.

| Setting | Streaming Impact | Performance Impact |
|---------|-----------------|-------------------|
| `full` | Blocks streaming (full entity needed for ETag) | Higher memory, higher latency for 304 savings |
| `ims_only` | Allows streaming | Moderate — IMS handled by upstream Last-Modified |
| `off` | Allows streaming | Lowest overhead — no conditional request processing |

---

## Deployment Pattern Recommendations

### AI Agent Gateway

AI agents (Claude, GPT, etc.) benefit most from low TTFB on large documents.
They tolerate higher latency on first byte less than browsers and typically
do not cache aggressively.

**Recommended profile:** `streaming_first`

**Key tuning:**
- Enable zero-copy after validation: `markdown_streaming_zero_copy on`
- Generous memory limits for large documentation: `markdown_limits memory=16m`
- Generous decompression budget: `markdown_decompress_max_size 32m`
- Streaming buffer sized for agent consumption rate: `markdown_limits streaming_buffer=512k`
- Cache validation off (agents rarely send conditional requests): `markdown_cache_validation off`

**Performance expectations:**
- Validated TTFB improvement over full-buffer for the checked-in 1 MiB fixture
- Validated peak-memory reduction for the checked-in 1 MiB fixture
- Decompression begins on first chunk rather than after full accumulation

### Mixed Traffic Proxy

Serves both browsers and AI agents. Needs to balance TTFB optimization
with broad compatibility and reasonable resource usage.

**Recommended profile:** `balanced`

**Key tuning:**
- Default streaming threshold handles large/small split automatically
- Moderate memory limits: `markdown_limits memory=8m`
- Keep zero-copy off until streaming is proven stable in your environment
- IMS-only caching preserves Last-Modified support for browser caches

**Performance expectations (pending benchmark validation):**
- Full-buffer path for responses below `markdown_stream_threshold` (default 1m)
- Auto-streaming kicks in for large responses, providing TTFB improvement
- Full-buffer copy reduction active when compressed responses take the
  full-buffer path (including Brotli and full-cache-validation requests)

### CDN Origin

Serves as the origin for a CDN (Cloudflare, Fastly, CloudFront). Cache
correctness is the primary concern — the CDN needs proper ETag/304 support
to avoid unnecessary re-fetching from origin.

**Recommended profile:** `strict_cache`

**Key tuning:**
- Full cache validation: `markdown_cache_validation full` (forced by profile)
- Moderate memory limits: `markdown_limits memory=8m`
- Streaming disabled (forced by profile) — all responses full-buffered for
  ETag generation
- Consider generous parser budget for complex pages: `markdown_parser_budget 64m`

**Performance expectations (pending benchmark validation):**
- Higher TTFB than streaming profiles (full buffer required)
- Full-buffer copy reduction provides the primary optimization benefit
- 304 Not Modified responses reduce bandwidth — CDN cache hit ratio is the
  key performance metric for this pattern

---

## Production Example Configurations

### Example 1: AI Agent Gateway with Performance Optimizations

```nginx
# AI Agent Gateway — streaming_first with 0.9.1 optimizations
# Validated: nginx -t passes with this configuration

http {
    markdown_profile streaming_first;

    server {
        listen 8080;
        server_name agents.example.com;

        location /docs/ {
            markdown_filter on;

            # 0.9.1 performance: enable zero-copy output (Stage 1 opt-in)
            markdown_streaming_zero_copy on;

            # Resource limits tuned for large documentation
            markdown_limits memory=16m timeout=10s streaming_buffer=512k max_inflight=32;
            markdown_decompress_max_size 32m;
            markdown_parser_budget 64m;
            markdown_parse_timeout 10s;

            # Agent-friendly extensions
            markdown_token_estimate on;
            markdown_front_matter on;
            markdown_flavor gfm;

            # Noise reduction for cleaner agent context
            markdown_prune_noise on;
            markdown_prune_selectors "nav footer aside";

            proxy_pass http://documentation_backend;
        }

        # Metrics endpoint for monitoring and doctor advice tool
        location /markdown-metrics {
            markdown_metrics;
            allow 10.0.0.0/8;
            deny all;
        }
    }
}
```

### Example 2: CDN Origin with Strict Caching

```nginx
# CDN Origin — strict_cache for full conditional request support
# Validated: nginx -t passes with this configuration

http {
    markdown_profile strict_cache;

    # Trust CDN edge nodes for forwarded headers
    markdown_trusted_proxies 10.0.0.0/8 172.16.0.0/12;

    server {
        listen 8080;
        server_name origin.example.com;

        location / {
            markdown_filter on;

            # Resource limits for origin workload
            markdown_limits memory=8m timeout=5s max_inflight=64;
            markdown_decompress_max_size 16m;
            markdown_parser_budget 32m;
            markdown_parse_timeout 5s;

            # Standard output format
            markdown_flavor commonmark;

            # Security: deny conversion for authenticated requests
            markdown_auth_policy deny;
            markdown_auth_cookies session* auth_token;

            proxy_pass http://application_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }

        # Metrics endpoint (internal network only)
        location /markdown-metrics {
            markdown_metrics;
            allow 10.0.0.0/8;
            deny all;
        }
    }
}
```

---

## Cross-References

- **[Streaming Rollout Cookbook](streaming-rollout-cookbook.md)** — phased
  streaming engine rollout strategy, monitoring guidance, and emergency
  disable procedures. Follow this cookbook when enabling streaming for the
  first time before tuning performance profiles.

- **[Configuration Guide](CONFIGURATION.md)** — complete directive reference
  including profile defaults table, forced field conflicts, and configuration
  inheritance rules. Consult this for the full list of available directives
  and their accepted values.

- **[Performance Rollout and Rollback Guide](performance-rollout-091.md)** —
  rollback procedures for each 0.9.1 optimization, trigger conditions, and
  verification steps.

- **[Doctor Advice Tool](doctor.md)** — automated diagnostic tool that
  analyzes runtime metrics and produces tuning recommendations. Use after
  deployment to identify performance bottlenecks.

- **[Operations Guide](OPERATIONS.md)** — metrics reference and operational
  procedures for the module.

---

## Benchmark Claim Qualification

All performance claims in this document are qualified as follows:

| Claim Category | Evidence Status |
|----------------|----------------|
| TTFB improvement under `streaming_first` | Validated for the checked-in 1 MiB module fixture |
| Peak memory reduction for streaming paths | Validated for the checked-in 1 MiB module fixture |
| Full-buffer copy reduction latency benefit | Pending benchmark validation |
| Zero-copy output reduced per-chunk overhead | Pending benchmark validation |
| Streaming decompression TTFB benefit for gzip/deflate | Pending benchmark validation |

The module-level benchmark harness produces validated evidence packs. The
evidence gate (`make release-gates-check-091`) enforces the following thresholds
before claims can be promoted to "validated":

- p50 latency: ≤ +10% vs baseline
- p95 latency: ≤ +15% vs baseline
- TTFB (streaming, large): ≤ +10% or improvement
- Streaming fallback rate: ≤ 5% absolute
- Memory slope (RSS/input_MB): ≤ +20% vs baseline

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.1 | 2026-07-05 | Kiro | Initial performance profile comparison and tuning documentation |
