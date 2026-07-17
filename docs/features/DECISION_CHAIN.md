# Decision Chain Model

## Overview

Every request that reaches the Markdown filter module passes through an ordered sequence of checks called the decision chain. The first failing check determines the outcome and assigns a reason code. If all checks pass, conversion is attempted and the outcome depends on whether conversion succeeds or fails.

Reason codes are the canonical, machine-readable outcome identifiers. They are emitted in two places and use the **same lowercase snake_case strings** everywhere:

- Decision log entries: `markdown: reason=<code> ...` (see `components/nginx-module/src/ngx_http_markdown_decision_log_impl.h`)
- Prometheus metrics labels (`reason="<code>"`, see `components/nginx-module/src/ngx_http_markdown_prometheus_impl.h`)

The single source of truth for the reason code list is `components/rust-converter/src/decision/reason_code.rs`, mirrored in [Observability Schema v1](../architecture/observability-schema-v1.md). This document describes the check order, what each check evaluates, and how outcomes are determined. Rollout procedures are in the [Rollout Cookbook](../guides/ROLLOUT_COOKBOOK.md); rollback procedures are in the [Rollback Guide](../guides/ROLLBACK_GUIDE.md).

## Decision Chain Flowchart

```mermaid
flowchart TD
    A["Request enters module"] --> B{"markdown_filter<br/>enabled?"}
    B -->|No| C["disabled"]
    B -->|Yes| D{"Method<br/>GET/HEAD?"}
    D -->|No| E["not_eligible"]
    D -->|Yes| F{"Status<br/>200 or 206?"}
    F -->|No| G["not_eligible"]
    F -->|Yes| H{"Range<br/>request?"}
    H -->|Yes| I["not_eligible"]
    H -->|No| J{"Content-Type<br/>text/html?"}
    J -->|No| K["not_eligible"]
    J -->|Yes| L{"Size within<br/>budget?"}
    L -->|No| M["not_eligible"]
    L -->|Yes| N{"Auth policy<br/>denies request?"}
    N -->|Yes| O["not_eligible"]
    N -->|No| P{"Accept header<br/>requests MD?"}
    P -->|Rejects (q=0)| Q["skipped_accept_reject"]
    P -->|No header (strict)| R["skipped_no_accept"]
    P -->|No match| S["skipped_accept"]
    P -->|Yes| T["Attempt conversion"]
    T -->|Success| U["converted"]
    T -->|Failure + pass| V["failed_open"]
    T -->|Failure + fail_closed| W["failed_closed"]
```

> **Note on eligibility granularity.** Checks 2 through 8 (method, status, range,
> content-type, size, auth) no longer produce distinct reason codes. They are
> collapsed into a single canonical code `not_eligible`, because the request
> state is the same — *not eligible for conversion* — regardless of which
> specific check short-circuited. The individual failing check is still visible
> in the decision log's structured metadata (`method`, `content_type`, `status`)
> for diagnostics, but the reason code string is `not_eligible`.

## Check Order

The decision chain evaluates checks in a fixed order. The first check that fails stops evaluation and assigns the corresponding reason code. This "short-circuit" behavior means a request that fails multiple checks always gets the reason code of the earliest failing check in the sequence.

