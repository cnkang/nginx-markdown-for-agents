# Version Rollback Guide: 0.9.2

This document covers **version downgrade** from 0.9.2 to an older binary and
its matching configuration. For the faster operational response that disables
or narrowly scopes conversion **without replacing the binary**, see
[OPERATIONAL_ROLLBACK.md](OPERATIONAL_ROLLBACK.md).

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
   if command -v systemctl >/dev/null 2>&1 && sudo systemctl is-active --quiet nginx 2>/dev/null; then
     # systemd-managed NGINX: wait for a confirmed shutdown.
     timeout 30 sh -c 'while sudo systemctl is-active --quiet nginx; do sleep 1; done'
     if sudo systemctl is-active --quiet nginx; then
       echo "NGINX did not stop within 30s — investigate before continuing" >&2
       exit 1
     fi
   else
     # systemctl unavailable or does not manage NGINX: independently verify
     # that no NGINX master process remains before replacing the module.
     if pgrep -x nginx >/dev/null 2>&1; then
       echo "NGINX master process still running after 'nginx -s quit' — investigate before continuing" >&2
       exit 1
     fi
   fi
   ```

2. **Restore the 0.9.1 module binary:**

   Derive the module directory from the active NGINX build rather than
   hard-coding one path:

   ```bash
   MODULES_DIR="${MODULES_DIR:-$(nginx -V 2>&1 | sed -n 's/.*--modules-path=\([^ ]*\).*/\1/p')}"
   if [[ -z "$MODULES_DIR" || ! -d "$MODULES_DIR" ]]; then
     # No modules-path in the running binary: do NOT guess a directory.
     # Set MODULES_DIR explicitly to the directory that holds the currently
     # loaded module .so (confirm it with: nginx -T 2>/dev/null | grep load_module).
     echo "ERROR: nginx reports no --modules-path; set MODULES_DIR explicitly" >&2
     echo "       to the directory holding the active module binary." >&2
     exit 1
   fi
   if [[ -z "$MODULES_DIR" || ! -d "$MODULES_DIR" ]]; then
     echo "ERROR: cannot locate the NGINX modules directory" >&2
     exit 1
   fi
   MODULE_091_ARTIFACT="${MODULE_091_ARTIFACT:?set this to the downloaded and verified 0.9.1 module artifact}"
   if [[ ! -f "$MODULE_091_ARTIFACT" ]]; then
     echo "ERROR: verified 0.9.1 module artifact not found: $MODULE_091_ARTIFACT" >&2
     exit 1
   fi
   sudo cp "$MODULE_091_ARTIFACT" \
       "$MODULES_DIR/.ngx_http_markdown_filter_module.so.restore" && \
       sudo mv -f "$MODULES_DIR/.ngx_http_markdown_filter_module.so.restore" \
       "$MODULES_DIR/ngx_http_markdown_filter_module.so"
   ```

   Or download the 0.9.1 binary from the GitHub release archive. Verify the
   `SHA256SUMS` and `SHA256SUMS.asc` files, confirming the signing key's
   fingerprint through an independent trusted source, before copying or
   installing the binary. Follow the standard verification block in
   `docs/guides/PACKAGE_INSTALLATION.md` (isolated `GNUPGHOME`, fingerprint
   check against `docs/guides/GPG_KEY_MANAGEMENT.md` §3, `VALIDSIG`
   extraction from the Good-signature status line) — the one-sentence
   summary here is not a substitute for that procedure.

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
   # Fetch the tag, verify its cryptographic signature, and compare its
   # resolved commit against independently authenticated release evidence
   # before checking out.  Each step fails the script on error, so a
   # failed signature or a commit mismatch stops before checkout:
   set -euo pipefail
   # Force the fetch so a stale or re-signed tag cannot survive locally:
   # without --force, git keeps an existing tag when the remote moved.
   git fetch --force origin tag v0.9.1
   git tag -v v0.9.1
   expected_sha="<SHA from independently authenticated release evidence>"
   resolved_sha="$(git rev-parse v0.9.1^{commit})"
   if [ "$resolved_sha" != "$expected_sha" ]; then
     echo "FAIL: tag v0.9.1 resolves to $resolved_sha, expected $expected_sha" >&2
     exit 1
   fi
   git checkout v0.9.1
   cd components/rust-converter && cargo build --release --target "$(rustc -vV | sed -n 's/^host: //p')" && cd ../..
   # Rebuild NGINX module per your build procedure
   ```

