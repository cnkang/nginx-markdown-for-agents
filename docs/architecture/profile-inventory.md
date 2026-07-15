# Profile Inventory — Config V2 Field Mapping (Profiles, Wave 0)

| Field | Value |
|-------|-------|
| Version | 0.9.1 |
| Feature | Profiles Production Defaults |
| Status | Inventory |
| Created | 2026-06-28 |

---

This file is a profile-field map, not the 1.0 compatibility inventory. For
active versus reject-only command-table state and evidence-backed stability
classification, use
[PUBLIC_SURFACE_INVENTORY.md](PUBLIC_SURFACE_INVENTORY.md). In particular,
parser-stored OTel placeholders are not proof of production behavior.

## 1. Active Config V2 Directives with Defaults

Directives below are the **active** Config V2 directives registered in
`ngx_http_markdown_config_directives_impl.h`. Legacy reject-only stubs
(markdown_max_size, markdown_timeout, markdown_streaming_budget,
markdown_on_error, markdown_streaming_on_error, markdown_etag,
markdown_etag_policy, markdown_conditional_requests, markdown_on_wildcard,
markdown_trust_forwarded_headers, markdown_forwarded_headers,
markdown_large_body_threshold, markdown_streaming_engine,
markdown_memory_budget) are excluded — they emit
`NGX_CONF_ERROR` with a migration hint and execute no behavior.

### Core Conversion Directives

| Directive | Default | Context | Notes |
|-----------|---------|---------|-------|
| `markdown_filter` | off | http, server, location | on\|off\|$variable |
| `markdown_accept` | strict | http, server, location | strict\|wildcard\|force (Config V2) |
| `markdown_flavor` | commonmark | http, server, location | commonmark\|gfm; mdx/org-mode are rejected in v0.9.1 |
| `markdown_token_estimate` | off | http, server, location | on\|off |
| `markdown_front_matter` | off | http, server, location | on\|off |
| `markdown_buffer_chunked` | on | http, server, location | on\|off |
| `markdown_content_types` | text/html | http, server, location | positive allowlist |
| `markdown_stream_types` | (none) | http, server, location | exclusion list |

### Limits (Config V2 unified block)

| Directive | Default | Context | Notes |
|-----------|---------|---------|-------|
| `markdown_limits` | memory=10m timeout=5s streaming_buffer=2m max_inflight=64 | http, server, location | Per-key inheritance |

Individual resolved defaults from merge logic:

| Key | Resolved Default | Source |
|-----|-----------------|--------|
| memory | 10 MiB (10×1024×1024) | `ngx_conf_merge_size_value(max_size, 10*1024*1024)` |
| timeout | 5000 ms | `ngx_conf_merge_msec_value(timeout, 5000)` |
| streaming_buffer | 2 MiB | `NGX_HTTP_MARKDOWN_STREAM_BUDGET_DEFAULT` |
| max_inflight | 64 | `NGX_HTTP_MARKDOWN_MAX_INFLIGHT_DEFAULT` |

### Cache Validation (Config V2)

| Directive | Default | Context | Notes |
|-----------|---------|---------|-------|
| `markdown_cache_validation` | ims_only | http, server, location | off\|ims_only\|full |

Maps to two struct fields:
- `policy.generate_etag` (1=on for full, 0 for ims_only/off)
- `policy.conditional_requests` (FULL_SUPPORT=0, IF_MODIFIED_SINCE=1, DISABLED=2)

### Streaming (Config V2)

| Directive | Default | Context | Notes |
|-----------|---------|---------|-------|
| `markdown_streaming` | auto | http, server, location | off\|auto\|force; sole processing-path selector |
| `markdown_stream_threshold` | 1m | http, server, location | Minimum size for streaming candidacy |
| `markdown_stream_precommit_buffer` | 256k | http, server, location | Replay buffer size |
| `markdown_stream_flush_min` | 16k | http, server, location | Min batch before flush |
| `markdown_stream_excluded_types` | (none) | http, server, location | Additive to built-in exclusions |
| `markdown_streaming_shadow` | off | http, server, location | Shadow mode |
| `markdown_streaming_zero_copy` | off | http, server, location | Opt-in Rust-owned output buffers |

### Error Policy (Config V2)

| Directive | Default | Context | Notes |
|-----------|---------|---------|-------|
| `markdown_error_policy` | pass | http, server, location | pass\|fail_closed\|status \<code\> |

### Auth

