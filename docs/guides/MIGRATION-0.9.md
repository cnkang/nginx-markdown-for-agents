# Migration Guide: 0.9.0 (Breaking Release)

## Overview

**0.8.x → 0.9.0 is a breaking release.** This is the last breaking opportunity
before the 1.0.0 API freeze. All deprecated directives from prior releases are
removed, the profile system is introduced, error policy is consolidated, and
the observability surface is restructured.

If you are running 0.8.x in production, you must follow this guide before
upgrading. There is **no backward-compatible mode** — 0.9.0 rejects old
directive names at `nginx -t` time with actionable migration hints.

**Key changes at a glance:**

- Directive removals and renames (Config V2)
- Profile system (`markdown_profile`) for one-line production defaults
- Error policy consolidation (`markdown_error_policy`)
- Inflight guard (`markdown_limits max_inflight=...`)
- Metrics consolidated from per-reason counters to unified families
- Reason code strings renamed to lowercase_snake_case

**Upgrade path:** read this guide top-to-bottom, update your configuration,
run `nginx -t`, fix any errors using the mapping tables below, then reload.

---

## Breaking Changes Summary

The following breaking changes require configuration and/or tooling updates:

1. **Reason code naming** — all reason code strings changed from
   UPPERCASE_SNAKE_CASE to lowercase_snake_case. Affects Prometheus labels,
   structured logs, and diagnostics endpoint JSON.

2. **Directive removals/renames:**
   - `markdown_on_error` → `markdown_error_policy`
   - `markdown_trust_forwarded_headers` → `markdown_trusted_proxies`
   - `markdown_on_wildcard` → removed; use `markdown_accept wildcard`

3. **Profile system introduction** — `markdown_profile` provides tested
   production defaults (`balanced`, `strict_cache`, `streaming_first`).
   Explicit directives override profile defaults.

4. **Error policy consolidation** — `markdown_on_error pass|reject` is
   replaced by `markdown_error_policy pass|fail_closed`. The `reject` value
   is renamed to `fail_closed` for clarity.

5. **Inflight guard** — `markdown_limits max_inflight=N` introduces
   per-worker concurrency limits. When the inflight count exceeds the
   configured maximum, new requests receive the `overload` reason code
   and fall through without conversion.

6. **Metrics consolidation** — per-reason metric keys (e.g.,
   `markdown_skipped_accept_total`) are replaced by unified metric families
   with a `reason` label (e.g., `nginx_markdown_skips_total{reason="skipped_accept"}`).

---

Several legacy directives are removed and replaced by Config V2 directives.
Removed directives are kept as **reject-only stubs**: the parser entry still
exists, but the only behavior is to fail `nginx -t` with an actionable
migration hint. There is **no alias compatibility** and **no legacy fallback
behavior** — this keeps the breaking-release boundary unambiguous.

If a directive you use is not yet listed below, consult the `nginx -t` error
message, which always names the replacement.

---

## Trusted proxies / forwarded headers

### `markdown_trust_forwarded_headers` → `markdown_trusted_proxies`

The boolean trust model is removed. A request's forwarded headers
(`Forwarded`, `X-Forwarded-Proto`, `X-Forwarded-Host`) are now honored only
when the request's direct source IP matches a configured trusted-proxy CIDR.

`markdown_trust_forwarded_headers` and the never-shipped
`markdown_forwarded_headers` are reject-only stubs:

```
nginx: [emerg] "markdown_trust_forwarded_headers" directive has been removed
in 0.9.0; use "markdown_trusted_proxies <CIDR>..." instead
(see docs/guides/MIGRATION-0.9.md)
```

**Migration:**

| 0.8.x | 0.9.0 |
|-------|-------|
| `markdown_trust_forwarded_headers on;` (in `http`/`server`/`location`) | `markdown_trusted_proxies <CIDR>...;` (in `http` only — list your proxy ranges) |
| `markdown_trust_forwarded_headers off;` | omit `markdown_trusted_proxies` (the default ignores forwarded headers) |

