# v0.7.0 Release Gate Definitions

| Field | Value |
|-------|-------|
| Version | 0.7.0 |
| Status | ACTIVE |
| Created | 2026-05-17 |
| Source | design.md §14.0 |
| Detailed Gates | [0.7.0-release-gates.md](./0.7.0-release-gates.md) |

> This document defines the 5 release gates for v0.7.0 as specified in the
> technical design (§14.0). Each gate must pass before the release can proceed.
> For detailed check items, verification commands, and Go/No-Go criteria, see
> [0.7.0-release-gates.md](./0.7.0-release-gates.md).

---

## Gate Dependencies

```
Gate 1: P0 Runtime Correctness
  └─► Gate 2: Rust-first Architecture (depends on Gate 1)
        └─► Gate 3: Package Distribution (depends on Gate 2)
              └─► Gate 4: K8s/Ingress (depends on Gate 3)

Gate 5: Operability + Docs (depends on Gate 1; can run parallel to Gates 2-4)
```

**Release-blocking scope**: Gates 1, 2, and 5 are blocking for 0.7.0 GA.
Gates 3 and 4 are tracked as future/feasibility unless explicitly promoted.

---

## Gate 1: P0 Runtime Correctness

**Prerequisites**: None

| # | Check Item | Verification Command | Pass Criteria |
|---|-----------|---------------------|---------------|
| 1.1 | NGINX streaming unit tests | `make test-nginx-unit-streaming` | Exit 0 |
| 1.2 | Rust full test suite | `make test-rust` | Exit 0 |
| 1.3 | Rust fuzz smoke | `make test-rust-fuzz-smoke` | Exit 0, no crashes |
| 1.4 | Chunked E2E smoke | `make verify-chunked-native-e2e-smoke` | Exit 0 |
| 1.5 | Coverage aggregate ≥ 80% | `make coverage-c && make coverage-rust` | C ≥ 80%, Rust ≥ 80% |
| 1.6 | Coverage critical paths ≥ 90% | Coverage report review | Critical paths ≥ 90% (required) |

**Fail action**: Block release. Fix runtime correctness issues before proceeding.

---

## Gate 2: Rust-first Architecture

**Prerequisites**: Gate 1 passes

| # | Check Item | Verification Command | Pass Criteria |
|---|-----------|---------------------|---------------|
| 2.1 | FFI boundary tests | `make test-rust && make test-nginx-unit` | Exit 0 |
| 2.2 | cbindgen header drift check | `make build && make check-headers` | No drift detected |
| 2.3 | Rust E2E tests | `make test-e2e-rust` | Exit 0 |
| 2.4 | Migration contract doc coverage | Manual review | All FFI functions documented in `docs/architecture/FFI_MIGRATION_CONTRACT.md` |

**Fail action**: Block release. Resolve FFI boundary or migration contract gaps.

---

## Gate 3: Package Distribution

**Prerequisites**: Gate 2 passes

| # | Check Item | Verification Command | Pass Criteria |
|---|-----------|---------------------|---------------|
| 3.1 | DEB build (full matrix) | CI workflow `release-deb.yml` | All matrix entries build successfully |
| 3.2 | RPM build (full matrix) | CI workflow `release-rpm.yml` | All matrix entries build successfully |
| 3.3 | Package signature verification | `gpg --verify` / `rpm -K` | Valid signatures |
| 3.4 | Install smoke tests | `dpkg -i` + `rpm -i` + `nginx -V` + `curl` | Module loads and converts |
| 3.5 | Upgrade/rollback tests | Version switch test | Upgrade and rollback succeed without service interruption |

**Fail action**: Non-blocking for 0.7.0 GA (future scope). Track as P1 for next release.

---

## Gate 4: K8s/Ingress

**Prerequisites**: Gate 3 passes

| # | Check Item | Verification Command | Pass Criteria |
|---|-----------|---------------------|---------------|
| 4.1 | Helm chart lint | `helm lint charts/nginx-markdown` | Exit 0, no errors |
| 4.2 | K8s smoke tests | Smoke script execution | Conversion, Accept negotiation, /metrics all pass |
| 4.3 | F5 feasibility assessment | Documentation review | Assessment document complete with conclusions |

**Fail action**: Non-blocking for 0.7.0 GA (feasibility scope). Track findings for future releases.

---

## Gate 5: Operability + Docs

**Prerequisites**: Gate 1 passes (can run in parallel with Gates 2-4)

| # | Check Item | Verification Command | Pass Criteria |
|---|-----------|---------------------|---------------|
| 5.1 | Harness full verification | `make harness-check-full` | Exit 0 |
| 5.2 | Docs check | `make docs-check` | Exit 0 |
| 5.3 | Release gates strict | `make release-gates-check-strict` | Exit 0 |
| 5.4 | All new reason codes have validator | Validator coverage check | No unvalidated reason code |
| 5.5 | All new metrics have validator | Validator coverage check | No unvalidated metric |
| 5.6 | All new configs have validator | Validator coverage check | No unvalidated config directive |

**Fail action**: Block release. Docs/harness/validator gaps must be resolved.

---

## Validator Coverage Matrix (Gate 5 Detail)

| New Surface | Validator Type | Gate |
|-------------|---------------|------|
| `decompression_budget` config | config validator | Gate 5 |
| `parse_timeout` config | config validator | Gate 5 |
| `markdown_parser_budget` config | config validator | Gate 5 |
| `markdown_diagnostics` config | config validator | Gate 5 |
| `markdown_dynconf_dry_run` config | config validator | Gate 5 |
| `decompression_*_total` metrics | metric validator | Gate 5 |
| `parse_*_total` metrics | metric validator | Gate 5 |
| `replay_buffer_errors_total` metric | metric validator | Gate 5 |
| `DECOMP_*` reason codes | reason-code validator | Gate 5 |
| `PARSE_*` reason codes | reason-code validator | Gate 5 |
| `REPLAY_BUFFER_ERROR` reason code | reason-code validator | Gate 5 |
| DEB package metadata | package validator | Gate 3 |
| RPM package metadata | package validator | Gate 3 |
| Helm chart values | helm lint + values schema | Gate 4 |

---

## Automated Validation

Run the full gate validation suite:

```bash
# Gate 1
make test-nginx-unit-streaming
make test-rust
make test-rust-fuzz-smoke
make verify-chunked-native-e2e-smoke
make coverage-c
make coverage-rust

# Gate 2
make build && make check-headers
make test-e2e-rust

# Gate 5
make harness-check-full
make docs-check
make release-gates-check-strict
```

Or use the combined target:

```bash
make release-gates-check-070
```

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.7.0-draft | 2026-05-17 | spec-agent | Initial v0.7.0 gate definitions from design.md §14.0 |
