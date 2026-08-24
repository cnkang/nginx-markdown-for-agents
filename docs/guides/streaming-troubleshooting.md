# Streaming Troubleshooting Guide

This guide is for the frozen 0.9.2 streaming surface. Diagnose the selected
engine from the diagnostics endpoint and use Prometheus metrics for counters.
The module no longer exposes operator-facing threshold, profile, or zero-copy
switches.

Related docs:

- [Streaming Rollout Cookbook](streaming-rollout-cookbook.md)
- [Configuration Reference](CONFIGURATION.md)
- [Prometheus Metrics Guide](prometheus-metrics.md)

## First response

Capture the active configuration and the frozen metric families before making
changes:

```bash
nginx -T 2>/dev/null | grep -E 'markdown_(streaming|auto_decompress|cache_validation|limits|stream_excluded_types)'
curl -s -H 'Accept: application/json' \
  http://localhost/nginx-markdown/diagnostics | python3 -m json.tool
curl -s -H 'Accept: text/plain; version=0.0.4' \
  http://localhost/markdown-metrics
```

The diagnostics response is the source for the effective request policy. The
Prometheus endpoint exposes exactly these relevant families:

- `nginx_markdown_conversion_attempts_total{engine=...}`
- `nginx_markdown_conversion_deliveries_total{engine=...}`
- `nginx_markdown_streaming_events_total{transition=...}`
- `nginx_markdown_decompression_events_total{encoding=...,outcome=...,reason=...}`
- `nginx_markdown_requests_total{outcome=...,stage=...,reason=...}`

## Response uses full-buffer

Check the effective `markdown_streaming` policy first. The diagnostics
response for the target path reports the effective policy and the
selection reason. Prefer it over grepping `nginx -T`, which shows the
configured value, not the per-request decision:

```bash
curl -s http://127.0.0.1/nginx-markdown/diagnostics | jq '.streaming_config'
```

- `off` intentionally selects full-buffer.
- `auto` uses the module's internal bounded eligibility heuristic.
- `force` requests streaming, but does not override a hard incompatibility.

The following conditions still select full-buffer or passthrough:

- `markdown_cache_validation full` requires complete output before headers.
- `markdown_auto_decompress off` leaves compressed responses unchanged.
- An unsupported `Content-Encoding` is not converted.
- A response content type listed by `markdown_stream_excluded_types` gets
  excluded.
- A build without the Brotli streaming feature uses the bounded full-buffer
  Brotli path.

Inspect the `streaming` and `configuration.effective` objects in diagnostics,
then compare attempts by engine:

```bash
curl -s -H 'Accept: text/plain; version=0.0.4' \
  http://localhost/markdown-metrics | \
  grep -E 'conversion_attempts_total|streaming_events_total'
```

Do not add a threshold directive: the size heuristic is intentionally an
internal implementation detail of the frozen contract.

## Compressed response is not streaming

Verify the upstream encoding and the decompression policy:

```bash
curl -sD - -o /dev/null -H 'Accept: text/markdown' http://localhost/target-path | grep -iE 'content-(encoding|type|length)'
nginx -T 2>/dev/null | grep -E 'markdown_(auto_decompress|streaming|cache_validation)'
```

For a supported codec, the public decompression family distinguishes success
and failure without relying on private debug counters:

```bash
curl -s -H 'Accept: text/plain; version=0.0.4' \
  http://localhost/markdown-metrics | \
  grep nginx_markdown_decompression_events_total
```

For Brotli, confirm that the build contains the Brotli streaming feature. A
feature-disabled build uses bounded full-buffer decompression. It does not fail
the request solely because streaming is unavailable.

## Pre-commit fallback

Streaming can fall back before the module commits headers when the bounded replay
or parser pre-commit work cannot complete. The behavior depends on
`markdown_error_policy`:

- With `pass` (default), this is safe fail-open behavior: the fallback path
  preserves and returns the original response content. The pre-commit
  fallback returns the original HTML without modification.
- With `fail_closed`, the module does not unconditionally fail open. It
  follows its configured failure behavior: the module rejects the request
  with the configured error status instead of replaying the original body.

The request-level metric records the terminal outcome.

Inspect these transitions:

```bash
curl -s -H 'Accept: text/plain; version=0.0.4' \
  http://localhost/markdown-metrics | \
  grep 'streaming_events_total.*transition="fallback"'
```

If fallback is frequent, first review `markdown_limits conversion_memory=...`,
`parser_memory=...`, `parser_timeout=...`, and `streaming_buffer=...`. Keep
the values bounded and remember that `streaming_buffer` is a total working-set
and pre-commit replay budget, not a network chunk size. Change one setting at
a time. Also inspect the
`requests_total` reason labels to distinguish resource limits from malformed
input or policy bypass.

## Post-commit failure or shortened output

After the module commits converted headers, it cannot replay the original
body. A safe-finish can complete a converted response when earlier compressed
members produced valid Markdown. Check safe-finish and abort transitions:

```bash
curl -s -H 'Accept: text/plain; version=0.0.4' \
  http://localhost/markdown-metrics | \
  grep -E 'streaming_events_total.*(safe_finish_start|abort_start|resume_failure)'
```

Any sustained `abort_start` or `resume_failure` increase is a rollback
trigger. Set `markdown_streaming off` and reload while collecting the request
URI, content encoding, and reason labels from logs. Do not infer success from
`NGX_AGAIN`. Delivery counts only after downstream accepts the terminal
converted buffer.

## Decompression limits

You configure the active limits as key/value entries:

```nginx
markdown_limits decompressed_size=20m decompression_ratio=100
    conversion_memory=64m conversion_timeout=10s
    parser_memory=32m parser_timeout=5s streaming_buffer=2m
    max_inflight=64;
```

`decompressed_size` and `decompression_ratio` are cumulative across a gzip
response's members. A truncated final member, invalid framing, decoder error,
or I/O error records a decompression failure. A budget violation gets a
separate budget-limit failure classification. The module records the event in
`nginx_markdown_decompression_events_total` and follows the configured
`markdown_error_policy` before commit.

## Rollback checklist

1. Save diagnostics and Prometheus output (record a pre-reload baseline of
   `nginx_markdown_conversion_attempts_total` and
   `nginx_markdown_inflight_requests`).
2. Set `markdown_streaming off` (and, if needed,
   `markdown_auto_decompress off`).
3. Run `nginx -t && nginx -s reload`.
4. Wait for pre-reload requests to drain, then verify the **post-reload
   delta** of `conversion_attempts_total{engine="streaming"}` stops
   increasing: `conversion_attempts_total` is cumulative, so an absolute
   comparison against a pre-reload value is not meaningful while in-flight
   requests from before the reload are still converting. Compare the delta
   from your pre-reload baseline only after quiescence
   (`nginx_markdown_inflight_requests` has returned to zero and no pre-reload
   request remains in flight).
5. Re-enable with `auto` only after reviewing the reason labels and
   compressed-input evidence.
