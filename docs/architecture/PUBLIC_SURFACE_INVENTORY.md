# Public Surface Inventory for the 1.0 Freeze

This document is the repository-owned inventory used to decide which current
surfaces become compatibility commitments at 1.0. It records source metadata
and ABI declarations, cross-referenced with production-path evidence. The
drift gate (`make public-surface-drift-check`) validates source metadata and
ABI drift against this inventory. The unit, integration, and E2E test suites
verify runtime behavior, not the drift gate alone.

The extraction and drift-check decision appears in
[ADR-0025](ADR/0025-public-surface-inventory-drift-gate.md).

The evidence order for this inventory is:

1. the NGINX command table and production request path,
2. production-path unit and end-to-end tests,
3. generated Rust/C FFI headers and their in-repository callers,
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
There are 25 `markdown_*` command-table entries: 25 active parser entries and
0 reject-only migration entries. Removed directive names are deliberately
absent so NGINX's standard unknown-directive error is the migration behavior.

Context abbreviations below are `H` = `http`, `S` = `server`, and `L` =
location. Unless a row says otherwise, active `H/S/L` values use the normal
NGINX rule: a child inherits the parent value when unset and an explicit child
value overrides it. `markdown_limits` inherits each key independently.

### Active stable directives

| Area | Directives | Context | Default / operator use | Production and test evidence |
|------|------------|---------|------------------------|------------------------------|
| Core selection | `markdown_filter`, `markdown_flavor`, `markdown_accept` | H/S/L | off; commonmark; strict. Select conversion, output flavor, and Accept negotiation. | command table and config-handler tests |
| Output metadata | `markdown_token_estimate`, `markdown_front_matter` | H/S/L | off; off. Opt in to token and front-matter output. | conversion FFI options and result handling; Rust converter tests and C conversion tests |
| Resource policy | `markdown_limits`, `markdown_auto_decompress` | H/S/L | unified per-key limits; automatic decompression on by default. | config create/merge and bounded parser/decompression paths |
| Failure and cache policy | `markdown_error_policy`, `markdown_cache_validation` | H/S/L | pass; ims_only. Choose pre-commit failure behavior and conditional validation. | error and conditional production paths; `error_impl_test.c`, `conditional_production_test.c` |
| Transfer/content types | `markdown_content_types` | H/S/L | text/html by default; chunked transfer is automatic. | eligibility and body-filter routing |
| Authentication | `markdown_auth_policy`, `markdown_auth_cookies` | H/S/L | allow; none. | auth decision paths; `auth_production_test.c` |
| Trusted base-URL proxies | `markdown_trusted_proxies` | H | no trusted proxy. The process-wide CIDR list gates forwarded-header use and is configured only in `http`. | base-URL decision path, handler tests, Rust trusted-proxy tests, and the command-context contract test |
| Streaming selector | `markdown_streaming` | H/S/L | auto. This is the sole processing-path selector: off, auto, or force. | streaming header/body filters; `streaming_config_contract_test.c`, `stream_e2e_test.c`, native chunked E2E |
| Streaming controls | `markdown_stream_excluded_types` | H/S/L | explicit streaming exclusions; built-in event-stream exclusions remain enforced. | streaming routing and replay/flush paths |
| Pruning | `markdown_prune_noise`, `markdown_prune_selectors`, `markdown_prune_protection_selectors` | H/S/L | on; built-in `nav footer aside`; empty protection list. | converter pruning path and Rust regression tests |
| Logs and metrics | `markdown_log_verbosity` | H/S/L | info by default; metrics are Prometheus-only. | production log gating and metrics rendering |
| Metrics endpoint | `markdown_metrics` | L | no endpoint by default. Installs the handler in the configured location. | `ngx_http_markdown_metrics_handler`; `tools/e2e/verify_metrics_endpoint_e2e.sh` and Rust E2E metrics scenario |
| Global metrics storage | `markdown_metrics_shm_size` | H | bounded SHM allocation; global and not inherited through S/L. | SHM initialization and metrics unit/E2E tests |
| Dynamic configuration | `markdown_dynamic_config`, `markdown_dynamic_config_path`, `markdown_dynconf_dry_run` | H | off; no path; off. One watcher per worker; requests bind one snapshot for their lifetime. | dynconf reload, snapshot, and effective-config tests |
| Diagnostics | `markdown_diagnostics` | L | off; the built-in handler permits loopback clients only, while native NGINX access-phase directives may narrow access further. | diagnostics production/access/output tests |

The streaming threshold is an internal 1 MiB heuristic. The module selects
zero-copy delivery automatically from ownership and backpressure state.
Neither zero-copy nor shadow comparison is a public directive. Dynamic
configuration
is stable and uses atomic staged promotion, request snapshot binding, and
bounded diagnostics state.

