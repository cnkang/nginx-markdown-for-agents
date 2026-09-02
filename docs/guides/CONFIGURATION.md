# Configuration Reference (0.9.2)

This is the frozen configuration reference. The command table has 25
active `markdown_*` directives. Resource limits use one `markdown_limits`
directive with bounded key/value entries. Dynamic configuration has its own
five-key JSON overlay.

## Minimal configuration

```nginx
load_module modules/ngx_http_markdown_filter_module.so;

# Minimal upstream used by the example proxy_pass below.
# Replace with your actual origin service (or proxy to a socket/port).
upstream backend {
    server 127.0.0.1:8080;
}

http {
    markdown_filter on;
    markdown_streaming auto;
    markdown_auto_decompress on;
    markdown_error_policy pass;
    markdown_limits conversion_memory=64m conversion_timeout=10s
        parser_memory=32m parser_timeout=5s streaming_buffer=2m
        decompressed_size=10m decompression_ratio=100 max_inflight=64;

    server {
        listen 8080;
        location /docs {
            proxy_pass http://backend;
        }
    }
}
```

Always validate changes with `nginx -t` before reload. The authoritative
machine-readable surface is
[`docs/harness/public-surface-inventory.json`](../harness/public-surface-inventory.json).

## Active directive table

All active directives accept the contexts recorded in the public inventory.
The table below is the operator-facing summary. Inheritance follows normal
NGINX `http` → `server` → `location` configuration merging.

| Directive | Context | Purpose | Typical values |
|---|---|---|---|
| `markdown_filter` | `http, server, location` | Enable conversion | `on`, `off`, or a complex value |
| `markdown_limits` | `http, server, location` | Set bounded resource limits | key/value entries listed below |
| `markdown_error_policy` | `http, server, location` | Handle conversion errors | `pass`, `fail_closed`, `status <code>` |
| `markdown_flavor` | `http, server, location` | Markdown dialect | `commonmark`, `gfm` |
| `markdown_token_estimate` | `http, server, location` | Emit token estimates | `on`, `off` |
| `markdown_front_matter` | `http, server, location` | Front-matter behavior | `on`, `off` |
| `markdown_accept` | `http, server, location` | Accept negotiation policy | `strict`, `wildcard`, `force` |
| `markdown_auth_policy` | `http, server, location` | Authentication handling | `allow`, `deny` |
| `markdown_auth_cookies` | `http, server, location` | Authentication cookie names | space-separated names |
| `markdown_cache_validation` | `http, server, location` | Cache/ETag policy | `off`, `ims_only`, `full` |
| `markdown_streaming` | `http, server, location` | Requested conversion engine | `off`, `auto`, `force` |
| `markdown_log_verbosity` | `http, server, location` | Decision log verbosity | `error`, `warn`, `info`, `debug` |
| `markdown_content_types` | `http, server, location` | Convertible media types | space-separated media types |
| `markdown_trusted_proxies` | `http` | Trusted proxy CIDRs | CIDR list |
| `markdown_metrics_shm_size` | `http` | Metrics shared-memory size | NGINX size value |
| `markdown_metrics` | `location` | Expose the metrics endpoint | flag directive |
| `markdown_prune_noise` | `http, server, location` | Remove configured page noise | `on`, `off` |
| `markdown_prune_selectors` | `http, server, location` | Noise selectors | selector list |
| `markdown_prune_protection_selectors` | `http, server, location` | Protected selectors | selector list |
| `markdown_auto_decompress` | `http, server, location` | Convert compressed upstream bodies | `on`, `off` |
| `markdown_dynamic_config` | `http` | Enable the dynconf watcher | `on`, `off` |
| `markdown_dynamic_config_path` | `http` | Watched dynconf JSON path | filesystem path |
| `markdown_dynconf_dry_run` | `http` | Validate without promotion | `on`, `off` |
| `markdown_diagnostics` | `location` | Expose diagnostics JSON | `on`, `off` |
| `markdown_stream_excluded_types` | `http, server, location` | Exclude types from streaming | media-type list |

The public metric endpoint is Prometheus-only. There is no active
`markdown_metrics_format` directive.

## Resource limits

`markdown_limits` accepts each key at most once. Unknown keys, zero values,
overflow, and malformed entries fail `nginx -t` or the atomic dynconf
validation path.

The frozen single-key fragment is also valid: `markdown_limits
streaming_buffer=2m`.

