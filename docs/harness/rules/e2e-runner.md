---
domain: e2e-runner
rules: [37]
paths:
  - "tools/e2e-harness/**"
  - "tools/e2e/**"
---

## E2E Test Runner

### 37. Rust-first E2E test runner (e2e-harness)

Required:
- New product-level HTTP E2E scenarios must be implemented in
  `tools/e2e-harness/` as Rust scenario modules under
  `src/scenarios/`.  Each scenario must follow the established pattern:
  Reuse_Mode executes actual HTTP tests against a provided NGINX binary;
  Bootstrap_Mode must resolve a runnable runtime directly or through a
  documented compatibility bridge while preserving harness-owned scenario
  execution and assertions.
- Adding new Python pytest files under
  `components/nginx-module/tests/e2e/` is forbidden.  That directory is
  classified as Remove in the 0.6.3 test surface audit; no new files may be
  added.
- Adding new independent shell E2E scenario scripts under `tools/e2e/`
  that contain embedded assertion logic is forbidden for product-level HTTP
  behavior.  New scenarios must use the Rust harness.
- Allowed exceptions:
  - Thin shell wrappers in `tools/e2e/` that delegate to
    `e2e-harness scenario <name>` are permitted.
  - Shell scripts for 0.6.3-deferred scenarios (streaming, security, etc.)
    remain on their current paths until migrated in a future release.
  - `tools/e2e/run_e2e_suite.sh` retains its role as the canonical
    `make test-e2e` orchestrator, delegating migrated scenarios to the
    e2e-harness binary.
- `make test-e2e-rust` builds and runs the Rust harness migrated suite.
  `make test-rust` continues to refer only to the Rust converter test suite.
- Every migrated scenario must have a parity entry in
  `docs/project/0.6.3-e2e-parity.md` documenting the shell source,
  Rust scenario, case mapping, and any parity gaps.

Verification:
- `cargo build --manifest-path tools/e2e-harness/Cargo.toml`
- `cargo test --manifest-path tools/e2e-harness/Cargo.toml`
- `cargo fmt --check --manifest-path tools/e2e-harness/Cargo.toml`
- `cargo clippy --manifest-path tools/e2e-harness/Cargo.toml --all-targets --all-features -- -D warnings`
- `make test-e2e-rust`
