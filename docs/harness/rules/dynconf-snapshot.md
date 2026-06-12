---
domain: dynconf-snapshot
rules: [34, 35, 45]
paths:
  - "components/nginx-module/src/dynconf/**"
  - "components/nginx-module/src/config/**"
  - "components/nginx-module/src/ngx_http_markdown_filter_module.h"
---

## Dynconf Snapshot Isolation

### 34. Request-path code must read dynconf-mutable fields through effective_conf, not live conf
Historical issues: P0 request-level consistency gap (snapshot bound but not consumed);
P0 snapshot race (active_snapshot read twice in header_filter).

Required:
- In request-path code (body filter, conversion, logging, budget, streaming),
  dynconf-mutable fields (`enabled`, `enabled_source`, `prune_noise`,
  `log_verbosity`, `memory_budget`, `streaming_budget`) must be read through
  `ctx->effective_conf` via the `ngx_http_markdown_effective_*()` helpers,
  not directly from `conf->`.
- Direct `conf->` reads of mutable fields are only allowed in:
  - Configuration/initialization/merge code (`config_core_impl.h`,
    `config_handlers_impl.h`)
  - Dynconf snapshot construction/apply helpers (`dynconf_impl.h`)
  - Fallback paths where `eff` is NULL (early header filter, allocation
    failure) — these must be documented with a comment explaining why
    `eff` is unavailable.
- When adding a new dynconf-mutable field, the following must be updated in
  the same changeset:
  1. `ngx_http_markdown_dynconf_snapshot_t` — add the field
  2. `ngx_http_markdown_effective_conf_t` — add the field
  3. `ngx_http_markdown_dynconf_snapshot_from_conf()` — copy the field
  4. `ngx_http_markdown_dynconf_apply_snapshot()` — apply the field
  5. `ngx_http_markdown_build_effective_conf()` — populate from snapshot or conf
  6. A new `ngx_http_markdown_effective_<field>()` helper function
  7. All request-path reads of the field — switch to the helper
  8. At least one regression test proving snapshot consistency

Snapshot race elimination (v0.6.2):
- In `ngx_http_markdown_header_filter()`, the global
  `ngx_http_markdown_dynconf_watcher.active_snapshot` must be read exactly
  once, at function entry, into a function-lifetime `snap_copy` variable.
  `early_eff` is derived from that `snap_copy` once via
  `ngx_http_markdown_build_effective_conf()`, also at function entry.
- Both `snap_copy` and `early_eff` must have function-lifetime scope (not
  block scope), so they remain valid through ctx binding.
- When binding the snapshot and effective view into the request context,
  copy directly from the function-level variables:
  `*ctx->dynconf_snapshot = snap_copy` and `*ctx->effective_conf = early_eff`.
  Do NOT re-read `ngx_http_markdown_dynconf_watcher.active_snapshot` or
  re-invoke `ngx_http_markdown_build_effective_conf()` — either creates a
  race window where a concurrent timer reload can swap the global snapshot
  between the initial capture and the ctx bind, causing the request to see
  inconsistent configuration.
- The binding must be performed by
  `ngx_http_markdown_bind_request_snapshot()`, which encapsulates the
  allocation + copy + degraded-mode logging in one place.
- `ngx_http_markdown_handle_ctx_alloc_failure()` must accept an `eff`
  parameter and pass it to `log_decision_with_category()`.  Passing NULL
  causes the log path to fall back to live conf, violating the effective-conf
  model.  The caller in `header_filter` passes `&early_eff`.

dynconf_path_configured lifecycle (v0.6.2):
- The `dynconf_path_configured` flag must live in
  `ngx_http_markdown_main_conf_t` (config-parse scope), not as a
  file-scope static variable.  A file-scope static survives across
  NGINX reloads, leaving stale state that prevents re-configuration.
- `ngx_http_markdown_set_dynconf_path()` reads and writes the flag through
  `ngx_http_conf_get_module_main_conf()`, providing per-reload isolation.

Verification:
- `tools/harness/detect_live_conf_reads.sh components/nginx-module/src/`
- `make harness-security-checks`