| Order | Check | What It Evaluates | Reason Code on Failure |
|-------|-------|--------------------|------------------------|
| 1 | Scope enablement | Is `markdown_filter` enabled (`on`, `1`, `true`, `yes`, or a variable that resolves to a truthy value) for this request's location/server/http context? | `disabled` |
| 2 | HTTP method | Is the request method `GET` or `HEAD`? Other methods (POST, PUT, DELETE, etc.) are not eligible. | `not_eligible` |
| 3 | Response status | Is the upstream response status `200 OK` or `206 Partial Content`? Non-200/206 responses (redirects, errors, etc.) are not eligible. | `not_eligible` |
| 4 | Range request | Is this a range request (`Range` header present)? Range requests are not eligible because partial content cannot be converted. | `not_eligible` |
| 5 | Content-Type | Is the upstream `Content-Type` header `text/html` (with any charset parameter)? Non-HTML content types are not eligible. | `not_eligible` |
| 6 | Response size | Is the response body size within the configured `markdown_limits memory=` budget? Oversized responses are not eligible. | `not_eligible` |
| 7 | Auth policy | Is the request authenticated and `markdown_auth_policy` set to `deny`? Authenticated requests are detected through the existing `Authorization` header and auth-cookie checks. | `not_eligible` |
| 8 | Accept negotiation | Does the `Accept` header indicate the client wants Markdown? Evaluated per `markdown_accept` (`strict` | `wildcard` | `force`). | `skipped_accept_reject` / `skipped_no_accept` / `skipped_accept` (see below) |
| 9 | Conversion attempt | All checks passed. The module attempts HTML-to-Markdown conversion. | _(see outcome determination below)_ |

### Accept negotiation outcomes

When the request is eligible on checks 1–7 but the `Accept` header does not
resolve in favor of Markdown, one of three distinct skip reason codes is emitted
(this is the one eligibility branch that preserves sub-case granularity, because
the failure cause is operationally meaningful for content negotiation):

| Condition | Reason Code |
|-----------|-------------|
| `Accept` explicitly rejects Markdown (`text/markdown;q=0` or a wildcard with `q=0`) | `skipped_accept_reject` |
| No `Accept` header present and `markdown_accept` is `strict` | `skipped_no_accept` |
| `Accept` present but does not request Markdown (and not the reject case above) | `skipped_accept` |

When `markdown_accept` is `wildcard`, `text/*` and `*/*` also qualify for conversion;
when `force`, conversion is attempted regardless of the `Accept` header.

## First-Failing-Check Rule

The module evaluates checks 1 through 9 in the order listed above. As soon as one check fails, the module assigns the corresponding reason code and stops. No subsequent checks are evaluated.

For example, if a `POST` request arrives for a path where `markdown_filter` is `on`, the module assigns `not_eligible` (check 2) without evaluating status, content-type, size, auth, or Accept checks.

This behavior is important for operators diagnosing why a request was skipped. The reason code always points to the first condition that prevented conversion, not to all conditions that would have prevented it.

## Outcome Determination

When all eligibility checks pass (checks 1–9), the module attempts conversion. The outcome depends on whether conversion succeeds and, if it fails, on the `markdown_error_policy` configuration:

### Success: converted

Conversion succeeded. The client receives the Markdown representation of the HTML response. The reason code is `converted` and the request state is CONVERTED.

### Failure with `markdown_error_policy pass`: failed_open

Conversion was attempted but failed (HTML parse error, timeout, resource limit, decompression error, or internal/system error). Because `markdown_error_policy` is set to `pass` (the default), the module serves the original HTML response unchanged. The client is unaffected. The reason code is `failed_open` and the request state is FAILED.

This is the recommended configuration for production rollouts. Conversion failures never break client responses.

### Failure with `markdown_error_policy fail_closed`: failed_closed

Conversion was attempted but failed. Because `markdown_error_policy` is set to `fail_closed`, the module returns a `502 Bad Gateway` error. The reason code is `failed_closed` and the request state is FAILED.

Use `fail_closed` only when you need strict guarantees that clients never receive HTML when they requested Markdown. This is not recommended during initial rollout.

## Failure Sub-Classification

When conversion fails (either `failed_open` or `failed_closed`), the module also records a failure sub-classification that provides more detail about what went wrong. These appear as a separate `category=` field in decision log entries and as distinct `reason` label values on the `nginx_markdown_failures_total` metric. They do not change the primary outcome (`failed_open` or `failed_closed`), which is determined solely by the `markdown_error_policy` setting.

