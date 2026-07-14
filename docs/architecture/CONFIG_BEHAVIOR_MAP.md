# Configuration to Behavior Map

This document maps the public NGINX directives to the runtime behavior they control.

Use it when you already know the directive names, but need to answer questions such as:

- which phase does this directive affect
- which branch of the request lifecycle does it change
- which part of the implementation should I inspect

For the full request flow, read [REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md). For operator-facing syntax and examples, read [../guides/CONFIGURATION.md](../guides/CONFIGURATION.md).

## How to Read This Map

Each directive is described in four dimensions:

- behavior: what it changes from the user's point of view
- lifecycle impact: which phase or branch it affects
- implementation areas: where that behavior primarily lives in code
- practical note: how to think about it during rollout or debugging

```mermaid
flowchart LR
    subgraph HeaderPhase["Header Filter Phase"]
        MF["markdown_filter"]
        MW["markdown_accept"]
        AP["markdown_auth_policy"]
        AC["markdown_auth_cookies"]
        CV["markdown_cache_validation"]
        LV["markdown_log_verbosity"]
        SE["markdown_streaming_engine"]
        PR["markdown_profile"]
        AD["markdown_auto_decompress"]
    end

    subgraph BodyPhase["Body Filter Phase"]
        ML["markdown_limits"]
        OE["markdown_error_policy"]
        FL["markdown_flavor"]
        BC["markdown_buffer_chunked"]
        ST["markdown_stream_types"]
        TF["markdown_trusted_proxies"]
        SS["markdown_streaming_shadow"]
        ZC["markdown_streaming_zero_copy"]
    end

    subgraph Metrics["Metrics"]
        MM["markdown_metrics"]
        MFmt["markdown_metrics_format"]
        MSHM["markdown_metrics_shm_size"]
    end

    HeaderPhase --> BodyPhase --> Metrics
```


## Core Enablement and Negotiation

### `markdown_filter`

| Aspect | Detail |
|--------|--------|
| Behavior | Enables or disables Markdown conversion for the current context; can be static or variable-driven |
| Lifecycle impact | Header filter entry decision; cached per request before body processing begins |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_request_impl.h`, `components/nginx-module/src/ngx_http_markdown_eligibility.c` |
| Practical note | This is the top-level switch. If it resolves to off in header phase, the body filter will not revisit that decision. Because `markdown_filter` accepts NGINX variables, operators can combine it with `map` directives to implement User-Agent-based bot targeting — for example, rewriting the Accept header for known AI crawlers so they receive Markdown automatically. See the bot-targeted conversion examples in [../guides/DEPLOYMENT_EXAMPLES.md](../guides/DEPLOYMENT_EXAMPLES.md#bot-targeted-conversion-user-agent-based). |

### `markdown_accept`

| Aspect | Detail |
|--------|--------|
| Behavior | Extends content negotiation so wildcard `Accept` values can trigger Markdown conversion |
| Lifecycle impact | Header-phase negotiation decision before eligibility checks |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_accept.c`, `components/nginx-module/src/ngx_http_markdown_request_impl.h` |
| Practical note | Use this only when wildcard clients should really receive Markdown; it broadens the set of requests entering the conversion path. |

## Resource and Failure Controls

### `markdown_limits`

| Aspect | Detail |
|--------|--------|
| Behavior | Unified resource limits block: `memory=<size>`, `timeout=<time>`, `streaming_buffer=<size>`, `max_inflight=<N>` (Config V2, 0.9.0) |
| Lifecycle impact | Body-phase buffering budget, conversion timeout, streaming memory, and inflight guard |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_buffer.c`, `components/nginx-module/src/ngx_http_markdown_config_handlers_impl.h` |
| Practical note | Consolidates the removed `markdown_max_size`, `markdown_memory_budget`, `markdown_timeout`, and `markdown_streaming_budget` directives. Any subset of keys may be given; unspecified keys inherit. |

### `markdown_error_policy`

| Aspect | Detail |
|--------|--------|
| Behavior | Chooses fail-open (`pass`) or fail-closed (`reject`) behavior when conversion or related processing fails |
| Lifecycle impact | Buffering failure, decompression failure, conditional-processing failure, and conversion failure branches |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_payload_impl.h`, `components/nginx-module/src/ngx_http_markdown_conversion_impl.h`, `components/nginx-module/src/ngx_http_markdown_error.c` |
| Practical note | This directive shapes the operational posture of the system more than the conversion result itself. |

## Output Shape and Metadata

### `markdown_flavor`

| Aspect | Detail |
|--------|--------|
| Behavior | Selects the Markdown flavor emitted by the Rust converter |
| Lifecycle impact | Rust conversion options preparation before FFI call |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_conversion_impl.h`, `components/rust-converter/src/converter.rs` |
| Practical note | This changes output semantics, not request eligibility. |

### `markdown_token_estimate`

| Aspect | Detail |
|--------|--------|
| Behavior | Enables token estimation and the related response metadata |
| Lifecycle impact | Rust conversion options and successful-response header update |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_conversion_impl.h`, `components/nginx-module/src/ngx_http_markdown_headers.c`, `components/rust-converter/src/token_estimator.rs` |
| Practical note | Useful for agent-facing consumers, but it adds work to the successful conversion path. |

### `markdown_front_matter`

| Aspect | Detail |
|--------|--------|
| Behavior | Prepends YAML front matter to the generated Markdown |
| Lifecycle impact | Rust conversion options and output rendering path |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_conversion_impl.h`, `components/rust-converter/src/metadata.rs`, `components/rust-converter/src/converter.rs` |
| Practical note | This changes output shape for downstream consumers and may affect caches or clients that expect plain Markdown only. |

## Authentication and Cache Policy

### `markdown_auth_policy`

| Aspect | Detail |
|--------|--------|
| Behavior | Allows or denies conversion for authenticated requests |
| Lifecycle impact | Eligibility branch in header phase and cache-policy adjustment on successful output |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_auth.c`, `components/nginx-module/src/ngx_http_markdown_eligibility.c`, `components/nginx-module/src/ngx_http_markdown_headers.c` |
| Practical note | This directive affects both whether conversion happens and how cache headers are rewritten if it does. |