**Before (0.8.x):**

```nginx
server {
    markdown_trust_forwarded_headers on;
}
```

**After (0.9.0):**

```nginx
http {
    # Honor forwarded headers only from these proxy ranges.
    markdown_trusted_proxies 10.0.0.0/8 172.16.0.0/12 2001:db8::/32;
}
```

### Key differences

- **http context only.** `markdown_trusted_proxies` is rejected in `server` and
  `location` blocks (per-location trust creates a local trust-bypass risk that
  is hard to audit):

  ```
  nginx: [emerg] "markdown_trusted_proxies" directive is only valid in the
  http context, not in server or location (see docs/guides/MIGRATION-0.9.md)
  ```

- **CIDR-gated trust.** Only requests whose direct source IP matches a
  configured CIDR have their forwarded headers honored. A direct public client
  can no longer spoof `X-Forwarded-Host`.

- **IPv4 and IPv6** CIDRs are validated at config time; an invalid CIDR fails
  `nginx -t`:

  ```
  nginx: [emerg] invalid CIDR "10.0.0.0/99" in "markdown_trusted_proxies"
  directive; expected an IPv4 or IPv6 CIDR (e.g. 10.0.0.0/8, 2001:db8::/32)
  or "off"
  ```

- **`off`** disables trust entirely:

  ```nginx
  http {
      markdown_trusted_proxies off;
  }
  ```

- **Source IP** is the direct connection peer (`realip` / PROXY-protocol
  resolved). The `X-Forwarded-For` header is never used as the source IP.

See [CONFIGURATION.md](CONFIGURATION.md) (`markdown_trusted_proxies`) for the
full directive reference and deployment guidance (`realip`, PROXY protocol,
Unix sockets).


---

## Observability: Reason code naming convention

### Breaking Change

0.9.0 renames all reason code strings from UPPERCASE_SNAKE_CASE to
lowercase_snake_case. This affects:

- Prometheus metric label values (`reason="..."`)
- Structured log entries
- Diagnostics endpoint JSON output
- Any tooling that matches on reason code strings

### Reason Code Mapping: 0.8.x → 0.9.0

| # | 0.8.x `as_str()` | 0.9.0 `as_str()` | Variant Name Change |
|--:|-------------------|-------------------|---------------------|
| 0 | `CONVERTED` | `converted` | — |
| 1 | `SKIPPED_ACCEPT` | `skipped_accept` | — |
| 2 | `SKIPPED_NO_ACCEPT` | `skipped_no_accept` | — |
| 3 | `SKIPPED_CONDITIONAL` | `skipped_conditional` | — |
| 4 | `FAILED_DECOMPRESSION` | `decompression_error` | `FailedDecompression` → `DecompressionError` |
| 5 | `DECOMPRESSION_BUDGET_EXCEEDED` | `decompression_budget_exceeded` | — |
| 6 | `DECOMPRESSION_FORMAT_ERROR` | `decompression_format_error` | — |
| 7 | `DECOMPRESSION_TRUNCATED_INPUT` | `decompression_truncated_input` | — |
| 8 | `DECOMPRESSION_IO_ERROR` | `decompression_io_error` | — |
| 9 | `PARSE_TIMEOUT` | `timeout` | `ParseTimeout` → `Timeout` |
| 10 | `PARSE_BUDGET_EXCEEDED` | `budget_exceeded` | `ParseBudgetExceeded` → `BudgetExceeded` |
| 11 | `REPLAY_BUFFER_ERROR` | `replay_error` | `ReplayBufferError` → `ReplayError` |
| 12 | `SKIPPED_ACCEPT_REJECT` | `skipped_accept_reject` | — |
| 13 | `FFI_CALL_ERROR` | `ffi_panic` | `FfiCallError` → `FfiPanic` |
| 14 | `NOT_ELIGIBLE` | `not_eligible` | — |
| 15 | `DISABLED` | `disabled` | — |
| 16 | `FAILED_OPEN` | `failed_open` | — |
| 17 | `FAILED_CLOSED` | `failed_closed` | — |