### Removed OTel surface

The 0.9.2 production surface no longer includes the OTel directives and
implementation. There is no experimental or reject-only OTel command-table entry.
NGINX's standard unknown-directive error is the expected migration behavior.
ADR-0027 records the conditions required for a future 1.x reintroduction.

### Reject-only migration directives

There are no reject-only migration entries in the final command table. All
removed names are intentionally absent and therefore use NGINX's standard
unknown-directive error at `nginx -t` time.

| Migration area | Replacement / migration conclusion |
|----------------|------------------------------------|
| Legacy size/time/error/cache/trusted-proxy directives | Use the corresponding `markdown_limits`, `markdown_error_policy`, `markdown_cache_validation`, or `markdown_trusted_proxies` contract. |
| `markdown_streaming_engine` | Use `markdown_streaming off|auto|force`; the old directive is absent. |
| OTel directives | No 0.9.2 replacement; follow ADR-0027 before any future 1.x design. |

## Diagnostics JSON Contract

The stable operator endpoint is the JSON produced by
`ngx_http_markdown_diagnostics_build_json` in
`components/nginx-module/src/ngx_http_markdown_diagnostics.c`. There is no
parallel Rust diagnostics specimen or schema export. The handler is strictly
read-only: it accepts only `GET` and `HEAD`. There is no mutation endpoint or
rollback response schema.

| Top-level field | Current shape | Class |
|-----------------|---------------|-------|
| `schema_version` | integer constant `2` | `STABLE_FOR_1_0` |
| `product_version` | non-empty product version string | `STABLE_FOR_1_0` |
| `worker` | `{pid, scope}` with `scope="worker-local"` | `STABLE_FOR_1_0` |
| `build` | `{source_sha, nginx_version, rust_version, features}` | `STABLE_FOR_1_0` |
| `configuration` | `{static_digest, dynconf, effective, effective_sources}`; strict additional-properties-free schema | `STABLE_FOR_1_0` |
| `runtime` | `{inflight, pending_output, module_metrics}` worker-local non-negative counters | `STABLE_FOR_1_0` |
| `recent_decisions` | bounded array of `{timestamp, outcome, stage, reason, error_origin, duration_ms}` | `STABLE_FOR_1_0` |

The full JSON Schema is `schemas/diagnostics.schema.json`. The effective
field/source contract artifact ships alongside the schema.
Legacy `config_snapshot`, profile, streaming, and duplicated metrics fields are
not part of the v2 wire schema. The endpoint accepts only GET and HEAD. HEAD computes
the complete body length but sends no body.

## Metrics and Reason-Code Contract

The v1 Prometheus renderer emits the production wire schema. The
endpoint is Prometheus-only. `markdown_metrics_format` and the legacy JSON/text
selection surfaces no longer exist.

### Prometheus families currently emitted

These 12 production names are the frozen registry:

```text
nginx_markdown_requests_total
nginx_markdown_conversion_attempts_total
nginx_markdown_conversion_deliveries_total
nginx_markdown_conversion_duration_seconds
nginx_markdown_input_bytes_total
nginx_markdown_output_bytes_total
nginx_markdown_inflight_requests
nginx_markdown_streaming_peak_memory_bytes
nginx_markdown_streaming_events_total
nginx_markdown_decompression_events_total
nginx_markdown_dynconf_reloads_total
nginx_markdown_build_info
```

`schemas/metrics-v1.registry.json` is authoritative. The versioned
`artifacts/release/<version>/metrics-registry.json` file is only its generated
projection. The renderer must emit exactly these families in Prometheus 0.0.4
format. The label sets
stay closed and bounded: outcomes/stages/reasons use the frozen taxonomy,
engine is `full_buffer|streaming`, transition is the six-value lifecycle
allowlist, and encoding/outcome/reason use the registry allowlists. Path, URI,
profile, and per-path dimensions are absent.

### Reason labels

