# 0.5.0 Cross-Spec Naming Conventions

## Overview

All 0.5.0 sub-specs must follow the naming conventions defined in this document for any new operator-facing surface.

## NGINX Configuration Directives

- Existing directive prefix: `markdown_` (unchanged)
- Streaming-related new directive prefix: `markdown_streaming_`
- Format: lowercase with underscores
- Regex validation: `^markdown_(streaming_)?[a-z][a-z0-9_]*$`
- C macro constant prefix: `NGX_HTTP_MARKDOWN_`
- C macro constant regex: `^NGX_HTTP_MARKDOWN_[A-Z][A-Z0-9_]*$`

Examples:
- `markdown_filter` — existing directive
- `markdown_streaming_enabled` — streaming-related new directive
- `NGX_HTTP_MARKDOWN_STREAMING_ON` — C macro constant

## Prometheus Metrics

- Prefix: `nginx_markdown_`
- Format: snake_case
- Regex validation: `^nginx_markdown_[a-z][a-z0-9_]*(_total|_bytes|_seconds|_info)?$`
- Follows Prometheus naming best practices: `<namespace>_<subsystem>_<name>_<unit>`
- Counters end with `_total`
- Use `_bytes`, `_seconds`, `_info` suffixes
- Streaming-related metrics include `streaming` label or prefix

### Example Metrics

| Metric Name | Type | Description |
|-------------|------|-------------|
| `nginx_markdown_streaming_requests_total` | counter | Total streaming path requests |
| `nginx_markdown_streaming_fallback_total` | counter | Total streaming fallbacks (labels: `reason`, `phase`) |
| `nginx_markdown_streaming_precommit_failopen_total` | counter | Total pre-commit fail-open events |
| `nginx_markdown_streaming_postcommit_error_total` | counter | Total post-commit errors |
| `nginx_markdown_streaming_budget_exceeded_total` | counter | Total memory budget exceeded events |
| `nginx_markdown_streaming_shadow_total` | counter | Total shadow mode comparison attempts |
| `nginx_markdown_streaming_shadow_diff_total` | counter | Total shadow mode output differences |
| `nginx_markdown_streaming_peak_memory_bytes` | gauge | Peak memory estimate for most recent request |
| `nginx_markdown_streaming_ttfb_seconds` | gauge | Time to first byte for most recent request (millisecond resolution) |

### Label Constraints

Allowed labels (low cardinality): `reason`, `stage`, `phase`, `engine`, `format`, `le`

Forbidden labels (high cardinality): `url`, `host`, `ua`, `query`, `referer`, `remote_addr`, `path`

## Decision Reason Codes

- Format: uppercase SNAKE_CASE
- Regex validation: `^[A-Z][A-Z0-9_]*$`
- Used in logs and metrics labels

### Streaming Reason Codes

| Category | Code | Meaning |
|----------|------|---------|
| Streaming success | `STREAMING_CONVERT` | Streaming path successful conversion |
| Streaming shadow | `STREAMING_SHADOW` | Shadow mode comparison attempt |
| Streaming fallback | `STREAMING_FALLBACK_PREBUFFER` | Pre-commit phase fallback to full-buffer |
| Streaming failure | `STREAMING_FAIL_POSTCOMMIT` | Post-commit phase failure (fail-closed) |
| Streaming pre-commit | `STREAMING_PRECOMMIT_FAILOPEN` | Pre-commit fail-open (original HTML served) |
| Streaming pre-commit | `STREAMING_PRECOMMIT_REJECT` | Pre-commit fail-closed (error returned) |
| Streaming skip | `STREAMING_SKIP_UNSUPPORTED` | Capability unsupported for streaming, using full-buffer |
| Engine selection | `ENGINE_FULLBUFFER` | Full-buffer engine selected |
| Engine selection | `ENGINE_STREAMING` | Streaming engine selected |
| Budget | `STREAMING_BUDGET_EXCEEDED` | Streaming memory budget exceeded |
