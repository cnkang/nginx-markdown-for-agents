---
domain: dynconf-snapshot
rules: [34, 35, 45, 71]
status: partially-retired
paths:
  - "components/nginx-module/src/ngx_http_markdown_effective_conf_impl.h"
  - "components/nginx-module/src/ngx_http_markdown_config_core_impl.h"
  - "components/nginx-module/src/ngx_http_markdown_filter_module.h"
---

## Static Configuration Binding (formerly Dynconf Snapshot Isolation)

> **Convergence note (0.9.2).** The 0.9.2 convergence dropped the
> dynamic-configuration (runtime hot-reload) subsystem. The runtime snapshot,
> the reload timer, and the mtime-retry state no longer exist. Only the
> **static** per-level configuration model remains. The merge step computes
> each field once at configuration time. The module binds one `effective_conf`
> view at header-filter entry. Explicit static settings mark a block-mask that
> propagates down the configuration tree.
>
> This file keeps rule numbers **34/35/45/71** for traceability. Rules **34**
> and **35** described the retired runtime-reload behavior. Both rules now read
> **RETIRED** and hold only a historical record. Rules **45** and **71**
> describe the retained **static** binding and block-mask and stay **CURRENT**.

---

### 34. [RETIRED — historical] Request path read runtime-mutable fields through a bound snapshot

> **RETIRED in 0.9.2.** This rule governed the retired runtime hot-reload
> overlay, the snapshot that a reload timer could swap. No such overlay exists
> in the current release, so the snapshot-race and snapshot-consumption
> requirements below no longer apply. The static **Rule 45** replaces it: the
> module binds the `effective_conf` view once and reads merged fields through
> the `ngx_http_markdown_effective_*()` helpers. The paragraphs below stay only
> so the history reads clearly. Do not treat them as current requirements.

Historical record (pre-0.9.2, the module no longer enforces this):
- Request-path code once read runtime-mutable fields through a per-request
  snapshot that a reload timer could swap. The main historical hazards were a
  request-level consistency gap (the code bound a snapshot but never consumed
  it) and a race where the header filter read the global active snapshot twice.
- The historical mitigation captured the global snapshot exactly once at
  header-filter entry into a function-lifetime variable, derived the effective
  view from that single capture, and bound both into the request context
  without a second read of the global snapshot.
- Adding a runtime-mutable field once meant updating the snapshot struct, the
  apply helper, the build-effective-view helper, an accessor, every
  request-path read, and a snapshot-consistency test.

Current status: the current release drops the runtime snapshot, the reload
timer, and the retry state. Static Rule 45 replaces the request-path read
contract.

---

### 35. [RETIRED — historical] Runtime reload isolation and retry contract

> **RETIRED in 0.9.2.** This rule governed the retired runtime reload path: the
> per-location enable gate, the observed-versus-applied modification-time
> tracking, the atomic rejection of unknown keys, and the startup re-apply of
> an external file. The current release contains none of that machinery. The
> paragraphs below hold only a historical record.

Historical record (pre-0.9.2, the module no longer enforces this):
- A per-location enable flag once decided whether the global runtime view
  reached a location. Locations with the feature off saw no runtime view.
- The reload watcher once tracked an observed modification time apart from a
  confirmed-applied modification time. It advanced the confirmed value only
  after specific reload outcomes and retried on the next poll cycle while the
  two values differed.
- Unknown keys in the external file once triggered atomic rejection of the
  whole file rather than a silent skip. Startup once re-applied an existing
  external file so runtime overrides survived a restart.

Current status: the module now reads configuration once at load time through
the normal directive path. No external runtime file, reload watcher, or retry
state remains.

---

### 45. [CURRENT — static] Effective-configuration NULL-safe access and cross-TU visibility

> **CURRENT.** This rule describes the retained static binding. The module
> builds the `effective_conf` view once from the merged static configuration
> and binds it at header-filter entry. Nothing swaps the view at runtime.

Required:
- When request-path code reads `ctx->effective_conf` fields (for example the
  merged `markdown_limits`), the code must handle a NULL `effective_conf`. A
  NULL view can appear on early header-filter paths before the module binds the
  view, or after an allocation failure. A NULL `effective_conf` must fall back
  to `conf->` with an explicit comment that states why the view is missing.
- When a configuration field spans multiple translation units (for example a
  buffer-limit field that both `filter_module.c` and the streaming impl use),
  the field declaration and accessor must live in a shared header
  (`filter_module.h`) rather than a source file. A field with `static` linkage
  in one `.c` file stays invisible to other translation units. That invisible
  field causes either a link error or the silent use of a stale or zero
  default.
- When a helper uses `NGX_CONF_UNSET_SIZE` as the "use default" sentinel, the
  code must use that sentinel value uniformly. Do not mix `NGX_CONF_UNSET_SIZE`
  with a literal `(size_t)-1` across the effective-view helper chain. Pick one
  form and keep it uniform.
- The streaming eligibility check must guard `effective_conf` against NULL
  before it dereferences the merged limit fields. When the view is NULL, the
  eligibility function must return the non-streaming (full-buffer) path rather
  than dereference NULL.

Verification:
- `make test-nginx-unit` — the effective-conf tests cover the NULL fallback,
  early binding, allocation-failure behavior, and the non-NULL and NULL
  eligibility paths.

---

### 71. [CURRENT — static] Static explicit settings block overrides and propagate to child levels

> **CURRENT.** This rule describes the retained static block-mask. It states a
> configuration-merge invariant that stands on its own, apart from the retired
> runtime overlay.

Required:

- An explicit directive in static configuration marks its field in the
  block-mask. The mask propagates down the configuration tree. An explicit
  http-level setting therefore blocks the field in every child location,
  because locations inherit the explicit configuration of the http block.
- A field that static configuration leaves unset stays open at child levels, so
  a more specific location may set it explicitly.
- The merged `effective_conf` view carries the block-mask for diagnostics so
  operators can see which fields an explicit ancestor setting pinned.

Verification:

- `make test-nginx-unit` — the configuration tests assert that the block-mask
  propagates from an explicit http-level setting to child locations.