2. **Restore the matching 0.9.1 configuration, install, validate, and start:**

   ```bash
   # Reuse the guarded shutdown logic from the prebuilt procedure above:
   # detect whether systemd owns NGINX before invoking systemctl.
   sudo nginx -s quit
   if command -v systemctl >/dev/null 2>&1 && sudo systemctl is-active --quiet nginx 2>/dev/null; then
     timeout 30 sh -c 'while sudo systemctl is-active --quiet nginx; do sleep 1; done'
     if sudo systemctl is-active --quiet nginx; then
       echo "NGINX did not stop within 30s — investigate before continuing" >&2
       exit 1
     fi
   else
     # systemctl unavailable or does not manage NGINX: verify that the
     # manually managed master process has stopped before continuing.
     if pgrep -f "nginx: master process" >/dev/null 2>&1; then
       echo "NGINX master process still running after 'nginx -s quit' — investigate before continuing" >&2
       exit 1
     fi
   fi
   # Restore the versioned 0.9.1 nginx.conf and dynamic-configuration file here.
   # Locate the module directory explicitly: derive it from the active nginx
   # configuration, or set MODULES_DIR yourself when following this procedure
   # independently.
   MODULES_DIR="${MODULES_DIR:-$(nginx -V 2>&1 | sed -n 's/.*--modules-path=\([^ ]*\).*/\1/p')}"
   if [[ -z "$MODULES_DIR" || ! -d "$MODULES_DIR" ]]; then
     echo "ERROR: cannot locate the NGINX modules directory" >&2
     exit 1
   fi
   sudo cp objs/ngx_http_markdown_filter_module.so \
       "$MODULES_DIR/.ngx_http_markdown_filter_module.so.restore" && \
   sudo mv -f "$MODULES_DIR/.ngx_http_markdown_filter_module.so.restore" \
       "$MODULES_DIR/ngx_http_markdown_filter_module.so"
   sudo nginx -t && sudo nginx
   ```

### Helm

```bash
# Identify the revision corresponding to 0.9.1
helm history nginx-markdown --namespace nginx-markdown

# Verify the target revision, then rollback with an explicit revision and --wait
helm rollback nginx-markdown <0.9.1-revision> --namespace nginx-markdown --wait
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
| `markdown_otel on` + `markdown_otel_endpoint <url>` | `markdown_otel_tracing on`; 0.9.0 has no endpoint directive, so move `<url>` to the 0.9.0 collector/native-OTel configuration before enabling tracing. Do not leave `markdown_otel_endpoint` in the restored NGINX configuration. |

### Step 3: Install 0.9.0 binary and validate

```bash
# Reuse the guarded shutdown logic from the prebuilt procedure:
# detect whether systemd owns NGINX before invoking systemctl.
sudo nginx -s quit
if command -v systemctl >/dev/null 2>&1 && sudo systemctl is-active --quiet nginx 2>/dev/null; then
  timeout 30 sh -c 'while sudo systemctl is-active --quiet nginx; do sleep 1; done'
  if sudo systemctl is-active --quiet nginx; then
    echo "NGINX did not stop within 30s — investigate before continuing" >&2
    exit 1
  fi
else
  # systemctl unavailable or does not manage NGINX: verify that the
  # manually managed master process has stopped before continuing.
  if pgrep -f "nginx: master process" >/dev/null 2>&1; then
    echo "NGINX master process still running after 'nginx -s quit' — investigate before continuing" >&2
    exit 1
  fi
