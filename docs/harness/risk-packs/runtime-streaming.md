# Runtime Streaming Pack

Use this as the primary pack when the change touches request streaming, pending
chains, fail-open flow, or deferred output ordering.

## Triggers

- touched files under `components/nginx-module/**`
- touched files under `tools/e2e/**`
- keywords like `NGX_AGAIN`, `last_buf`, `pending chain`, `streaming`

## Common Supporting Packs

- `observability-metrics` when a streaming outcome changes logs, counters, or
  operator-visible behavior

## Sync Points

- pending chain persistence vs terminal buffer emission
- header-forwarded state vs fail-open branches
- UTF-8 tail handling vs chunk boundaries
- replay/runtime fixtures vs new failure paths

## Minimum Verification

```bash
make harness-check
make test-rust-streaming
make test-nginx-unit-streaming
make verify-chunked-native-e2e-smoke
```

## Canonical References

- [../../architecture/REQUEST_LIFECYCLE.md](../../architecture/REQUEST_LIFECYCLE.md)
- [../../testing/README.md](../../testing/README.md)
- [../../../AGENTS.md](../../../AGENTS.md)