| Directive | Default | Context | Notes |
|-----------|---------|---------|-------|
| `markdown_auth_policy` | allow | http, server, location | allow\|deny |
| `markdown_auth_cookies` | (none) | http, server, location | Pattern list |

### Trusted Proxies (Config V2, CIDR trust model)

| Directive | Default | Context | Notes |
|-----------|---------|---------|-------|
| `markdown_trusted_proxies` | (empty = no trust) | http only | CIDR list \| off |

### Observability / Operations

The implemented OTel tracing pair is experimental. Duplicate or unimplemented
OTel controls are reject-only and therefore are not active profile fields.

| Directive | Default | Context | Notes |
|-----------|---------|---------|-------|
| `markdown_log_verbosity` | info | http, server, location | error\|warn\|info\|debug |
| `markdown_metrics` | (location handler) | location | Enables metrics endpoint |
| `markdown_metrics_format` | auto | http, server, location | auto\|prometheus |
| `markdown_metrics_per_path` | off | http, server, location | on\|off |
| `markdown_metrics_per_path_cardinality` | 100 | http | Global |
| `markdown_metrics_shm_size` | 8×pagesize | http | Global |
| `markdown_diagnostics` | off | http, server, location | on\|off |
| `markdown_diagnostics_allow` | (loopback only) | http, server, location | CIDR |
| `markdown_otel` | off | http, server, location | on\|off |
| `markdown_otel_endpoint` | (empty) | http, server, location | Internal URI |

### Parsing / Decompression

| Directive | Default | Context | Notes |
|-----------|---------|---------|-------|
| `markdown_parse_timeout` | 30s | http, server, location | Parser deadline |
| `markdown_parser_budget` | 64m | http, server, location | Parser memory cap |
| `markdown_decompress_max_size` | (same as memory limit) | http, server, location | Decompressed output cap |
| `markdown_auto_decompress` | on | http, server, location | on\|off |

Reject-only OTel names (not active profile fields):
`markdown_otel_tracing`, `markdown_otel_metrics`,
`markdown_otel_service_name`, `markdown_otel_span_buffer_size`, and
`markdown_otel_export_timeout`.

### Pruning

| Directive | Default | Context | Notes |
|-----------|---------|---------|-------|
| `markdown_prune_noise` | on | http, server, location | on\|off |
| `markdown_prune_selectors` | built-in (nav footer aside) | http, server, location | Tag names |
| `markdown_prune_protection_selectors` | (empty) | http, server, location | Protected tags |

### Dynamic Config

| Directive | Default | Context | Notes |
|-----------|---------|---------|-------|
| `markdown_dynamic_config` | off | http, server, location | on\|off |
| `markdown_dynamic_config_path` | (none) | http, server, location | Path |
| `markdown_dynconf_dry_run` | off | http, server, location | on\|off |

### LLM / Token Estimation

| Directive | Default | Context | Notes |
|-----------|---------|---------|-------|
| `markdown_llm_provider` | default | http, server, location | Provider enum |
| `markdown_chars_per_token` | 0 (use provider default) | http, server, location | Fixed-point ×10 |

---

## 2. Config Struct Fields — Profile-Relevant Subset

Source: `ngx_http_markdown_conf_t` in `ngx_http_markdown_filter_module.h`.

The `ngx_http_markdown_profile_defaults_t` struct (already defined in the
header) captures the profile-relevant fields:

| Struct Field | Directive | Profile-Relevant | Rationale |
|--------------|-----------|:---:|-----------|
| `accept_policy` | markdown_accept | ✓ | Core negotiation behavior differs per profile |
| `policy.conditional_requests` | markdown_cache_validation | ✓ | Cache-validation mode |
| `policy.generate_etag` | markdown_cache_validation | ✓ | ETag generation (tied to cache_validation) |
| `stream.policy` | markdown_streaming | ✓ | Processing-path selection |
| `max_size` / limits memory | markdown_limits memory= | ✓ | Resource cap |
| `timeout` / limits timeout | markdown_limits timeout= | ✓ | Resource timeout |
| `stream.budget` / limits streaming_buffer | markdown_limits streaming_buffer= | ✓ | Streaming buffer |
| `max_inflight` | markdown_limits max_inflight= | ✓ | Concurrency cap |
| `on_error` | markdown_error_policy | ✓ | Error behavior |
| `policy.auth_policy` | markdown_auth_policy | ✓ | Auth stance |
| `flavor` | markdown_flavor | ✓ | Output flavor |
| `ops.diagnostics_enabled` | markdown_diagnostics | ✓ | Operational visibility |