---

### 35. Dynconf snapshot isolation and reload retry contract

Required:
- When a location has `dynconf_enabled=0`, `header_filter` must pass NULL
  snapshot to `ngx_http_markdown_build_effective_conf()`, and
  `ngx_http_markdown_bind_request_snapshot()` must not allocate
  `ctx->dynconf_snapshot`.  The global snapshot must never influence
  non-dynconf locations.
- `ngx_http_markdown_dynconf_watcher_t` must maintain separate
  `last_mtime` (observed) and `applied_mtime` (confirmed after
  successful reload).  `applied_mtime` must be updated only after
  reload returns `RELOAD_APPLIED` or `RELOAD_NO_CHANGE`.
- When `last_mtime != applied_mtime`, the timer handler must retry
  the reload on the next poll cycle, regardless of whether
  `dynconf_check()` detects a new mtime change.
- Unknown dynconf keys must cause `NGX_ERROR` (atomic reload
  rejection), not `NGX_DECLINED` (silent ignore).  The entire file
  is rejected on any unrecognized key.
- `dynconf_start` must parse and apply the existing dynconf file
  immediately at startup if it exists.  If the initial parse fails,
  `applied_mtime` must be set to 0 so the timer retries on the
  next poll cycle.  This ensures runtime overrides persist across
  NGINX restart/reload.
- `harness-check-full` must include `harness-security-checks`.

Verification:
- `tools/harness/detect_live_conf_reads.sh` — checks dynconf_enabled
  gate on build_effective_conf, applied_mtime guard, and retry logic.
- `make test-nginx-unit` — effective_conf_test includes
  test_dynconf_snapshot_not_consumed_when_dynconf_disabled;
  dynconf_impl_test includes
  test_start_applies_existing_file_on_startup and
  test_start_invalid_file_leaves_applied_mtime_zero.
- `make harness-check-full` — now includes harness-security-checks.

---

### 45. Effective configuration NULL-safe access and cross-TU visibility
Historical issues: d91dd419, 7e1227a9, 31e017d9, 327bfe99, 4b97d0a7.

Required:
- When request-path code reads `ctx->effective_conf` fields (for example
  `memory_budget`, `streaming_budget`), the code must handle the case where
  `effective_conf` is NULL.  This can occur in early header_filter paths
  before snapshot binding, or after allocation failure.  A NULL `effective_conf`
  must fall back to `conf->` with an explicit comment documenting why `eff` is
  unavailable, consistent with Rule 34's fallback allowance.
- When a configuration field is consumed across multiple translation units
  (for example `effective_body_buffer_limit` used in both `filter_module.c`
  and `streaming_impl.h`), the field declaration and accessor must be in a
  shared header (`filter_module.h`), not in a source file.  A field declared
  `static` in one `.c` file is invisible to other translation units, causing
  either link errors or silent use of stale/zero defaults.
- When using `NGX_CONF_UNSET_SIZE` as a sentinel for "use default", ensure
  the sentinel value `(size_t)-1` is used consistently.  Do not mix
  `NGX_CONF_UNSET_SIZE` (which is `(size_t)-1` for size fields) with literal
  `(size_t)-1` in some places and `NGX_CONF_UNSET_SIZE` in others — pick one
  form and use it uniformly within the effective_conf helper chain.
- The eligibility check for streaming must guard against `effective_conf`
  being NULL before dereferencing its `memory_budget` or `streaming_budget`
  fields.  When `eff` is NULL, the eligibility function must return the
  non-streaming (full-buffer) path, not dereference NULL.

Verification:
- `grep -rn 'effective_conf\|eff->' components/nginx-module/src/ | grep -v '/\*'`
  — verify no effective_conf dereference without NULL guard in paths that
  can be called before snapshot binding.
- `grep -rn 'effective_body_buffer_limit' components/nginx-module/src/`
  — verify declaration is in a shared header, not a source file.
- `make test-nginx-unit` — eligibility tests cover non-NULL eff memory_budget
  path and NULL-eff fallback.
