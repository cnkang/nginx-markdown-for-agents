# Rollback Guide: 0.9.2

## Overview

This guide covers rolling back the 0.9.2 development candidate to a prior
release. 0.9.2 is a breaking release (see
[0.9.2-breaking-changes.md](0.9.2-breaking-changes.md)), but it has no
on-disk data migration. Rolling back the module binary restores the 0.9.1
directive surface only after the configuration is also restored. The 0.9.2
25-directive configuration and ABI 2 are not compatible with a 0.9.1 binary.
Publication and artifact availability are separate release gates.

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
   timeout 30 sh -c 'while sudo systemctl is-active --quiet nginx; do sleep 1; done'
   ```

2. **Restore the 0.9.1 module binary:**

   ```bash
   sudo cp /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so.0.9.1.bak \
       /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so
   ```

   Or download the 0.9.1 binary from the GitHub release archive. Verify the
   `SHA256SUMS` and `SHA256SUMS.asc` files, confirming the signing key's
   fingerprint through an independent trusted source, before copying or
   installing the binary.

3. **Restore the matching 0.9.1 configuration:**

   Restore the versioned 0.9.1 `nginx.conf` and any 0.9.1 dynamic-configuration
   file from the same backup or release-controlled configuration bundle. Do not
   validate a 0.9.2 configuration with the 0.9.1 binary. The 25-directive
   surface and dynconf schema are not compatible.

4. **Validate configuration:**

   ```bash
   sudo nginx -t
   ```

5. **Start NGINX:**

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

2. **Restore the matching 0.9.1 configuration, install, validate, and start:**

   ```bash
   sudo nginx -s quit
   timeout 30 sh -c 'while sudo systemctl is-active --quiet nginx; do sleep 1; done'
   # Restore the versioned 0.9.1 nginx.conf and dynamic-configuration file here.
   sudo cp objs/ngx_http_markdown_filter_module.so /usr/lib/nginx/modules/
   sudo nginx -t && sudo nginx
   ```

### Helm

```bash
helm rollback nginx-markdown --namespace nginx-markdown
```

### Docker

Before restarting, restore the 0.9.1-compatible `docker-compose.yml` and
any configuration files. Then restart:

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
| `markdown_otel on` | `markdown_otel_tracing on` (partial — some 0.9.1 OTel directives have no 0.9.0 equivalent; note that 0.9.2 removed OTel entirely, so no 0.9.2 configuration contains either form) |

### Step 3: Install 0.9.0 binary and validate

```bash
sudo nginx -s quit
while sudo systemctl is-active --quiet nginx; do sleep 1; done
# Restore the versioned 0.9.0 nginx.conf and dynamic-configuration file before
# installing the 0.9.0 binary. The 0.9.1 configuration is not compatible.
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
complete new file. It does not guarantee that all workers apply the new
snapshot at the same instant. Each worker has its own watcher cycle, so
workers can briefly report different `config_version` values and serve
different active snapshots while convergence is in progress:

```bash
set -eu
path=/etc/nginx/markdown-dynamic.conf
tmp="${path}.tmp.$$"
umask 077
cat > "$tmp" <<'EOF'
{
  "schema_version": 1,
  "filter": "off",
  "error_policy": "pass",
  "streaming_budget": 1048576
}
EOF
mv -f "$tmp" "$path"
```

The watcher observes the changed modification time, parses and validates the
complete file, then promotes it through the normal staged reload. If parsing
or validation fails, the active snapshot and its `applied_mtime` remain at the
last successfully applied state. Verify convergence with the read-only
diagnostics endpoint or with request behavior from the relevant workers. If
you need a strong synchronization boundary, perform a controlled NGINX
reload. Do not assume that every worker has restored the new snapshot
immediately.

Do not send `POST /nginx-markdown/diagnostics?action=rollback`. The module rejects it
with `405 Method Not Allowed`. This deliberate absence avoids restoring a
worker-local snapshot while other NGINX workers continue serving a different
configuration.

---

## Known Irreversible Changes

There is no irreversible on-disk state change, but the public configuration
and bundled ABI changes are not reversible by swapping only the binary:

- Diagnostics mapping fix is backward-compatible
- C reason code constants include the 0.9.2 registry additions
- The 0.9.2 production surface removed OTel
- Dynconf diagnostics remains read-only. File restore is atomic and auditable
- Public surface inventory is a build-time gate

Restore the matching 0.9.1 configuration and binary together when rolling
back. No data formats or on-disk state require a migration.

---

## Metrics and Diagnostics Changes on Rollback

When rolling back from 0.9.2 to 0.9.1:

| Aspect | Impact |
|--------|--------|
| `reason_to_code` mapping | `bypass_no_transform` entry removed from diagnostics JSON |
| C reason code constants | Decompression series (4–11) constants unavailable in `components/nginx-module/src/ngx_http_markdown_reason.c` |
| OTel surface | Present in 0.9.1 documentation; removed from 0.9.2, so restore the old configuration before rollback |
| Dynconf diagnostics | `POST action=rollback` is rejected; restore the watched file atomically |
| `stream_state` logging | `PRE_COMMIT` fallthrough returns to silent behavior |
| Prometheus metrics | No metric name or label changes — counters continue from their current values |

Metric counters are **not reset** on rollback. They continue accumulating
from their current values under the downgraded module.

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-08 | Kang | Clarified that OTel directives exist in no 0.9.2 configuration (OTel removed) |
| 0.9.2 | 2026-07-30 | Kang | Initial rollback guide for 0.9.2 |