Fields **NOT** profile-relevant (remain at built-in defaults regardless of
profile):

- `enabled` / `enabled_source` — per-location toggle, not a "preset"
- `token_estimate`, `front_matter` — output decoration, operator choice
- `buffer_chunked` — transport detail
- `stream_types`, `content_types` — site-specific allowlists
- `trusted_proxies` — security boundary, always explicit (CIDR trust model)
- `metrics_*`, `otel_*` — observability plumbing
- `prune_*` — content surgery, site-specific
- `dynconf_*` — operational plumbing
- `llm_provider`, `chars_per_token` — estimation tuning
- `decompress.*`, `parse_timeout`, `parser_budget` — hard safety caps
- `log_verbosity` — debugging

---

## 3. Current Merge / Effective-Config Logic

### 3.1 Config Merge (parse time)

Implemented in `ngx_http_markdown_merge_conf()` at
`ngx_http_markdown_config_core_impl.h`:

```
merge_conf(cf, parent, child):
  1. merge_enabled(child, parent)           — enabled/complex inheritance
  2. Save explicit-set flags (max_size_set, stream_threshold_set, etc.)
  3. merge_core_values(child, parent)       — standard ngx_conf_merge_*
     a. merge_core_base_values  — max_size, timeout, on_error, flavor,
                                  accept_policy, auth, generate_etag,
                                  conditional_requests, buffer_chunked,
                                  decompress fields
     b. merge_core_ops_values   — trust_forwarded, metrics, otel
     c. merge_core_ptr_values   — arrays, thresholds, max_inflight
  4. merge_stream_values(child, parent)     — NGX_MD_MERGE_STREAM macro
  5. Set *_explicit flags from saved pre-merge state
  6. merge_advanced_values(child, parent)   — prune, memory_budget, dynconf
  7. apply_memory_budget_override           — budget → max_size when not explicit
  8. Resolve decompress.max_size default    — inherits max_size if still unset
  9. Validate decompress.max_size ≠ 0 when auto_decompress
  10. Validate streaming vs cache_validation conflicts (spec 49)
  11. Log merged configuration
```

Merge semantics: standard NGINX `ngx_conf_merge_*` — child value wins if
set (≠ UNSET sentinel); otherwise parent value; otherwise compile-time
default.

### 3.2 Effective-Config View (request time)

Implemented in `ngx_http_markdown_build_effective_conf()` at
`ngx_http_markdown_dynconf_impl.h`:

```
build_effective_conf(eff, snapshot, conf):
  if snapshot != NULL && snapshot.valid:
    read dynconf-mutable fields from snapshot
  else:
    read dynconf-mutable fields from live conf
```

Dynconf-mutable fields (the only fields in the effective-conf view today):
- `enabled`, `enabled_source`
- `prune_noise`
- `log_verbosity`
- `memory_budget`
- `streaming_budget` (when streaming enabled)

### 3.3 Profile Integration Point (to implement)

The planned merge order is:

```
effective = builtin_defaults
if profile != NONE:
    effective.apply(profile.defaults())     ← profile overrides builtins
effective.apply(explicit_directives)        ← explicit overrides profile
```

This maps to using `ngx_http_markdown_profile_defaults_t` values as the
"default" argument in `ngx_conf_merge_*` calls, replacing the hard-coded
compile-time defaults when a profile is active.

---

## 4. Profile-to-Field Override Mapping

### 4.1 Forced Fields (explicit conflict → error)

| Profile | Forced Field | Forced Value | Rationale |
|---------|-------------|:---:|-----------|
| `strict_cache` | streaming | off | Full buffering required for ETag generation |
| `streaming_first` | cache_validation | off | Streaming cannot generate transformed ETag |
| `streaming_first` | streaming | force | Profile purpose is streaming-first |

`balanced` has **no forced fields** — all its defaults can be overridden.

### 4.2 All Profile Defaults

| Field | `strict_cache` | `balanced` | `streaming_first` | Built-in (no profile) |
|-------|:-----------:|:--------:|:---------------:|:------------------:|
| accept_policy | strict | strict | wildcard | strict |
| cache_validation | full | ims_only | off | ims_only |
| generate_etag | on | off | off | off |
| streaming | off | auto | force | auto |
| limits memory | 8m | 8m | 8m | 10m |
| limits timeout | 2s | 2s | 2s | 5s |
| limits streaming_buffer | — (off) | 256k | 256k | 2m |
| limits max_inflight | 64 | 64 | 64 | 64 |
| error_policy | pass | pass | pass | pass |
| auth_policy | allow | allow | allow | allow |
| flavor | commonmark | commonmark | commonmark | commonmark |
| diagnostics | off | off | off | off |

