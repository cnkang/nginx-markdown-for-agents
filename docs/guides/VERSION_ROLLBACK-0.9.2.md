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
20-directive configuration and ABI 3 are not compatible with a 0.9.1 binary.
Publication and artifact availability are separate release gates.

| Target | Section |
|--------|---------|
| 0.9.2 → 0.9.1 | [Rollback to 0.9.1](#rollback-to-091) |
| 0.9.2 → 0.9.0 | [Rollback to 0.9.0](#rollback-to-090) |

---

## Rollback to 0.9.1

### Prebuilt Module

1. **Stop NGINX gracefully:**

   ```bash
   sudo nginx -s quit
   if command -v systemctl >/dev/null 2>&1 && sudo systemctl is-active --quiet nginx 2>/dev/null; then
     # systemd-managed NGINX: wait for a confirmed shutdown.
     timeout 30 sh -c 'while sudo systemctl is-active --quiet nginx; do sleep 1; done'
     drain_status=$?
     # Abort when the drain hit the timeout, and require an explicit
     # "inactive" state: any other nonzero is-active result (query failure,
     # failed unit) must not be treated as a confirmed stop.
     if [ "$drain_status" -eq 124 ] || [ "$(sudo systemctl is-active nginx 2>/dev/null)" != "inactive" ]; then
       echo "NGINX did not stop within 30s — investigate before continuing" >&2
       exit 1
     fi
   else
     # systemctl unavailable or does not manage NGINX: poll for the master
     # process to exit after 'nginx -s quit' (graceful shutdown drains
     # in-flight requests first), then confirm it is really gone.
     timeout 30 sh -c 'while pgrep -x nginx >/dev/null 2>&1; do sleep 1; done'
     drain_status=$?
     if [ "$drain_status" -eq 124 ] || pgrep -x nginx >/dev/null 2>&1; then
       echo "NGINX master process still running 30s after 'nginx -s quit' — investigate before continuing" >&2
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

   Restore the complete versioned 0.9.1 configuration tree from the same
   backup or release-controlled configuration bundle — `nginx.conf`, every
   file under `conf.d/`, and every module-enablement file under
   `modules-enabled/` (or the equivalent include directories for your
   distribution). Do not validate a 0.9.2 configuration with the 0.9.1
   binary. The 20-directive surface and static configuration defaults are
   not compatible.

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
     drain_status=$?
     # Abort when the drain hit the timeout, and require an explicit
     # "inactive" state: any other nonzero is-active result (query failure,
     # failed unit) must not be treated as a confirmed stop.
     if [ "$drain_status" -eq 124 ] || [ "$(sudo systemctl is-active nginx 2>/dev/null)" != "inactive" ]; then
       echo "NGINX did not stop within 30s — investigate before continuing" >&2
       exit 1
     fi
   else
     # systemctl unavailable or does not manage NGINX: verify with a bounded
     # drain that no NGINX process remains before continuing.
     if ! timeout 30 sh -c 'while pgrep -x nginx >/dev/null 2>&1; do sleep 1; done'; then
       echo "NGINX master process still running after 'nginx -s quit' — investigate before continuing" >&2
       exit 1
     fi
   fi
   # The backup must contain nginx.conf, conf.d, module-enablement files,
   # and every included configuration file from the 0.9.1 deployment.
   set -euo pipefail
   CONFIG_BACKUP="${CONFIG_BACKUP:?set the complete versioned 0.9.1 configuration directory}"
   CONFIG_FILE="$(nginx -V 2>&1 | sed -n 's/.*--conf-path=\([^ ]*\).*/\1/p')"
   if [[ "${CONFIG_FILE}" != /*/nginx.conf \
       || ! -f "${CONFIG_BACKUP}/nginx.conf" ]]; then
     echo "ERROR: confirm the configuration backup and active NGINX paths" >&2
     exit 1
   fi
   CONFIG_DIR="${CONFIG_FILE%/nginx.conf}"
   if [[ -z "${CONFIG_DIR}" || "${CONFIG_DIR}" == / \
       || -e "${CONFIG_DIR}.restore-0.9.1" \
       || -e "${CONFIG_DIR}.pre-rollback" ]]; then
     echo "ERROR: unsafe configuration path or an earlier rollback exists" >&2
     exit 1
   fi
   MODULES_DIR="${MODULES_DIR:-$(nginx -V 2>&1 | sed -n 's/.*--modules-path=\([^ ]*\).*/\1/p')}"
   if [[ -z "$MODULES_DIR" || ! -d "$MODULES_DIR" ]]; then
     echo "ERROR: cannot locate the NGINX modules directory" >&2
     exit 1
   fi
   # Replace the whole tree so stale 0.9.2 include files cannot survive.
   sudo cp -a -- "${CONFIG_BACKUP}" "${CONFIG_DIR}.restore-0.9.1"
   # Keep the 0.9.2 module binary so a failed rollback can be undone.
   sudo cp -a -- "$MODULES_DIR/ngx_http_markdown_filter_module.so" \
       "$MODULES_DIR/.ngx_http_markdown_filter_module.so.pre-rollback"
   sudo mv -- "${CONFIG_DIR}" "${CONFIG_DIR}.pre-rollback"
   if ! sudo mv -- "${CONFIG_DIR}.restore-0.9.1" "${CONFIG_DIR}"; then
     sudo mv -- "${CONFIG_DIR}.pre-rollback" "${CONFIG_DIR}"
     exit 1
   fi
   sudo cp objs/ngx_http_markdown_filter_module.so \
       "$MODULES_DIR/.ngx_http_markdown_filter_module.so.restore" && \
   sudo mv -f "$MODULES_DIR/.ngx_http_markdown_filter_module.so.restore" \
       "$MODULES_DIR/ngx_http_markdown_filter_module.so"
   if ! sudo nginx -t; then
     echo "ERROR: rollback module fails nginx -t; restoring the 0.9.2 configuration tree and module" >&2
     sudo mv -- "${CONFIG_DIR}" "${CONFIG_DIR}.restore-failed"
     sudo mv -- "${CONFIG_DIR}.pre-rollback" "${CONFIG_DIR}"
     sudo mv -f "$MODULES_DIR/.ngx_http_markdown_filter_module.so.pre-rollback" \
         "$MODULES_DIR/ngx_http_markdown_filter_module.so"
     exit 1
   fi
   sudo nginx
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
  drain_status=$?
  # Abort when the drain hit the timeout, and require an explicit
  # "inactive" state: any other nonzero is-active result (query failure,
  # failed unit) must not be treated as a confirmed stop.
  if [ "$drain_status" -eq 124 ] || [ "$(sudo systemctl is-active nginx 2>/dev/null)" != "inactive" ]; then
    echo "NGINX did not stop within 30s — investigate before continuing" >&2
    exit 1
  fi
else
  # systemctl unavailable or does not manage NGINX: verify with a bounded
  # drain that no NGINX process remains before continuing.
  if ! timeout 30 sh -c 'while pgrep -x nginx >/dev/null 2>&1; do sleep 1; done'; then
    echo "NGINX master process still running after 'nginx -s quit' — investigate before continuing" >&2
    exit 1
  fi
fi
# Restore the versioned 0.9.0 nginx.conf before installing the 0.9.0 binary.
# The 0.9.1 configuration is not compatible.
MODULES_DIR="${MODULES_DIR:-$(nginx -V 2>&1 | sed -n 's/.*--modules-path=\([^ ]*\).*/\1/p')}"
if [[ -z "$MODULES_DIR" || ! -d "$MODULES_DIR" ]]; then
  echo "ERROR: cannot locate the NGINX modules directory" >&2
  exit 1
fi
# The 0.9.0 module binary and configuration tree must be supplied by the
# operator (e.g. from a backup of the pre-0.9.1 deployment).  Point these
# variables at those artifacts; the script refuses to guess.
MODULE_090="${MODULE_090:?set the path to the 0.9.0 module .so}"
CONFIG_090="${CONFIG_090:?set the path to the versioned 0.9.0 configuration directory}"
CONFIG_FILE="$(nginx -V 2>&1 | sed -n 's/.*--conf-path=\([^ ]*\).*/\1/p')"
CONFIG_DIR="${CONFIG_FILE%/nginx.conf}"
if [[ -z "${CONFIG_DIR}" || "${CONFIG_DIR}" == / \
    || ! -f "${CONFIG_090}/nginx.conf" ]]; then
  echo "ERROR: confirm the 0.9.0 configuration backup and active NGINX paths" >&2
  exit 1
fi
# Swap the configuration tree atomically, keeping the current tree for
# rollback of this rollback.
# Preserve the live 0.9.1 module BEFORE touching the configuration so a
# failure at any later step can restore the exact pre-rollback binary.
# Guard BOTH commands: a failed backup must abort before any
# configuration change, or the recovery path would reference a backup
# that does not exist.
sudo cp -a -- "$MODULES_DIR/ngx_http_markdown_filter_module.so" \
    "$MODULES_DIR/.ngx_http_markdown_filter_module.so.pre-0.9.0.bak.tmp" || {
  sudo rm -f -- "$MODULES_DIR/.ngx_http_markdown_filter_module.so.pre-0.9.0.bak.tmp" 2>/dev/null || true
  echo "ERROR: could not back up the 0.9.1 module; aborting before any configuration change" >&2
  exit 1
}
sudo mv -f "$MODULES_DIR/.ngx_http_markdown_filter_module.so.pre-0.9.0.bak.tmp" \
    "$MODULES_DIR/.ngx_http_markdown_filter_module.so.pre-0.9.0.bak" || {
  sudo rm -f -- "$MODULES_DIR/.ngx_http_markdown_filter_module.so.pre-0.9.0.bak.tmp" 2>/dev/null || true
  echo "ERROR: could not finalize the 0.9.1 module backup; aborting before any configuration change" >&2
  exit 1
}
sudo cp -a -- "${CONFIG_090}" "${CONFIG_DIR}.restore-0.9.0"
sudo mv -- "${CONFIG_DIR}" "${CONFIG_DIR}.pre-0.9.0"
if ! sudo mv -- "${CONFIG_DIR}.restore-0.9.0" "${CONFIG_DIR}"; then
  sudo mv -- "${CONFIG_DIR}.pre-0.9.0" "${CONFIG_DIR}"
  exit 1
fi
sudo cp -a -- "${MODULE_090}" \
    "$MODULES_DIR/.ngx_http_markdown_filter_module.so.restore" || {
  # The 0.9.0 configuration is already active; a module staging failure
  # must restore the 0.9.1 configuration before exiting so the pair
  # stays consistent (0.9.1 module + 0.9.1 config).  Check EACH move
  # independently: if either fails, report manual recovery instead of
  # claiming the pair was restored.
  if ! sudo mv -- "${CONFIG_DIR}" "${CONFIG_DIR}.restore-failed" 2>/dev/null; then
    echo "ERROR: could not stage the 0.9.0 module AND could not move the active 0.9.0 tree aside; NGINX remains stopped. Restore manually from ${MODULE_090} and ${CONFIG_DIR}.pre-0.9.0" >&2
    exit 1
  fi
  if ! sudo mv -- "${CONFIG_DIR}.pre-0.9.0" "${CONFIG_DIR}" 2>/dev/null; then
    echo "ERROR: could not stage the 0.9.0 module AND could not restore the 0.9.1 tree; NGINX remains stopped. The 0.9.1 tree is at ${CONFIG_DIR}.pre-0.9.0; restore manually with: sudo mv -- ${CONFIG_DIR}.pre-0.9.0 ${CONFIG_DIR}" >&2
    exit 1
  fi
  echo "ERROR: could not stage the 0.9.0 module; restored the 0.9.1 configuration. NGINX remains stopped. Restore manually from ${MODULE_090} and ${CONFIG_DIR}.pre-0.9.0" >&2
  exit 1
}
sudo mv -f "$MODULES_DIR/.ngx_http_markdown_filter_module.so.restore" \
    "$MODULES_DIR/ngx_http_markdown_filter_module.so" || {
  if ! sudo mv -- "${CONFIG_DIR}" "${CONFIG_DIR}.restore-failed" 2>/dev/null; then
    echo "ERROR: could not replace the active module with the 0.9.0 module AND could not move the active 0.9.0 tree aside; NGINX remains stopped. Restore manually from ${MODULE_090} and ${CONFIG_DIR}.pre-0.9.0" >&2
    exit 1
  fi
  if ! sudo mv -- "${CONFIG_DIR}.pre-0.9.0" "${CONFIG_DIR}" 2>/dev/null; then
    echo "ERROR: could not replace the active module with the 0.9.0 module AND could not restore the 0.9.1 tree; NGINX remains stopped. The 0.9.1 tree is at ${CONFIG_DIR}.pre-0.9.0; restore manually with: sudo mv -- ${CONFIG_DIR}.pre-0.9.0 ${CONFIG_DIR}" >&2
    exit 1
  fi
  echo "ERROR: could not replace the active module with the 0.9.0 module; restored the 0.9.1 configuration. NGINX remains stopped. Restore manually from ${MODULE_090} and ${CONFIG_DIR}.pre-0.9.0" >&2
  exit 1
}
if ! sudo nginx -t; then
  echo "ERROR: 0.9.0 module fails nginx -t; restoring the 0.9.1 tree and module" >&2
  # Stage the 0.9.1 module restore FIRST and verify the copy fully
  # succeeds, so a failure here cannot leave the 0.9.1 configuration
  # paired with the 0.9.0 module.
  sudo cp -a -- "$MODULES_DIR/.ngx_http_markdown_filter_module.so.pre-0.9.0.bak" \
      "$MODULES_DIR/.ngx_http_markdown_filter_module.so.restore-failed" || {
    echo "ERROR: 0.9.1 module restore copy failed; NGINX remains stopped. Restore manually from $MODULES_DIR/.ngx_http_markdown_filter_module.so.pre-0.9.0.bak and ${CONFIG_DIR}.pre-0.9.0" >&2
    exit 1
  }
  sudo mv -f -- "$MODULES_DIR/.ngx_http_markdown_filter_module.so.restore-failed" \
      "$MODULES_DIR/ngx_http_markdown_filter_module.so" || {
    echo "ERROR: 0.9.1 module restore replace failed; NGINX remains stopped. Restore manually from $MODULES_DIR/.ngx_http_markdown_filter_module.so.pre-0.9.0.bak and ${CONFIG_DIR}.pre-0.9.0" >&2
    exit 1
  }
  # Module is back to 0.9.1; now restore the 0.9.1 configuration tree.
  # Guard BOTH moves: a failure in either must not leave the active
  # module and configuration tree inconsistent.
  sudo mv -- "${CONFIG_DIR}" "${CONFIG_DIR}.restore-failed" || {
    echo "ERROR: could not move the active configuration tree aside; NGINX remains stopped. Restore manually from $MODULES_DIR/.ngx_http_markdown_filter_module.so.pre-0.9.0.bak and ${CONFIG_DIR}.pre-0.9.0" >&2
    exit 1
  }
  sudo mv -- "${CONFIG_DIR}.pre-0.9.0" "${CONFIG_DIR}" || {
    # The 0.9.1 configuration restore failed.  The active tree was
    # moved to ${CONFIG_DIR}.restore-failed at this point — and that
    # tree is the 0.9.0 configuration, NOT the 0.9.1 tree.  Restoring
    # it would pair the 0.9.0 configuration with the already-restored
    # 0.9.1 module.  Fail closed instead: the 0.9.1 tree is still at
    # ${CONFIG_DIR}.pre-0.9.0 and the module is 0.9.1, so the operator
    # can complete the pair manually.
    echo "ERROR: 0.9.1 configuration restore failed; NGINX remains stopped. The 0.9.1 tree is at ${CONFIG_DIR}.pre-0.9.0 and the 0.9.1 module is installed; restore manually with: sudo mv -- ${CONFIG_DIR}.pre-0.9.0 ${CONFIG_DIR}" >&2
    exit 1
  }
  exit 1
fi
sudo nginx
```

**Warning:** 0.9.0 uses Rust 1.91 baseline. Source builders must downgrade
their toolchain or use prebuilt 0.9.0 binaries.

---

## Static configuration rollback

The diagnostics endpoint is read-only and accepts only `GET` and `HEAD`. The
0.9.2 runtime no longer includes the dynconf watcher or rollback file. To roll back a
configuration change, restore the versioned static `nginx.conf` that matches the
module binary, run `nginx -t`, and perform the normal controlled restart. Do
not send `POST /nginx-markdown/diagnostics?action=rollback`. No runtime
rollback API exists.


## Known Irreversible Changes

There is no irreversible on-disk state change, but the public configuration
and bundled ABI changes are not reversible by swapping only the binary:

- Diagnostics mapping fix is backward-compatible
- C reason code constants include the 0.9.2 registry additions
- The 0.9.2 production surface removed OTel
- Runtime dynconf no longer exists. Operators restore a versioned configuration
  and retain an auditable change record.
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
| Dynconf diagnostics | The runtime subsystem is removed; restore matching static configuration |
| Streaming terminal diagnostics | The retired standalone decision-state model is absent; rely on the current phase/terminal latch diagnostics and shared lowercase reason registry |
| Prometheus metric families | **Differ between the versions.** 0.9.2 exposes exactly the ten frozen v1 families (`nginx_markdown_build_info`, `nginx_markdown_conversion_attempts_total`, `nginx_markdown_conversion_deliveries_total`, `nginx_markdown_conversion_duration_seconds`, `nginx_markdown_decompression_events_total`, `nginx_markdown_input_bytes_total`, `nginx_markdown_output_bytes_total`, `nginx_markdown_requests_total`, `nginx_markdown_streaming_events_total`, `nginx_markdown_streaming_peak_memory_bytes`). The 0.9.1 binary re-emits the legacy surface it shipped with: per-path families (`per_path_conversions_total`, `per_path_overflow_total`, …), shadow metrics, profile/passthrough/decision families, and the debug/perf families removed in 0.9.2 (see `docs/guides/prometheus-metrics.md`). Renamed families include `conversions_total` → `conversion_attempts_total`/`conversion_deliveries_total`, `decompressions_total` → `decompression_events_total`, and `streaming_failure_total` → `streaming_events_total` labels. |

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
