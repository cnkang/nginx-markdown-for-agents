# Public Surface Inventory for the 1.0 Freeze

This document is the repository-owned inventory used to decide which current
surfaces become compatibility commitments at 1.0. It records production
behavior, not merely declarations or design intent.

The evidence order for this inventory is:

1. the NGINX command table and production request path;
2. production-path unit and end-to-end tests;
3. generated Rust/C FFI headers and their in-repository callers;
4. operator documentation.

When those sources disagree, the production path is the current behavior and
the disagreement is a pre-1.0 cleanup item. The canonical syntax and detailed
defaults remain in the [Configuration Guide](../guides/CONFIGURATION.md).

## Classification

| Class | Compatibility conclusion |
|-------|--------------------------|
| `STABLE_FOR_1_0` | Preserve name, accepted values, defaults, inheritance, and wire meaning after the 1.0 freeze. Changes must be additive or follow a later major-version process. |
| `EXPLICITLY_EXPERIMENTAL` | Usable only with explicit opt-in. No 1.0 compatibility promise until production behavior and tests are complete. |
| `REMOVE_BEFORE_1_0` | Must not enter the 1.0 compatibility contract. Remove, replace, or keep only as a reject-only migration diagnostic before the freeze. |
| `INTERNAL_ONLY` | Repository-private boundary. It is not an operator API or third-party SDK contract. |

## NGINX Directive Registry

The source of truth is
`components/nginx-module/src/ngx_http_markdown_config_directives_impl.h`.
There are 63 `markdown_*` command-table entries: 44 active parser entries and
19 reject-only migration entries.

Context abbreviations below are `H` = `http`, `S` = `server`, and `L` =
`location`. Unless a row says otherwise, active `H/S/L` values use the normal
NGINX rule: a child inherits the parent value when unset and an explicit child
value overrides it. `markdown_limits` inherits each key independently.

### Active stable directives

