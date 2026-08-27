# Decision Chain Model

## Overview

Every request that reaches the Markdown filter module passes through an ordered sequence of checks called the decision chain. The first failing check determines the outcome and assigns a reason code. If all checks pass, the module attempts conversion and the outcome depends on whether conversion succeeds or fails.

Canonical reason codes are the machine-readable, operator-visible outcome
identifiers. The module emits those codes in two places and uses the **same
lowercase snake_case strings** in both:

- Decision log entries: `markdown: reason=<code> ...` (see `components/nginx-module/src/ngx_http_markdown_decision_log_impl.h`)
- Prometheus metrics labels (`reason="<code>"`, see `components/nginx-module/src/ngx_http_markdown_metrics_v1_renderer.h`)

Streaming engine transitions are internal event names, not canonical reason
codes or Prometheus label values. The logger emits them in the bounded
`event=` field. See the boundary below.

The single source of truth for the reason code list is
`components/rust-converter/reason_registry.toml`. The Rust, C, diagnostics, and
the generator consume that registry. The generator creates release projections
and mirrors them in [Observability Schema v2](../architecture/observability-schema-v2.md).
This document describes the check order, what each check evaluates, and how the
module determines outcomes.
Rollout procedures are in the [Rollout Cookbook](../guides/ROLLOUT_COOKBOOK.md).
Rollback procedures are in the [Rollback Guide](../guides/ROLLBACK_GUIDE.md).

## Decision Chain Flowchart

