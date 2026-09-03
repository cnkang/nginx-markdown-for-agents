# Migration Guide: 0.9.1 → 0.9.2 (Final Breaking Freeze)

> Reference: a compact standalone summary of every breaking change
> (removed directives, replacements, impact) lives in
> [0.9.2-breaking-changes.md](0.9.2-breaking-changes.md).

## Overview

**0.9.2 is the final breaking release before 1.0.** The configuration surface
shrank from 63 directives to exactly 25. All removed directives now
produce NGINX's standard "unknown directive" error during `nginx -t`.

After 0.9.2, all 1.x releases maintain backward compatibility for a minimum of
24 months.

**Upgrade path:** update your nginx.conf to remove/replace removed directives
(see tables below), replace the module binary, then run `nginx -t` to validate.

## Dynamic Configuration JSON Migration

0.9.2 accepts the JSON v1 dynconf contract. Convert legacy line-format files
before upgrading. The old `markdown_filter` key becomes `filter`, and
`streaming_budget` becomes `streaming_buffer`. 0.9.2 removes `memory_budget`
from runtime configuration. Set the static `markdown_limits
conversion_memory=<size>` directive instead.

```text
# BEFORE (legacy line format)
schema_version=0.9
markdown_filter=on
streaming_budget=16m
memory_budget=64m
```

```json
{
  "schema_version": 1,
  "filter": "on",
  "streaming_buffer": 16777216
}
```

The 0.9.2 watcher accepts only JSON v1. The watcher rejects a legacy line-format
file. The file does not trigger a successful reload or replace the active or
last-known-good snapshot. Migrate it and verify the next reload through the
diagnostics endpoint.

The JSON contract is fail-closed: unknown keys, duplicate keys, unsupported
schema versions, invalid types, and out-of-range values reject the entire
candidate. The active or last-known-good snapshot remains unchanged. Before
deployment, validate the JSON shape and supported keys, then check
`configuration.dynconf` plus
`nginx_markdown_dynconf_reloads_total{reason=...}` after the first poll.

### Diagnostics JSON schema v2

The diagnostics endpoint now returns `schema_version: 2`. Migrate consumers
that read the 0.9.1 directive/profile-oriented response as follows:

| 0.9.1 field | 0.9.2 field or action |
|-------------|------------------------|
| `config_snapshot` | `configuration.static_digest`, plus `configuration.effective` and `configuration.effective_sources` for effective dynconf fields |
| `metrics_snapshot` | `runtime.module_metrics` for the bounded module counters |
| `dynconf_state` | `configuration.dynconf` |
| `streaming_config` / old `effective_config` | `configuration.effective` and `configuration.effective_sources` |
| `profile`, `overridden_fields`, `forced_fields` | Removed; profile presets no longer exist |

The v2 dynconf object also exposes `masked_keys` and bounded categorical
`last_error` values. Validate `schema_version` before accessing fields. Do not
parse removed v1 sections as if they were still present. The authoritative
schema is [`schemas/diagnostics.schema.json`](../../schemas/diagnostics.schema.json).

### Content-Encoding policy

Malformed, unknown, and excessively deep `Content-Encoding` chains follow the
configured `markdown_error_policy`. Only `pass` forwards the original
response. `fail_closed` and status policies reject it.

### Historical reason-code names

The active 0.9.2 registry accepts only canonical lowercase reason keys. The
following names occurred in pre-0.9.2 logs or dashboards. Operators must
migrate them when alerts or queries use those names:

| Historical name | 0.9.2 canonical reason |
|---|---|
| `CONVERTED`, `ELIGIBLE_CONVERTED` | `converted` |
| `SKIPPED_ACCEPT`, `SKIP_ACCEPT` | `skipped_accept` |
| `SKIPPED_NO_ACCEPT` | `skipped_no_accept` |
| `SKIPPED_CONDITIONAL` | `skipped_conditional` |
| `FAILED_DECOMPRESSION` | `decompression_error` |
| `DECOMPRESSION_BUDGET_EXCEEDED` | `decompression_budget_exceeded` |
| `DECOMPRESSION_FORMAT_ERROR` | `decompression_format_error` |
| `DECOMPRESSION_TRUNCATED_INPUT` | `decompression_truncated_input` |
| `DECOMPRESSION_IO_ERROR` | `decompression_io_error` |
| `PARSE_TIMEOUT` | `timeout` |
| `PARSE_BUDGET_EXCEEDED` | `budget_exceeded` |
| `REPLAY_BUFFER_ERROR` | `replay_error` |
| `SKIPPED_ACCEPT_REJECT` | `skipped_accept_reject` |
| `FFI_CALL_ERROR`, `FAIL_SYSTEM` | `ffi_panic` |
| `NOT_ELIGIBLE` | `not_eligible` |
| `DISABLED` | `disabled` |
| `FAILED_OPEN`, `ELIGIBLE_FAILED_OPEN` | `failed_open` |
| `FAILED_CLOSED`, `ELIGIBLE_FAILED_CLOSED` | `failed_closed` |
| `FAIL_CONVERSION` | `conversion_error` |
| `FAIL_RESOURCE_LIMIT` | `memory_budget_exceeded` |
| `BYPASS_NO_TRANSFORM` | `bypass_no_transform` |

Former streaming lifecycle labels are not reason aliases. They are now
lowercase implementation events in the structured `event=` field, such as
`engine_streaming`, `streaming_convert`, `streaming_budget_exceeded`, and
`streaming_precommit_failopen`.

---

## Breaking Changes Summary

| Category | Count | Action |
|----------|-------|--------|
| Reject-only migration stubs removed | 19 | Already caused `nginx -t` failure; now produce standard "unknown directive" |
| Active directives removed | 14 | Replace with equivalents (see below) |
| Directives unified into `markdown_limits` | 4 | Use `markdown_limits key=value` syntax |
| Active directives removed (no replacement) | 1 | `markdown_stream_flush_min` — flushing uses an internal heuristic |
| Total retained directives | 25 | No change needed |

---

## Removed Active Directives — Before/After

### `markdown_profile` → explicit settings

The profile directive no longer exists. Use explicit directive settings instead.

```nginx
# BEFORE (0.9.1)
markdown_profile balanced;

# AFTER (0.9.2) — recommended explicit preset
markdown_limits conversion_memory=64m conversion_timeout=5s parser_timeout=5s max_inflight=64;
markdown_cache_validation ims_only;
markdown_error_policy pass;
```

```nginx
# BEFORE (0.9.1)
markdown_profile strict_cache;

# AFTER (0.9.2) — recommended explicit preset
markdown_limits conversion_memory=128m conversion_timeout=10s max_inflight=32;
markdown_cache_validation full;
markdown_error_policy pass;
```

```nginx
# BEFORE (0.9.1)
markdown_profile streaming_first;

# AFTER (0.9.2) — recommended explicit preset
markdown_streaming force;
markdown_limits conversion_memory=256m conversion_timeout=30s streaming_buffer=16m max_inflight=128;
markdown_error_policy pass;
markdown_accept wildcard;
```

These presets are recommendations, not equivalents. The 0.9.1 profiles
shared one resource envelope: 8 MiB conversion memory, 2 s timeout, and
64 max-inflight. The balanced and streaming_first profiles also set a
256 KiB streaming buffer. The values above raise the envelope several
times over. Choose values for your workload. Do not treat these numbers
as a 1:1 replacement for a profile.

### `markdown_metrics_format` → removed (single format)

The metrics endpoint now always renders in Prometheus text format. The
`markdown_metrics_format` directive is no longer needed.

```nginx
# BEFORE (0.9.1)
location /markdown-metrics {
    markdown_metrics;
    markdown_metrics_format prometheus;
}

# AFTER (0.9.2)
location /markdown-metrics {
    markdown_metrics;
}
```

