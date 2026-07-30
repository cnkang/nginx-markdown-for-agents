# Rollback Guide: 0.9.2

## Overview

This guide covers rolling back from nginx-markdown-for-agents 0.9.2 to a
prior release. 0.9.2 is a non-breaking release with no irreversible changes,
so rollback is straightforward.

| Target | Section |
|--------|---------|
| 0.9.2 → 0.9.1 | [Rollback to 0.9.1](#rollback-to-091) |
| 0.9.2 → 0.9.0 | [Rollback to 0.9.0](#rollback-to-090) |
| Dynconf rollback | [Dynconf Rollback](#dynconf-rollback) |

---

## Rollback to 0.9.1

### Prebuilt Module

1. **Stop NGINX gracefully:**

   ```bash
   sudo nginx -s quit
   ```

2. **Restore the 0.9.1 module binary:**

   ```bash
   sudo cp /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so.0.9.1.bak \
       /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so
   ```

   Or download the 0.9.1 binary from the GitHub release archive.

3. **Validate configuration:**

   ```bash
   sudo nginx -t
   ```

4. **Start NGINX:**

   ```bash
   sudo nginx
   ```

### Source Build

1. **Checkout the 0.9.1 tag and rebuild:**

   ```bash
   cd nginx-markdown-for-agents
   git checkout v0.9.1
   cd components/rust-converter && cargo build --release && cd ../..
   # Rebuild NGINX module per your build procedure
   ```

2. **Install, validate, and start:**

   ```bash
   sudo cp objs/ngx_http_markdown_filter_module.so /usr/lib/nginx/modules/
   sudo nginx -t && sudo nginx
   ```

### Helm

```bash
helm rollback nginx-markdown --namespace nginx-markdown
```

### Docker

```bash
# Update image tag to v0.9.1 and restart
docker compose up -d
```

---

## Rollback to 0.9.0

Rolling back to 0.9.0 requires a two-step process because 0.9.1 introduced
breaking changes relative to 0.9.0.

### Step 1: Roll back to 0.9.1

Follow the [Rollback to 0.9.1](#rollback-to-091) procedure above.

### Step 2: Migrate configuration from 0.9.1 to 0.9.0

You must revert the configuration changes introduced by 0.9.1. See
[docs/guides/MIGRATION-0.9.1.md](MIGRATION-0.9.1.md) for the full mapping.
Key reversions:

| 0.9.1 Directive | 0.9.0 Directive |
|-----------------|-----------------|
| `markdown_streaming off` | `markdown_streaming_engine off` |
| `markdown_streaming auto` | `markdown_streaming_engine auto` |
| `markdown_streaming force` | `markdown_streaming_engine on` |
| `markdown_flavor commonmark` | `markdown_flavor commonmark` (unchanged) |
| `markdown_otel on` | `markdown_otel_tracing on` (partial — some 0.9.1 OTel directives have no 0.9.0 equivalent) |

### Step 3: Install 0.9.0 binary and validate

```bash
sudo cp /path/to/ngx_http_markdown_filter_module.so.0.9.0 \
    /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so
sudo nginx -t && sudo nginx
```

**Warning:** 0.9.0 uses Rust 1.91 baseline. Source builders must downgrade
their toolchain or use prebuilt 0.9.0 binaries.

---

## Dynconf Rollback

0.9.2 introduces a dynconf rollback API. Use it to revert the active
dynamic configuration to the previously applied snapshot without a full
version rollback:

```bash
curl -X POST "http://localhost/nginx-markdown/diagnostics?action=rollback"
```

**Response:**

```json
{
  "status": "ok",
  "action": "rollback",
  "previous_mtime": "2026-07-30T10:00:00Z",
  "current_mtime": "2026-07-30T09:55:00Z"
}
```

**When to use:**

- A dynconf apply produced unexpected behavior and you want to revert
  to the previous configuration without restarting NGINX.
- You are testing a configuration change and want to quickly undo it.

**Limitations:**

- Only one level of rollback is available (previous snapshot).
- Rollback is a runtime operation — it does not modify the dynconf file
  on disk. A subsequent NGINX reload will re-read the on-disk file.

See [DYNAMIC_CONFIG.md](DYNAMIC_CONFIG.md) for full usage.

---

## Known Irreversible Changes

**None for 0.9.2.** All changes in 0.9.2 are additive or corrective:

- Diagnostics mapping fix is backward-compatible
- C reason code constants are additive
- OTel lifecycle cleanup is transparent
- Dynconf rollback API is opt-in
- Public surface inventory is a build-time gate

No data formats, on-disk state, or metric counters are changed in a way
that cannot be reversed by downgrading the module binary.

---

## Metrics and Diagnostics Changes on Rollback

When rolling back from 0.9.2 to 0.9.1:

| Aspect | Impact |
|--------|--------|
| `reason_to_code` mapping | `bypass_no_transform` entry removed from diagnostics JSON |
| C reason code constants | Decompression series (4–11) constants unavailable in C header |
| OTel worker cleanup | Reload/shutdown may leak OTel state (0.9.1 behavior) |
| Dynconf rollback API | Endpoint returns 404 for `action=rollback` |
| `stream_state` logging | `PRE_COMMIT` fallthrough returns to silent behavior |
| Prometheus metrics | No metric name or label changes — counters continue from their current values |

Metric counters are **not reset** on rollback. They continue accumulating
from their current values under the downgraded module.

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-07-30 | Kang | Initial rollback guide for 0.9.2 |