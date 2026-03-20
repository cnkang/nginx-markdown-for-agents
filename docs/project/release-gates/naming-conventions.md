# Cross-Spec Naming Convention Reference — 0.4.0

> Requirements references: 4.1, 4.2, 4.3, 4.4, 4.5

All 0.4.0 sub-specs must follow these conventions for any new operator-facing surface.

## Summary

| Surface                  | Prefix                    | Format                    | Regex                                                                  |
|--------------------------|---------------------------|---------------------------|------------------------------------------------------------------------|
| NGINX directives         | `markdown_`               | lowercase + underscores   | `^markdown_[a-z][a-z0-9_]*$`                                          |
| Prometheus metrics       | `nginx_markdown_`         | snake_case                | `^nginx_markdown_[a-z][a-z0-9_]*(_total\|_bytes\|_seconds\|_info)?$`  |
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
| `markdown_max_size`                    | Maximum response size to convert     |
| `markdown_timeout`                     | Conversion timeout                   |
| `markdown_on_error`                    | Failure policy (pass / reject)       |
| `markdown_flavor`                      | Markdown output flavor               |
| `markdown_token_estimate`              | Token estimation metadata            |
| `markdown_front_matter`               | YAML front matter injection          |
| `markdown_on_wildcard`                 | Wildcard Accept handling             |
| `markdown_auth_policy`                 | Auth-based eligibility policy        |
| `markdown_auth_cookies`                | Cookie forwarding for auth           |
| `markdown_etag`                        | ETag generation                      |
| `markdown_conditional_requests`        | Conditional request support          |
| `markdown_log_verbosity`              | Log verbosity level                  |
| `markdown_buffer_chunked`              | Chunked buffering control            |
| `markdown_stream_types`               | Eligible MIME types                  |
| `markdown_trust_forwarded_headers`     | Trust X-Forwarded-* headers          |
| `markdown_large_body_threshold`        | Large-body processing threshold      |
| `markdown_metrics_shm_size`           | Shared memory zone size for metrics  |
| `markdown_metrics`                     | Metrics endpoint enable/config       |

New directives introduced in 0.4.0 must follow the same `markdown_` prefix and lowercase-underscore pattern.

---

## 2. Prometheus Metrics

- **Prefix:** `nginx_markdown_`
- **Format:** snake_case
- **Regex:** `^nginx_markdown_[a-z][a-z0-9_]*(_total|_bytes|_seconds|_info)?$`
- **Structure:** `<namespace>_<subsystem>_<name>_<unit>` per Prometheus naming best practices.
- Counters end with `_total`. Use `_bytes`, `_seconds`, `_info` suffixes as appropriate.

### Defined metrics

| Metric name                                       | Type      | Labels          |
|---------------------------------------------------|-----------|-----------------|
| `nginx_markdown_conversions_total`                | counter   | —               |
| `nginx_markdown_conversions_bypassed_total`       | counter   | —               |
| `nginx_markdown_conversion_duration_seconds`      | histogram | `le`            |
| `nginx_markdown_input_bytes_total`                | counter   | —               |
| `nginx_markdown_output_bytes_total`               | counter   | —               |
| `nginx_markdown_failures_total`                   | counter   | `stage`         |
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

- **Format:** uppercase snake_case
- **Regex:** `^[A-Z][A-Z0-9_]*$`
- Used in both log messages and Prometheus metric label values. Codes must be consistent across both surfaces.

### Reason code table

| Category | Code                      | Meaning                                              |
|----------|---------------------------|------------------------------------------------------|
| Eligible | `ELIGIBLE_CONVERTED`      | Eligible and successfully converted                  |
| Eligible | `ELIGIBLE_FAILED_OPEN`    | Eligible, conversion failed, served original HTML    |
| Eligible | `ELIGIBLE_FAILED_CLOSED`  | Eligible, conversion failed, returned error          |
| Skip     | `SKIP_METHOD`             | Not GET/HEAD                                         |
| Skip     | `SKIP_STATUS`             | Not 200/206                                          |
| Skip     | `SKIP_CONTENT_TYPE`       | Not text/html                                        |
| Skip     | `SKIP_SIZE`               | Exceeds `markdown_max_size`                          |
| Skip     | `SKIP_STREAMING`          | Unbounded streaming response                         |
| Skip     | `SKIP_AUTH`               | Auth policy denies                                   |
| Skip     | `SKIP_RANGE`              | Range request                                        |
| Skip     | `SKIP_CONFIG`             | Disabled by configuration                            |
| Failure  | `FAIL_CONVERSION`         | HTML parse or conversion error                       |
| Failure  | `FAIL_RESOURCE_LIMIT`     | Timeout or memory limit                              |
| Failure  | `FAIL_SYSTEM`             | Internal/system error                                |

These codes map to the existing `ngx_http_markdown_eligibility_t` enum values and `ngx_http_markdown_error_category_t` categories.

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