| Area | Directives | Context | Default / operator use | Production and test evidence |
|------|------------|---------|------------------------|------------------------------|
| Core selection | `markdown_filter`, `markdown_profile`, `markdown_flavor`, `markdown_accept` | H/S/L | off; no profile; commonmark; strict. Select conversion, profile, output flavor, and Accept negotiation. `mdx` and `org-mode` are rejected; only commonmark and gfm are supported. | command table and config handlers; `config_handlers_impl_test.c`, `profile_test.c`, `accept_production_test.c` |
| Output metadata | `markdown_token_estimate`, `markdown_front_matter` | H/S/L | off; off. Opt in to token and front-matter output. | conversion FFI options and result handling; Rust converter tests and C conversion tests |
| Resource policy | `markdown_limits`, `markdown_parse_timeout`, `markdown_parser_budget`, `markdown_decompress_max_size`, `markdown_auto_decompress` | H/S/L | memory=10m, timeout=5s, streaming_buffer=2m, max_inflight=64; 30s; 64m; inherits effective memory limit; on. | config create/merge, bounded parser/decompression paths; `config_core_impl_test.c`, `parse_timeout_test.c`, `decompression_production_test.c` |
| Failure and cache policy | `markdown_error_policy`, `markdown_cache_validation` | H/S/L | pass; ims_only. Choose pre-commit failure behavior and conditional validation. | error and conditional production paths; `error_impl_test.c`, `conditional_production_test.c` |
| Transfer/content types | `markdown_buffer_chunked`, `markdown_stream_types`, `markdown_content_types` | H/S/L | on; none; text/html. | eligibility and body-filter routing; `streaming_test.c`, `eligibility_impl_test.c` |
| Authentication | `markdown_auth_policy`, `markdown_auth_cookies` | H/S/L | allow; none. | auth decision paths; `auth_production_test.c` |
| Trusted base-URL proxies | `markdown_trusted_proxies` | H | no trusted proxy. The process-wide CIDR list gates forwarded-header use and is configured only in `http`. | base-URL decision path, handler tests, Rust trusted-proxy tests, and the command-context contract test |
| Streaming selector | `markdown_streaming` | H/S/L | auto. This is the sole processing-path selector: off, auto, or force. | streaming header/body filters; `streaming_config_contract_test.c`, `stream_e2e_test.c`, native chunked E2E |
| Streaming controls | `markdown_stream_threshold`, `markdown_stream_precommit_buffer`, `markdown_stream_flush_min`, `markdown_stream_excluded_types` | H/S/L | 1m; 256k; 16k; none in addition to built-ins. | streaming routing, replay, and flush paths; `streaming_test.c`, `stream_commit_test.c`, native chunked E2E |
| Shadow verification | `markdown_streaming_shadow` | H/S/L | off. Runs a non-serving comparison after successful full-buffer conversion. | `ngx_http_markdown_shadow_compare`; shadow config, metric, difference, and error-isolation tests in `streaming_test.c` |
| Zero-copy delivery | `markdown_streaming_zero_copy` | H/S/L | off. Opts eligible streaming chunks into Rust-owned buffer delivery, with pool-copy fallbacks. | `ngx_http_markdown_streaming_send_zero_copy_feed_output`; `streaming_config_contract_test.c`, `feature_gate_toggle_property_test.c`, native chunked slow-downstream E2E |
| Pruning | `markdown_prune_noise`, `markdown_prune_selectors`, `markdown_prune_protection_selectors` | H/S/L | on; built-in `nav footer aside`; empty protection list. | converter pruning path and Rust regression tests |
| Token estimate tuning | `markdown_llm_provider`, `markdown_chars_per_token` | H/S/L | default provider; 0 selects provider default. | config-to-FFI option mapping and token-estimator tests |
| Logs and metrics | `markdown_log_verbosity`, `markdown_metrics_format`, `markdown_metrics_per_path` | H/S/L | info; auto; off. | production log gating and metrics rendering; `metrics_format_select_test.c`, `metrics_output_test.c`, `prometheus_renderer_test.c` |
| Metrics endpoint | `markdown_metrics` | L | no endpoint by default. Installs the handler in the configured location. | `ngx_http_markdown_metrics_handler`; `tools/e2e/verify_metrics_endpoint_e2e.sh` and Rust E2E metrics scenario |
| Global metrics storage | `markdown_metrics_shm_size`, `markdown_metrics_per_path_cardinality` | H | 8 pages; 100 paths. These are global SHM settings and do not inherit through S/L. | SHM init and bounded per-path RB-tree; metrics unit tests and metrics endpoint E2E |
| Dynamic configuration | `markdown_dynamic_config`, `markdown_dynamic_config_path`, `markdown_dynconf_dry_run` | H/S/L parser surface; one watcher per worker | off; no path; off. First initialized watcher owns the runtime file. Request-bound snapshots remain consistent across reloads. Operators roll back by restoring a prior valid file; the diagnostics endpoint is read-only. | `ngx_http_markdown_dynconf_timer_handler`, effective-conf binding and staged reload tests in `dynconf_impl_test.c`, `dynconf_snapshot_test.c`, and `effective_conf_test.c` |
| Diagnostics | `markdown_diagnostics`, `markdown_diagnostics_allow` | H/S/L | off; loopback-only when no explicit CIDR list exists. The handler is attached to the configured location. | `ngx_http_markdown_diagnostics_handler`; diagnostics production/access/output tests and `tests/e2e/diagnostics_endpoint_test.sh` |

`markdown_streaming_shadow` and `markdown_streaming_zero_copy` are
`STABLE_FOR_1_0`, not experimental placeholders. Both default off, inherit
predictably, have a documented rollback (`off` plus reload), execute real
production paths, and have runtime-focused tests. Their opt-in defaults are a
rollout property, not an absence of support.

Dynamic configuration is also `STABLE_FOR_1_0`: the supported runtime key set,
atomic staged promotion, request snapshot binding, and dry-run behavior are
implemented and tested. The single-watcher scope and read-only diagnostics
surface are part of the stable contract. Restoring an earlier file goes through
the same parse, validation, and promotion path as every other reload.

### Active experimental OTel directives

| Directive | Context | Current behavior | Class |
|-----------|---------|------------------|-------|
| `markdown_otel` | H/S/L | off by default; enables per-conversion span creation, request-scoped JSON rendering, and nonblocking internal-subrequest initiation | `EXPLICITLY_EXPERIMENTAL` |
| `markdown_otel_endpoint` | H/S/L | empty by default; internal URI used for the span export subrequest | `EXPLICITLY_EXPERIMENTAL` |

The implemented OTel subset remains experimental because it has focused unit
coverage (`otel_impl_test.c`) but no collector-backed production E2E contract.
The duplicate and unimplemented OTel controls are reject-only below. They do
not allocate configuration state and cannot silently imply runtime behavior.

### Reject-only migration directives

These entries never execute legacy behavior. They exist only so `nginx -t`
can fail with an actionable migration message. Their old semantics are
`REMOVE_BEFORE_1_0`; retaining a reject-only parser stub does not make the old
name a stable API.

