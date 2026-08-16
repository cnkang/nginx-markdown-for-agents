# Dynamic Configuration Guide

## Table of Contents

1. [Overview](#overview)
2. [Enabling Dynamic Configuration](#enabling-dynamic-configuration)
3. [Supported Runtime Keys](#supported-runtime-keys)
4. [Precedence and Masked Keys](#precedence-and-masked-keys)
5. [Reload Semantics](#reload-semantics)
6. [Last-Known-Good and Rollback](#last-known-good-and-rollback)
7. [Dry-Run Validation](#dry-run-validation)
8. [Operational Recommendations](#operational-recommendations)

---

## Overview

Dynamic configuration (dynconf) enables runtime modification of select
module parameters without restarting NGINX. A periodic timer (1s interval)
polls a configuration file for changes and applies them using a two-phase,
staged-commit model.

The project classifies dynconf as `STABLE_FOR_1_0`. The supported key set, atomic staged
promotion, one-snapshot-per-request rule, dry-run behavior, and
last-known-good rollback form the compatibility contract. See the
[Public Surface Inventory](../architecture/PUBLIC_SURFACE_INVENTORY.md#dynamic-configuration-contract)
for the production evidence and freeze boundary.

For directive syntax and full parameter reference, see
[CONFIGURATION.md](CONFIGURATION.md#dynamic-configuration-dynconf).

---

## Enabling Dynamic Configuration

```nginx
http {
    markdown_dynamic_config on;
    markdown_dynamic_config_path /etc/nginx/markdown-dynconf.json;
}
```

Dynconf is off by default. Enable it only when operational workflows
require hot-reload without restart.

---

## Supported Runtime Keys

The watched file is JSON. It must contain `"schema_version": 1` and may
contain only these runtime keys:

| Key | Value |
|-----|-------|
| `filter` | `on`, `off` |
| `prune_noise` | `on`, `off` |
| `log_verbosity` | `error`, `warn`, `info`, `debug` |
| `error_policy` | `pass`, `fail_closed`, `status 429`, `status 503` |
| `streaming_buffer` | integer bytes from 64 KiB through 1 GiB |

The 0.9.2 default for `streaming_buffer` is 2 MiB, the same default that
0.9.1 used. The 256 KiB value appeared only in the removed `balanced` and
`streaming_first` profiles. Operators who explicitly pinned those profiles
should pin `streaming_buffer=262144` in `markdown_limits` to retain it.

Example:

```json
{
  "schema_version": 1,
  "filter": "on",
  "prune_noise": "off",
  "log_verbosity": "info",
  "error_policy": "pass",
  "streaming_buffer": 2097152
}
```

Missing or unknown schema versions, unknown keys, duplicate keys, invalid
types, and out-of-range values reject the entire file. Structural directives
(`markdown_content_types`, `markdown_stream_excluded_types`, auth policy, and
conditional requests) require `nginx -s reload`.

## Precedence and Masked Keys

Dynconf values participate in a five-tier precedence order, from highest to
lowest:

1. Request-variable evaluation of `markdown_filter`.
2. Explicit static server/location configuration.
3. A dynconf runtime value when the field is not blocked.
4. An inherited `http {}` baseline.
5. The built-in default.

An explicit server or location value sets that field's block bit and remains
effective after a dynconf reload. An `http {}` baseline supplies the fallback
but does not block a runtime override. When a candidate contains a blocked
key, the watcher logs a warning. Diagnostics exposes the key in
`configuration.dynconf.masked_keys`. The static value remains effective.

The configuration cycle aggregates the minimum applicable conversion-memory
value and the union of dynconf block masks across merged locations. This
summary is cycle-owned and does not maintain a fixed-size per-location index.

### Migrating legacy line-format files

Older releases used line-format keys. Convert them before relying on the JSON
v1 contract:

```text
schema_version=0.9
markdown_filter=on
streaming_budget=16m
memory_budget=64m
```

becomes:

```json
{
  "schema_version": 1,
  "filter": "on",
  "streaming_buffer": 16777216
}
```

`memory_budget` is no longer a runtime key. Use static
`markdown_limits conversion_memory=<size>` as its replacement. When a watched file's
first byte is not `{`, the watcher logs
`legacy line format detected - migrate to JSON v1` once per worker.

---

## Reload Semantics

The dynconf timer performs a three-phase staged commit:

1. **Parse stage:** The module parses the entire file into a staging snapshot.
2. **Validate stage:** The module validates every key and value.
3. **Promote stage:** If all fields pass, the staging snapshot atomically
   replaces the active snapshot.

On any parse or validation error, the module discards the staging snapshot.
The active snapshot remains unchanged. Partial updates are never applied.

### Diagnostics and reload state

The diagnostics endpoint reports the dynconf `generation` and
`last_success` fields for an active or last-known-good snapshot. A successful
reload advances the generation and records `last_success`. A failed
validation leaves the active snapshot and generation unchanged, enabling a
retry on the next poll cycle.

The diagnostics `configuration.dynconf.masked_keys` array lists runtime keys
that static configuration blocked in the last validated candidate.

---

## Last-Known-Good and Rollback

The module maintains a last-known-good (LKG) configuration snapshot for state
tracking and diagnostics when the module promotes a new configuration.

### LKG Preservation

When a reload succeeds, the module preserves the previous active snapshot as
the last-known-good configuration. This happens automatically on every
successful reload cycle.

After the first successful reload, the module retains the static snapshot as
the bootstrap LKG when applicable. That snapshot has no dynconf canonical
digest, so diagnostics renders `configuration.dynconf.lkg_digest` as `null`.
After each later successful reload, `lkg_digest` identifies the previous
active dynconf configuration. See
[`schemas/diagnostics.schema.json`](../../schemas/diagnostics.schema.json) for
the schema contract.

### Operator Rollback

The diagnostics endpoint is read-only and accepts only `GET` and `HEAD`. It
does not expose a rollback operation. To roll back, atomically restore a
previous valid dynconf file (with a changed modification time). The normal
poll cycle parses and validates the restored contents before promoting them as
a new active snapshot.

### Timing Guarantees

- `generation` and `last_success` advance only on a successful reload. A
  failed validation never advances either value.
- Requests in flight at the time of a restoring reload continue using their
  previously-bound snapshot (request consistency stays preserved).
- The LKG snapshot gets replaced only when a new reload succeeds — a failed
  reload does not discard the existing LKG.

### Example Scenario

```
Time 0: Active=v1, LKG=none
Time 1: Reload v2 succeeds → Active=v2, LKG=v1, generation/last_success updated
Time 2: Reload v3 fails   → Active=v2, LKG=v1, generation/last_success unchanged
Time 3: Restore v1 file
Time 4: Reload v1 succeeds → Active=v1, LKG=v2, generation/last_success updated
```

---

## Dry-Run Validation

The `markdown_dynconf_dry_run` directive validates dynconf changes without
applying them. When enabled, the timer parses and validates the
configuration file but does not promote the staging snapshot to active.

```nginx
markdown_dynconf_dry_run on;
```

The module logs a bounded categorical validation result at `info` level for
the dry-run candidate. It does not include raw file paths, secrets, or raw
configuration content in the result.

### Dry-Run Workflow

1. Enable dry-run mode and reload NGINX.
2. Write the new dynconf file.
3. Wait for the timer cycle (1s) and check the error log.
4. If validation passes, disable dry-run and reload to apply.

Dry-run mode is useful for pre-flighting configuration changes in
production environments where a bad dynconf file could affect traffic.

---

## Operational Recommendations

- Place the dynconf file on a local filesystem (not NFS) for reliable file
  change detection.
- Use dry-run mode to validate changes before applying them in
  production.
- Monitor `configuration.dynconf.generation` and
  `configuration.dynconf.last_success` via the diagnostics endpoint to
  confirm successful reloads.
- Configure all dynconf directives at the `http` level. The watcher is
  process-wide, with one active file watcher per worker process.
- Unknown dynconf keys cause atomic rejection of the entire file
  (not silent skip).

---

## Related Documents

- [CONFIGURATION.md](CONFIGURATION.md) — Full directive reference
- [OPERATIONS.md](OPERATIONS.md) — General operational guide

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-07 | Codex | Aligned the guide with the JSON schema v1 dynconf contract |
| 0.7.0 | 2026-05-17 | Kang | Initial creation: LKG/rollback semantics, dry-run validation, reload behavior |
