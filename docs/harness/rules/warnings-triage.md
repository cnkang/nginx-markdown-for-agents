---
domain: warnings-triage
rules: [21]
paths:
  - "components/rust-converter/**"
  - "components/nginx-module/**"
---

# Warnings Triage

### 21. Warning triage and command reproducibility guardrails

Required:
- Treat compiler/test warnings as triage items, not automatic cleanup work:
  classify each warning as either a real defect signal or expected test-harness
  structure before deciding whether to change code.
- When a warning is reported from one test target, run at least one broader
  compile-only sweep for the touched area (for example `cargo test --tests
  --no-run`) to detect the full warning surface before applying fixes.
- Prefer fixing real unused-code warnings by removing stale fields/assignments
  or consuming values in assertions; use blanket `allow` only for deliberate
  shared test-support modules and keep the scope minimal.
- In multi-crate repositories, reproduce user-provided commands in the correct
  crate directory if repository root has no `Cargo.toml`; explicitly record the
  effective working directory used for verification.