### `markdown_metrics_per_path` / `markdown_metrics_per_path_cardinality` → removed

The module removed per-path metrics to avoid unbounded cardinality.

```nginx
# BEFORE (0.9.1)
markdown_metrics_per_path on;
markdown_metrics_per_path_cardinality 200;

# AFTER (0.9.2)
# Remove both directives. Use log-based path analysis instead.
```

### `markdown_diagnostics_allow` → removed

The diagnostics location no longer accepts a module-level CIDR allow-list.
The handler is loopback-only by default. Standard NGINX `allow`/`deny`
directives can narrow that boundary but cannot broaden it.

```nginx
# BEFORE (0.9.1)
markdown_diagnostics_allow 10.0.0.0/8;

# AFTER (0.9.2)
location /nginx-markdown/diagnostics {
    markdown_diagnostics on;
    allow 127.0.0.1;
    allow ::1;
    deny all;
}
```

### `markdown_buffer_chunked` → removed (always buffers)

Chunked response buffering is now always enabled internally.

```nginx
# BEFORE (0.9.1)
markdown_buffer_chunked off;

# AFTER (0.9.2)
# Remove the directive. Chunked buffering is always on.
```

### `markdown_streaming_shadow` → removed

Shadow mode comparison no longer exists.

```nginx
# BEFORE (0.9.1)
markdown_streaming_shadow on;

# AFTER (0.9.2)
# Remove the directive. No replacement.
```

### `markdown_streaming_zero_copy` → removed (internalized)

Zero-copy output is now an internal optimization, not operator-controlled.

```nginx
# BEFORE (0.9.1)
markdown_streaming_zero_copy on;

# AFTER (0.9.2)
# Remove the directive. The module manages zero-copy internally.
```

### `markdown_llm_provider` / `markdown_chars_per_token` → removed

LLM provider selection no longer exists. Token estimation uses a fixed ratio.

```nginx
# BEFORE (0.9.1)
markdown_llm_provider openai-gpt;
markdown_chars_per_token 4;

# AFTER (0.9.2)
# Remove both directives. Token estimation uses a fixed internal ratio.
markdown_token_estimate on;
```

### `markdown_stream_types` → replaced by `markdown_stream_excluded_types`

No direct equivalent exists because the old positive allowlist and the new
exclusion list have different semantics. Use the exclusion list instead.

> **Note:** `markdown_stream_types` is a removed directive name. It appears
> here only in migration documentation that shows before/after examples.

```nginx
# BEFORE (0.9.1)
markdown_stream_types text/html application/xhtml+xml;

# AFTER (0.9.2)
# markdown_content_types controls conversion eligibility.
# markdown_stream_excluded_types controls streaming exclusions.
markdown_stream_excluded_types text/csv application/xml;
```

### `markdown_stream_threshold` → removed (internalized)

The streaming threshold is now a fixed internal constant (1 MiB). Chunked
responses (no Content-Length) stream only when `markdown_streaming` permits
streaming. The directive's `off` behavior stays unchanged. Chunked
responses do not stream unconditionally.

```nginx
# BEFORE (0.9.1)
markdown_stream_threshold 512k;

# AFTER (0.9.2)
# Remove the directive. The threshold stays fixed at 1 MiB internally.
```

### `markdown_stream_precommit_buffer` → `markdown_limits streaming_buffer=`

Precommit buffer size is now controlled through `markdown_limits`. The release
drops the `markdown_stream_flush_min` directive separately and provides no
replacement because flush coalescing uses an internal heuristic.

```nginx
# BEFORE (0.9.1)
markdown_stream_precommit_buffer 512k;
markdown_stream_flush_min 32k;

# AFTER (0.9.2)
markdown_limits streaming_buffer=512k;
# Flush minimum is now an internal heuristic.
```

### `markdown_parse_timeout` / `markdown_parser_budget` / `markdown_decompress_max_size` → `markdown_limits`

`markdown_limits` unifies these standalone directives.