| Reject-only directive | Replacement / migration conclusion |
|-----------------------|------------------------------------|
| `markdown_max_size` | `markdown_limits memory=<size>` |
| `markdown_timeout` | `markdown_limits timeout=<time>` |
| `markdown_streaming_budget` | `markdown_limits streaming_buffer=<size>` |
| `markdown_on_error` | `markdown_error_policy` |
| `markdown_streaming_on_error` | `markdown_error_policy` |
| `markdown_on_wildcard` | `markdown_accept` |
| `markdown_etag` | `markdown_cache_validation` |
| `markdown_etag_policy` | `markdown_cache_validation` |
| `markdown_conditional_requests` | `markdown_cache_validation` |
| `markdown_trust_forwarded_headers` | `markdown_trusted_proxies` |
| `markdown_forwarded_headers` | `markdown_trusted_proxies` |
| `markdown_large_body_threshold` | no direct replacement; use `markdown_streaming` and streaming candidacy controls |
| `markdown_streaming_engine` | `off` -> `markdown_streaming off`; `auto` -> `auto`; `on` -> `force` |
| `markdown_memory_budget` | `markdown_limits memory=<size>` |
| `markdown_otel_tracing` | `markdown_otel on|off`; duplicate enable switch |
| `markdown_otel_metrics` | OTLP metrics are not implemented; use `markdown_metrics` |
| `markdown_otel_service_name` | service-name override is not implemented |
| `markdown_otel_span_buffer_size` | span retry buffering is not implemented |
| `markdown_otel_export_timeout` | configure proxy timeouts on the internal endpoint location |

## Diagnostics JSON Contract

The stable operator endpoint is the JSON produced by
`ngx_http_markdown_diagnostics_build_json` in
`components/nginx-module/src/ngx_http_markdown_diagnostics.c`. There is no
parallel Rust diagnostics specimen or schema export.

| Top-level field | Current shape | Class |
|-----------------|---------------|-------|
| `schema_version` | integer, currently `1` | `STABLE_FOR_1_0` |
| `config_snapshot` | object containing active directive-shaped location configuration keys | `STABLE_FOR_1_0` |
| `recent_decisions` | newest-first array of `timestamp`, numeric `reason_code`, nullable `reason_code_str`, and `duration_ms` | `STABLE_FOR_1_0` |
| `metrics_snapshot` | `conversions_total`, `delivery_total`, `requests_total`, `failopen_total`, `overload_total`, `backpressure_total` | `STABLE_FOR_1_0` |
| `streaming_metrics` | feature-gated request/outcome/candidate/output and engine-choice counters | `STABLE_FOR_1_0` |
| `dynconf_state` | decimal-string `active_mtime` and `last_known_good_mtime`, numeric `config_version`, boolean `lkg_valid` | `STABLE_FOR_1_0` |
| `streaming_config` | effective selector/source/error/threshold/precommit/flush fields, or null without streaming support; `on_error` is exactly `pass`, `fail_closed`, `status 429`, or `status 503` | `STABLE_FOR_1_0` |
| `profile` | active profile string | `STABLE_FOR_1_0` |
| `overridden_fields` / `forced_fields` | arrays describing profile resolution | `STABLE_FOR_1_0` |
| `effective_config` | resolved accept/cache/streaming/limits/error policy, or null without location config; `error_policy` uses the same exact unified-policy values as `streaming_config.on_error` | `STABLE_FOR_1_0` |

Removed directive-shaped keys are not emitted in `config_snapshot`. Active
Config V2 values are represented under `effective_config.limits_*` and related
active fields rather than preserving legacy aliases in the wire schema.

## Metrics and Reason-Code Contract

The production wire schema is emitted by
`ngx_http_markdown_metrics_write_json` and
`ngx_http_markdown_metrics_write_prometheus`. The endpoint supports JSON,
human-readable text, and Prometheus text selected by `Accept` plus
`markdown_metrics_format`.

### Prometheus families currently emitted

These production names are candidates for `STABLE_FOR_1_0` after the schema
documentation is reconciled with the renderer:

