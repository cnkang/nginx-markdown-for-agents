# Streaming Rollout Cookbook

This cookbook describes a controlled rollout of the frozen 0.9.2 streaming
surface. It uses the module's current explicit directives and Prometheus
families. Profiles, threshold directives, and zero-copy switches are not part
of the active configuration contract.

This is the streaming-specific supplement to the broader
[Rollout Cookbook](ROLLOUT_COOKBOOK.md), which covers general staged rollout,
selective enablement, and non-streaming operational patterns.

## Before rollout

Validate the binary and configuration:

```bash
# Create a private temporary directory so baseline snapshots do not land in
# predictable paths under /tmp.
SNAPSHOT_DIR="$(mktemp -d)"
nginx -t
nginx -T 2>/dev/null | grep -E 'markdown_(filter|streaming|auto_decompress|cache_validation|limits|error_policy)'
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

After NGINX loads the configuration and the local metrics/diagnostics locations
are reachable, capture the baseline. Keeping these commands after the endpoint
definition prevents a successful shell redirect from being mistaken for valid
evidence when the endpoint is not available yet:

```bash
curl -fsS -H 'Accept: text/plain; version=0.0.4' \
  http://localhost/markdown-metrics > "$SNAPSHOT_DIR/markdown-metrics.baseline"
curl -fsS -H 'Accept: application/json' \
  http://localhost/nginx-markdown/diagnostics > \
  "$SNAPSHOT_DIR/markdown-diagnostics.baseline.json"
```

Keep `markdown_error_policy pass` during the initial rollout so conversion
errors that occur before headers commit can preserve the upstream response.
After NGINX commits headers or converted bytes, the original HTML is no longer
replayable. A later failure follows the safe-finish/abort contract and
may leave the client with a truncated Markdown response. Use
`markdown_streaming force` only for paths whose response size, cache
requirements, and compressed encodings have completed testing.

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
SNAPSHOT_DIR="${SNAPSHOT_DIR:-$(mktemp -d)}"
curl -sD - -o "$SNAPSHOT_DIR/markdown.out" \
  -H 'Accept: text/markdown' http://staging.example.com/docs/
curl -sD - -o "$SNAPSHOT_DIR/markdown-gzip.out" \
  -H 'Accept: text/markdown' http://staging.example.com/gzip-docs/
```

The expected converted response has `Content-Type: text/markdown` and valid
Markdown output.

Measure one known streaming-eligible request per before/after snapshot
pair. The metrics endpoint runs on the NGINX host itself, so target the
loopback address for snapshot capture:

```bash
# Pick the uncompressed request as the single measured request.
curl -s http://localhost/markdown-metrics \
  > "$SNAPSHOT_DIR/metrics.before"
# Send exactly one streaming-eligible request between the snapshots,
# then capture the after snapshot without any other conversion request.
curl -s -H 'Accept: text/markdown' http://localhost/docs/ \
  > /dev/null
curl -s http://localhost/markdown-metrics \
  > "$SNAPSHOT_DIR/metrics.after"
```

Assert that `nginx_markdown_conversion_deliveries_total{engine="streaming"}`
increases by exactly one between the two snapshots for that single request.
If you need byte accounting, compare the delta of
`nginx_markdown_output_bytes_total`. Never use a delivery count as a byte
measurement. A downstream `NGX_AGAIN` is a suspension, not a successful
delivery.

## Go/no-go signals

Continue when:

- `nginx -t` passes after each change,
- conversion delivery counts grow only after successful terminal delivery,
- streaming resume failures and post-commit aborts remain zero or explained,
- decompression failures stay limited to intentionally malformed fixtures,
- inflight returns to zero after the test traffic drains,
- the diagnostics effective configuration matches the intended location.

Pause and investigate when:

- `failed_open`, `failed_closed`, `aborted`, `abort_start`, or `resume_failure`
  grows unexpectedly. Compare each rate or count with the established
  pre-rollout baseline,
- compressed responses show repeated decompression failures with
  `reason="truncated_input"`, `reason="format_error"`, or
  `reason="io_error"` on `nginx_markdown_decompression_events_total`
  (treat `outcome` as success or failure only),
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

Apply with `nginx -t && nginx -s reload`, then wait for the graceful reload to
drain. The aggregate `nginx_markdown_inflight_requests` gauge (or the
diagnostics inflight field) returns to zero when the request population is
quiescent, but it is not proof that pre-reload requests have drained: it only
shows that no request is currently mid-conversion, and new requests admitted
after the reload keep it at or above one while they convert. To verify
pre-reload drain, record a baseline of `nginx_markdown_inflight_requests`
and the conversion counters immediately before the reload, then compare
**deltas after quiescence**, or wait until the diagnostics/error logs show the
pre-reload requests reaching terminal. Do not poll the cumulative
`nginx_markdown_requests_total` counter, which only advances and cannot show
drain. Only then issue controlled requests for the rolled-back scope and
compare the before/after deltas. The exported counters do not provide a
reliable post-reload request scope. Verify that streaming attempts stop and
full-buffer attempts remain healthy. Preserve the diagnostics JSON, Prometheus
snapshot, error-log excerpts, and the exact configuration used for the
incident.