```nginx
# BEFORE (0.9.1)
markdown_parse_timeout 10s;
markdown_parser_budget 32m;
markdown_decompress_max_size 20m;

# AFTER (0.9.2)
markdown_limits parser_timeout=10s parser_memory=32m decompressed_size=20m;
```

### `markdown_otel` / `markdown_otel_endpoint` → removed

The experimental OpenTelemetry subsystem no longer exists.

```nginx
# BEFORE (0.9.1)
markdown_otel on;
markdown_otel_endpoint http://otel-collector:4317;

# AFTER (0.9.2)
# Remove both directives. Use NGINX's native OTel module instead.
```

---

## Removed Reject-Only Directives (19)

These directives already caused `nginx -t` failure with a migration hint in
0.9.0/0.9.1. In 0.9.2, the migration stubs no longer exist. NGINX
produces its standard "unknown directive" error.

| Removed Directive | Replacement |
|-------------------|-------------|
| `markdown_max_size` | `markdown_limits conversion_memory=<size>` |
| `markdown_timeout` | `markdown_limits conversion_timeout=<time>` |
| `markdown_streaming_budget` | `markdown_limits streaming_buffer=<size>` |
| `markdown_on_error` | `markdown_error_policy pass\|fail_closed\|status <code>` |
| `markdown_streaming_on_error` | `markdown_error_policy pass\|fail_closed\|status <code>` |
| `markdown_on_wildcard` | `markdown_accept strict\|wildcard\|force` |
| `markdown_etag` | `markdown_cache_validation off\|ims_only\|full` |
| `markdown_etag_policy` | `markdown_cache_validation off\|ims_only\|full` |
| `markdown_conditional_requests` | `markdown_cache_validation off\|ims_only\|full` |
| `markdown_trust_forwarded_headers` | `markdown_trusted_proxies <CIDR>...` |
| `markdown_forwarded_headers` | `markdown_trusted_proxies <CIDR>...` |
| `markdown_large_body_threshold` | Removed, no replacement |
| `markdown_streaming_engine` | `markdown_streaming off\|auto\|force` |
| `markdown_memory_budget` | `markdown_limits conversion_memory=<size>` |
| `markdown_otel_tracing` | Removed (use NGINX native OTel) |
| `markdown_otel_metrics` | Removed (use NGINX native OTel) |
| `markdown_otel_service_name` | Removed, no replacement |
| `markdown_otel_span_buffer_size` | Removed, no replacement |
| `markdown_otel_export_timeout` | Removed, no replacement |

---

## Trusted forwarded-hop behavior

`markdown_trusted_proxies` applies only to HTTP forwarded URL headers. For a
request from a trusted peer, the module processes aligned `Forwarded` or
`X-Forwarded-*` chains from right to left. It strips trusted proxy hops and
uses the first untrusted hop for the client-facing value. If every hop matches
a trusted proxy, the module discards the forwarded set. Bracketed IPv6
literals such as `[2001:db8::1]` can match a trusted CIDR. Bracketed IPv4
literals such as `[192.0.2.1]` never match. The module discards malformed or
mismatched lists as a whole.

---

## `markdown_limits` Unified Syntax

All resource limits are now managed through the single `markdown_limits`
directive with key=value pairs:

```nginx
markdown_limits conversion_timeout=30s
               parser_timeout=10s
               conversion_memory=64m
               parser_memory=32m
               streaming_buffer=2m
               decompressed_size=10m
               decompression_ratio=100
               max_inflight=64;
```

| Key | Type | Default | Range |
|-----|------|---------|-------|
| `conversion_timeout` | duration (ms, s) | 30s | 1ms–1h |
| `parser_timeout` | duration (ms, s) | 10s | 1ms–1h |
| `conversion_memory` | size (k, m, g) | 64m | 64k–1g |
| `parser_memory` | size (k, m, g) | 32m | 64k–1g |
| `streaming_buffer` | size (k, m, g) | 2m | 64k–1g |
| `decompressed_size` | size (k, m, g) | 10m | 64k–1g |
| `decompression_ratio` | integer | 100 | 1–10000 |
| `max_inflight` | integer | 64 | 1–65535 |

