# ADR-0019: 0.9.0 Production Readiness Release Gate Framework

## Status

Accepted (0.9.0 contract freeze — Wave 1; gate implementation lands in Wave 7)

## Context

Each release line has a versioned gate (`release-gates-check-070` …
`-080`). 0.9.0 is a breaking production-readiness release and needs its own
capstone gate. This ADR freezes the gate **name**, structure, and blocking
semantics now (Wave 1) so downstream specs reference a stable target; the gate body is implemented in the
release-gates wave (final wave).

## Decision

### Frozen names (1.0-stable)

- Make target: `make release-gates-check-090` (**new**; does not exist yet).
- Validators: `tools/release/gates/validate_release_gates_090.py`,
  `validate_config_directives.py` (merged validator covering v0.7.0–v0.8.0 directives).
- Production-examples smoke: `make test-production-examples-nginx-t` and
  `make test-production-examples-e2e-smoke` (**new**).

### Structure

`release-gates-check-090` is modeled on the real 17-step `-080` gate
(`Makefile:568`), extended with 0.9.0-specific steps (Config V2 reject-only
golden errors, HeaderPlan fault-injection, reason-registry/diagnostics renderer
contract, production-examples smoke, version-consistency). It MUST NOT recursively
invoke `release-gates-check-080` from inside its own recipe; prior-version
validators it reuses are invoked directly and **caller-parameterized** for the
active version (`RELEASE_GATE_EXPECTED_CARGO_VERSION=0.9.0`), per AGENTS.md Rule
13.

### Blocking semantics (frozen)

| Class | Examples | Behavior |
|-------|----------|----------|
| **Blocking** | build/check-headers, `test-rust`, `test-nginx-unit`, `test-e2e-rust`, docs-check, harness-check, gate/naming/config-directive validators, **production-examples `nginx -t` smoke** | fail → gate fails |
| **Capstone** | aggregate 0.9.0 contract validators (config V2, reason registry, schema v1, HeaderPlan) | fail → gate fails |
| **Soft / skippable** | `test-rust-fuzz-smoke`, `verify-chunked-native-e2e-smoke` (needs `NGINX_BIN`), `coverage-c`/`coverage-rust`, sanitizer smoke, security-static, supply-chain, perf | env-gated skip with recorded evidence; not silently promoted to blocking |
| **Env-limited blocking** | `test-production-examples-nginx-t` (needs `NGINX_BIN`) | fails when binary absent **unless** `RELEASE_GATE_ALLOW_SKIP_MODULE=1`, mirroring the 091 module-benchmark skip contract; tag-release CI must still provide `NGINX_BIN` |

Production-examples smoke is **0.9.0 blocking**. When the module-enabled
NGINX binary is genuinely unavailable (for example local macOS without a
module-enabled `nginx`), `RELEASE_GATE_ALLOW_SKIP_MODULE=1` records a SKIP
and lets non-release validation proceed; tag-release CI checkouts must
provide `NGINX_BIN` and must not rely on this escape hatch. Soft gates emit a
summary/artifact and must not silently skip; if a soft toolchain is genuinely
absent the gate records the skip reason rather than weakening the check. Gate
validators must run in a clean checkout and must not depend on untracked local
working directories.

## Consequences

### Positive

- Stable, referenceable gate name for the production-examples and
  release-gates work and CI.
- Clear blocking vs soft taxonomy avoids accidental hard-blocking on optional
  toolchains.
- Clean-checkout discipline keeps the gate runnable in CI.

### Negative

- New validators must be written and kept in sync with the active version.
- Production-examples smoke requires the example configs to be `nginx -t` clean
  (production-examples dependency).

## Alternatives Considered

- **Reuse `-080` directly**: rejected — 0.9.0 is breaking; `-080` asserts 0.8.x
  invariants and would not validate Config V2 / reason registry / schema v1.
- **Recursive `make release-gates-check-080` call inside `-090`**: rejected per
  Rule 13 (duplicate/expensive re-runs, version-assertion conflicts).

## References

- [ADR-0014: Release Matrix Source of Truth](0014-release-matrix-source-of-truth.md)
- [ADR-0015: Config V2 Breaking Migration](0015-090-config-v2-breaking-migration.md)
- The existing `release-gates-check-080` recipe and gate validators under
  `tools/release/gates/` model the 0.9.0 gate structure.
- AGENTS.md Rule 13, 48, 55

## Date

2026-06-30

## Authors

Kang

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.0 | 2026-06-30 | Kang | Initial ADR — 0.9.0 gate name/structure/blocking-semantics freeze (body implemented in Wave 7) |
| 0.9.1 | 2026-07-14 | Kang | Add `RELEASE_GATE_ALLOW_SKIP_MODULE=1` env-limited skip for `test-production-examples-nginx-t`, mirroring the 091 module-benchmark skip contract |