```text
nginx_markdown_backpressure_resume_total
nginx_markdown_backpressure_total
nginx_markdown_conversion_latency_bucket_total
nginx_markdown_conversions_total
nginx_markdown_copied_output_total
nginx_markdown_decision_total
nginx_markdown_decompression_budget_exceeded_total
nginx_markdown_decompression_failures_total
nginx_markdown_decompression_format_error_total
nginx_markdown_decompression_fullbuffer_total
nginx_markdown_decompression_io_error_total
nginx_markdown_decompression_streaming_total
nginx_markdown_decompression_truncated_input_total
nginx_markdown_decompressions_total
nginx_markdown_delivery_total
nginx_markdown_estimated_token_savings_total
nginx_markdown_excluded_content_type_total
nginx_markdown_failopen_total
nginx_markdown_failures_total
nginx_markdown_inflight_current
nginx_markdown_inflight_high_watermark
nginx_markdown_input_bytes_total
nginx_markdown_large_response_path_total
nginx_markdown_output_bytes_total
nginx_markdown_overload_total
nginx_markdown_parse_budget_exceeded_total
nginx_markdown_parse_timeouts_total
nginx_markdown_passthrough_total
nginx_markdown_path_conversion_time_ms_total
nginx_markdown_path_conversions_total
nginx_markdown_pending_output_high_watermark_bytes
nginx_markdown_per_path_conversion_time_ms_total
nginx_markdown_per_path_conversions_total
nginx_markdown_per_path_entries
nginx_markdown_per_path_overflow_total
nginx_markdown_perf_decompression_budget_exceeded_total
nginx_markdown_replay_buffer_errors_total
nginx_markdown_requests_total
nginx_markdown_skips_total
nginx_markdown_streaming_budget_exceeded_total
nginx_markdown_streaming_candidate_total
nginx_markdown_streaming_engine_choice_total
nginx_markdown_streaming_failure_total
nginx_markdown_streaming_failures_total
nginx_markdown_streaming_fallback_total
nginx_markdown_streaming_output_bytes_total
nginx_markdown_streaming_path_total
nginx_markdown_streaming_peak_memory_bytes
nginx_markdown_streaming_shadow_diff_total
nginx_markdown_streaming_shadow_total
nginx_markdown_streaming_total
nginx_markdown_streaming_ttfb_seconds
nginx_markdown_true_streaming_selected_total
nginx_markdown_zero_copy_output_total
```

The renderer's label keys are:

| Label key | Families / bounded values | Class |
|-----------|---------------------------|-------|
| `reason` | `skips_total` reason strings; `failures_total` uses bounded `conversion_error`, `resource_limit`, and `system_error` categories | `STABLE_FOR_1_0` |
| `engine` | `streaming`, `full_buffer`, `passthrough`, `not_eligible` | `STABLE_FOR_1_0` |
| `result` | streaming `success`, `failed`, `fallback` outcomes | `STABLE_FOR_1_0` |
| `stage` | `precommit_failopen`, `precommit_reject`, `postcommit_error` | `STABLE_FOR_1_0` |
| `phase`, `action` | precommit pass/reject and postcommit abort/safe_finish | `STABLE_FOR_1_0` |
| `format` | gzip, deflate, brotli | `STABLE_FOR_1_0` |
| `le` | fixed conversion-duration boundaries `0.01`, `0.1`, `1.0`, `+Inf` | `STABLE_FOR_1_0` |
| `path` | explicit per-path opt-in, capped by `markdown_metrics_per_path_cardinality`, with `__other__` overflow | `STABLE_FOR_1_0` |

`nginx_markdown_streaming_engine_choice_total` is a runtime outcome metric,
not a configuration selector. Its name remains valid even though
`markdown_streaming_engine` is reject-only.

The Rust `MetricLabel` whitelist (`reason`, `profile`, `path_mode`,
`cache_validation`) is currently `INTERNAL_ONLY`: it is not the label set used
by the C Prometheus renderer. The draft-only family names
`nginx_markdown_skipped_total`, `nginx_markdown_errors_total`,
`nginx_markdown_failed_open_total`, and
`nginx_markdown_failed_closed_total` are `REMOVE_BEFORE_1_0` unless the
production renderer is deliberately migrated to them with tests and operator
documentation.

### Reason labels

The numeric discriminants and strings below are `STABLE_FOR_1_0`. Their source
is `components/rust-converter/src/decision/reason_code.rs`; C accesses the
registry through FFI.

```text
0 converted
1 skipped_accept
2 skipped_no_accept
3 skipped_conditional
4 decompression_error
5 decompression_budget_exceeded
6 decompression_format_error
7 decompression_truncated_input
8 decompression_io_error
9 timeout
10 budget_exceeded
11 replay_error
12 skipped_accept_reject
13 ffi_panic
14 not_eligible
15 disabled
16 failed_open
17 failed_closed
18 conversion_error
19 memory_budget_exceeded
20 overload
21 invalid_dynconf
22 degraded_snapshot
23 header_plan_apply_error
24 streaming_mid_flight_error
25 bypass_no_transform
```