| Key | Meaning |
|---|---|
| `conversion_timeout` | Wall-clock limit for conversion |
| `parser_timeout` | Cooperative parser deadline |
| `conversion_memory` | Full-buffer input admission and generated-output bound; transient scratch allocations are also charged against this budget so conversion aborts with a controlled error instead of growing peak memory past it |
| `parser_memory` | Rust parser allocation bound |
| `streaming_buffer` | Per-request streaming working-set and replay budget |
| `decompressed_size` | Cumulative decompressed output bound |
| `decompression_ratio` | Maximum decompressed/input ratio |
| `max_inflight` | Per-worker concurrent conversion bound |

Example:

```nginx
markdown_limits conversion_timeout=10s parser_timeout=5s
    conversion_memory=64m parser_memory=32m streaming_buffer=2m
    decompressed_size=10m decompression_ratio=100 max_inflight=64;
```

`parser_timeout` is a cooperative parser-work allowance, not a preemptive
wall-clock interrupt. The converter checks it at parser and traversal
checkpoints and at finalization. An uninterruptible parser operation may
overshoot the configured value. Upstream stalls do not consume this allowance.

The bounds are cumulative where the decoder has multiple gzip members. The
decoder rejects a truncated final member. A decompression failure follows
`markdown_error_policy` before commit and cannot replay the original body
after streaming headers have been sent.

`streaming_buffer` is a total per-request byte budget shared by the Rust
streaming converter's working set and the pre-commit original-body replay
buffer. It is not an upstream chunk-size or flush-size setting. Streaming
budget and replay-overflow errors follow `markdown_error_policy`. With
`pass`, the module normally forwards the original response — but only while
the replay buffer still holds the original body. If a replay overflow makes
that body unavailable before commit, even `pass` cannot forward it. In that
exception the decision engine rejects the request using `REJECT_STATUS`
instead. With `fail_closed` or `status <code>`, the module rejects the
request and returns the configured reject status.

The 0.9.2 default for `streaming_buffer` is 2 MiB, the same default that
0.9.1 used. The 256 KiB value appeared only in the removed `balanced` and
`streaming_first` profiles. Operators who explicitly pinned those profiles
should set `markdown_limits streaming_buffer=256k` to retain that behavior.

## Streaming policy

The requested policy is `markdown_streaming off | auto | force`.

- `off` selects bounded full-buffer conversion.
- `auto` applies a bounded internal response-shape heuristic.
- `force` requests streaming after hard eligibility and cache gates.

The heuristic threshold is internal and is intentionally not a directive.
Streaming is still blocked by full cache validation, excluded content types,
unsupported encodings, and build-time feature boundaries. For compressed
responses, conversion requires `markdown_auto_decompress on`. Brotli
uses the streaming decoder only when built with the feature enabled.
Otherwise it uses bounded full-buffer decompression.

### Cache interaction

`markdown_cache_validation full` requires complete converted output before
headers and therefore selects full-buffer. `ims_only` or `off` permits
streaming when the other gates pass. The module commits streaming headers
before the first converted body buffer, so post-commit errors use safe-finish
or abort handling rather than replaying the upstream body.

## Authentication and request selection

`markdown_accept` controls whether a request asks for Markdown. Keep the
policy strict for staged rollout so browsers sending `*/*` are not converted
unexpectedly. `markdown_auth_policy` and `markdown_auth_cookies` prevent
conversion of authenticated content according to the configured policy.

`markdown_content_types` controls general conversion eligibility.
`markdown_stream_excluded_types` is an additional streaming-only exclusion.
An excluded type may still use full-buffer conversion when otherwise eligible.
Built-in streaming exclusions include event-stream and newline-delimited JSON
media types.

`markdown_trusted_proxies` is an explicit CIDR allowlist for forwarded URL
headers. The module ignores forwarded headers from an untrusted peer. For a
trusted request, the module processes aligned `Forwarded` or `X-Forwarded-*`
chains from right to left. It strips trusted proxy hops and uses the first
untrusted hop for the client-facing value. If every hop matches a trusted
proxy, the module discards the chain. Bracketed IPv6 literals such as
`[2001:db8::1]` can match a trusted CIDR. Bracketed IPv4 literals such as
`[192.0.2.1]` never match. The module discards malformed or mismatched
forwarded lists as a whole.

## Markdown output and pruning

