# E2E Migration Pack

Use this pack when Rust harness migration changes touch the canonical E2E
surface, wrapper parity, or harness governance for 0.6.3.

## Triggers

- touched `tools/e2e-harness/**`
- touched migrated wrapper paths under `tools/e2e/verify_*_e2e.sh`
- touched `tools/e2e/run_e2e_suite.sh`
- touched migration docs (`docs/project/0.6.3-e2e-parity.md`,
  `docs/project/0.6.3-test-surface-audit.md`, `docs/testing/E2E_TESTS.md`)
- keywords like `e2e-harness`, `bootstrap`, `reuse mode`, `wrapper parity`,
  `test-e2e-rust`

## Risks

- `make test-e2e` regresses on clean environments because harness binary
  resolution/build is not self-contained
- Bootstrap mode stays non-runnable while docs claim migration is complete
- migrated shell scripts retain scenario assertions and drift from the Rust
  canonical path
- fixture backends return descriptor payloads instead of real HTTP semantics
- harness policy/docs and validator logic drift from each other

## Common Supporting Packs

- `runtime-streaming` when migrated scenarios interact with streaming behavior
- `nginx-protocol-safety` when status/header/body semantics change
- `docs-tooling-drift` when migration status, parity tables, or release docs
  are updated

## Sync Points

- `make test-e2e` continues to pass through the canonical suite entrypoint
  while delegating migrated scenarios to Rust harness execution
- `make test-e2e-rust` is executable with clear runtime resolution semantics
  for both Reuse and Bootstrap paths
- migrated wrappers (if retained) delegate to `e2e-harness scenario <name>`
  and do not keep scenario-specific assertions
- `tools/harness/check_harness_sync.py` enforces the harness contract for
  `tools/e2e-harness/` and its binary declaration
- migration docs, release notes, and AGENTS rules describe the same current
  migration state

## Minimum Verification

```bash
make docs-check
make harness-check
make test-e2e-rust
make test-e2e
cargo fmt --check --manifest-path tools/e2e-harness/Cargo.toml
cargo clippy --manifest-path tools/e2e-harness/Cargo.toml --all-targets --all-features -- -D warnings
cargo test --manifest-path tools/e2e-harness/Cargo.toml --all-features
```

## Canonical References

- [../../../AGENTS.md](../../../AGENTS.md) (Rule 37)
- [../../testing/E2E_TESTS.md](../../testing/E2E_TESTS.md)
- [../../archive/project-history/0.6.3-e2e-parity.md](../../archive/project-history/0.6.3-e2e-parity.md)
- [../../archive/project-history/0.6.3-test-surface-audit.md](../../archive/project-history/0.6.3-test-surface-audit.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.3 | 2026-05-12 | Kang | Initial migration risk pack for Rust-first E2E harness rollout |