### `markdown_auth_cookies`

| Aspect | Detail |
|--------|--------|
| Behavior | Defines which cookie names count as authentication signals |
| Lifecycle impact | Auth detection during eligibility checks and authenticated-response handling |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_auth.c` |
| Practical note | If authenticated traffic is unexpectedly converted or bypassed, inspect this mapping first. |

## Cache and Conditional Request Behavior

### `markdown_cache_validation`

| Aspect | Detail |
|--------|--------|
| Behavior | Controls conditional request handling and ETag generation: `off`, `ims_only`, or `full` (Config V2, 0.9.0) |
| Lifecycle impact | Conditional resolution branch after buffering and before full conversion; ETag generation on success path |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_conversion_impl.h`, `components/nginx-module/src/ngx_http_markdown_conditional.c`, `components/nginx-module/src/ngx_http_markdown_headers.c` |
| Practical note | Replaces the removed `markdown_etag` and `markdown_conditional_requests` directives. `full` generates a transformed ETag; `ims_only` supports If-Modified-Since via upstream Last-Modified; `off` disables both. |

## Logging and Observability

### `markdown_log_verbosity`

| Aspect | Detail |
|--------|--------|
| Behavior | Sets the module-local threshold for emitted module logs |
| Lifecycle impact | Logging across header filter, body filter, decompression, conditional handling, conversion, and metrics paths |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_config_handlers_impl.h`, `components/nginx-module/src/ngx_http_markdown_config_core_impl.h`, `components/nginx-module/src/ngx_http_markdown_request_impl.h`, `components/nginx-module/src/ngx_http_markdown_payload_impl.h`, `components/nginx-module/src/ngx_http_markdown_conversion_impl.h`, `components/nginx-module/src/ngx_http_markdown_metrics_impl.h` |
| Practical note | This does not change runtime behavior directly, but it changes how visible each branch becomes during debugging. |

### `markdown_metrics`

| Aspect | Detail |
|--------|--------|
| Behavior | Enables a dedicated metrics endpoint at a location |
| Lifecycle impact | Separate location-handler path, not the normal conversion filter chain |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_config_handlers_impl.h`, `components/nginx-module/src/ngx_http_markdown_config_directives_impl.h`, `components/nginx-module/src/ngx_http_markdown_metrics_impl.h` |
| Practical note | When debugging this directive, think “handler mode”, not “header/body filter mode”. |

## Transfer and Streaming-Oriented Controls

### `markdown_buffer_chunked`

| Aspect | Detail |
|--------|--------|
| Behavior | Controls whether chunked transfer responses may be buffered and converted |
| Lifecycle impact | Eligibility and body-buffering path for chunked upstream responses |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_eligibility.c`, `components/nginx-module/src/ngx_http_markdown_request_impl.h`, `components/nginx-module/src/ngx_http_markdown_payload_impl.h` |
| Practical note | This directive does not provide streaming conversion; it only controls whether chunked responses may still enter the full-buffering path. |

### `markdown_stream_types`

| Aspect | Detail |
|--------|--------|
| Behavior | Excludes configured content types from conversion, typically for streaming-style responses |
| Lifecycle impact | Header-phase eligibility branch |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_request_impl.h`, `components/nginx-module/src/ngx_http_markdown_eligibility.c` |
| Practical note | Treat this as an explicit bypass list for response shapes that do not fit the buffering model well. |

### `markdown_profile`

| Aspect | Detail |
|--------|--------|
| Behavior | Applies a named preset of defaults (`balanced`, `strict_cache`, `streaming_first`) to simplify configuration |
| Lifecycle impact | Config merge phase; overrides built-in defaults before explicit directives are applied |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_config_core_impl.h`, Rust `Profile` enum |
| Practical note | Use this to quickly align with a known deployment pattern. Explicit directives still override profile defaults. |

### `markdown_auto_decompress`

| Aspect | Detail |
|--------|--------|
| Behavior | Automatically decompresses upstream responses (gzip, deflate, br) before conversion |
| Lifecycle impact | Header filter detection; body filter decompression flow |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_decompression.c` |
| Practical note | Default is `on`. If `off`, compressed responses are passed through unchanged. |

### `markdown_streaming_zero_copy`

| Aspect | Detail |
|--------|--------|
| Behavior | Enables zero-copy output path for streaming conversion to reduce internal copying |
| Lifecycle impact | Streaming body filter output path |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_zerocopy_buf.h` |
| Practical note | Opt-in feature (default `off`). Use in high-throughput environments to reduce CPU/memory overhead. |

## Practical Use Cases

### “Why did this request stay as HTML?”

Start with directives that affect entry and bypass:

- `markdown_filter`
- `markdown_accept`
- `markdown_auth_policy`
- `markdown_stream_types`
- `markdown_buffer_chunked`

Then move to [REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md) and trace header-phase eligibility.

### “Why did this request fail open?”

Start with:

- `markdown_error_policy`
- `markdown_limits`
- `markdown_cache_validation`

Then inspect the failure branches in [REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md).

### “Which directives affect the Rust converter call?”

Mostly:

- `markdown_flavor`
- `markdown_limits`
- `markdown_token_estimate`
- `markdown_front_matter`
- `markdown_cache_validation`

Those are the knobs most directly reflected in the conversion options passed through FFI.


## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
