# Rollback Guide: 0.9.2

## Overview

This guide covers rolling back the 0.9.2 development candidate to a prior
release. The candidate is intended to be non-breaking and has no known
irreversible changes, but publication and artifact availability are separate
release gates.

| Target | Section |
|--------|---------|
| 0.9.2 → 0.9.1 | [Rollback to 0.9.1](#rollback-to-091) |
| 0.9.2 → 0.9.0 | [Rollback to 0.9.0](#rollback-to-090) |
| Dynconf restore | [Dynconf Restore](#dynconf-restore) |

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

## Dynconf Restore

The diagnostics endpoint is read-only and accepts only `GET` and `HEAD`.
There is no runtime rollback API or rollback response schema. To restore a
previous dynamic configuration, replace the watched file atomically. Atomic
rename guarantees that every read observes either the complete old file or the
complete new file; it does not guarantee that all workers apply the new
snapshot at the same instant. Each worker has its own watcher cycle, so
workers can briefly report different `config_version` values and serve
different active snapshots while convergence is in progress:

```bash
set -eu
path=/etc/nginx/markdown-dynamic.conf
tmp="${path}.tmp.$$"
umask 077
cat > "$tmp" <<'EOF'
schema_version=0.9
memory_budget=1m
EOF
mv -f "$tmp" "$path"
```

The watcher observes the changed modification time, parses and validates the
complete file, then promotes it through the normal staged reload. If parsing
or validation fails, the active snapshot and its `applied_mtime` remain at the
last successfully applied state. Verify convergence with the read-only
diagnostics endpoint or with request behavior from the relevant workers. If a
strong synchronization boundary is required, perform a controlled NGINX
reload; do not assume that every worker has restored the new snapshot
immediately.

Do not send `POST /nginx-markdown/diagnostics?action=rollback`; it is rejected
with `405 Method Not Allowed`. This deliberate absence avoids restoring a
worker-local snapshot while other NGINX workers continue serving a different
configuration.

---

## Known Irreversible Changes

**None for 0.9.2.** All changes in 0.9.2 are additive or corrective:

- Diagnostics mapping fix is backward-compatible
- C reason code constants are additive
- OTel remains request-scoped with no worker-owned lifecycle state
- Dynconf diagnostics remains read-only; file restore is atomic and auditable
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
| OTel ownership | Request-scoped in both versions; no worker-owned state is flushed |
| Dynconf diagnostics | `POST action=rollback` is rejected; restore the watched file atomically |
| `stream_state` logging | `PRE_COMMIT` fallthrough returns to silent behavior |
| Prometheus metrics | No metric name or label changes — counters continue from their current values |

Metric counters are **not reset** on rollback. They continue accumulating
from their current values under the downgraded module.

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-07-30 | Kang | Initial rollback guide for 0.9.2 |
