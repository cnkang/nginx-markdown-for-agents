# v0.7.0 Deprecated Directives Grace Period Verification

| Field | Value |
|-------|-------|
| Task | F02.3 — Deprecated directive grace period verification |
| Date | 2026-05-17 |
| Status | VERIFIED |
| Spec | 27-v070_prod |

---

## Summary

v0.7.0 has **two user-facing deprecated configuration directives** from 0.6.x
that remain fully functional with a grace period. No directive that worked in
0.6.x silently fails or causes a startup error in 0.7.0.

---

## Deprecated Directives

### 1. `markdown_max_size`

| Attribute | Value |
|-----------|-------|
| Deprecated since | 0.6.0 |
| Superseded by | `markdown_memory_budget` |
| Planned removal | 0.8.0 |
| Status in 0.7.0 | **Fully functional** (grace period) |

**Evidence:**
- Directive is present in `ngx_http_markdown_config_directives_impl.h` commands
  array with full `NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF`
  context support.
- Eligibility check in `ngx_http_markdown_eligibility.c` still reads
  `conf->max_size` for size-based skip decisions.
- Merge logic respects explicit `markdown_max_size` with highest priority over
  `markdown_memory_budget` (explicit path-specific wins over unified).
- Dynconf snapshot in `ngx_http_markdown_dynconf_snapshot.c` still reports
  `markdown_max_size` in the active configuration snapshot.
- Documentation in `docs/guides/CONFIGURATION.md` notes the deprecation and
  states it is "still accepted with a deprecation warning at info verbosity."

**Grace period behavior:**
- Parses without error.
- Functions identically to pre-0.6.0 behavior.
- Priority chain: explicit `markdown_max_size` > `markdown_memory_budget` >
  compiled-in default.

---

### 2. `markdown_streaming_budget`

| Attribute | Value |
|-----------|-------|
| Deprecated since | 0.6.0 |
| Superseded by | `markdown_memory_budget` |
| Planned removal | 0.8.0 |
| Status in 0.7.0 | **Fully functional** (grace period, streaming-enabled builds) |

**Evidence:**
- Directive is present in `ngx_http_markdown_config_directives_impl.h` commands
  array under `#ifdef MARKDOWN_STREAMING_ENABLED`.
- Maps to `conf->streaming.budget` field via `ngx_conf_set_size_slot`.
- Documentation in `docs/guides/streaming-default-migration.md` explicitly
  states these directives "continue to work" with documented priority chain.

**Grace period behavior:**
- Parses without error in streaming-enabled builds.
- Functions identically to pre-0.6.0 behavior.
- Priority chain: explicit `markdown_streaming_budget` > `markdown_memory_budget`
  > compiled-in default.

---

## Internal Deprecations (Non-User-Facing)

### Legacy Reason Code String Literals

The C-side reason code string literals in `ngx_http_markdown_reason.c` are
marked as "DEPRECATED (v0.7.0)" in code comments. These are **internal
implementation details**, not user-facing configuration directives. The single
source of truth for reason codes has moved to the Rust enum in
`components/rust-converter/src/decision/reason_code.rs`, with FFI accessors
providing the canonical values.

This deprecation is invisible to operators — it affects only internal code
organization and does not change any user-facing behavior or configuration
syntax.

---

## Verification Conclusion

| Check | Result |
|-------|--------|
| Any 0.6.x directive causes startup error in 0.7.0? | **NO** |
| Any 0.6.x directive silently ignored in 0.7.0? | **NO** |
| Deprecated directives still parse correctly? | **YES** |
| Deprecated directives still function correctly? | **YES** |
| Deprecation documented with migration path? | **YES** |
| Planned removal version documented? | **YES** (0.8.0) |

**Conclusion:** All 0.6.x configuration directives remain fully supported in
v0.7.0. The two deprecated directives (`markdown_max_size`,
`markdown_streaming_budget`) operate in a grace period with documented migration
paths and a planned removal in 0.8.0. No operator action is required for the
0.6.x → 0.7.0 upgrade.
