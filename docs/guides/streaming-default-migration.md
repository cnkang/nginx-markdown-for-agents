# Streaming Default & Noise Pruning Migration Guide

**Version**: 0.8.0
**Audience**: Operators upgrading from 0.5.x/0.6.x/0.7.x to 0.8.0

> **v0.9.1+ operators:** `markdown_streaming_engine` has been removed and replaced by `markdown_streaming`. If you are reading this guide for the streaming behavior changes, please replace `markdown_streaming_engine off;` with `markdown_streaming off;` in your configuration files.

## Overview

v0.6.0 introduced two default behavior changes. v0.8.0 removes the
v0.6.x compatibility bridge entirely:

| Default | 0.5.x | 0.6.0–0.7.x | 0.8.0 | Impact |
|---|---|---|---|---|
| `markdown_streaming_engine` | `off` (full-buffer) | `auto` (per-request selection) | `auto` (per-request selection) | Large/chunked responses use streaming by default |
| `markdown_prune_noise` | N/A (compile-time opt-in) | `on` (runtime, default-enabled) | `on` (runtime, default-enabled) | Noise regions (nav, footer, ads) removed by default |
| `markdown_streaming_auto_threshold` | N/A (new in 0.6.0) | Accepted (32k default) | **Removed** — `nginx -t` fails | Must use `markdown_stream_threshold` |
| `markdown_streaming_engine` `$variable` | Accepted | Accepted | **Removed** — `nginx -t` fails | Must use fixed `off`/`auto`/`on` |

**Key guarantee**: Explicit `off`/`on` configurations produce identical behavior
to 0.5.x. However, configurations using `markdown_streaming_auto_threshold` or
`markdown_streaming_engine $variable` **must be updated before upgrading to 0.8.0**.

## Migration Paths

### Path A: Accept 0.8.0 Defaults (Recommended)

No configuration changes needed (if not using removed directives). You get:

- **Auto mode**: responses >= 1 MiB or chunked use streaming; smaller responses use full-buffer
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
    markdown_streaming_engine off;
    markdown_prune_noise off;
    # ... existing configuration
}
```

This produces identical output to 0.5.x with no behavioral differences.

### Path C: Selective Rollback

Restore streaming default but keep pruning, or vice versa:

```nginx
# Keep auto streaming, disable pruning
markdown_prune_noise off;

# Keep pruning, disable auto streaming
markdown_streaming_engine off;
```

## Streaming Auto Mode Details

### How Auto Mode Selects Engine

```
if Content-Length >= markdown_stream_threshold:
    → streaming engine
elif Transfer-Encoding: chunked:
    → streaming engine
else:
    → full-buffer engine
```

### Configuring the Threshold

```nginx
# Lower threshold: stream more responses (default 1m)
markdown_stream_threshold 512k;

# Higher threshold: stream only very large responses
markdown_stream_threshold 5m;
```

**Note**: `markdown_streaming_auto_threshold` was removed in 0.8.0.
Use `markdown_stream_threshold` instead.

### Monitoring Auto Mode Selection

Prometheus metrics:

```
nginx_markdown_engine_selection_total{engine="streaming",reason="auto"} 1234
nginx_markdown_engine_selection_total{engine="full_buffer",reason="auto"} 5678
```

JSON metrics:

```json
{
  "engine_selection": {
    "streaming_auto": 1234,
    "fullbuffer_auto": 5678
  }
}
```

## Noise Pruning Configuration

### Default Prune Selectors

These HTML element tags are pruned by default:

```
nav, footer, aside
```

> **Note**: v0.6.0 pruning matches by tag name only. CSS selector
> syntax (`.class`, `#id`, `[role="..."]`) is not yet supported and
> is tracked for a future release. Use tag-name selectors only.

### Custom Selectors

Replace defaults with your own (tag names only):

```nginx
markdown_prune_selectors "nav footer aside sidebar";
```

### Protection Selectors

Keep specific regions that would otherwise be pruned (tag names only):

```nginx
# Prune all nav/footer except the <mainnav> element
markdown_prune_protection_selectors "mainnav";
```

Protection selectors take priority over prune selectors. An element matching both is kept.

### Empty-Output Fallback

If pruning removes all content:

1. The unpruned conversion result is returned (no data loss)
2. `PRUNE_EMPTY_FALLBACK` reason code is logged
3. `prune_empty_fallback_total` metric is incremented

## Deprecation and Removal Notices

### `markdown_streaming_auto_threshold` — REMOVED in 0.8.0

This directive is no longer registered. `nginx -t` will fail with
"unknown directive" if it appears in your configuration. Replace with
`markdown_stream_threshold`:

```nginx
# Before (0.6.x/0.7.x) — NO LONGER ACCEPTED
markdown_streaming_auto_threshold 64k;

# After (0.8.0) — required
markdown_stream_threshold 64k;
```

### `markdown_streaming_engine` `$variable` — REMOVED in 0.8.0

The directive no longer accepts NGINX variables. Only `off`, `auto`, and
`on` are accepted:

```nginx
# Before (0.6.x/0.7.x) — NO LONGER ACCEPTED
markdown_streaming_engine $streaming_flag;

# After (0.8.0) — use a fixed value
markdown_streaming_engine auto;
```

### `markdown_max_size` and `markdown_streaming_budget`

These per-engine size directives are superseded by `markdown_memory_budget` (unified budget). They continue to work in 0.6.0 with the following priority:

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

1. **Immediate rollback**: Add `markdown_streaming_engine off; markdown_prune_noise off;` at http level
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
| 0.8.3 | 2026-06-26 | Kang | No changes; version alignment with 0.8.3 release |
| 0.8.0 | 2026-06-16 | Kang | Updated for 0.8.0: markdown_streaming_auto_threshold removed, $variable support removed, default threshold changed to 1m |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.6.0 | 2026-04-28 | v060-prod | Initial migration guide |
