# Streaming Default & Noise Pruning Migration Guide

**Version**: 0.6.0
**Audience**: Operators upgrading from 0.5.x to 0.6.0

## Overview

v0.6.0 introduces two default behavior changes:

| Default | 0.5.x | 0.6.0 | Impact |
|---|---|---|---|
| `markdown_streaming_engine` | `off` (full-buffer) | `auto` (per-request selection) | Large/chunked responses use streaming by default |
| `markdown_prune_noise` | N/A (compile-time opt-in) | `on` (runtime, default-enabled) | Noise regions (nav, footer, ads) removed by default |

**Key guarantee**: Explicit `off`/`on` configurations produce identical behavior to 0.5.x.

## Migration Paths

### Path A: Accept 0.6.0 Defaults (Recommended)

No configuration changes needed. You get:

- **Auto mode**: responses > 32 KiB or chunked use streaming; smaller responses use full-buffer
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
if Content-Length > markdown_streaming_auto_threshold:
    → streaming engine
elif Transfer-Encoding: chunked:
    → streaming engine
else:
    → full-buffer engine
```

### Configuring the Threshold

```nginx
# Lower threshold: stream more responses (default 32k)
markdown_streaming_auto_threshold 16k;

# Higher threshold: stream only very large responses
markdown_streaming_auto_threshold 64k;
```

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

## Deprecation Notices

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
| 0.6.0 | 2026-04-28 | v060-prod | Initial migration guide |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
