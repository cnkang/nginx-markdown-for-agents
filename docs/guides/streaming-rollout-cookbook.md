# Streaming Rollout Cookbook

This cookbook describes a controlled rollout of the frozen 0.9.2 streaming
surface. It uses the module's current explicit directives and Prometheus
families. Profiles, threshold directives, and zero-copy switches are not part
of the active configuration contract.

## Before rollout

Validate the binary and configuration:

```bash
nginx -t
nginx -T 2>/dev/null | grep -E 'markdown_(filter|streaming|auto_decompress|cache_validation|limits|error_policy)'
curl -s -H 'Accept: text/plain; version=0.0.4' \
  http://localhost/markdown-metrics > /tmp/markdown-metrics.baseline
curl -s -H 'Accept: application/json' \
  http://localhost/nginx-markdown/diagnostics > /tmp/markdown-diagnostics.baseline.json
```

Start with explicit, bounded settings:

```nginx
http {
    markdown_filter off;
    markdown_streaming auto;
    markdown_auto_decompress on;
    markdown_cache_validation ims_only;
    markdown_error_policy pass;
    markdown_limits conversion_memory=64m conversion_timeout=10s
        parser_memory=32m parser_timeout=5s streaming_buffer=2m
        decompressed_size=20m decompression_ratio=100 max_inflight=64;

    server {
        # Required before collecting the baseline. Keep the endpoint local-only.
        location = /markdown-metrics {
            allow 127.0.0.1;
            allow ::1;
            deny all;
            markdown_metrics;
        }

        location /docs {
            markdown_filter on;
            proxy_pass http://backend;
        }
    }
}
```

Keep `markdown_error_policy pass` during the initial rollout so conversion
errors preserve the upstream response. Use `markdown_streaming force` only for
paths whose response size, cache requirements, and compressed encodings have completed testing.

## Staged enablement

1. Enable one low-traffic staging location.
2. Observe at least one normal traffic cycle.
3. Enable a second representative path.
4. Enable one low-traffic production path.
5. Expand only after the counters and logs remain stable.

For each stage, record:

- `nginx_markdown_requests_total` by `outcome`, `stage`, and `reason`,
- conversion attempts and deliveries by `engine`,
- streaming transitions by `transition`,
- decompression events by `encoding`, `outcome`, and `reason`,
- the diagnostics `configuration.effective` object.

## Verification requests

Use requests that exercise both uncompressed and compressed upstreams:

```bash
curl -sD - -o /tmp/markdown.out \
  -H 'Accept: text/markdown' http://staging.example.com/docs/
curl -sD - -o /tmp/markdown-gzip.out \
  -H 'Accept: text/markdown' http://staging.example.com/gzip-docs/
```

The expected converted response has `Content-Type: text/markdown` and valid
Markdown output. Compare body length and terminal delivery metrics, a
downstream `NGX_AGAIN` is a suspension, not a successful delivery.

## Go/no-go signals

Continue when:

- `nginx -t` passes after each change,
- conversion delivery counts grow only after successful terminal delivery,
- streaming resume failures and post-commit aborts remain zero or explained,
- decompression failures stay limited to intentionally malformed fixtures,
- inflight returns to zero after the test traffic drains,
- the diagnostics effective configuration matches the intended location.

Pause and investigate when:

- `failed_closed`, `abort_start`, or `resume_failure` grows unexpectedly,
- compressed responses show repeated `truncated_input`, `format_error`, or
  `io_error` outcomes,
- conversion attempts exceed the request population,
- full-buffer and streaming delivery conservation no longer holds,
- a reload changes a request's effective configuration mid-request.

## Emergency rollback

```nginx
location /docs {
    markdown_filter on;
    markdown_streaming off;
    markdown_auto_decompress off;
    proxy_pass http://backend;
}
```

Apply with `nginx -t && nginx -s reload`, then verify that streaming attempts
stop and full-buffer attempts remain healthy. Preserve the diagnostics JSON,
Prometheus snapshot, error-log excerpts, and the exact configuration used for
the incident.
