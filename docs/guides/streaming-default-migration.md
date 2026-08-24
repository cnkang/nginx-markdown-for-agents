# Streaming Default & Noise Pruning Migration Guide

**Version**: 0.8.0
**Audience**: Operators upgrading from 0.5.x/0.6.x/0.7.x to 0.8.0

> **v0.9.1+ operators:** the 0.9.1 release removed `markdown_streaming_engine` and replaced it with `markdown_streaming`. If you are reading this guide for the streaming behavior changes, please replace `markdown_streaming_engine off;` with `markdown_streaming off;` in your configuration files. Only the historical sections below retain the obsolete `markdown_streaming_engine` directive for background. Current configuration examples use `markdown_streaming`.
>
> **v0.9.2 operators:** the 0.9.2 release also removed `markdown_stream_threshold` and `markdown_streaming_zero_copy`. The auto-route threshold is now fixed internally at 1 MiB and is not operator-configurable. The historical sections below that reference `markdown_stream_threshold` and `markdown_streaming_engine` remain for background only. They describe directives that no longer exist, and operators must not use them in current configurations.

## Overview

v0.6.0 introduced two default behavior changes. v0.8.0 removes the
v0.6.x compatibility bridge entirely:

| Default | 0.5.x | 0.6.0–0.7.x | 0.8.0 | Impact |
|---|---|---|---|---|
| `markdown_streaming_engine` | `off` (full-buffer) | `auto` (per-request selection) | `auto` (per-request selection) | Large/chunked responses use streaming by default |
| `markdown_prune_noise` | N/A (compile-time opt-in) | `on` (runtime, default-enabled) | `on` (runtime, default-enabled) | Noise regions (nav, footer, ads) removed by default |
| `markdown_streaming_auto_threshold` | N/A (new in 0.6.0) | Accepted (32k default) | **Removed** — `nginx -t` fails | Auto mode used the then-current threshold; see the 0.9.2 note below |
| `markdown_streaming_engine` `$variable` | Accepted | Accepted | **Removed** — `nginx -t` fails | Must use fixed `off`/`auto`/`force` in 0.9.2 (historical `on` value replaced by `force`) |

**Key guarantee**: An explicit `off` configuration produces identical behavior
to 0.5.x. The 0.9.2 policy values are `off`, `auto`, and `force`. The
historical `on` value is not an accepted 0.9.2 value (the module replaces it
with `force`), so the equivalence claim covers only `off`. Configurations using
`markdown_streaming_auto_threshold` or
`markdown_streaming_engine $variable` **need updating before you upgrade to 0.8.0**.
You must update these configurations before the upgrade. The removed directives fail `nginx -t`. Update them or the upgrade fails.

## Migration Paths

### Path A: Accept 0.8.0 Defaults (Recommended)

No configuration changes needed (if not using removed directives). You get:

- **Auto mode**: responses >= 1 MiB or chunked use streaming when no hard incompatibility applies (see exceptions: `markdown_cache_validation full`, excluded content types, unsupported encodings, and Brotli builds without streaming support). Smaller responses use full-buffer
- **Noise pruning**: nav, footer, aside, ads, cookie banners removed from output

Monitor the new reason codes in logs:

```
ELIGIBLE_STREAMING_AUTO    # auto mode selected streaming
ELIGIBLE_FULLBUFFER_AUTO   # auto mode selected full-buffer
PRUNE_REGION_SKIPPED       # region removed by pruning
PRUNE_EMPTY_FALLBACK       # pruning removed everything, fell back to full output
```

### Path B: Restore 0.5.x Behavior Exactly

Add to your `http` block:

```nginx
http {
    markdown_streaming off;   # 0.8.0-era name was markdown_streaming_engine; 0.9.1+ uses markdown_streaming off
    markdown_prune_noise off;
    # ... existing configuration
}
```

This produces identical output to 0.5.x with no behavioral differences.
(0.9.1+ operators: replace `markdown_streaming_engine` with
`markdown_streaming`. The 0.9.1 release removed the engine name.)

### Path C: Selective Rollback

Restore streaming default but keep pruning, or vice versa:

```nginx
# Keep auto streaming, disable pruning
markdown_prune_noise off;

# Keep pruning, disable auto streaming
markdown_streaming off;   # 0.9.1+ syntax (0.8.0-era name was markdown_streaming_engine)
```

## Streaming Auto Mode Details

### How Auto Mode Selects Engine

```
if Content-Length >= the configured threshold (historical 0.8.x behavior):
    → streaming engine
elif Transfer-Encoding: chunked:
    → streaming engine
else:
    → full-buffer engine
```

### Configuring the Threshold (historical through 0.9.1)

```nginx
# Lower threshold: stream more responses (default 1m)
markdown_stream_threshold 512k;

# Higher threshold: stream only very large responses
markdown_stream_threshold 5m;
```