---

## 5. Profile Expansion Mapping Table

### `strict_cache`

Optimized for CDN / caching proxy: full conditional request support,
no streaming. Operators that need strong ETag-based cache invalidation.

| Config V2 Directive | Effective Value | Override Behavior |
|--------------------|----|-------|
| `markdown_accept` | strict | Overridable |
| `markdown_cache_validation` | full | Overridable |
| `markdown_streaming` | **off (FORCED)** | Conflict → error |
| `markdown_limits memory=` | 8m | Overridable |
| `markdown_limits timeout=` | 2s | Overridable |
| `markdown_limits streaming_buffer=` | (N/A) | N/A (streaming off) |
| `markdown_limits max_inflight=` | 64 | Overridable |
| `markdown_error_policy` | pass | Overridable |
| `markdown_auth_policy` | allow | Overridable |
| `markdown_flavor` | commonmark | Overridable |
| `markdown_diagnostics` | off | Overridable |

### `balanced`

Recommended default: strict negotiation, IMS-only caching (no ETag
overhead), streaming on auto.

| Config V2 Directive | Effective Value | Override Behavior |
|--------------------|----|-------|
| `markdown_accept` | strict | Overridable |
| `markdown_cache_validation` | ims_only | Overridable |
| `markdown_streaming` | auto | Overridable |
| `markdown_limits memory=` | 8m | Overridable |
| `markdown_limits timeout=` | 2s | Overridable |
| `markdown_limits streaming_buffer=` | 256k | Overridable |
| `markdown_limits max_inflight=` | 64 | Overridable |
| `markdown_error_policy` | pass | Overridable |
| `markdown_auth_policy` | allow | Overridable |
| `markdown_flavor` | commonmark | Overridable |
| `markdown_diagnostics` | off | Overridable |

### `streaming_first`

Optimized for AI agent workloads with large documents: aggressive
streaming, no caching overhead, wildcard Accept.

| Config V2 Directive | Effective Value | Override Behavior |
|--------------------|----|-------|
| `markdown_accept` | wildcard | Overridable |
| `markdown_cache_validation` | **off (FORCED)** | Conflict → error |
| `markdown_streaming` | **force (FORCED)** | Conflict → error |
| `markdown_limits memory=` | 8m | Overridable |
| `markdown_limits timeout=` | 2s | Overridable |
| `markdown_limits streaming_buffer=` | 256k | Overridable |
| `markdown_limits max_inflight=` | 64 | Overridable |
| `markdown_error_policy` | pass | Overridable |
| `markdown_auth_policy` | allow | Overridable |
| `markdown_flavor` | commonmark | Overridable |
| `markdown_diagnostics` | off | Overridable |

---

## 6. Conflict Rules Summary

| Conflict | Level | Condition |
|----------|-------|-----------|
| Forced field override | **error** | Explicit directive sets value ≠ profile's forced value |
| `cache_validation full` + `streaming force` | **error** | Mutually exclusive (existing streaming/conditional conflict check) |
| `cache_validation full` + `streaming auto` | **warning** | Runtime blocks streaming (existing streaming/conditional conflict check) |
| Duplicate `markdown_profile` | **error** | Same context has >1 profile directive |
| Unknown profile name | **error** | Directive parse fails |

---

## 7. Implementation Notes

- The `ngx_http_markdown_profile_defaults_t` struct is already defined in
  the header (`ngx_http_markdown_filter_module.h`). Profile constants
  (`NGX_HTTP_MARKDOWN_PROFILE_*`) and the `conf.profile.name` / `.set`
  fields are also already present.
- The `merge_conf` implementation needs to resolve profile defaults BEFORE
  the standard `ngx_conf_merge_*` calls, substituting profile values as
  the "default" argument when a profile is active.
- Forced-field conflict detection belongs in `merge_conf` (or a post-merge
  validation step) and in the Rust `detect_conflicts` FFI for dynconf
  dry-run.
- The effective-conf view (`ngx_http_markdown_effective_conf_t`) does not
  need profile awareness — profiles are resolved entirely at config parse
  time and cached in the merged `ngx_http_markdown_conf_t`.
