# Cross-Spec Naming Convention Reference — 0.4.0

> Requirements references: 4.1, 4.2, 4.3, 4.4, 4.5

All 0.4.0 sub-specs must follow these conventions for any new operator-facing surface.

## Summary

| Surface                  | Prefix                    | Format                    | Regex                                                                  |
|--------------------------|---------------------------|---------------------------|------------------------------------------------------------------------|
| NGINX directives         | `markdown_`               | lowercase + underscores   | `^markdown_[a-z][a-z0-9_]*$`                                          |
| Prometheus metrics       | `nginx_markdown_`         | snake_case                | `^nginx_markdown_([a-z][a-z0-9_]*_seconds_(bucket\|sum\|count)\|(?!.*_(bucket\|sum\|count)$)[a-z][a-z0-9_]*(_total\|_bytes\|_seconds\|_info)?)$`  |
| Decision reason codes    | —                         | uppercase snake_case      | `^[A-Z][A-Z0-9_]*$`                                                   |
| Benchmark report fields  | —                         | lowercase kebab-case JSON | `^[a-z][a-z0-9-]*$`                                                   |
| C macro constants        | `NGX_HTTP_MARKDOWN_`      | uppercase + underscores   | `^NGX_HTTP_MARKDOWN_[A-Z][A-Z0-9_]*$`                                 |

---

## 1. NGINX Configuration Directives

- **Prefix:** `markdown_`
- **Format:** lowercase with underscores
- **Regex:** `^markdown_[a-z][a-z0-9_]*$`
- **C macro constants** use the `NGX_HTTP_MARKDOWN_` prefix (see §5).

### Existing directives

| Directive                              | Purpose                              |
|----------------------------------------|--------------------------------------|
| `markdown_filter`                      | Enable/disable the filter            |
| `markdown_memory_budget`               | Maximum response size to convert     |
| `markdown_timeout`                     | Conversion timeout                   |
| `markdown_error_policy`                | Failure policy (pass / fail_closed)  |
| `markdown_flavor`                      | Markdown output flavor               |
| `markdown_token_estimate`              | Token estimation metadata            |
| `markdown_front_matter`               | YAML front matter injection          |
| `markdown_accept`                     | Accept negotiation (strict/wildcard) |
| `markdown_auth_policy`                 | Auth-based eligibility policy        |
| `markdown_auth_cookies`                | Cookie forwarding for auth           |
| `markdown_etag`                        | ETag generation                      |
| `markdown_conditional_requests`        | Conditional request support          |
| `markdown_log_verbosity`              | Log verbosity level                  |
| `markdown_buffer_chunked`              | Chunked buffering control            |
| `markdown_stream_types`               | Eligible MIME types                  |
| `markdown_trusted_proxies`            | Trust X-Forwarded-* headers          |
| `markdown_large_body_threshold`        | Large-body processing threshold      |
| `markdown_metrics_shm_size`           | Shared memory zone size for metrics  |
| `markdown_metrics`                     | Metrics endpoint enable/config       |

New directives introduced in 0.4.0 must follow the same `markdown_` prefix and lowercase-underscore pattern.

---

## 2. Prometheus Metrics

- **Prefix:** `nginx_markdown_`
- **Format:** snake_case
- **Regex:** `^nginx_markdown_([a-z][a-z0-9_]*_seconds_(bucket|sum|count)|(?!.*_(bucket|sum|count)$)[a-z][a-z0-9_]*(_total|_bytes|_seconds|_info)?)$`
- **Structure:** `<namespace>_<subsystem>_<name>_<unit>` per Prometheus naming best practices.
- Counters end with `_total`. Use `_bytes`, `_seconds`, `_info` suffixes as appropriate.
- Histograms with `_seconds` base metrics may expose `_seconds_bucket`, `_seconds_sum`, and `_seconds_count`.

### Defined metrics

| Metric name                                       | Type      | Labels          |
|---------------------------------------------------|-----------|-----------------|
| `nginx_markdown_conversions_total`                | counter   | —               |
| `nginx_markdown_conversions_bypassed_total`       | counter   | —               |
| `nginx_markdown_conversion_duration_seconds`      | histogram | `le`            |
| `nginx_markdown_input_bytes_total`                | counter   | —               |
| `nginx_markdown_output_bytes_total`               | counter   | —               |
| `nginx_markdown_failures_total`                   | counter   | `reason`        |
| `nginx_markdown_decompressions_total`             | counter   | `format`        |

### Label cardinality rules

Labels must be low-cardinality.