**Historical note:** the 0.8.0 release removed
`markdown_streaming_auto_threshold`. `markdown_stream_threshold` served as
the 0.8.x/0.9.1 replacement. The 0.9.2 release removed both threshold
directives. Current auto mode uses a fixed internal 1 MiB threshold with
no configuration option.

### Monitoring Auto Mode Selection

The public metrics endpoint is Prometheus text 0.0.4 only. Engine selection
shows up in the `engine` label on the frozen conversion families:

```
nginx_markdown_conversion_attempts_total{engine="streaming"} 1234
nginx_markdown_conversion_attempts_total{engine="full_buffer"} 5678
```

## Noise Pruning Configuration

### Default Prune Selectors

The module prunes these HTML element tags by default:

```
nav, footer, aside
```

> **Note**: v0.6.0 pruning matches by tag name only. CSS selector
> syntax (`.class`, `#id`, `[role="..."]`) is not yet supported and
> stays on the roadmap for a future release. Use tag-name selectors only.

### Custom Selectors

Replace defaults with your own (tag names only):

```nginx
markdown_prune_selectors "nav footer aside sidebar";
```

### Protection Selectors

Keep specific regions that the pruning would otherwise remove (tag names only):

```nginx
# Prune all nav/footer except the <mainnav> element
markdown_prune_protection_selectors "mainnav";
```

Protection selectors take priority over prune selectors. An element matching both is kept.

### Empty-Output Fallback

If pruning removes all content:

1. The module returns the unpruned conversion result (no data loss)
2. It logs the `PRUNE_EMPTY_FALLBACK` reason code
3. Historical 0.8-era builds incremented `prune_empty_fallback_total`. This
   metric is not part of the frozen 0.9.2 metrics contract.

## Deprecation and Removal Notices

### `markdown_streaming_auto_threshold` — REMOVED in 0.8.0

This directive is no longer registered. `nginx -t` will fail with
"unknown directive" if it appears in your configuration. In 0.9.2, use
`markdown_streaming off|auto|force`. Auto mode uses the fixed internal 1 MiB
threshold.

```nginx
# Before (0.6.x/0.7.x) — NO LONGER ACCEPTED
markdown_streaming_auto_threshold 64k;

# After (0.9.2) — required only when selecting an explicit policy
markdown_streaming auto;
```

### `markdown_streaming_engine` `$variable` — REMOVED in 0.8.0

The directive no longer accepts NGINX variables. In 0.9.2, `markdown_streaming`
accepts `off`, `auto`, and `force` (the historical `on` value became
`force`. `on` remains valid only in the 0.8.0-era `markdown_streaming_engine`):

```nginx
# Before (0.6.x/0.7.x) — NO LONGER ACCEPTED
markdown_streaming_engine $streaming_flag;

# After (0.8.0) — use a fixed value
markdown_streaming_engine auto;
```

### `markdown_max_size` and `markdown_streaming_budget`

`markdown_memory_budget` (unified budget) supersedes these per-engine size
directives. They continue to work in 0.6.0 with the following priority:

```
explicit markdown_max_size       → highest priority
explicit markdown_memory_budget  → medium priority
compiled-in default              → lowest priority
```

**Planned removal**: 0.8.0. Migrate before then:

```nginx
# Before (0.5.x)
markdown_max_size 4m;
markdown_streaming_budget 8m;

# After (0.6.0, unified)
markdown_memory_budget 8m;
```

## Rollback Procedure

If 0.6.0 defaults cause issues:

1. **Immediate rollback**: Add `markdown_streaming off; markdown_prune_noise off;` at http level (0.9.1/0.9.2 syntax; use `markdown_streaming off;` as the primary command)
2. **Selective rollback**: Disable only the problematic default
3. **Binary rollback**: Downgrade to 0.5.x binary — configuration is backward-compatible

## Verification

After upgrading, verify behavior:

```bash
# Check which engine is selected
curl -s http://localhost/metrics | grep engine_selection

# Check pruning activity
curl -s http://localhost/metrics | grep prune

# Verify output quality on representative pages
curl -s -H "Accept: text/markdown" http://localhost/page | head -50
```

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-15 | Kang | Active migration examples use markdown_streaming; historical names only in history section |
| 0.9.2 | 2026-08-08 | Kang | Clarified that configuration examples use 0.8.0-era directive names |
| 0.8.3 | 2026-06-26 | Kang | No changes; version alignment with 0.8.3 release |
| 0.8.0 | 2026-06-16 | Kang | Updated for 0.8.0: markdown_streaming_auto_threshold removed, $variable support removed, default threshold changed to 1m |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.6.0 | 2026-04-28 | v060-prod | Initial migration guide |
