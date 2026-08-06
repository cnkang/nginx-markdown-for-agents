# Request Lifecycle (0.9.2)

This document describes the request path at the frozen release-contract boundary.
The important invariant is that eligibility, engine selection, streaming
backpressure, and terminal metrics describe one request exactly once.

## Lifecycle

```text
request
  -> header filter: method/status/type/Accept/cache gates
  -> bind one effective dynconf snapshot
  -> select passthrough, full-buffer, or streaming
  -> body filter: bounded input and conversion
  -> commit headers before converted body
  -> deliver with NGX_OK / NGX_AGAIN / NGX_DONE semantics
  -> one terminal outcome and request cleanup
```

## Header filter

The header filter rejects ineligible methods, statuses, ranges, content types,
authentication cases, and `Accept` values before allocating conversion state.
It also captures the active dynamic-configuration snapshot once and builds the
request's effective view. Later timer reloads cannot change that request's
policy midway through processing.

The effective view includes `markdown_filter`, `prune_noise`,
`log_verbosity`, `error_policy`, and `streaming_buffer` as runtime-overridable
fields. Static-only limits and structural directives remain owned by the
NGINX configuration lifecycle.

## Engine selection

`markdown_streaming off` selects bounded full-buffer conversion. `auto` uses a
bounded internal response-shape heuristic; it does not expose a threshold
directive. `force` requests streaming after the hard eligibility gates pass.

Full cache validation, unsupported encodings, excluded content types, and
build-disabled streaming features can still select full-buffer or passthrough.
The selected engine is latched before the first conversion attempt, and the
attempt metric increments at most once per request.

## Body filter and decompression

The body filter retains input ownership across downstream `NGX_AGAIN` and
feeds the selected conversion path without unbounded growth. Compressed
responses use the decoder selected by `markdown_auto_decompress` and the
encoding. Decompression limits are configured under `markdown_limits`:

```nginx
markdown_limits conversion_memory=64m conversion_timeout=10s
    parser_memory=32m parser_timeout=5s streaming_buffer=2m
    decompressed_size=20m decompression_ratio=100 max_inflight=64;
```

Decoder accounting is response-wide. Gzip member completion does not reset
the decompressed-size or expansion-ratio budgets, and a truncated final
member is a failure.

## Commit and delivery

Headers are committed before the first converted body buffer. A pre-commit
failure can use the configured fail-open policy and replay the original
buffered response. After commit, the module cannot replay the original body;
it enters safe-finish or abort handling.

Downstream return codes have strict meanings:

- `NGX_OK`: the submitted chain was accepted;
- `NGX_AGAIN`: suspend and retain the correct pending-chain owner;
- `NGX_DONE`: terminal finalize path; return immediately after finalization;
- `NGX_ERROR`: terminal failure path.

Delivery counters advance only after successful terminal delivery. A request
produces one terminal request outcome, while streaming transitions and
decompression events are recorded at their own bounded event points.

## Verification surfaces

Use diagnostics JSON to inspect the effective configuration and provenance.
Use the Prometheus endpoint with `Accept: text/plain; version=0.0.4` to inspect
the twelve frozen metric families. The schema, renderer, reason-code,
and conservation gates are the authoritative compatibility checks.