Cross-key constraints: `parser_timeout <= conversion_timeout`,
`parser_memory <= conversion_memory`, `streaming_buffer <= conversion_memory`.
The 0.9.2 default for `streaming_buffer` is 2 MiB, the same default that
0.9.1 used. The 256 KiB value appeared only in the removed profiles
(balanced and streaming_first). Pin `markdown_limits streaming_buffer=2m`
to keep the module default explicit.

---

## Final 25-Directive Contract

After 0.9.2, these 25 directives constitute the frozen public surface:

| # | Directive | Context |
|---|-----------|---------|
| 1 | `markdown_filter` | http, server, location |
| 2 | `markdown_flavor` | http, server, location |
| 3 | `markdown_accept` | http, server, location |
| 4 | `markdown_token_estimate` | http, server, location |
| 5 | `markdown_front_matter` | http, server, location |
| 6 | `markdown_limits` | http, server, location |
| 7 | `markdown_auto_decompress` | http, server, location |
| 8 | `markdown_error_policy` | http, server, location |
| 9 | `markdown_cache_validation` | http, server, location |
| 10 | `markdown_content_types` | http, server, location |
| 11 | `markdown_auth_policy` | http, server, location |
| 12 | `markdown_auth_cookies` | http, server, location |
| 13 | `markdown_trusted_proxies` | http |
| 14 | `markdown_streaming` | http, server, location |
| 15 | `markdown_stream_excluded_types` | http, server, location |
| 16 | `markdown_prune_noise` | http, server, location |
| 17 | `markdown_prune_selectors` | http, server, location |
| 18 | `markdown_prune_protection_selectors` | http, server, location |
| 19 | `markdown_log_verbosity` | http, server, location |
| 20 | `markdown_metrics` | location |
| 21 | `markdown_metrics_shm_size` | http |
| 22 | `markdown_dynamic_config` | http only — move it to the `http {}` block |
| 23 | `markdown_dynamic_config_path` | http only — move it to the `http {}` block |
| 24 | `markdown_dynconf_dry_run` | http only — move it to the `http {}` block |
| 25 | `markdown_diagnostics` | location |

---

## Verification

After upgrading:

```bash
set -euo pipefail
# Validate configuration (must pass with no removed directives)
sudo nginx -t

# Doctor check
bash tools/doctor/nginx-markdown-doctor.sh

# Verify metrics endpoint still works
curl --fail-with-body -sS -H 'Accept: text/plain; version=0.0.4' \
  http://localhost/markdown-metrics | sed -n '1,5p'

# Verify diagnostics endpoint
curl --fail-with-body -sS http://localhost/nginx-markdown/diagnostics \
  | python3 -m json.tool >/dev/null
```

---

## Previous Versions

| From | To | Guide |
|------|----|-------|
| 0.9.0 | 0.9.1 | [docs/guides/MIGRATION-0.9.1.md](MIGRATION-0.9.1.md) |
| 0.8.x | 0.9.0 | [docs/guides/MIGRATION-0.9.0.md](MIGRATION-0.9.0.md) |
| 0.7.x | 0.8.0 | [docs/guides/MIGRATION-0.8.md](MIGRATION-0.8.md) |

---

## Document Updates

| Version | Date | Author | Changes |
|---|---|---|---|
| 0.9.2 | 2026-08-15 | Hermes | Corrected profile preset guidance: recommended presets, not equivalents; fixed the streaming_buffer default claim. |
| 0.9.2 | 2026-08-08 | Hermes | Non-native-reader writing pass: active voice for removal descriptions. |
| 0.9.2 | 2026-07-30 | Kang | Complete rewrite for 0.9.2 breaking freeze: 25-directive contract, before/after examples for all removed directives |
