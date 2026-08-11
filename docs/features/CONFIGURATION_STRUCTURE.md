# NGINX Markdown Filter Module Configuration Structure

This page is an implementation reference for the merged
`ngx_http_markdown_conf_t` object. For operator syntax and examples, see
[`docs/guides/CONFIGURATION.md`](../guides/CONFIGURATION.md).

## Configuration shape

The public command registry is frozen at 25 directives. Resource controls are
one directive with eight independently inherited keys. The old standalone
budget directives are not part of the live command table.

```c
typedef struct {
    ngx_flag_t   enabled;
    ngx_uint_t   enabled_source;
    ngx_http_complex_value_t *enabled_complex;
    size_t       max_size;       /* limits.conversion_memory */
    ngx_msec_t   timeout;        /* limits.conversion_timeout */
    ngx_uint_t   on_error;
    ngx_uint_t   error_status;
    ngx_uint_t   flavor;
    ngx_flag_t   token_estimate;
    ngx_flag_t   front_matter;
    ngx_uint_t   accept_policy;
    ngx_http_markdown_policy_cfg_t policy;

    struct {
        ngx_array_t *content_types;
        size_t       large_body_threshold; /* internal routing heuristic */
        ngx_uint_t   max_inflight;
    } routing;

    struct {
        ngx_flag_t   auto_decompress;
        size_t       max_size;       /* limits.decompressed_size */
        ngx_msec_t   parse_timeout;  /* limits.parser_timeout */
        size_t       parser_budget;  /* limits.parser_memory */
    } decompress;

    ngx_http_markdown_limits_t limits;
    ngx_http_markdown_stream_cfg_t stream;
    ngx_http_markdown_ops_cfg_t ops;
    ngx_http_markdown_advanced_cfg_t advanced;
} ngx_http_markdown_conf_t;
```

The exact C declaration is authoritative:
`components/nginx-module/src/ngx_http_markdown_filter_module.h`.

## Public limit mapping

```nginx
markdown_limits conversion_timeout=30s parser_timeout=10s
    conversion_memory=64m parser_memory=32m streaming_buffer=2m
    decompressed_size=10m decompression_ratio=100 max_inflight=64;
```

Each key inherits independently across `http`, `server`, and `location`.
The merge step then binds the effective values to the runtime fields shown in
the structure above and rejects cross-key violations before mutation:

- `parser_timeout <= conversion_timeout`
- `parser_memory <= conversion_memory`
- `streaming_buffer <= conversion_memory`

The defaults are `30s`, `10s`, `64m`, `32m`, `2m`, `10m`, `100`, and `64`, in
the order shown in the example.

## Field groups

| Group | Runtime fields | Public controls |
| --- | --- | --- |
| Core | `enabled`, `max_size`, `timeout`, `on_error`, `error_status` | `markdown_filter`, `markdown_limits`, `markdown_error_policy` |
| Conversion | `flavor`, `token_estimate`, `front_matter`, `accept_policy` | `markdown_flavor`, `markdown_token_estimate`, `markdown_front_matter`, `markdown_accept` |
| Policy | `policy.auth_policy`, cookie patterns, cache validation | `markdown_auth_policy`, `markdown_auth_cookies`, `markdown_cache_validation` |
| Routing | content-type allowlist, in-flight limit | `markdown_content_types`, `markdown_limits max_inflight` |
| Streaming | mode, exclusions, bounded buffer | `markdown_streaming`, `markdown_stream_excluded_types`, `markdown_limits streaming_buffer` |
| Decompression | automatic mode and bounded decoder state | `markdown_auto_decompress`, `markdown_limits decompressed_size`, `markdown_limits decompression_ratio` |
| Observability | logs, diagnostics, metrics shared memory | `markdown_log_verbosity`, `markdown_diagnostics`, `markdown_metrics_shm_size`, `markdown_metrics` |
| Dynamic config | watcher, path, dry-run | `markdown_dynamic_config`, `markdown_dynamic_config_path`, `markdown_dynconf_dry_run` |

## Lifecycle

1. `ngx_http_markdown_create_conf` allocates the structure and marks values
   unset.
2. Directive handlers record explicit values and reject duplicates or
   incompatible contexts.
3. `ngx_http_markdown_merge_conf` applies per-key inheritance and defaults,
   validates cross-key limits, then derives internal runtime fields.
4. Request processing reads the effective merged configuration. Dynamic config
   applies only its five frozen keys (`filter`, `prune_noise`, `log_verbosity`,
   `error_policy`, and `streaming_buffer`).

## Related checks

- `python3 tools/release/gates/validate_config_directives.py`
- `python3 tools/harness/detect_public_surface_drift.py`
- `make schema-drift-check`

These checks keep the command table, nested limit schema, source handlers, and
documentation synchronized.

## Document Updates

| Version | Date | Changes |
| --- | --- | --- |
| 0.9.2 | 2026-08-04 | Align implementation reference with the frozen 25-directive command table and eight-key `markdown_limits` contract. |
