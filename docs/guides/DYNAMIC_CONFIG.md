# Dynamic Configuration Guide

## Table of Contents

1. [Overview](#overview)
2. [Enabling Dynamic Configuration](#enabling-dynamic-configuration)
3. [Supported Runtime Keys](#supported-runtime-keys)
4. [Reload Semantics](#reload-semantics)
5. [Last-Known-Good and Rollback](#last-known-good-and-rollback)
6. [Dry-Run Validation](#dry-run-validation)
7. [Operational Recommendations](#operational-recommendations)

---

## Overview

Dynamic configuration (dynconf) enables runtime modification of select
module parameters without restarting NGINX. A periodic timer (1s interval)
polls a configuration file for changes and applies them using a two-phase,
staged-commit model.

For directive syntax and full parameter reference, see
[CONFIGURATION.md](CONFIGURATION.md#dynamic-configuration-061).

---

## Enabling Dynamic Configuration

```nginx
http {
    markdown_dynamic_config on;
    markdown_dynamic_config_path /etc/nginx/markdown-dynconf.conf;
}
```

Dynconf is off by default. Enable it only when operational workflows
require hot-reload without restart.

---

## Supported Runtime Keys

| Key | Value | Maps to |
|-----|-------|---------|
| `schema_version` | `0.9` | **mandatory** — file rejected if missing or unknown |
| `markdown_filter` | `on` \| `off` | `conf->enabled` |
| `prune_noise` | `on` \| `off` | `conf->prune_noise` |
| `log_verbosity` | `error` \| `warn` \| `info` \| `debug` | `conf->log_verbosity` |
| `streaming_budget` | `<size>` (e.g. `64k`, `4m`) | `conf->streaming_budget` |
| `memory_budget` | `<size>` (e.g. `128k`) | `conf->memory_budget` |

### Schema Version (0.9.0+)

Every dynconf file **must** include `schema_version = 0.9`. This field
enables the module to reject configuration files written for incompatible
versions. Behavior:

- **Missing `schema_version`** → entire file rejected (INVALID_FILE)
- **Unknown version** (e.g. `1.0`, `0.8`) → entire file rejected
- **Correct version** (`0.9`) → file parsed normally

Structural directives (`markdown_content_types`, `markdown_stream_types`,
auth policy, conditional requests) require `nginx -s reload`.

---

## Reload Semantics

The dynconf timer performs a two-phase staged commit:

1. **Parse phase:** The entire file is parsed into a staging snapshot.
2. **Validate phase:** Every key and value is validated.
3. **Promote phase:** If all lines pass, the staging snapshot atomically
   replaces the active snapshot.

On any parse or validation error, the staging snapshot is discarded and
the active snapshot remains unchanged. Partial updates are never applied.

### `applied_mtime` Behavior

The `applied_mtime` timestamp records when the active snapshot was last
successfully updated. It updates only on successful reload — a failed
validation leaves `applied_mtime` unchanged, enabling retry on the next
poll cycle.

---

## Last-Known-Good and Rollback

The module maintains a last-known-good (LKG) configuration snapshot to
support safe rollback after a bad configuration change.

### LKG Preservation

When a reload succeeds, the previous active snapshot is preserved as the
last-known-good configuration. This happens automatically on every
successful reload cycle.

### Manual Rollback

To restore the LKG as the active configuration:

1. The operator triggers a rollback (via the diagnostics endpoint or by
   restoring the previous dynconf file contents).
2. The module replaces the active snapshot with the preserved LKG snapshot.
3. `applied_mtime` updates to reflect the rollback event.

### Timing Guarantees

- `applied_mtime` only updates on successful reload or rollback. A failed
  validation never advances the timestamp.
- Requests in flight at the time of rollback continue using their
  previously-bound snapshot (request consistency is preserved).
- The LKG snapshot is replaced only when a new reload succeeds — a failed
  reload does not discard the existing LKG.

### Example Scenario

```
Time 0: Active=v1, LKG=none
Time 1: Reload v2 succeeds → Active=v2, LKG=v1, applied_mtime updated
Time 2: Reload v3 fails   → Active=v2, LKG=v1, applied_mtime unchanged
Time 3: Rollback triggered → Active=v1, LKG=v1, applied_mtime updated
```

---

## Dry-Run Validation

The `markdown_dynconf_dry_run` directive validates dynconf changes without
applying them. When enabled, the timer parses and validates the
configuration file but does not promote the staging snapshot to active.

```nginx
markdown_dynconf_dry_run on;
```

Validation results are logged at `info` level, including line numbers and
field-level error details for any failures.

### Dry-Run Workflow

1. Enable dry-run mode and reload NGINX.
2. Write the new dynconf file.
3. Wait for the timer cycle (1s) and check the error log.
4. If validation passes, disable dry-run and reload to apply.

Dry-run mode is useful for pre-flighting configuration changes in
production environments where a bad dynconf file could affect traffic.

---

## Operational Recommendations

- Place the dynconf file on a local filesystem (not NFS) for reliable
  mtime detection.
- Use dry-run mode to validate changes before applying them in
  production.
- Monitor `applied_mtime` via the diagnostics endpoint to confirm
  successful reloads.
- Configure dynconf at the `http` or `server` level — only one global
  watcher per worker process is supported.
- Unknown dynconf keys cause atomic rejection of the entire file
  (not silent skip).

---

## Related Documents

- [CONFIGURATION.md](CONFIGURATION.md) — Full directive reference
- [OPERATIONS.md](OPERATIONS.md) — General operational guide

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.7.0 | 2026-05-17 | Kang | Initial creation: LKG/rollback semantics, dry-run validation, reload behavior |
| 0.9.0 | 2026-07-01 | Kang | Added mandatory schema_version field for version compatibility |