Production-path evidence includes `reason_code_test.c`,
`reason_code_ffi_test.c`, `prometheus_renderer_test.c`,
`metrics_output_test.c`, and the metrics endpoint E2E scenario.

## OTel Contract

OTel is `EXPLICITLY_EXPERIMENTAL` as a family. The only implemented operator
flow is span creation followed by an OTLP/JSON-shaped POST to a configured
internal NGINX URI, with log export as fallback. The exporter is request-scoped;
there is no retry buffer, collector timeout control, or OTLP metrics path.

Before any OTel surface can be frozen, a collector-backed E2E test must verify
the exact payload, trace-context propagation, exporter failure behavior,
service-name override, and non-blocking request semantics.

## Dynamic Configuration Contract

The stable dynconf file schema is version `0.9` with these runtime keys:

| Key | Meaning |
|-----|---------|
| `schema_version` | mandatory compatibility discriminator |
| `markdown_filter` | on/off request conversion gate |
| `prune_noise` | on/off pruning override |
| `log_verbosity` | error/warn/info/debug |
| `streaming_budget` | runtime streaming buffer size |
| `memory_budget` | runtime memory limit |

Unknown keys or invalid values reject the entire staged update. Successful
reloads atomically promote a snapshot; failed reloads preserve the active and
last-known-good snapshots. Every request binds one effective snapshot at the
header filter and keeps it for the request lifetime.

## Rust/C FFI Boundary

Every generated C symbol below is `INTERNAL_ONLY`. The Rust static library and
NGINX module are released as one product; this project does not publish the
generated header as a third-party SDK or promise ABI compatibility to external
callers.

| Group | Entrypoints |
|-------|-------------|
| Conversion ownership | `markdown_converter_new`, `markdown_convert`, `markdown_result_free`, `markdown_converter_free` |
| ABI handshake | `markdown_abi_version` |
| Accept/eligibility/decision | `markdown_negotiate_accept`, `markdown_decide_eligibility`, `markdown_decide_conditional` |
| Header and URL planning | `markdown_build_header_plan`, `markdown_header_plan_free`, `markdown_decide_base_url` |
| Trusted proxy ownership | `markdown_trusted_proxies_new`, `markdown_trusted_proxies_push`, `markdown_trusted_proxies_free` |
| Initialization helpers | `markdown_options_init`, `markdown_result_init`, `markdown_header_plan_init`, `markdown_decomp_result_init` |
| Bounded decompression | `markdown_decompress_bounded`, `markdown_decompress_free` |
| Conflict/error policy | `markdown_detect_conflicts`, `markdown_free_conflicts`, `markdown_classify_error_code` |
| Incremental conversion | `markdown_incremental_new_with_code`, `markdown_incremental_feed`, `markdown_incremental_finalize`, `markdown_incremental_free` |
| Streaming conversion | `markdown_streaming_new_with_code`, `markdown_streaming_feed`, `markdown_streaming_finalize`, `markdown_streaming_abort`, `markdown_streaming_safe_finish`, `markdown_streaming_output_free` |
| Reason registry | `markdown_reason_code_str`, `markdown_reason_code_metric_key`, `markdown_reason_code_count` |

Internal status does not weaken the safety contract. Struct layout, ownership,
panic containment, result initialization, and generated-header drift remain
blocking build/test concerns. It only means those symbols may be pruned or
changed in lockstep across Rust, generated headers, and C before or after 1.0
without creating an external compatibility promise.

## Freeze Checklist

The public surface is ready to freeze only when all of the following are true:

- every active directive is either stable or explicitly experimental;
- reject-only directives execute no legacy behavior and have accurate hints;
- the diagnostics endpoint and its documentation describe the same wire JSON;
- `config_snapshot` contains no legacy directive-shaped keys;
- the metrics catalog, reason-family mapping, and label set match the production
  renderer;
- incomplete OTel controls are reject-only and execute no runtime behavior;
- `markdown_trusted_proxies` has one documented, tested context/inheritance
  contract;
- all FFI declarations have an in-repository consumer or a documented reason to
  remain internal;
- `make docs-check`, `make test-nginx-unit`, `make test-rust`, and the relevant
  production E2E gates pass.