### New Reason Codes in 0.9.0

These 8 codes are new — they have no 0.8.x equivalent:

| # | `as_str()` | Category | Description |
|--:|-----------|----------|-------------|
| 18 | `conversion_error` | Error | Generic conversion failure |
| 19 | `memory_budget_exceeded` | Error | Memory budget exceeded |
| 20 | `overload` | Error | Inflight guard triggered |
| 21 | `invalid_dynconf` | Error | Invalid dynamic configuration |
| 22 | `degraded_snapshot` | Error | Degraded dynconf snapshot |
| 23 | `header_plan_apply_error` | Error | Header plan commit failure |
| 24 | `streaming_mid_flight_error` | Error | Streaming mid-flight error |
| 25 | `bypass_no_transform` | Skip | No-Transform directive bypass |

### Metric Family Consolidation: 0.8.x → 0.9.0

0.8.x used per-reason metric keys. 0.9.0 consolidates them into unified metric families with a `reason` label. All metric names use the `nginx_markdown_` prefix.

| 0.8.x Metric Key | 0.9.0 Metric Family | Label |
|-------------------|---------------------|-------|
| `nginx_markdown_conversions_total` | `nginx_markdown_conversions_total` | `reason="converted"` |
| `nginx_markdown_skipped_accept_total` | `nginx_markdown_skips_total` | `reason="skipped_accept"` |
| `nginx_markdown_skipped_no_accept_total` | `nginx_markdown_skips_total` | `reason="skipped_no_accept"` |
| `nginx_markdown_skipped_conditional_total` | `nginx_markdown_skips_total` | `reason="skipped_conditional"` |
| `nginx_markdown_skipped_accept_reject_total` | `nginx_markdown_skips_total` | `reason="skipped_accept_reject"` |
| `nginx_markdown_skipped_not_eligible_total` | `nginx_markdown_skips_total` | `reason="not_eligible"` |
| `nginx_markdown_skipped_disabled_total` | `nginx_markdown_skips_total` | `reason="disabled"` |
| `nginx_markdown_failed_decompression_total` | `nginx_markdown_failures_total` | `reason="decompression_error"` |
| `nginx_markdown_decompression_budget_exceeded_total` | `nginx_markdown_failures_total` | `reason="decompression_budget_exceeded"` |
| `nginx_markdown_decompression_format_error_total` | `nginx_markdown_failures_total` | `reason="decompression_format_error"` |
| `nginx_markdown_decompression_truncated_input_total` | `nginx_markdown_failures_total` | `reason="decompression_truncated_input"` |
| `nginx_markdown_decompression_io_error_total` | `nginx_markdown_failures_total` | `reason="decompression_io_error"` |
| `nginx_markdown_parse_timeouts_total` | `nginx_markdown_failures_total` | `reason="timeout"` |
| `nginx_markdown_parse_budget_exceeded_total` | `nginx_markdown_failures_total` | `reason="budget_exceeded"` |
| `nginx_markdown_replay_buffer_errors_total` | `nginx_markdown_failures_total` | `reason="replay_error"` |
| `nginx_markdown_ffi_call_errors_total` | `nginx_markdown_failures_total` | `reason="ffi_panic"` |
| `nginx_markdown_failed_open_total` | `nginx_markdown_failopen_total` | `reason="failed_open"` |
| `nginx_markdown_failed_closed_total` | `nginx_markdown_failures_total` | `reason="failed_closed"` |

### Dashboard Migration Tips

#### Regex Replace Patterns

For Grafana dashboards and alert rules, apply these transformations:

```bash
# Step 1: Replace old per-reason metric names with unified family + label
# Example: markdown_skipped_accept_total → markdown_skipped_total{reason="skipped_accept"}

# Step 2: Lowercase all reason label values in existing queries
sed -i 's/reason="SKIP_/reason="skipped_/g' dashboard.json
sed -i 's/reason="FAIL_/reason="failed_/g' dashboard.json

# Step 3: Replace old metric names with new unified families
sed -i 's/markdown_skipped_accept_total/markdown_skipped_total{reason="skipped_accept"}/g' dashboard.json
sed -i 's/markdown_parse_timeouts_total/markdown_errors_total{reason="timeout"}/g' dashboard.json
```

#### Alert Migration Tips

| 0.8.x Alert Query | 0.9.0 Alert Query |
|-------------------|-------------------|
| `rate(markdown_parse_timeouts_total[5m]) > 0` | `rate(nginx_markdown_failures_total{reason="timeout"}[5m]) > 0` |
| `rate(markdown_failed_open_total[5m]) > 0.01` | `rate(nginx_markdown_failopen_total[5m]) > 0.01` |
| `sum(rate(markdown_ffi_call_errors_total[5m]))` | `rate(nginx_markdown_failures_total{reason="system_error"}[5m])` |
| `nginx_markdown_skips_total{reason="SKIP_ACCEPT"}` | `nginx_markdown_skips_total{reason="skipped_accept"}` |

#### Key Changes for Alert Authors

1. **Label values are lowercase**: `SKIP_ACCEPT` → `skipped_accept`
2. **Metrics consolidated**: individual error counters → `markdown_errors_total{reason="..."}`
3. **Wildcard matching**: use `markdown_errors_total` without a `reason` filter
   to catch all errors regardless of type
4. **New errors**: 8 new reason codes may trigger on `markdown_errors_total`
   that didn't exist in 0.8.x

### Reference

- Full schema: [Observability Schema v1](../architecture/observability-schema-v1.md)
- Single source of truth: `components/rust-converter/src/decision/reason_code.rs`


---

## Directive Mapping Table

| 0.8.x Directive | 0.9.0 Replacement | Notes |
|-----------------|-------------------|-------|
| `markdown_on_error pass` | `markdown_error_policy pass` | Same semantics |
| `markdown_on_error reject` | `markdown_error_policy fail_closed` | Renamed for clarity |
| `markdown_trust_forwarded_headers on` | `markdown_trusted_proxies <CIDR>...` | Now CIDR-based; http context only |
| `markdown_trust_forwarded_headers off` | _(omit directive)_ | Default ignores forwarded headers |
| `markdown_on_wildcard` | `markdown_accept wildcard` | Different syntax; controls wildcard Accept matching |
| _(new)_ | `markdown_profile balanced\|strict_cache\|streaming_first` | One-line production defaults |
| _(new)_ | `markdown_limits memory=64m timeout=5s max_inflight=64` | Key-value resource limits |

### Profile defaults (for reference)

| Setting | `balanced` | `strict_cache` | `streaming_first` |
|---------|-----------|---------------|-------------------|
| Error policy | `pass` | `pass` | `pass` |
| Cache validation | `ims_only` | `full` | `off` |
| Streaming | `auto` | `off` | `force` |
| Max inflight | 64 | 64 | 64 |

---

## Complete Config Diff: 0.8.x → 0.9.0

### Before (0.8.x)

```nginx
load_module modules/ngx_http_markdown_filter_module.so;

http {
    # Trust forwarded headers from any source (boolean)
    markdown_trust_forwarded_headers on;

    # Error handling: pass through on failure
    markdown_on_error pass;

    # Size and timeout limits
    markdown_max_size 10m;
    markdown_parse_timeout 30s;
    markdown_parser_budget 64m;
    markdown_decompress_max_size 20m;

    # Streaming configuration
    markdown_streaming_engine auto;
    markdown_stream_threshold 1m;
    markdown_stream_flush_min 4k;

    # Accept wildcard matching
    markdown_on_wildcard;

    upstream backend {
        server 127.0.0.1:8080;
    }

    server {
        listen 80;

        location /docs/ {
            markdown_filter on;
            markdown_conditional_requests full_support;
            proxy_set_header Accept-Encoding "";
            proxy_pass http://backend;
        }

        # Metrics endpoint (old per-reason counters)
        location = /nginx-markdown/metrics {
            markdown_metrics on;
            allow 127.0.0.1;
            deny all;
        }
    }
}
```