| Failure Reason Code | Meaning |
|---------------------|---------|
| `conversion_error` | HTML parse or conversion error — the input HTML could not be processed |
| `memory_budget_exceeded` | Memory limit reached (`markdown_limits memory=` or parser budget) |
| `timeout` | Parser execution exceeded `markdown_parse_timeout` |
| `budget_exceeded` | Parser memory exceeded `markdown_parser_budget` |
| `ffi_panic` | Internal/system error (unexpected Rust↔C panic) |
| `decompression_error` / `decompression_budget_exceeded` / `decompression_format_error` / `decompression_truncated_input` / `decompression_io_error` | Decompression failures (see [Automatic Decompression](../features/AUTOMATIC_DECOMPRESSION.md)) |
| `replay_error` | Fail-open replay buffer init/append failure |
| `overload` | Inflight guard rejected the request |
| `invalid_dynconf` / `degraded_snapshot` / `header_plan_apply_error` | Dynamic configuration or header-plan errors |
| `streaming_mid_flight_error` | Streaming conversion mid-flight error |

## Request States

Every request that enters the decision chain ends up in one of four mutually exclusive states. The request state is derived from the reason code — no additional runtime field is stored.

| Request State | Reason Codes | Meaning |
|---------------|-------------|---------|
| NOT_ENABLED | `disabled` | Module is disabled for this scope. The request was never evaluated for eligibility. |
| SKIPPED | `not_eligible`, `skipped_accept`, `skipped_no_accept`, `skipped_accept_reject`, `bypass_no_transform` | Module is enabled but the request did not pass one of the eligibility checks. |
| CONVERTED | `converted` | All checks passed and conversion succeeded. |
| FAILED | `failed_open`, `failed_closed` | All checks passed, conversion was attempted, but it did not succeed. |

Operators can determine request state counts from metrics and logs:
- NOT_ENABLED: count of `reason="disabled"` in decision log entries (`grep "reason=disabled" error.log`)
- SKIPPED: count of `reason="not_eligible"`, `reason="skipped_*"` in decision log entries
- CONVERTED: `nginx_markdown_conversions_total` metric
- FAILED: `nginx_markdown_failopen_total` (`failed_open`) + `nginx_markdown_failed_closed_total` (`failed_closed`)

## Reason Code Reference

The complete set of 26 reason codes is defined in `components/rust-converter/src/decision/reason_code.rs` and mirrored in [Observability Schema v1](../architecture/observability-schema-v1.md). All `as_str()` values are lowercase snake_case. The table below maps the high-level decision outcomes described in this document to their reason codes; the full registry (including decompression, dynconf, and streaming sub-codes) lives in the schema document.

| Decision Outcome | Reason Code | Request State | Description |
|---|---|---|---|
| Module disabled | `disabled` | NOT_ENABLED | Module disabled by configuration for this scope |
| Not eligible (method/status/range/content-type/size/auth) | `not_eligible` | SKIPPED | Response not eligible for conversion |
| Accept negotiation — no match | `skipped_accept` | SKIPPED | Accept header present but does not request Markdown |
| Accept negotiation — no header (strict) | `skipped_no_accept` | SKIPPED | No Accept header present and `markdown_accept` is `strict` |
| Accept negotiation — explicit reject | `skipped_accept_reject` | SKIPPED | `Accept` explicitly rejects Markdown (`q=0`) |
| Conditional request matched (304) | `skipped_conditional` | SKIPPED | Conditional request matched (If-None-Match / If-Modified-Since) |
| No-transform bypass | `bypass_no_transform` | SKIPPED | `no-transform` Cache-Control directive present |
| Conversion succeeded | `converted` | CONVERTED | Markdown produced successfully |
| Conversion failed, original HTML served | `failed_open` | FAILED | `markdown_error_policy pass` |
| Conversion failed, error returned | `failed_closed` | FAILED | `markdown_error_policy fail_closed` |

> **Removed reason codes.** Earlier releases documented per-check uppercase codes
> such as `SKIP_METHOD`, `SKIP_STATUS`, `SKIP_CONFIG`, and `ELIGIBLE_CONVERTED`.
> These were consolidated in the 0.9.0 observability schema: eligibility checks
> 2–7 now emit `not_eligible`, scope-off emits `disabled`, and the conversion
> outcomes are `converted` / `failed_open` / `failed_closed`. If you are
> correlating old dashboards or alerts, update them to the lowercase codes above.