The numeric discriminants and strings below are `STABLE_FOR_1_0`. The single
declarative source is `components/rust-converter/reason_registry.toml`.
The generator emits `reason_code.rs`, `markdown_reason_meta.h`, and the
release artifacts. These files are projections. C accesses the generated
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
26 encoding_header_invalid
```

Production-path evidence includes generated reason artifacts,
`reason_code_test.c`, `reason_code_ffi_test.c`, Prometheus renderer tests,
and the metrics endpoint E2E scenario. The generator is the only permitted
source for reverse lookup and compatibility aliases.

## OTel Contract

The 0.9.2 production surface no longer includes OTel. A future reintroduction
must satisfy the same six conditions as ADR-0027. The implementation needs a
stable NGINX-native dependency. It needs complete span, context, export,
timeout, retry, and degradation behavior. It needs a seven-day production soak
with the stated leak, p99, outage, and correlation criteria. It must not block
the request path. Compile-time gating remains required until release-candidate
soak evidence exists. Directive compatibility with the NGINX-native module is
also required.

## Dynamic Configuration Contract

The stable dynconf file schema is version `1` with these runtime keys:

| Key | Meaning |
|-----|---------|
| `schema_version` | mandatory compatibility discriminator |
| `filter` | on/off request conversion gate |
| `prune_noise` | on/off pruning override |
| `log_verbosity` | error/warn/info/debug |
| `error_policy` | pass/fail_closed/status 429/status 503 |
| `streaming_buffer` | runtime streaming buffer size in bytes |

Unknown keys or invalid values reject the entire staged update. Successful
reloads atomically promote a snapshot. Failed reloads preserve the active and
last-known-good snapshots. Every request binds one effective snapshot at the
header filter and keeps it for the request lifetime.

## Rust/C FFI Boundary

Every generated C symbol below is `INTERNAL_ONLY`. The Rust static library and
NGINX module ship as one product. This project does not publish the
generated header as a third-party SDK or promise ABI compatibility to external
callers. The [FFI ABI Compatibility](FFI_ABI_COMPATIBILITY.md) document
defines the in-repository compatibility handshake and update procedure. Any incompatible
Rust/C layout, signature, or numeric-constant change must update that contract,
the generated header, all in-repository callers, and the release notes in one
change. No external append-only promise applies to the FFI export set.

The registry below is the complete in-repository production registry, not a
third-party SDK list. Production C callers use the dynamic-configuration,
incremental/streaming, and encoding/hash helpers, so the registry includes
them.

| Group | Entrypoints |
|-------|-------------|
| Conversion ownership | `markdown_converter_new`, `markdown_convert`, `markdown_result_free`, `markdown_converter_free` |
| ABI handshake | `markdown_abi_version`, `markdown_abi_header_hash`, `markdown_abi_symbol_set_hash`, `markdown_abi_layout_fingerprint` |
| Accept/eligibility/decision | `markdown_negotiate_accept`, `markdown_decide_eligibility`, `markdown_decide_conditional` |
| Header and URL planning | `markdown_build_header_plan`, `markdown_header_plan_free`, `markdown_decide_base_url` |
| Trusted proxy ownership | `markdown_trusted_proxies_new`, `markdown_trusted_proxies_push`, `markdown_trusted_proxies_free` |
| Initialization helpers | `markdown_options_init`, `markdown_result_init`, `markdown_header_plan_init`, `markdown_decomp_result_init`, `markdown_base_url_input_init` |
| Bounded decompression | `markdown_decompress_bounded`, `markdown_decompress_free` |
| Error classification | `markdown_classify_error_code` |
| Dynamic configuration | `markdown_dynconf_parse`, `markdown_dynconf_result_init`, `markdown_dynconf_result_free` |
| Incremental conversion | `markdown_incremental_new_with_code`, `markdown_incremental_feed`, `markdown_incremental_finalize`, `markdown_incremental_free` |
| Streaming conversion | `markdown_streaming_new_with_code`, `markdown_streaming_feed`, `markdown_streaming_finalize`, `markdown_streaming_abort`, `markdown_streaming_safe_finish`, `markdown_streaming_output_free` |
| Reason registry | `markdown_reason_code_str`, `markdown_reason_code_metric_key`, `markdown_reason_code_count` |
| Encoding chain and hash helpers | `markdown_chain_decode_free`, `markdown_chain_decode_result_init`, `markdown_decode_encoding_chain`, `markdown_parse_encoding_chain`, `markdown_sha256_hex` |

Internal status does not weaken the safety contract. Struct layout, ownership,
panic containment, result initialization, and generated-header drift remain
blocking build/test concerns. It only means the project may prune or change
those symbols in lockstep across Rust, generated headers, and C before or after
1.0 without creating an external compatibility promise. The safety properties
stay binding regardless.

## Freeze Checklist

The public surface is ready to freeze only when all of the following are true:

- every active directive is either stable or explicitly experimental,
- removed directives are absent from the command table and use the standard
  NGINX unknown-directive migration behavior,
- the diagnostics endpoint and its documentation describe the same wire JSON,
- the module rejects diagnostics mutation methods and no undocumented rollback API
  or response schema exists. The endpoint exposes read-only state only.
- the diagnostics schema contains exactly the seven frozen top-level fields,
- the metrics catalog, reason-family mapping, and label set match the production
  renderer,
- OTel is absent from production code and the command table,
- `markdown_trusted_proxies` has one documented, tested context/inheritance
  contract,
- all FFI declarations have an in-repository consumer or a documented reason to
  remain internal,
- `make docs-check`, `make test-nginx-unit`, `make test-rust`, and the relevant
  production E2E gates pass.