### After (0.9.0)

```nginx
load_module modules/ngx_http_markdown_filter_module.so;

http {
    # Profile provides tested defaults — one line replaces many directives
    markdown_profile balanced;

    # CIDR-based trusted proxies (replaces boolean trust)
    markdown_trusted_proxies 10.0.0.0/8 172.16.0.0/12;

    # Error policy (renamed from markdown_on_error)
    markdown_error_policy pass;

    # Consolidated resource limits (key-value syntax)
    markdown_limits memory=64m timeout=5s max_inflight=64;

    # Accept negotiation (replaces markdown_on_wildcard)
    markdown_accept wildcard;

    # Streaming configuration (replaces markdown_streaming_engine in v0.9.1)
    markdown_streaming auto;
    markdown_stream_threshold 1m;
    markdown_stream_flush_min 4k;

    upstream backend {
        server 127.0.0.1:8080;
    }

    server {
        listen 80;

        location /docs/ {
            markdown_filter on;
            # Conditional requests controlled by profile (ims_only)
            proxy_set_header Accept-Encoding "";
            proxy_pass http://backend;
        }

        # Metrics endpoint (unified families with reason label)
        location = /nginx-markdown/metrics {
            markdown_metrics on;
            allow 127.0.0.1;
            deny all;
        }
    }
}
```

### Annotation of changes

| Line area | What changed | Why |
|-----------|-------------|-----|
| `markdown_trust_forwarded_headers on` | → `markdown_trusted_proxies <CIDR>...` | CIDR-gated trust prevents header spoofing from untrusted sources |
| `markdown_on_error pass` | → `markdown_error_policy pass` | Renamed; `reject` becomes `fail_closed` for clarity |
| `markdown_max_size` / `markdown_memory_budget` + timeout + budget | → `markdown_limits memory=64m timeout=5s max_inflight=64` | Consolidated key-value limits directive |
| `markdown_on_wildcard` | → `markdown_accept wildcard` | New directive syntax for Accept matching control |
| _(none)_ | + `markdown_profile balanced` | Profile system provides tested production defaults |
| Metrics queries | Use `nginx_markdown_skips_total{reason="..."}` | Per-reason counters consolidated into unified families |

---

## Common Migration Failures

### `nginx -t` rejects old directive

```
nginx: [emerg] "markdown_on_error" directive has been removed in 0.9.0;
use "markdown_error_policy pass|fail_closed" instead
(see docs/guides/MIGRATION-0.9.md)
```

**Fix:** Replace `markdown_on_error pass` with `markdown_error_policy pass`,
or `markdown_on_error reject` with `markdown_error_policy fail_closed`.

### Trusted proxies rejected in server/location context

```
nginx: [emerg] "markdown_trusted_proxies" directive is only valid in the
http context, not in server or location
(see docs/guides/MIGRATION-0.9.md)
```

**Fix:** Move `markdown_trusted_proxies` to the `http {}` block. Per-server
trust is no longer supported (it creates local trust-bypass risks).

### Invalid CIDR in trusted proxies

```
nginx: [emerg] invalid CIDR "10.0.0.0/99" in "markdown_trusted_proxies"
directive; expected an IPv4 or IPv6 CIDR (e.g. 10.0.0.0/8, 2001:db8::/32)
or "off"
```

**Fix:** Use valid CIDR notation. Common ranges: `10.0.0.0/8` (private),
`172.16.0.0/12` (Docker), `192.168.0.0/16` (local).

### Profile conflicts with explicit directives

```
nginx: [warn] "markdown_error_policy fail_closed" overrides
profile "balanced" default (pass) in location /docs/
```

**Note:** This is a warning, not an error. Explicit directives always override
profile defaults. The warning helps you audit intentional overrides vs.
accidental conflicts.