### v0.9.0 Additional Reason Codes and Behavior

| Reason Code / Behavior | Description |
|------------------------|-------------|
| `replay_error` | Fail-open replay buffer init or append failure; sets `precommit_error` flag (prevents duplicate finalize calls) |
| `failopen_completed` | Once-then-skip flag preventing duplicate `ngx_http_finalize_request` calls within a request lifetime |
| `decompression_budget_exceeded` | Decompression budget (`markdown_decompress_max_size`) exceeded; classified as a decompression error |
| `decompression_format_error` | Compressed input has invalid format (not valid gzip/deflate/brotli) |
| `decompression_truncated_input` | Compressed input was truncated (incomplete stream) |
| `decompression_io_error` | I/O error during decompression operation |
| `timeout` | Parser execution exceeded `markdown_parse_timeout` (default 30s) |
| `budget_exceeded` | Parser memory exceeded `markdown_parser_budget` (default 64m) |
| `overload` | Inflight guard rejected the request |
| `invalid_dynconf` / `degraded_snapshot` | Dynamic configuration error / degraded snapshot |
| `header_plan_apply_error` | Header plan apply error |
| `streaming_mid_flight_error` | Streaming conversion mid-flight error |
| Delivery vs Decision counter separation | `failopen_count` (delivery) increments only after downstream `NGX_OK`; decision counter increments on decision regardless of downstream status |

All reason codes use lowercase snake_case format. The same strings appear in both decision log entries and Prometheus metrics labels, so operators can correlate log entries with metric counters without translation.

## Implementation Details

The check order matches the eligibility evaluation in `components/nginx-module/src/ngx_http_markdown_eligibility.c`, with the header-filter and Accept-negotiation additions:

- Scope enablement (check 1) is evaluated before `ngx_http_markdown_check_eligibility()` is called.
- Auth policy (check 7) is evaluated as part of eligibility.
- Accept negotiation (check 8) is evaluated after the core eligibility checks pass.

The reason code strings are produced by the Rust `ReasonCode::as_str()` registry and surfaced to C via the `markdown_reason_code_str()` FFI accessor. C-side code never hard-codes reason code literals; it converts the `ReasonCode` discriminant into the canonical lowercase string. See [Observability Schema v1](../architecture/observability-schema-v1.md) for the full registry and FFI accessor list.

## Related Documentation

- [Rollout Cookbook](../guides/ROLLOUT_COOKBOOK.md) — staged rollout procedures with observation checkpoints
- [Rollback Guide](../guides/ROLLBACK_GUIDE.md) — how to disable or narrow conversion scope
- [Configuration Guide](../guides/CONFIGURATION.md) — directive reference and configuration examples
- [Content Negotiation](CONTENT_NEGOTIATION.md) — Accept header parsing and wildcard behavior
- [Observability Schema v1](../architecture/observability-schema-v1.md) — authoritative reason code registry, metric families, label whitelist
- [Operations Guide](../guides/OPERATIONS.md) — monitoring and troubleshooting

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.0 | 2026-07-09 | Kang | **Synchronized with observability-schema-v1 (single source of truth).** All reason codes switched to lowercase snake_case; eligibility checks 2–7 collapsed to `not_eligible`; scope-off → `disabled`; conversion outcomes → `converted`/`failed_open`/`failed_closed`; removed legacy `NGX_HTTP_MARKDOWN_INELIGIBLE_*` enum column; Accept negotiation split into `skipped_accept`/`skipped_no_accept`/`skipped_accept_reject` |
| 0.7.0 | 2026-05-17 | Kang | Added v0.7.0 reason codes (REPLAY_BUFFER_ERROR, DECOMPRESSION_BUDGET_EXCEEDED, PARSE_TIMEOUT, PARSE_BUDGET_EXCEEDED, SKIPPED_NO_ACCEPT, SKIPPED_CONDITIONAL) and delivery/decision counter separation |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
