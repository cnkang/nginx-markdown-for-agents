# Config Contract: nginx-markdown-for-agents 0.9.2

Frozen public-surface contract for 0.9.2. **Ground truth source:**
`docs/harness/public-surface-inventory.json` (validated by the drift gate
`make public-surface-drift-check`). Prose guidance lives in
`docs/guides/CONFIGURATION.md` — this file is the table-of-record, not a
tutorial.

## Active Directives (25)

| Directive | Syntax | Default | Context |
|---|---|---|---|

| `markdown_accept` | `strict|wildcard|force` | strict | http/server/location |
| `markdown_auth_cookies` | `<pattern> [<pattern> ...]` | none | http/server/location |
| `markdown_auth_policy` | `allow|deny` | allow | http/server/location |
| `markdown_auto_decompress` | `on|off` | on | http/server/location |
| `markdown_cache_validation` | `off|ims_only|full` | ims_only | http/server/location |
| `markdown_content_types` | `<type> [<type> ...]` | text/html | http/server/location |
| `markdown_diagnostics` | `on|off` | off | location |
| `markdown_dynamic_config` | `on|off` | off | http |
| `markdown_dynamic_config_path` | `<path>` | (none) | http |
| `markdown_dynconf_dry_run` | `on|off` | off | http |
| `markdown_error_policy` | `pass|fail_closed|status <code>` | pass | http/server/location |
| `markdown_filter` | `on|off|$variable` | off | http/server/location |
| `markdown_flavor` | `commonmark|gfm` | commonmark | http/server/location |
| `markdown_front_matter` | `on|off` | off | http/server/location |
| `markdown_limits` | `key=value ...` | (per-key inheritance) | http/server/location |
| `markdown_log_verbosity` | `error|warn|info|debug` | info | http/server/location |
| `markdown_metrics` | `(no args)` | off | location |
| `markdown_metrics_shm_size` | `<size>` | 8*pagesize | http |
| `markdown_prune_noise` | `on|off` | on | http/server/location |
| `markdown_prune_protection_selectors` | `<string>` | empty | http/server/location |
| `markdown_prune_selectors` | `<string>` | nav footer aside | http/server/location |
| `markdown_stream_excluded_types` | `<type> [<type> ...]` | none | http/server/location |
| `markdown_streaming` | `off|auto|force` | auto | http/server/location |
| `markdown_token_estimate` | `on|off` | off | http/server/location |
| `markdown_trusted_proxies` | `<CIDR>... | off` | off | http |

## Dynconf Keys (6)

Runtime-mutable keys for `markdown_dynamic_config`. Unknown/duplicate keys,
invalid types, and out-of-range values reject the whole file. `schema_version`
must be present and equal `1`.

| Key | Type | Allowed values | Default | Inheritance |
|---|---|---|---|---|

| `filter` | flag | `on`, `off` | inherited | per-key |
| `prune_noise` | flag | `on`, `off` | inherited | per-key |
| `log_verbosity` | enum | `error`, `warn`, `info`, `debug` | inherited | per-key |
| `error_policy` | enum | `pass`, `fail_closed`, `status 429`, `status 503` | inherited | per-key |
| `streaming_buffer` | size | (size: 64 KiB – 1 GiB) | inherited | per-key |
| `schema_version` | version | `1` | required | none |

## Metric Families (12)

Frozen v1 registry. `bounded` = labeled with bounded-cardinality values.
`fixed` = no labels.

| Metric | Type | Labels | Cardinality |
|---|---|---|---|

| `nginx_markdown_requests_total` | counter | `outcome`, `reason`, `stage` | bounded |
| `nginx_markdown_conversion_attempts_total` | counter | `engine` | bounded |
| `nginx_markdown_conversion_deliveries_total` | counter | `engine` | bounded |
| `nginx_markdown_conversion_duration_seconds` | histogram | `engine` | bounded |
| `nginx_markdown_input_bytes_total` | counter | — | fixed |
| `nginx_markdown_output_bytes_total` | counter | — | fixed |
| `nginx_markdown_inflight_requests` | gauge | — | fixed |
| `nginx_markdown_streaming_peak_memory_bytes` | gauge | — | fixed |
| `nginx_markdown_streaming_events_total` | counter | `reason`, `transition` | bounded |
| `nginx_markdown_decompression_events_total` | counter | `encoding`, `outcome`, `reason` | bounded |
| `nginx_markdown_dynconf_reloads_total` | counter | `outcome`, `reason` | bounded |
| `nginx_markdown_build_info` | gauge | `features`, `nginx_version`, `version` | bounded |

