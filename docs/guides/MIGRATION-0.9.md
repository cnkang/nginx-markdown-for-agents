# Migration Guide: 0.9.0 (Breaking Release)

0.9.0 is a breaking release. Several legacy directives are removed and replaced
by Config V2 directives. Removed directives are kept as **reject-only stubs**:
the parser entry still exists, but the only behavior is to fail `nginx -t` with
an actionable migration hint. There is **no alias compatibility** and **no
legacy fallback behavior** — this keeps the breaking-release boundary
unambiguous.

This guide is organized by owning spec. Sections are appended as each 0.9.0
spec lands; if a directive you use is not yet listed, consult the
`nginx -t` error message, which always names the replacement.

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

These 7 codes are new — they have no 0.8.x equivalent:

| # | `as_str()` | Category | Description |
|--:|-----------|----------|-------------|
| 18 | `conversion_error` | Error | Generic conversion failure |
| 19 | `memory_budget_exceeded` | Error | Memory budget exceeded |
| 20 | `overload` | Error | Inflight guard triggered |
| 21 | `invalid_dynconf` | Error | Invalid dynamic configuration |
| 22 | `degraded_snapshot` | Error | Degraded dynconf snapshot |
| 23 | `header_plan_apply_error` | Error | Header plan commit failure |
| 24 | `streaming_mid_flight_error` | Error | Streaming mid-flight error |

### Metric Family Consolidation: 0.8.x → 0.9.0

0.8.x used per-reason metric keys. 0.9.0 consolidates them into 5 unified
families with a `reason` label.

| 0.8.x Metric Key | 0.9.0 Metric Family | Label |
|-------------------|---------------------|-------|
| `markdown_conversions_total` | `markdown_conversions_total` | `reason="converted"` |
| `markdown_skipped_accept_total` | `markdown_skipped_total` | `reason="skipped_accept"` |
| `markdown_skipped_no_accept_total` | `markdown_skipped_total` | `reason="skipped_no_accept"` |
| `markdown_skipped_conditional_total` | `markdown_skipped_total` | `reason="skipped_conditional"` |
| `markdown_skipped_accept_reject_total` | `markdown_skipped_total` | `reason="skipped_accept_reject"` |
| `markdown_skipped_not_eligible_total` | `markdown_skipped_total` | `reason="not_eligible"` |
| `markdown_skipped_disabled_total` | `markdown_skipped_total` | `reason="disabled"` |
| `markdown_failed_decompression_total` | `markdown_errors_total` | `reason="decompression_error"` |
| `markdown_decompression_budget_exceeded_total` | `markdown_errors_total` | `reason="decompression_budget_exceeded"` |
| `markdown_decompression_format_error_total` | `markdown_errors_total` | `reason="decompression_format_error"` |
| `markdown_decompression_truncated_input_total` | `markdown_errors_total` | `reason="decompression_truncated_input"` |
| `markdown_decompression_io_error_total` | `markdown_errors_total` | `reason="decompression_io_error"` |
| `markdown_parse_timeouts_total` | `markdown_errors_total` | `reason="timeout"` |
| `markdown_parse_budget_exceeded_total` | `markdown_errors_total` | `reason="budget_exceeded"` |
| `markdown_replay_buffer_errors_total` | `markdown_errors_total` | `reason="replay_error"` |
| `markdown_ffi_call_errors_total` | `markdown_errors_total` | `reason="ffi_panic"` |
| `markdown_failed_open_total` | `markdown_failed_open_total` | `reason="failed_open"` |
| `markdown_failed_closed_total` | `markdown_failed_closed_total` | `reason="failed_closed"` |

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
| `rate(markdown_parse_timeouts_total[5m]) > 0` | `rate(markdown_errors_total{reason="timeout"}[5m]) > 0` |
| `rate(markdown_failed_open_total[5m]) > 0.01` | `rate(markdown_failed_open_total{reason="failed_open"}[5m]) > 0.01` |
| `sum(rate(markdown_ffi_call_errors_total[5m]))` | `rate(markdown_errors_total{reason="ffi_panic"}[5m])` |
| `nginx_markdown_skips_total{reason="SKIP_ACCEPT"}` | `markdown_skipped_total{reason="skipped_accept"}` |

#### Key Changes for Alert Authors

1. **Label values are lowercase**: `SKIP_ACCEPT` → `skipped_accept`
2. **Metrics consolidated**: individual error counters → `markdown_errors_total{reason="..."}`
3. **Wildcard matching**: use `markdown_errors_total` without a `reason` filter
   to catch all errors regardless of type
4. **New errors**: 7 new reason codes may trigger on `markdown_errors_total`
   that didn't exist in 0.8.x

### Reference

- Full schema: [Observability Schema v1](../architecture/observability-schema-v1.md)
- Single source of truth: `components/rust-converter/src/decision/reason_code.rs`