| Allowed labels | Forbidden labels                                        |
|----------------|---------------------------------------------------------|
| `reason`       | `url`, `host`, `ua`, `query`, `referer`, `remote_addr`, `path` |
| `stage`        |                                                         |
| `format`       |                                                         |
| `le`           |                                                         |

---

## 3. Decision Reason Codes

Reason codes are emitted in both decision-log `reason=` fields and Prometheus
metric label values, and must be consistent across both surfaces. Two tiers:

- **Decision-chain reason codes** — lowercase snake_case. Returned by
  `ngx_http_markdown_get_reason_code_str()` (resolved from the Rust
  `ReasonCode` registry). Examples: `not_eligible`, `skipped_accept`,
  `skipped_conditional`, `disabled`, `converted`, `failed_open`, `failed_closed`,
  `bypass_no_transform`.
- **Streaming engine reason codes** — uppercase snake_case. Examples:
  `ENGINE_STREAMING`, `STREAMING_CONVERT`, `STREAMING_FALLBACK_PREBUFFER`,
  `STREAMING_FAIL_POSTCOMMIT`, `STREAMING_SKIP_UNSUPPORTED`,
  `STREAMING_BUDGET_EXCEEDED`, `STREAMING_PRECOMMIT_FAILOPEN`,
  `STREAMING_PRECOMMIT_REJECT`, `STREAMING_SHADOW`.

The authoritative reason-code list is
`components/rust-converter/src/decision/reason_code.rs` (decision-chain) plus
the streaming accessors in
`components/nginx-module/src/ngx_http_markdown_reason.c`.

### Decision-chain reason code table (lowercase)

| Category | Code                    | Meaning                                              |
|----------|-------------------------|------------------------------------------------------|
| Skipped  | `not_eligible`          | Not eligible (method, status, range, content-type, size, or auth) |
| Skipped  | `skipped_accept`        | `Accept` present but does not request Markdown       |
| Skipped  | `skipped_no_accept`     | No `Accept` header and `markdown_accept strict`      |
| Skipped  | `skipped_accept_reject` | `Accept` explicitly rejects Markdown                 |
| Skipped  | `skipped_conditional`   | Conditional request matched → 304                    |
| Skipped  | `bypass_no_transform`   | `no-transform` Cache-Control present                 |
| Disabled | `disabled`              | Module disabled for this scope                       |
| Converted| `converted`             | All checks passed, conversion succeeded              |
| Failed   | `failed_open`           | Conversion failed, original HTML served (`pass`)     |
| Failed   | `failed_closed`         | Conversion failed, error returned (`fail_closed`)    |
| Failed   | `conversion_error`      | HTML parse or conversion error                       |
| Failed   | `memory_budget_exceeded`| Memory limit reached                                 |
| Failed   | `timeout`               | Parser exceeded `markdown_parse_timeout`             |
| Failed   | `ffi_panic`             | Internal/system error (Rust↔C panic)                 |

These codes no longer use the legacy `ngx_http_markdown_eligibility_t` enum or
`ngx_http_markdown_error_category_t` categories.

---

## 4. Benchmark Report Fields

- **Format:** lowercase kebab-case JSON keys
- **Regex:** `^[a-z][a-z0-9-]*$`

### Defined fields

| Field                      | Description                                    |
|----------------------------|------------------------------------------------|
| `token-reduction-percent`  | Percentage reduction in token count            |
| `p50-latency-ms`          | 50th-percentile conversion latency (ms)        |
| `p99-latency-ms`          | 99th-percentile conversion latency (ms)        |
| `input-bytes`             | Raw input size in bytes                        |
| `output-bytes`            | Converted output size in bytes                 |
| `fallback-rate`           | Fraction of requests that fell back to HTML    |
| `corpus-name`             | Name of the benchmark corpus                   |
| `fixture-id`             | Identifier of the test fixture                 |
| `conversion-result`       | Outcome: `converted`, `skipped`, `failed-open` |

---

## 5. C Macro Constants

- **Prefix:** `NGX_HTTP_MARKDOWN_`
- **Format:** uppercase with underscores
- **Regex:** `^NGX_HTTP_MARKDOWN_[A-Z][A-Z0-9_]*$`

### Examples

```c
#define NGX_HTTP_MARKDOWN_ON_ERROR_PASS    0
#define NGX_HTTP_MARKDOWN_ON_ERROR_REJECT  1
```

All project-specific C preprocessor constants must use the `NGX_HTTP_MARKDOWN_` prefix. Function-like macros use the lowercase `ngx_http_markdown_` prefix per NGINX convention.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