fi
# Restore the versioned 0.9.0 nginx.conf and dynamic-configuration file before
# installing the 0.9.0 binary. The 0.9.1 configuration is not compatible.
MODULES_DIR="${MODULES_DIR:-$(nginx -V 2>&1 | sed -n 's/.*--modules-path=\([^ ]*\).*/\1/p')}"
if [[ -z "$MODULES_DIR" || ! -d "$MODULES_DIR" ]]; then
  echo "ERROR: cannot locate the NGINX modules directory" >&2
  exit 1
fi
sudo cp /path/to/ngx_http_markdown_filter_module.so.0.9.0 \
    "$MODULES_DIR/.ngx_http_markdown_filter_module.so.restore" && \
sudo mv -f "$MODULES_DIR/.ngx_http_markdown_filter_module.so.restore" \
    "$MODULES_DIR/ngx_http_markdown_filter_module.so"
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
different active snapshots while convergence is in progress.

The dynamic configuration path is root-owned, so run the following restore
commands from a root shell. Prefixing individual commands with `sudo` is not
enough: the heredoc and the temporary file redirection happen in the calling
shell before `sudo` runs, and cannot create files in the root-owned directory.

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
  "streaming_buffer": 1048576
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
| `recent_decisions[].reason` | `bypass_no_transform` entry removed from diagnostics JSON |
| C reason code constants | Decompression series (4–11) constants unavailable in `components/nginx-module/src/ngx_http_markdown_reason.c` |
| OTel surface | Present in 0.9.1 documentation; removed from 0.9.2, so restore the old configuration before rollback |
| Dynconf diagnostics | `POST action=rollback` is rejected; restore the watched file atomically |
| Streaming terminal diagnostics | The retired standalone decision-state model is absent; rely on the current phase/terminal latch diagnostics and shared lowercase reason registry |
| Prometheus metric families | **Differ between the versions.** 0.9.2 exposes exactly the eleven frozen v1 families (`nginx_markdown_build_info`, `nginx_markdown_conversion_attempts_total`, `nginx_markdown_conversion_deliveries_total`, `nginx_markdown_conversion_duration_seconds`, `nginx_markdown_decompression_events_total`, `nginx_markdown_dynconf_reloads_total`, `nginx_markdown_input_bytes_total`, `nginx_markdown_output_bytes_total`, `nginx_markdown_requests_total`, `nginx_markdown_streaming_events_total`, `nginx_markdown_streaming_peak_memory_bytes`). The 0.9.1 binary re-emits the legacy surface it shipped with: per-path families (`per_path_conversions_total`, `per_path_overflow_total`, …), shadow metrics, profile/passthrough/decision families, and the debug/perf families removed in 0.9.2 (see `docs/guides/prometheus-metrics.md`). Renamed families include `conversions_total` → `conversion_attempts_total`/`conversion_deliveries_total`, `decompressions_total` → `decompression_events_total`, and `streaming_failure_total` → `streaming_events_total` labels. |

After rollback, validate every dashboard and alert that consumes the
`/markdown-metrics` endpoint: 0.9.2 family names and label sets do not exist
under the 0.9.1 binary, and the 0.9.1 legacy families reappear. Queries that
reference removed or renamed families must update their alert rules, and
operators must re-test the alerts against the downgraded binary before
they consider the rollback complete.

Metric counters are **not reset** on a graceful reload (`nginx -s reload`
signals a same-version HUP). They continue accumulating from their current
values under the downgraded module. A full NGINX stop and subsequent start
(the quit → swap → start procedure used by this rollback guide) resets
shared-memory counters, so delta calculations must establish a new baseline
after the rollback restart. A graceful reload preserves them.

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-24 | Kang | Both shutdown blocks reuse the guarded systemd-detection logic with manual-master verification; MODULES_DIR fallback no longer guesses the first existing directory and requires explicit configuration |
| 0.9.2 | 2026-08-15 | Kang | Modules path derived from nginx -V; bounded shutdown loop; metric-family difference table |
| 0.9.2 | 2026-08-08 | Kang | Clarified that OTel directives exist in no 0.9.2 configuration (OTel removed) |
| 0.9.2 | 2026-07-30 | Kang | Initial rollback guide for 0.9.2 |
