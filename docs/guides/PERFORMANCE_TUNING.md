# Performance Settings (0.9.2)

The 0.9.2 contract uses explicit settings instead of a profile abstraction.
This keeps the behavior visible in configuration and prevents a preset from
silently changing cache, decompression, or resource-limit semantics.

## Recommended presets

### General workload

```nginx
markdown_filter on;
markdown_streaming auto;
markdown_auto_decompress on;
markdown_cache_validation ims_only;
markdown_limits conversion_memory=64m conversion_timeout=10s
    parser_memory=32m parser_timeout=5s streaming_buffer=2m
    decompressed_size=20m decompression_ratio=100 max_inflight=64;
```

### Cache-sensitive workload

```nginx
markdown_filter on;
markdown_streaming off;
markdown_auto_decompress on;
markdown_cache_validation full;
markdown_limits conversion_memory=64m conversion_timeout=10s
    parser_memory=32m parser_timeout=5s decompressed_size=16m
    decompression_ratio=50 max_inflight=64;
```

### Large-document or agent workload

```nginx
markdown_filter on;
markdown_streaming force;
markdown_auto_decompress on;
markdown_cache_validation ims_only;
markdown_limits conversion_memory=128m conversion_timeout=30s
    parser_memory=64m parser_timeout=10s streaming_buffer=8m
    decompressed_size=64m decompression_ratio=100 max_inflight=128;
```

`force` requests streaming when the response is eligible. It does not bypass
hard exclusions, full cache validation, unsupported encodings, or a build-time
feature boundary.

## Tuning order

Tune one bounded setting at a time and record the resulting metrics:

1. `markdown_streaming` controls the requested conversion engine.
2. `markdown_cache_validation` controls whether the module requires complete output.
3. `markdown_auto_decompress` controls compressed-response conversion.
4. `markdown_limits streaming_buffer` bounds streaming working memory.
5. `markdown_limits conversion_memory` and `parser_memory` bound conversion
   allocations.
6. `markdown_limits decompressed_size` and `decompression_ratio` protect
   compressed inputs.
7. `markdown_limits max_inflight` bounds concurrent conversions.

Monitor these frozen families while tuning:

- `nginx_markdown_conversion_attempts_total`,
- `nginx_markdown_conversion_deliveries_total`,
- `nginx_markdown_conversion_duration_seconds`,
- `nginx_markdown_streaming_events_total`,
- `nginx_markdown_decompression_events_total`.

## Memory and backpressure

Streaming keeps bounded buffers and honors downstream backpressure. `NGX_AGAIN`
means that pending output remains owned by the module or the downstream chain.
It is not a delivery success. Delivery counters advance only after the
downstream filter accepts the terminal converted buffer. An internal handoff
is not delivery.

If the inflight gauge does not return to zero after traffic drains, stop the
rollout and collect diagnostics before changing limits. Never solve an
allocation failure by removing a bound.

## Historical note

Older release notes and migration documents may mention preset names or
internal optimization switches. Those names describe historical releases and
must not copy into a current 0.9.2 configuration.
