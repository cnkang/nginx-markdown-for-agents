# Migration Guide: 0.9.1 → 0.9.2

## Overview

**0.9.1 → 0.9.2 is a non-breaking release.** There are no configuration
directive changes, no ABI changes, and no metric contract changes. All
0.9.1 configurations are valid under 0.9.2 without modification.

This guide documents the new features and behavioral corrections available
in 0.9.2.

**Upgrade path:** replace the module binary, restart NGINX, and optionally
adopt the new features described below.

---

## New Features

### Diagnostics `reason_to_code` Mapping Fix

The `bypass_no_transform` reason code was missing from the diagnostics
`reason_to_code` mapping table. This caused the diagnostics endpoint to
omit the numeric code for `bypass_no_transform` decisions.

**Impact:** Operators using the diagnostics endpoint to map reason strings
to numeric codes will now see the complete mapping. No configuration change
required.

### C Reason Code Constants Synchronized

The C module reason code constants in
`components/nginx-module/src/ngx_http_markdown_reason.c` now include the
eight decompression error-series constants (codes 4–11). The Rust converter
emitted these codes, but the C module previously lacked symbolic definitions
for them.

**Impact:** Source builders and integrators referencing C reason code
constants for decompression errors can now use the complete set. No
configuration change required.

### OTel Ownership on Reload/Shutdown

The experimental OTel implementation is request-scoped. Spans and their
nonblocking export subrequests use request-pool ownership; there are no
worker-owned OTel threads, queues, timers, or file descriptors to flush at
reload or shutdown. No configuration change is required.

### Dynconf Restore Procedure

The diagnostics endpoint is read-only and accepts only `GET` and `HEAD`. It has
no runtime rollback action. To restore a previous dynamic configuration,
write a complete valid key/value file to a temporary file and atomically rename
it over `markdown_dynamic_config_path`; the watcher will validate the changed
file and promote it through the normal reload path. The internal LKG snapshot
protects the active state when the restored file is invalid.

See [DYNAMIC_CONFIG.md](DYNAMIC_CONFIG.md) and
[ROLLBACK-0.9.2.md](ROLLBACK-0.9.2.md) for the complete procedure.

**Impact:** Opt-in. Existing configurations are unaffected.

### Public Surface Inventory and Drift Detection Gate

A new release gate (`make release-gates-check-092`) validates that the
public API surface (FFI exports, configuration directives, metric names,
reason codes) has not drifted from the declared inventory. This is a
build-time and CI gate, not a runtime feature.

**Impact:** No configuration change required. CI and release workflows
should add `make release-gates-check-092` to their gate chain.

### Release Gates 0.9.2

The `release-gates-check-092` Make target provides the 0.9.2 release gate
chain, additive on 0.9.1 gates:

```bash
make release-gates-check-092
```

This includes all 0.9.1 gates plus the public surface drift detection gate.

---

## Fixed

- `stream_state` `PRE_COMMIT` fallthrough now logs an invariant violation
  instead of silently advancing state. This improves debuggability for
  streaming backpressure edge cases.

---

## No Action Required

All 0.9.1 configurations are valid under 0.9.2. There are no:

- Directive additions, removals, or renames
- ABI changes
- Metric contract changes
- Default behavior changes

---

## Verification

After upgrading, verify:

```bash
# Configuration still valid
sudo nginx -t

# Doctor check
bash tools/doctor/nginx-markdown-doctor.sh

# Diagnostics endpoint shows complete reason_to_code mapping
curl -s http://localhost/nginx-markdown/diagnostics | \
  python3 -m json.tool | grep bypass_no_transform
```

---

## Previous Versions

| From | To | Guide |
|------|----|-------|
| 0.9.0 | 0.9.1 | [docs/guides/MIGRATION-0.9.1.md](MIGRATION-0.9.1.md) |
| 0.8.x | 0.9.0 | [docs/guides/MIGRATION-0.9.md](MIGRATION-0.9.md) |
| 0.7.x | 0.8.0 | [docs/guides/MIGRATION-0.8.md](MIGRATION-0.8.md) |

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-07-30 | Kang | Initial migration guide for 0.9.1 → 0.9.2 |