## Reason Codes (27)

| # | string | metric key |
|---|---|---|

| 0 | `converted` | `markdown_conversions_total` |
| 1 | `skipped_accept` | `markdown_skipped_total` |
| 2 | `skipped_no_accept` | `markdown_skipped_total` |
| 3 | `skipped_conditional` | `markdown_skipped_total` |
| 4 | `decompression_error` | `markdown_errors_total` |
| 5 | `decompression_budget_exceeded` | `markdown_errors_total` |
| 6 | `decompression_format_error` | `markdown_errors_total` |
| 7 | `decompression_truncated_input` | `markdown_errors_total` |
| 8 | `decompression_io_error` | `markdown_errors_total` |
| 9 | `timeout` | `markdown_errors_total` |
| 10 | `budget_exceeded` | `markdown_errors_total` |
| 11 | `replay_error` | `markdown_errors_total` |
| 12 | `skipped_accept_reject` | `markdown_skipped_total` |
| 13 | `ffi_panic` | `markdown_errors_total` |
| 14 | `not_eligible` | `markdown_skipped_total` |
| 15 | `disabled` | `markdown_skipped_total` |
| 16 | `failed_open` | `markdown_failed_open_total` |
| 17 | `failed_closed` | `markdown_failed_closed_total` |
| 18 | `conversion_error` | `markdown_errors_total` |
| 19 | `memory_budget_exceeded` | `markdown_errors_total` |
| 20 | `overload` | `markdown_errors_total` |
| 21 | `invalid_dynconf` | `markdown_errors_total` |
| 22 | `degraded_snapshot` | `markdown_errors_total` |
| 23 | `header_plan_apply_error` | `markdown_errors_total` |
| 24 | `streaming_mid_flight_error` | `markdown_errors_total` |
| 25 | `bypass_no_transform` | `markdown_skipped_total` |
| 26 | `encoding_header_invalid` | `markdown_errors_total` |

## FFI Surface (47 exports, ABI v2)

- **ABI version:** 2 (frozen for 0.9.2)
- **Classification:** all `INTERNAL_ONLY`
- **Generated header:** `components/rust-converter/include/markdown_converter.h`
- Signature format: `name(params) -> return_type`

## markdown_limits Keys

`markdown_limits key=value ...` — each key at most once. Unknown keys, zero
values, overflow, and malformed entries fail `nginx -t` or the atomic
dynconf validation path. Defaults are inheritance-based (`NGX_CONF_UNSET`).
0.9.2 documents no explicit defaults.

| Key | Meaning |
|---|---|

| `conversion_timeout` | Wall-clock limit for conversion |
| `parser_timeout` | Cooperative parser deadline |
| `conversion_memory` | Full-buffer input/conversion bound |
| `parser_memory` | Rust parser allocation bound |
| `streaming_buffer` | Streaming working/replay bound (dynconf: 64 KiB – 1 GiB) |
| `decompressed_size` | Cumulative decompressed output bound |
| `decompression_ratio` | Maximum decompressed/input ratio |
| `max_inflight` | Per-worker concurrent conversion bound |

## Removed Surface (do not reference in 0.9.2 docs/configs)

- OTel: `markdown_otel`, `markdown_otel_endpoint` (ADR-0027)
- Profiles: `markdown_profile` presets `balanced` / `strict_cache` / `streaming_first`
- Per-path metrics: `markdown_metrics_format`, `markdown_metrics_per_path*`
- Shadow/zero-copy knobs: `markdown_streaming_shadow`, `markdown_streaming_zero_copy`
- LLM surface: `markdown_llm_provider`, `markdown_chars_per_token`
- Standalone limits unified into `markdown_limits`: `markdown_stream_precommit_buffer`,
  `markdown_stream_flush_min`, `markdown_parse_timeout`, `markdown_parser_budget`,
  `markdown_decompress_max_size`

Full list (38 removals): `docs/guides/0.9.2-breaking-changes.md`.

## Document Updates

| Version | Date | Changes |
| --- | --- | --- |
| 0.9.2 | 2026-08-07 | Pilot: generate contract tables from public-surface-inventory.json ground truth (directives, dynconf, metrics, reason codes, FFI, limits). |