```mermaid
flowchart TD
    A["Request enters module"] --> B{"markdown_filter<br/>enabled?"}
    B -->|No| C["disabled"]
    B -->|Yes| D{"Method<br/>GET/HEAD?"}
    D -->|No| E["not_eligible"]
    D -->|Yes| F{"Status<br/>200?"}
    F -->|206| G2["not_eligible<br/>(range)"]
    F -->|Other non-200| G["not_eligible"]
    F -->|Yes| H{"Range<br/>request?"}
    H -->|Yes| I["not_eligible"]
    H -->|No| J{"Content-Type<br/>text/html?"}
    J -->|No| K["not_eligible"]
    J -->|Yes| L{"Size within<br/>budget?"}
    L -->|No| M["memory_budget_exceeded"]
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

> **Note on eligibility granularity.** Checks 2 through 5 and check 7
> (method, status, range, content-type, auth) no longer produce distinct reason
> codes. The module maps them to a single canonical code `not_eligible`, because
> the request state is the same — *not eligible for conversion* — regardless of
> which specific check short-circuited. The individual failing check is still
> visible in the decision log's structured metadata (`method`, `content_type`,
> `status`) for diagnostics, but the reason code string is `not_eligible`.
> The size check (check 6) is the exception: an over-limit response reports the
> dedicated `memory_budget_exceeded` classification, as [Parser Budget](PARSER_BUDGET.md) documents.

## Check Order

The decision chain evaluates checks in a fixed order. The first check that fails stops evaluation and assigns the corresponding reason code. This "short-circuit" behavior means a request that fails multiple checks gets the reason code of the earliest failing check.

| Order | Check | What It Evaluates | Reason Code on Failure |
|-------|-------|--------------------|------------------------|
| 1 | Scope enablement | Is `markdown_filter` enabled (`on`, `1`, `true`, `yes`, or a variable that resolves to a truthy value) for this request's location/server/http context? | `disabled` |
| 2 | HTTP method | Is the request method `GET` or `HEAD`? Other methods (POST, PUT, DELETE, etc.) are not eligible. | `not_eligible` |
| 3 | Response status | Is the upstream response status `200 OK`? A `206 Partial Content` status is classified as a range request (same reason code as check 4). Other non-200 responses (redirects, errors, etc.) are not eligible. | `not_eligible` |
| 4 | Range request | Is this a range request (`Range` header present)? Range requests are not eligible because partial content cannot be converted. | `not_eligible` |
| 5 | Content-Type | Is the upstream `Content-Type` header `text/html` (with any charset parameter)? Non-HTML content types are not eligible. | `not_eligible` |
| 6 | Response size | Is the response body size within the configured `markdown_limits conversion_memory=` budget? This is a hard cumulative input-size cap applied to both buffered and streaming paths. The cap gates eligibility and blocks conversion before the FFI attempt. Oversized input is never truncated. | `memory_budget_exceeded` |
| 7 | Auth policy | Is the request authenticated and `markdown_auth_policy` set to `deny`? Authenticated requests are detected through the existing `Authorization` header and auth-cookie checks. | `not_eligible` |
| 8 | Accept negotiation | Does the `Accept` header indicate the client wants Markdown? Evaluated per `markdown_accept` (`strict` | `wildcard` | `force`). | `skipped_accept_reject` / `skipped_no_accept` / `skipped_accept` (see below) |
| 9 | Conversion attempt | All checks passed. The module attempts HTML-to-Markdown conversion. | _(see outcome determination below)_ |

### Accept negotiation outcomes

Checks 2–5 and 7 collapse to the canonical `not_eligible` reason when they
reject a request, and the size check (6) reports `memory_budget_exceeded`.
Accept negotiation is the other exception: when the request is otherwise
eligible but the `Accept` header does not resolve in favor of Markdown, the module emits one of three distinct skip
reason codes (this is the one eligibility branch that preserves sub-case
granularity, because the failure cause is operationally meaningful for content
negotiation):

| Condition | Reason Code |
|-----------|-------------|
| `Accept` explicitly rejects Markdown (`text/markdown;q=0` or a wildcard with `q=0`) | `skipped_accept_reject` |
| No `Accept` header present and `markdown_accept` is `strict` | `skipped_no_accept` |
| `Accept` present but does not request Markdown (and not the reject case above) | `skipped_accept` |

When `markdown_accept` is `wildcard`, `text/*` and `*/*` also qualify for conversion.
When `markdown_accept` is `force`, the module attempts conversion regardless of the `Accept` header.

## First-Failing-Check Rule

The module evaluates checks 1 through 9 in the order listed above. As soon as one check fails, the module assigns the corresponding reason code and stops. No subsequent checks run.

For example, if a `POST` request arrives for a path where `markdown_filter` is `on`, the module assigns `not_eligible` (check 2). It skips the status, content-type, size, auth, and Accept checks.

This behavior matters for operators diagnosing why the module skipped a request. The reason code always points to the first condition that prevented conversion. It does not list all conditions that would have prevented it.

## Outcome Determination

When all eligibility checks pass (checks 1–9), the module attempts conversion. The outcome depends on whether conversion succeeds and, if it fails, on the `markdown_error_policy` configuration:

### Success: converted

Conversion succeeded. The client receives the Markdown representation of the HTML response. The reason code is `converted` and the request state becomes CONVERTED.

### Failure with `markdown_error_policy pass`: failed_open

The module attempted conversion but failed before it committed the response
(HTML parse error, timeout, resource limit, decompression error, or internal/
system error). Because `markdown_error_policy` is set to `pass` (the default),
the module replays the complete original HTML response unchanged. The reason
code is `failed_open` and the request state becomes FAILED.

This is the recommended configuration for production rollouts. Conversion
failures before commit do not break client responses.

If a streaming conversion fails after downstream filters have already accepted
headers or Markdown bytes, the original HTML is no longer available for replay
and the headers/body cannot be rewritten. The module records the
`streaming_mid_flight_error` sub-classification and completes through its
protocol-safe finish or abort path. The client may receive a truncated Markdown
response. This post-commit case is intentionally not described as an
unchanged fail-open HTML response.

### Failure with `markdown_error_policy fail_closed`: failed_closed

The module attempted conversion but it failed. Because `markdown_error_policy` is set to `fail_closed`, the module returns the configured error status (`ngx_http_markdown_conf_t.error_status`), which defaults to `502 Bad Gateway` and operators may customize it. The reason code is `failed_closed` and the request state becomes FAILED.

Use `fail_closed` only when you need strict guarantees that clients never receive HTML when they requested Markdown. This is not recommended during initial rollout.

## Failure Sub-Classification

When conversion fails (either `failed_open` or `failed_closed`), the module records a failure sub-classification. It provides more detail about what went wrong. These appear as a separate `category=` field in decision log entries and as distinct `reason` label values on the `nginx_markdown_requests_total` metric. They do not change the primary outcome (`failed_open` or `failed_closed`), which depends solely on the `markdown_error_policy` setting.

| Failure Reason Code | Meaning |
|---------------------|---------|
| `conversion_error` | HTML parse or conversion error — the input HTML could not be processed |
| `memory_budget_exceeded` | Conversion-memory limit reached (`markdown_limits conversion_memory=`) |
| `timeout` | The request exceeded the authoritative overall conversion deadline `markdown_limits conversion_timeout=`; `parser_timeout=` triggers an earlier parser checkpoint when nonzero and smaller than `conversion_timeout`, while `conversion_timeout=` remains the overall upper bound and is never extended by `parser_timeout=` |
| `budget_exceeded` | Parser memory exceeded `markdown_limits parser_memory=`; this is distinct from `memory_budget_exceeded` and takes precedence for parser allocations |
| `ffi_panic` | Internal/system error (unexpected Rust↔C panic) |
| `decompression_error` / `decompression_budget_exceeded` / `decompression_format_error` / `decompression_truncated_input` / `decompression_io_error` | Decompression failures (see [Automatic Decompression](../features/AUTOMATIC_DECOMPRESSION.md)) |
| `replay_error` | Fail-open replay buffer init/append failure |
| `overload` | Inflight guard rejected the request |
| `invalid_dynconf` / `degraded_snapshot` / `header_plan_apply_error` | Dynamic configuration or header-plan errors |
| `streaming_mid_flight_error` | Streaming conversion mid-flight error |

## Request States

Every request that enters the decision chain ends up in one of four mutually exclusive states. The module derives the request state from the reason code. It stores no additional runtime field.

| Request State | Reason Codes | Meaning |
|---------------|-------------|---------|
| NOT_ENABLED | `disabled` | Module is disabled for this scope. The request was never evaluated for eligibility. |
| SKIPPED | `not_eligible`, `skipped_accept`, `skipped_no_accept`, `skipped_accept_reject`, `skipped_conditional`, `bypass_no_transform` | Module is enabled but the request did not pass one of the eligibility checks. |
| CONVERTED | `converted` | All checks passed and conversion succeeded. |
| FAILED | `failed_open`, `failed_closed` | All checks passed, conversion was attempted, but it did not succeed. |

Operators can determine request state counts from metrics and logs:
- NOT_ENABLED: count of `reason="disabled"` in decision log entries (`grep "reason=disabled" error.log`)
- SKIPPED: count of `reason="not_eligible"`, `reason="skipped_*"`, and
  `reason="bypass_no_transform"` in decision log entries
- CONVERTED: `nginx_markdown_requests_total{outcome="converted"}` metric (successful deliveries are additionally tracked by `nginx_markdown_conversion_deliveries_total`)
- FAILED: `nginx_markdown_requests_total{outcome="failed_open"}` (`failed_open`)
- FAILED: `nginx_markdown_requests_total{outcome="failed_closed"}` (`failed_closed`)

## Reason Code Reference

The registry declares the complete set of 27 reason codes in
`components/rust-converter/reason_registry.toml`. The generator projects it
into `reason_code.rs`, C metadata, diagnostics lookup, and release artifacts.
The projections mirror [Observability Schema v2](../architecture/observability-schema-v2.md).
All `as_str()` values are lowercase snake_case. The table below maps the
high-level decision outcomes described in this document to their reason codes.
The full registry (including decompression, dynconf, and canonical streaming
outcome codes) lives in the schema document. Streaming implementation events
are not registry entries.

| Decision Outcome | Reason Code | Request State | Description |
|---|---|---|---|
| Module disabled | `disabled` | NOT_ENABLED | Module disabled by configuration for this scope |
| Not eligible (method/status/range/content-type/auth) | `not_eligible` | SKIPPED | Response not eligible for conversion |
| Size gate blocked (`markdown_limits conversion_memory=` exceeded) | `memory_budget_exceeded` | FAILED | Hard cumulative input-size cap blocks conversion before the FFI attempt. The input is never truncated, and the primary outcome follows `markdown_error_policy` |
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
> The 0.9.0 observability schema consolidated these: eligibility checks
> 2–5 and 7 emit `not_eligible`, the size gate (6) emits
> `memory_budget_exceeded`, scope-off emits `disabled`, and the conversion
> outcomes are `converted` / `failed_open` / `failed_closed`. If you are
> correlating old dashboards or alerts, update them to the lowercase codes above.

### v0.9.0 Additional Reason Codes and Behavior

| Reason Code / Behavior | Description |
|------------------------|-------------|
| `replay_error` | Fail-open replay buffer init or append failure; sets `precommit_error` flag (prevents duplicate finalize calls) |
| `decompression_budget_exceeded` | Decompression budget (`markdown_limits decompressed_size=`) exceeded; classified as a decompression error |
| `decompression_format_error` | Compressed input has invalid format (not valid gzip/deflate/brotli) |
| `decompression_truncated_input` | Compressed input was truncated (incomplete stream) |
| `decompression_io_error` | I/O error during decompression operation |
| `timeout` | Conversion exceeded the authoritative `markdown_limits conversion_timeout=` overall deadline; `parser_timeout=` may trigger an earlier checkpoint during the parse phase |
| `budget_exceeded` | Parser memory exceeded `markdown_limits parser_memory=` (default 32m) |
| `overload` | Inflight guard rejected the request |
| `invalid_dynconf` / `degraded_snapshot` | Dynamic configuration error / degraded snapshot |
| `header_plan_apply_error` | Header plan apply error |
| `streaming_mid_flight_error` | Streaming conversion mid-flight error |
| Delivery vs Decision counter separation | `failopen_count` (delivery) increments only after downstream `NGX_OK`; decision counter increments on decision regardless of downstream status |

`failopen_completed` is an internal request-lifetime control flag, not a
public reason code. It prevents duplicate `ngx_http_finalize_request` calls
when the module resumes or re-enters a fail-open path.

All canonical reason codes use lowercase snake_case format. The same strings
appear in both decision log entries and Prometheus metrics labels, so operators
can correlate log entries with metric counters without translation. Internal
streaming events are outside this operator-visible contract and use `event=`.

## Implementation Details

The check order matches the eligibility evaluation in `components/nginx-module/src/ngx_http_markdown_eligibility.c`, with the header-filter and Accept-negotiation additions:

- The module evaluates scope enablement (check 1) before it calls `ngx_http_markdown_check_eligibility()`.
- The module evaluates auth policy (check 7) as part of eligibility.
- The module evaluates Accept negotiation (check 8) after the core eligibility checks pass.

The generated Rust `ReasonCode::as_str()` projection produces the reason code
strings. The `markdown_reason_code_str()` FFI accessor surfaces them to C. C-side
canonical reason data comes from generated discriminant and metadata macros.
The accessor converts each discriminant into the canonical lowercase string.
Streaming transitions remain a separate bounded event surface. See
[Observability Schema v2](../architecture/observability-schema-v2.md)
for the full registry and FFI accessor list.

## Related Documentation

- [Rollout Cookbook](../guides/ROLLOUT_COOKBOOK.md) — staged rollout procedures with observation checkpoints
- [Rollback Guide](../guides/ROLLBACK_GUIDE.md) — how to disable or narrow conversion scope
- [Configuration Guide](../guides/CONFIGURATION.md) — directive reference and configuration examples
- [Content Negotiation](CONTENT_NEGOTIATION.md) — Accept header parsing and wildcard behavior
- [Observability Schema v2](../architecture/observability-schema-v2.md) — authoritative reason code registry, metric families, label whitelist
- [Operations Guide](../guides/OPERATIONS.md) — monitoring and troubleshooting

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.0 | 2026-07-09 | Kang | **Synchronized with observability-schema-v1 (single source of truth).** All reason codes switched to lowercase snake_case; eligibility checks 2–7 collapsed to `not_eligible`; scope-off → `disabled`; conversion outcomes → `converted`/`failed_open`/`failed_closed`; removed legacy `NGX_HTTP_MARKDOWN_INELIGIBLE_*` enum column; Accept negotiation split into `skipped_accept`/`skipped_no_accept`/`skipped_accept_reject` |
| 0.7.0 | 2026-05-17 | Kang | Added v0.7.0 reason codes (REPLAY_BUFFER_ERROR, DECOMPRESSION_BUDGET_EXCEEDED, PARSE_TIMEOUT, PARSE_BUDGET_EXCEEDED, SKIPPED_NO_ACCEPT, SKIPPED_CONDITIONAL) and delivery/decision counter separation |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
