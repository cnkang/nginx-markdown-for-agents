# ADR-0026: Dynamic Configuration File Restore Without a Runtime Rollback API

## Status

Accepted

## Date

2026-07-30

## Context

The module watches dynamic configuration and applies it in worker-local state. A runtime
rollback endpoint would therefore restore one worker's snapshot while other
workers could continue serving a different configuration. The 0.9.2 work
also needs a clear distinction between the internal last-known-good (LKG)
snapshot used to protect failed reloads and an operator-requested restore.

## Decision

Keep the diagnostics endpoint read-only: the endpoint accepts only `GET` and `HEAD`.
No rollback action or rollback response schema gets exposed. Operators
restore a previous dynamic configuration by writing a complete valid file to a
temporary path and atomically renaming it over the watched path. The normal
watcher then parses, validates, and promotes that file for every worker.

The internal LKG state remains available for diagnostics and failed-reload
protection. A failed restore does not replace the active snapshot or advance
the applied mtime. Dry-run outcomes record the observed mtime so unchanged
content is not repeatedly validated, while a later file change starts a new
validation.

## Consequences

### Positive Consequences

- File replacement gives operators one auditable input for all workers.
- Restore uses the same validation and promotion path as normal reloads.
- The read-only diagnostics contract avoids a worker-divergence API.

### Negative Consequences

- Operators need filesystem access to replace the watched configuration file.
- Operators must verify asynchronous restore through diagnostics or normal
  reload logs.
- The LKG snapshot is not a public command and cannot itself repair a file.

## Alternatives Considered

- **Worker-local runtime rollback endpoint:** rejected because workers could
  restore independently and serve divergent snapshots.
- **In-place writes to the watched file:** rejected because the watcher could
  read a partially written configuration.
- **Module-version rollback only:** insufficient for restoring dynamic settings
  without changing the module binary. Retained only for module-wide rollback.

## References

- [Dynamic Configuration Guide](../../guides/DYNAMIC_CONFIG.md)
- [0.9.2 Migration Guide](../../guides/MIGRATION-0.9.2.md)
- [0.9.2 Rollback Guide](../../guides/VERSION_ROLLBACK-0.9.2.md)
- [Dynconf file restore implementation plan](../../development/0.9.2-implementation-plan.md)
- [Dynconf implementation](../../../components/nginx-module/src/ngx_http_markdown_dynconf_impl.h)
- [ADR-0015: Config V2 Breaking Migration](0015-090-config-v2-breaking-migration.md)
