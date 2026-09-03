# Request Lifecycle (0.9.2)

This document describes the request path at the frozen release-contract boundary.
The important invariant is that eligibility, engine selection, streaming
backpressure, and terminal metrics describe one request. Backpressure may
suspend that same request multiple times, but every event remains associated
with it. The module records the terminal outcome and attempt metric exactly
once.

## Lifecycle

```text
request
  -> preaccess: capture and suppress Markdown conditional validators
  -> content handler, proxy, or cache obtains the source response
  -> header filter: method/status/type/Accept/cache gates
  -> bind one effective dynconf snapshot
  -> select passthrough, full-buffer, or streaming
  -> body filter: bounded input and conversion
  -> commit headers before converted body
  -> deliver with NGX_OK / NGX_AGAIN / NGX_DONE semantics
  -> one terminal outcome and request cleanup
```

## Header filter

The preaccess handler runs after access checks for GET and HEAD requests that
negotiate Markdown. It captures `If-None-Match` and `If-Modified-Since` from
the client and temporarily removes them from the active request-header list.
This prevents a static handler, proxy, or proxy cache from satisfying a source
HTML validator before the module can obtain the source body. The header filter
later evaluates the captured values against the Markdown representation. If
the request is not converted, or a fail-open path forwards the source bytes,
the module restores the captured headers before source delivery.

The header filter rejects ineligible methods, statuses, ranges, content types,
authentication cases, and `Accept` values before selecting a conversion path.
It also captures the active dynamic-configuration snapshot once and builds the
request's effective view. Later timer reloads cannot change that request's
policy midway through processing.

The effective view includes `enabled` (the `filter` field), `prune_noise`,
`log_verbosity`, `error_policy`, and `streaming_buffer` as runtime-overridable
fields. `conversion_memory`, `parser_memory`, `decompressed_size`,
`decompression_ratio`, `conversion_timeout`, `parser_timeout`, and
`max_inflight` remain static NGINX configuration constraints owned by the
configuration lifecycle.

## Engine selection

`markdown_streaming off` selects bounded full-buffer conversion. `auto` uses a
bounded internal response-shape heuristic. It does not expose a threshold
directive. `force` requests streaming after the hard eligibility gates pass.

User-configured streaming exclusions (`markdown_stream_excluded_types`)
select full-buffer or passthrough, as do built-in hard exclusions (full cache
validation for conditional requests, and streaming decoders absent from the
build). A known codec whose
streaming decoder is unavailable in the build (for example Brotli without
`NGX_HTTP_BROTLI`) uses bounded full-buffer decompression. A malformed,
unknown, or excessively deep `Content-Encoding` chain follows the configured
`markdown_error_policy` (`pass` forwards the original response, `fail_closed`
and `status <code>` reject it).
Combining `markdown_cache_validation full` with `markdown_streaming force`
fails during `nginx -t` with a configuration error, since streaming cannot
guarantee cache-validation semantics. The module latches the selected engine
before the first conversion attempt.
The attempt metric increments at most once per request.

## Body filter and decompression

The body filter retains input ownership across downstream `NGX_AGAIN` and
feeds the selected conversion path without unbounded growth. Compressed
responses use the decoder selected by `markdown_auto_decompress` and the
encoding. `markdown_limits` configures the decompression limits:

```nginx
# Example values; the defaults are conversion_memory=64m,
# conversion_timeout=30s, parser_memory=32m, parser_timeout=10s,
# streaming_buffer=2m, decompressed_size=10m.
markdown_limits conversion_memory=64m conversion_timeout=30s
    parser_memory=32m parser_timeout=10s streaming_buffer=2m
    decompressed_size=10m decompression_ratio=100 max_inflight=64;
```

Decoder accounting is response-wide. Gzip member completion does not reset
the decompressed-size or expansion-ratio budgets, and a truncated final
member is a failure.

## Commit and delivery

The module commits headers before the first converted body buffer. A pre-commit
failure can use the configured fail-open policy and replay the original
buffered response only when the replay buffer contains every upstream byte
read so far. If any upstream bytes have escaped that buffer, the module must
fail closed or take the configured non-replay fallback. After commit, the
module cannot replay the original body.
It enters safe-finish or abort handling.

Downstream return codes have strict meanings:

- `NGX_OK`: the module accepted the submitted chain
- `NGX_AGAIN`: suspend and retain the correct pending-chain owner
- `NGX_DONE`: terminal for this filter operation. Return immediately after
  finalization. NGINX may still retain a subrequest until its request-pool
  cleanup runs.
- `NGX_ERROR`: terminal failure path

Delivery counters advance only after successful terminal delivery. A request
produces one terminal request outcome. `NGX_DONE` does not cause a second
delivery count, and the inflight guard remains held until the request-pool
cleanup handler releases it. Streaming transitions and decompression events
record at their own bounded event points.

## Verification surfaces

Use diagnostics JSON to inspect the effective configuration and provenance.
Use the Prometheus endpoint with `Accept: text/plain; version=0.0.4` to inspect
the eleven frozen metric families. The schema, renderer, reason-code,
and conservation gates are the authoritative compatibility checks.
