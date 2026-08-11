# Migration Guide: 0.9.0 → 0.9.1

> **Historical guide.** This guide covers the 0.9.0 → 0.9.1 migration
> only. The 0.9.2 release removed the OTel directives that the migration
> table below recommends (`markdown_otel`, `markdown_otel_endpoint`). If
> you upgrade directly to 0.9.2, read
> [MIGRATION-0.9.2.md](MIGRATION-0.9.2.md) first.

## Overview

**0.9.0 → 0.9.1 is a breaking release.** The 0.9.0 release intended to be the
last breaking release before v1.0. The freeze was deliberately extended through
v0.9.1 because v1.0 had not shipped, adoption remained limited, and the final
toolchain, dependency, configuration, and ABI audit found cleanup worth
completing before the long-lived contract begins.

If you are running 0.9.0 in production, you must follow this guide before
upgrading. There is **no backward-compatible mode** — 0.9.1 rejects old
directives and configurations at `nginx -t` time with actionable migration
hints.

**Key changes at a glance:**

- Rust toolchain baseline raised from 1.91 to 1.97
- `markdown_streaming_engine` removed → `markdown_streaming off|auto|force`
- Non-semantic flavors (`mdx`, `org-mode`) removed
- FFI ABI reset to version 1
- Incomplete OTel controls now reject-only
- `markdown_trusted_proxies` now http-context only

**Upgrade path:** read this guide top-to-bottom, update your configuration,
run `nginx -t`, fix any errors using the mapping tables below, then reload.

---

## Breaking Changes Summary

### 1. Rust Baseline Raised from 1.91 to 1.97

All first-party crates now declare MSRV 1.97. Repository/CI/release builds use
exact Rust 1.97.0.

**Impact:**

- **Source builders** must update their toolchain:
  ```bash
  rustup toolchain install 1.97.0
  rustup default 1.97.0
  ```
- **Prebuilt module users** do not need Rust. Runtime compatibility remains
  governed by the published NGINX, OS/libc, architecture, and exact
  dynamic-module compatibility matrix.

**Automated detection:**

```bash
rustc --version
# Must show 1.97.0 or newer. If older, build will fail with:
# error: rustc version is older than MSRV 1.97.0
```

---

### 2. `markdown_streaming_engine` Removed → `markdown_streaming`

The active contract removed the `markdown_streaming_engine` directive.
Use `markdown_streaming off|auto|force` instead.

**Migration mapping:**

| 0.9.0 Directive | 0.9.1 Replacement |
|-----------------|-------------------|
| `markdown_streaming_engine off` | `markdown_streaming off` |
| `markdown_streaming_engine auto` | `markdown_streaming auto` |
| `markdown_streaming_engine on` | `markdown_streaming force` |

The old directive is a reject-only stub:

```nginx
nginx: [emerg] "markdown_streaming_engine" directive has been removed in 0.9.1;
use "markdown_streaming off|auto|force" instead
(see docs/guides/MIGRATION-0.9.1.md)
```

**Helm value migration:**

| 0.9.0 Helm Value | 0.9.1 Helm Value |
|-------------------|------------------|
| `markdown.streaming.engine` | `markdown.streaming.mode` |

**Diagnostics key migration:**

| 0.9.0 Key | 0.9.1 Key |
|-----------|-----------|
| `streaming_engine` | `streaming_config.policy` |
| _(none)_ | `streaming_config.policy_source` |

---

### 3. Non-Semantic Flavors Removed

`markdown_flavor mdx` and `markdown_flavor org-mode` now fail `nginx -t`
with a migration hint. They were experimental selectors that never produced
distinct output formats.

```nginx
nginx: [emerg] "markdown_flavor mdx" is not supported in 0.9.1;
use "markdown_flavor commonmark" or "markdown_flavor gfm" instead
(see docs/guides/MIGRATION-0.9.1.md)
```

**Migration:**

| 0.9.0 Flavor | 0.9.1 Replacement |
|--------------|-------------------|
| `mdx` | `commonmark` or `gfm` |
| `org-mode` | `commonmark` or `gfm` |

---

### 4. FFI ABI Reset to Version 1

The internal coordinated boundary removes:

- The duplicate streaming engine byte
- The unimplemented flavor discriminants
- The reserved `FFIConditionalResult` shape and its superseded conditional helper
- The superseded simple base-URL builder
- 15 exports with no production C consumer (dead decision/error-policy wrappers,
  standalone URL checks, Rust diagnostics accessors, redundant init helpers,
  NULL-only constructors, duplicate streaming finish/free/reason paths)

NGINX now validates `markdown_abi_version()` during preconfiguration and
refuses startup if the linked Rust archive and generated C header disagree.

**Impact:** The converter ABI is an internal boundary of the bundled module,
not a standalone third-party SDK contract. Source builders must rebuild with
the 0.9.1 Rust archive. Prebuilt module users stay unaffected (the bundled
module is already matched).