`markdown_flavor` accepts only `commonmark` and `gfm`. `markdown_token_estimate`
adds bounded token-estimate output. `markdown_front_matter` controls supported
front-matter handling. `markdown_prune_noise`, `markdown_prune_selectors`, and
`markdown_prune_protection_selectors` control bounded DOM-noise pruning. A
protected selector wins over a matching removal selector.

## Metrics and diagnostics

You expose metrics by placing `markdown_metrics` in a location, commonly
protected by loopback or an explicit access policy:

```nginx
location = /markdown-metrics {
    markdown_metrics;
    allow 127.0.0.1;
    allow ::1;
    deny all;
}
```

Scrape with `Accept: text/plain; version=0.0.4`. The endpoint emits exactly
the eleven frozen Prometheus families documented in
[`prometheus-metrics.md`](prometheus-metrics.md). `markdown_diagnostics` is a
read-only JSON endpoint for effective configuration, provenance, decisions,
and bounded runtime state. Its built-in access boundary is loopback-only.
Native NGINX `allow`/`deny` or authentication directives may narrow that
boundary but cannot broaden it. It accepts only `GET` and `HEAD`.

## Dynamic configuration (dynconf)

Enable the watcher and point it at a JSON file:

```nginx
http {
    markdown_dynamic_config on;
    markdown_dynamic_config_path /etc/nginx/markdown-dynconf.json;
    markdown_dynconf_dry_run off;
}
```

The file must contain `"schema_version": 1` and may contain only these five
runtime keys:

| Key | Values |
|---|---|
| `filter` | `on`, `off` |
| `prune_noise` | `on`, `off` |
| `log_verbosity` | `error`, `warn`, `info`, `debug` |
| `error_policy` | `pass`, `fail_closed`, `status 429`, `status 503` |
| `streaming_buffer` | 64 KiB through 1 GiB |

Example:

```json
{
  "schema_version": 1,
  "filter": "on",
  "prune_noise": "off",
  "log_verbosity": "info",
  "error_policy": "pass",
  "streaming_buffer": 2097152
}
```

Unknown keys, duplicate keys, invalid types, and out-of-range values reject
the entire file. A failed reload leaves the active and last-known-good
snapshots unchanged. A request binds one effective snapshot at header-filter
entry, so a timer reload cannot change that request midway through its body.
Structural directives and static-only limit keys still require `nginx -s
reload`.

## Reload and rollback

```bash
nginx -t && nginx -s reload
```

For an emergency streaming rollback, set `markdown_streaming off`. For a
compressed-input rollback, set `markdown_auto_decompress off`. Preserve the
diagnostics JSON, Prometheus response, logs, and exact configuration before
restoring the previous settings.

## Validation commands

```bash
python3 tools/release/gates/validate_config_directives.py
python3 tools/harness/detect_doc_sync.py
python3 tools/harness/detect_public_surface_drift.py
python3 tools/release/gates/validate_schema_drift.py
```

Historical migrations retain examples for removed directives under their
versioned documents. Do not copy those examples into a 0.9.2 configuration.

The authoritative removal table for 0.8.x-era directives is
[MIGRATION-0.9.0.md](MIGRATION-0.9.0.md#directive-mapping-table); the
mapping rows below that only appeared in later removals are listed here
for quick reference. All removals are also rejected by `nginx -t` with a
pointer to the migration guide.

## Removed directives

`markdown_streaming_auto_threshold` — REMOVED. Use the explicit
`markdown_streaming off | auto | force` policy. The selection threshold is
an internal heuristic and has no replacement directive. Use
`markdown_limits streaming_buffer=` only to bound the streaming working set and
pre-commit replay memory. This setting does not select the upstream chunk size.

`markdown_decompress_max_size` — REMOVED. Use the
`markdown_limits decompressed_size=` key instead.

`markdown_parse_timeout` — REMOVED. Use the
`markdown_limits parser_timeout=` key instead.

`markdown_parser_budget` — REMOVED. Use the
`markdown_limits parser_memory=` key instead.

`markdown_stream_threshold` — REMOVED. No replacement. The threshold is an
internal 1 MiB routing rule.

`markdown_stream_precommit_buffer` — REMOVED. Use the
`markdown_limits streaming_buffer=` key instead.

`markdown_stream_flush_min` — REMOVED. No replacement. Flushing uses an
internal heuristic.
