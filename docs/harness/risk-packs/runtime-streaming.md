# Runtime Streaming Pack

Use this as the primary pack when the change touches request streaming, pending
chains, fail-open flow, or deferred output ordering.

## Triggers

- primary surface identifiers: `streaming`, `pending-chain`, `fail-open`
- touched files under `components/nginx-module/**`
- touched files under
  `components/rust-converter/src/charset.rs`
- touched files under `tools/e2e-harness/**`
- keywords like `NGX_AGAIN`, `last_buf`, `pending chain`, `streaming`

## Common Supporting Packs

- `observability-metrics` when a streaming outcome changes logs, counters, or
  operator-visible behavior

## Sync Points

- pending chain persistence vs terminal buffer emission
- header-forwarded state vs fail-open branches
- UTF-8 tail handling vs chunk boundaries
- media URL extraction parity (`img`/`video`/`audio`/`source`/`track`/`area`)
  vs full-buffer behavior
- Derived-state reconciliation on multi-context drain (Rule 52): an end tag
  can pop contexts from the middle of the stack. In that case, the drain logic
  must update every derived state field for every popped context. These fields
  are `list_depth`, `blockquote_depth`, `in_preformatted`, and
  `ordered_list_counters`.
- replay/runtime fixtures vs new failure paths
- known-difference registry schema drift (`drift_type`/`severity`) vs parity
  suppressor semantics

## Minimum Verification

```bash
make harness-check
make test-rust-streaming
make test-nginx-unit-streaming
make verify-chunked-native-e2e-smoke
make test-e2e-rust
```

## Canonical References

- [../../architecture/REQUEST_LIFECYCLE.md](../../architecture/REQUEST_LIFECYCLE.md)
- [../../testing/README.md](../../testing/README.md)
- [../../../AGENTS.md](../../../AGENTS.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