---

### 5. Incomplete OTel Controls Now Reject-Only

The following directives are reject-only because their duplicate or
unimplemented values had no distinct production effect:

- `markdown_otel_tracing`
- `markdown_otel_metrics`
- `markdown_otel_service_name`
- `markdown_otel_span_buffer_size`
- `markdown_otel_export_timeout`

**Migration:** Use the experimental tracing surface instead:

| 0.9.0 Directive | 0.9.1 Replacement |
|-----------------|-------------------|
| `markdown_otel_tracing on` | `markdown_otel on` + `markdown_otel_endpoint <url>` |
| `markdown_otel_metrics on` | `markdown_otel on` + `markdown_otel_endpoint <url>` |
| `markdown_otel_service_name <name>` | _(not yet supported — omit for now)_ |
| `markdown_otel_span_buffer_size <N>` | _(not yet supported — omit for now)_ |
| `markdown_otel_export_timeout <T>` | _(not yet supported — omit for now)_ |

---

### 6. `markdown_trusted_proxies` Now Main-Only

`markdown_trusted_proxies` is now accepted only in the `http` context.
`server` and `location` uses fail `nginx -t` instead of creating a Rust-owned
handle without safe child inheritance.

```nginx
nginx: [emerg] "markdown_trusted_proxies" directive is only valid in the
http context, not in server or location
(see docs/guides/MIGRATION-0.9.1.md)
```

**Migration:** Move `markdown_trusted_proxies` to the `http {}` block:

**Before (0.9.0):**

```nginx
server {
    markdown_trusted_proxies 10.0.0.0/8;
}
```

**After (0.9.1):**

```nginx
http {
    markdown_trusted_proxies 10.0.0.0/8;
}
```

---

## Automated Migration

`nginx -t` provides actionable hints for all breaking changes. Run it first
and fix each error:

```bash
sudo nginx -t
```

Each reject-only stub emits a specific error message naming the replacement
directive and linking to this migration guide.

---

## Manual Migration Steps

1. **Update Rust toolchain** (source builds only):
   ```bash
   rustup toolchain install 1.97.0
   rustup default 1.97.0
   ```

2. **Replace `markdown_streaming_engine`:**
   - `off` → `markdown_streaming off`
   - `auto` → `markdown_streaming auto`
   - `on` → `markdown_streaming force`

3. **Replace non-semantic flavors:**
   - `markdown_flavor mdx` → `markdown_flavor commonmark` or `gfm`
   - `markdown_flavor org-mode` → `markdown_flavor commonmark` or `gfm`

4. **Move `markdown_trusted_proxies` to `http` context** if it appears in
   `server` or `location` blocks.

5. **Replace incomplete OTel directives** with `markdown_otel` and
   `markdown_otel_endpoint`.

6. **Rebuild from source** (if applicable) to match the new FFI ABI.

7. **Validate configuration:**
   ```bash
   sudo nginx -t
   ```

8. **Reload:**
   ```bash
   sudo nginx -s reload
   ```

---

## Prometheus Failure and Latency Contracts

0.9.1 corrects the Prometheus failure and latency contracts before the v1.0
freeze:

- `nginx_markdown_failures_total` now uses truthful bounded categories:
  `conversion_error`, `resource_limit`, and `system_error`.
- The misleading gauge-like `nginx_markdown_conversion_duration_seconds{le=...}`
  the cumulative counter family replaces it
  `nginx_markdown_conversion_latency_bucket_total{le=...}`.

**Dashboard migration:**

| 0.9.0 Query | 0.9.1 Query |
|-------------|-------------|
| `nginx_markdown_conversion_duration_seconds{le="0.1"}` | `nginx_markdown_conversion_latency_bucket_total{le="0.1"}` |
| `nginx_markdown_failures_total{reason="ffi_panic"}` | `nginx_markdown_failures_total{reason="system_error"}` |

---

## Rollback Plan: 0.9.1 → 0.9.0

If 0.9.1 causes issues in production:

1. Stop NGINX gracefully: `sudo nginx -s quit`
2. Restore the 0.9.0 module binary and configuration from backup
3. Validate: `sudo nginx -t`
4. Start: `sudo nginx`

**Important:** 0.9.1 and 0.9.0 module binaries are not interchangeable. The
Rust converter and C module must be a matched pair from the same version.

---

## Previous Versions

| From | To | Guide |
|------|----|-------|
| 0.8.x | 0.9.0 | [docs/guides/MIGRATION-0.9.md](MIGRATION-0.9.md) |
| 0.7.x | 0.8.0 | [docs/guides/MIGRATION-0.8.md](MIGRATION-0.8.md) |

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-08 | Kang | Marked guide historical; OTel directives removed in 0.9.2 |
| 0.9.1 | 2026-07-29 | Kang | Initial migration guide for 0.9.0 → 0.9.1 |