### Dashboards show no data after upgrade

**Cause:** Old metric names no longer exist. For example,
`markdown_parse_timeouts_total` is now
`markdown_errors_total{reason="timeout"}`.

**Fix:** Apply the metric family consolidation table (see the Observability
section above) to all dashboard queries and alert rules.

---

## Rollback Plan: 0.9.0 → 0.8.x

If 0.9.0 causes issues in production and you need to revert:

1. **Stop NGINX gracefully:**
   ```bash
   sudo nginx -s quit
   ```

2. **Restore the 0.8.x module binary:**
   ```bash
   sudo cp /path/to/backup/ngx_http_markdown_filter_module.so \
       /usr/lib/nginx/modules/
   ```

3. **Restore the 0.8.x configuration:**
   ```bash
   sudo cp /path/to/backup/nginx.conf /etc/nginx/nginx.conf
   # Restore any included configs as well
   ```

4. **Validate the restored configuration:**
   ```bash
   sudo nginx -t
   ```

5. **Start NGINX with the 0.8.x module:**
   ```bash
   sudo nginx
   ```

6. **Verify metrics and dashboards:**
   - Confirm old per-reason metric names are emitting again
   - Confirm UPPERCASE reason codes are back in logs/metrics

**Important:** 0.9.0 and 0.8.x module binaries are not interchangeable.
The Rust converter and C module must be a matched pair from the same version.
Do not mix versions across the FFI boundary.

---

## Previous Versions

For migration guides from earlier releases:

| From | To | Guide |
|------|----|-------|
| 0.7.x | 0.8.0 | [docs/guides/MIGRATION-0.8.md](MIGRATION-0.8.md) |
| 0.6.x | 0.7.0 | See CHANGELOG 0.7.0 breaking changes section |
| 0.5.x | 0.6.0 | See CHANGELOG 0.6.0 breaking changes section |

Each major version introduced breaking changes. If you are upgrading from
0.7.x or earlier, upgrade to 0.8.x first, validate, then upgrade to 0.9.0.
Skipping versions is not recommended.

---

## Installation Verification (doctor)

The `nginx-markdown-doctor` tool provides automated installation diagnostics:

```bash
# Human-readable output
bash tools/doctor/nginx-markdown-doctor.sh

# JSON output for scripting
bash tools/doctor/nginx-markdown-doctor.sh --json
```

The tool checks:
- NGINX version detection
- Module `.so` file existence at expected paths
- Basic configuration syntax validation

See [docs/guides/doctor.md](doctor.md) for full usage documentation.

---

## Configuration Validation

`nginx -t` is the primary configuration validator for 0.9.0 upgrades:

```bash
sudo nginx -t
```

The module registers reject-only stubs for all removed directives. When
`nginx -t` encounters a removed directive, it emits a specific error message
naming the replacement directive and linking to this migration guide.

**Validation workflow:**

1. Update your configuration files using the directive mapping table above.
2. Run `nginx -t` — fix any errors.
3. Repeat until `nginx -t` reports `syntax is ok` and `test is successful`.
4. Reload: `sudo nginx -s reload`

**Tip:** Run `nginx -t` in a staging environment first. The 0.9.0 module will
reject any configuration containing removed directives, preventing a reload
that would break a running instance.

---

## No Legacy Compatibility

**0.9.0 does not provide backward-compatible aliases, shims, or translation
layers for removed directives.** This is a deliberate design choice:

- Removed directives fail immediately at `nginx -t` time with an actionable
  error message. There is no silent degradation or fallback behavior.
- This ensures that operators explicitly acknowledge and complete the migration
  rather than running in an ambiguous mixed state.
- The breaking boundary is unambiguous: if `nginx -t` passes, your
  configuration is fully 0.9.0 compliant.

If you need to maintain both 0.8.x and 0.9.0 configurations (e.g., during a
staged rollout across a fleet), use separate configuration files and deploy
the matching module binary with each configuration version.
