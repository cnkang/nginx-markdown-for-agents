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

## Core Enablement and Negotiation

### `markdown_filter`

| Aspect | Detail |
|--------|--------|
| Behavior | Enables or disables Markdown conversion for the current context; can be static or variable-driven |
| Lifecycle impact | Header filter entry decision; cached per request before body processing begins |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_request_impl.h`, `components/nginx-module/src/ngx_http_markdown_eligibility.c` |
| Practical note | This is the top-level switch. If it resolves to off in header phase, the body filter will not revisit that decision. |

### `markdown_on_wildcard`

| Aspect | Detail |
|--------|--------|
| Behavior | Extends content negotiation so wildcard `Accept` values can trigger Markdown conversion |
| Lifecycle impact | Header-phase negotiation decision before eligibility checks |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_accept.c`, `components/nginx-module/src/ngx_http_markdown_request_impl.h` |
| Practical note | Use this only when wildcard clients should really receive Markdown; it broadens the set of requests entering the conversion path. |

## Resource and Failure Controls

### `markdown_max_size`

| Aspect | Detail |
|--------|--------|
| Behavior | Caps the size of responses the module will buffer and attempt to convert |
| Lifecycle impact | Body-phase buffering and fail-open/fail-closed branch |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_buffer.c`, `components/nginx-module/src/ngx_http_markdown_payload_impl.h` |
| Practical note | This is one of the main guards around the full-buffering architecture. Large documents often hit this branch first. |

### `markdown_timeout`

| Aspect | Detail |
|--------|--------|
| Behavior | Limits how long conversion may run in the Rust engine |
| Lifecycle impact | FFI conversion call and conversion failure classification |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_conversion_impl.h`, `components/rust-converter/src/ffi.rs`, `components/rust-converter/src/converter.rs` |
| Practical note | A timeout is not a header-phase decision. It takes effect only after the request has already entered the conversion path. |

### `markdown_on_error`

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

### `markdown_etag`

| Aspect | Detail |
|--------|--------|
| Behavior | Enables or disables Markdown-variant ETag generation |
| Lifecycle impact | Rust conversion options and success-path header updates |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_conversion_impl.h`, `components/nginx-module/src/ngx_http_markdown_headers.c`, `components/rust-converter/src/etag_generator.rs` |
| Practical note | This affects downstream cache behavior, not whether conversion is attempted. |

### `markdown_conditional_requests`

| Aspect | Detail |
|--------|--------|
| Behavior | Controls how aggressively the module handles Markdown-variant conditional requests |
| Lifecycle impact | Conditional resolution branch after buffering and before full conversion |
| Implementation areas | `components/nginx-module/src/ngx_http_markdown_conversion_impl.h`, `components/nginx-module/src/ngx_http_markdown_conditional.c` |
| Practical note | This directive changes the branch between “send 304”, “reuse a conditional result”, and “continue to full conversion”. |

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

## Cross-Cutting Behavior Not Controlled by a Public Directive

Some important runtime behavior is part of the architecture even though it is not currently exposed as a top-level directive.

Examples include:

- the small C/Rust FFI boundary
- automatic decompression support as implemented in the module
- base URL construction for link resolution
- metrics counter collection inside the worker process

These are best understood through:

- [SYSTEM_ARCHITECTURE.md](SYSTEM_ARCHITECTURE.md)
- [REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md)
- [ADR/0001-use-rust-for-conversion.md](ADR/0001-use-rust-for-conversion.md)
- [ADR/0002-full-buffering-approach.md](ADR/0002-full-buffering-approach.md)

## Practical Use Cases

### “Why did this request stay as HTML?”

Start with directives that affect entry and bypass:

- `markdown_filter`
- `markdown_on_wildcard`
- `markdown_auth_policy`
- `markdown_stream_types`
- `markdown_buffer_chunked`

Then move to [REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md) and trace header-phase eligibility.

### “Why did this request fail open?”

Start with:

- `markdown_on_error`
- `markdown_max_size`
- `markdown_timeout`
- `markdown_conditional_requests`

Then inspect the failure branches in [REQUEST_LIFECYCLE.md](REQUEST_LIFECYCLE.md).

### “Which directives affect the Rust converter call?”

Mostly:

- `markdown_flavor`
- `markdown_timeout`
- `markdown_token_estimate`
- `markdown_front_matter`
- `markdown_etag`

Those are the knobs most directly reflected in the conversion options passed through FFI.
