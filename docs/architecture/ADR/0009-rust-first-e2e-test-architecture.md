# ADR-0009: Rust-First E2E Test Architecture with Hybrid Runtime Coverage

## Status

Accepted

## Context

Before 0.6.3, product-level E2E assertions were mostly implemented as
shell-driven scripts. This gave practical runtime coverage but created
long-term maintenance pressure:

- assertion logic was spread across many shell files
- scenario parity between similar checks was harder to enforce
- typed assertions and reusable fixtures were limited compared with Rust

At the same time, not every E2E concern should move at once. Some scenarios are
still better served by focused native scripts that own runtime bootstrapping or
proxy/TLS behavior in the current release window.

The project needed one durable architecture decision that explains:

1. what moved to Rust in 0.6.3
2. what remains on shell paths and why
3. how `make test-e2e` remains stable while implementation evolves

## Decision

Adopt a Rust-first E2E architecture for product-level HTTP scenario assertions,
while keeping a hybrid suite during migration.

### 1. Rust becomes the canonical implementation for migrated E2E scenarios

The following scenarios are implemented in `tools/e2e-harness/src/scenarios/`:

- `accept-negotiation`
- `metrics-endpoint`
- `conditional-requests`
- `auth-cache`
- `status-codes`

For these scenarios, shell entrypoints are wrappers that delegate to
`e2e-harness scenario <name>`.

### 2. Keep `make test-e2e` as the stable public contract

`make test-e2e` continues to be the canonical user-facing command and runs the
hybrid suite. Migration is an implementation change, not a contract change.

### 3. Preserve non-migrated native E2E paths where they remain higher-signal

Some E2E paths remain shell-owned in 0.6.3 (for example proxy/TLS runtime
checks and deferred streaming-heavy scenarios). These remain valid until they
have equal-or-better Rust coverage and lifecycle guarantees.

### 4. Preserve C test ownership for C-boundary concerns

This ADR does not change C test ownership for NGINX/C internals, FFI boundary,
or compiler/toolchain-sensitive checks. Those boundaries are defined by
`docs/testing/C_TEST_BOUNDARY.md`.

## Consequences

### Positive Consequences

- E2E assertion logic is more structured and type-safe for migrated scenarios.
- Scenario parity is easier to review and maintain across releases.
- Migration can proceed incrementally without breaking user entrypoints.
- Runtime-specific shell checks remain available where they are still the
  strongest verification path.

### Negative Consequences

- The test surface is temporarily hybrid (Rust + shell), which adds maintenance
  coordination cost.
- Wrapper parity must be actively validated to avoid drift.
- CI and docs must keep migrated/non-migrated boundaries synchronized.

## Alternatives Considered

### 1. Keep all E2E checks in shell

Rejected because long-term maintainability and parity governance are weaker than
Rust harness scenarios.

### 2. Big-bang move of every E2E scenario to Rust in one release

Rejected because it increases delivery risk and would weaken confidence for
runtime-heavy scenarios that still rely on mature shell orchestration in 0.6.3.

### 3. Move product-level E2E into C integration tests instead of Rust harness

Rejected because these concerns are HTTP/runtime behavior checks, not NGINX-C
internal behavior; C boundary tests remain separate by design.

## References

- [../../testing/E2E_TESTS.md](../../testing/E2E_TESTS.md)
- [../../archive/project-history/0.6.3-e2e-parity.md](../../archive/project-history/0.6.3-e2e-parity.md)
- [../../archive/project-history/0.6.3-test-surface-audit.md](../../archive/project-history/0.6.3-test-surface-audit.md)
- [../../testing/C_TEST_BOUNDARY.md](../../testing/C_TEST_BOUNDARY.md)
- [0005-repo-owned-harness.md](0005-repo-owned-harness.md)
- [0001-use-rust-for-conversion.md](0001-use-rust-for-conversion.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.3 | 2026-05-13 | Kang | Initial ADR for Rust-first E2E test architecture |
