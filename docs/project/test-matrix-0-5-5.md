# 0.5.5 Test Matrix

## Overview

This matrix maps every change surface touched by the 0.5.5 stabilization
workstreams to at least one verification command. It is consistent with the
verification family mapping in `docs/harness/routing-manifest.json` and
distinguishes runtime verification targets from static verification targets.

## Change Surface to Verification Command Mapping

### Semantic Fidelity

| Change Surface | Verification Command | Type | Verification Family |
|---------------|---------------------|------|-------------------|
| Rust converter structural accuracy | `make test-rust` | Runtime | `ffi-boundary` |
| Rust converter semantic accuracy | `make test-rust` | Runtime | `ffi-boundary` |
| Conversion output correctness | `make build` | Runtime | `ffi-boundary` |
| Harness alignment | `make harness-check` | Static | `harness-sync` |

### Protocol Correctness

| Change Surface | Verification Command | Type | Verification Family |
|---------------|---------------------|------|-------------------|
| HTTP conditional request handling | `make test-nginx-unit` | Runtime | `nginx-protocol` |
| ETag generation and comparison | `make test-nginx-unit` | Runtime | `nginx-protocol` |
| HEAD request handling | `make test-nginx-integration` | Runtime | `nginx-protocol` |
| Status code mapping (304, 206) | `make test-nginx-unit` | Runtime | `nginx-protocol` |
| Harness alignment | `make harness-check` | Static | `harness-sync` |

### Auth / Cache Safety

| Change Surface | Verification Command | Type | Verification Family |
|---------------|---------------------|------|-------------------|
| Auth-aware cache-control rewriting | `make test-nginx-unit` | Runtime | `nginx-protocol` |
| Cookie pattern matching | `make test-nginx-unit` | Runtime | `nginx-protocol` |
| Cache-Control header semantics | `make test-nginx-integration` | Runtime | `nginx-protocol` |
| Harness alignment | `make harness-check` | Static | `harness-sync` |

### Streaming Parity and Evidence

| Change Surface | Verification Command | Type | Verification Family |
|---------------|---------------------|------|-------------------|
| Streaming conversion engine | `make test-rust-streaming` | Runtime | `rust-streaming` |
| Streaming C module integration | `make test-nginx-unit-streaming` | Runtime | `nginx-streaming` |
| Streaming e2e behavior | `make verify-chunked-native-e2e-smoke` | Runtime | `runtime-e2e` |
| Streaming failure/cache behavior | `make verify-streaming-failure-cache-e2e` | Runtime | `runtime-e2e` |
| Streaming parity evidence | `tests/streaming/evidence/summary.json` | Static | `release-governance` |
| Streaming reason codes | `make test-nginx-unit-streaming` | Runtime | `nginx-streaming` |
| Streaming metrics fields | `make docs-check` | Static | `observability-metrics` |
| FFI boundary (streaming stats) | `make build`, `make test-rust` | Runtime | `ffi-boundary` |
| Harness alignment | `make harness-check` | Static | `harness-sync` |

### Operator Diagnostics

| Change Surface | Verification Command | Type | Verification Family |
|---------------|---------------------|------|-------------------|
| Reason-code taxonomy | `make test-nginx-unit` | Runtime | `nginx-protocol` |
| Reason-code runtime emission | `make test-nginx-unit` | Runtime | `nginx-protocol` |
| Metrics naming consistency | `make docs-check` | Static | `observability-metrics` |
| Metrics HELP text accuracy | `make release-gates-check` | Static | `observability-metrics` |
| Operator docs alignment | `make docs-check` | Static | `docs-tooling` |
| Harness alignment | `make harness-check` | Static | `harness-sync` |

### Release Gate and Docs Sync

| Change Surface | Verification Command | Type | Verification Family |
|---------------|---------------------|------|-------------------|
| Release checklist completeness | Manual audit | Static | `release-governance` |
| Test matrix alignment | Manual audit | Static | `release-governance` |
| Go/no-go criteria definition | Manual audit | Static | `release-governance` |
| Documentation synchronization | `make docs-check` | Static | `docs-tooling` |
| Metrics naming in docs | `make release-gates-check` (naming) | Static | `observability-metrics` |
| 0.5.5 release gates | _(removed — validator deleted)_ | Static | `release-governance` |
| Command example validation | Manual audit | Static | `docs-tooling` |
| Harness routing references | `make harness-check` | Static | `harness-sync` |
| Version framing (CHANGELOG) | Manual audit | Static | `release-governance` |
| Planning note isolation | Manual audit | Static | `release-governance` |
| Full harness validation | `make harness-check-full` | Static | `release-quality` |

## Runtime vs Static Verification Summary

| Type | Count | Description |
|------|-------|-------------|
| Runtime | 18 | Commands that execute module code or run tests against compiled artifacts |
| Static | 21 | Commands that check docs, naming, configuration consistency, or manual audits |

## Coverage Gaps

| Change Surface | Gap | Rationale |
|---------------|-----|-----------|
| Streaming evidence artifact | No automated runtime generation in this workstream | The streaming parity workstream owns evidence generation; this workstream verifies the artifact exists |
| Command example execution | No automated execution against running NGINX | Requires running NGINX instance; validated manually or via static Accept header check |
| Release-gate validator version | `make release-gates-check` validates 0.5.0 surfaces, not 0.5.5 | The 0.5.5-specific validator has been removed; 0.5.0 targets serve as a non-regression baseline. |

## Consistency with Routing Manifest

All verification families referenced in this matrix are defined in
`docs/harness/routing-manifest.json`:

- `harness-sync` → cheap blocker → `make harness-check`
- `docs-tooling` → cheap blocker → `make docs-check`
- `ffi-boundary` → focused semantic → `make build`, `make test-rust`
- `nginx-protocol` → focused semantic → `make test-nginx-unit`, `make test-nginx-integration`
- `rust-streaming` → focused semantic → `make test-rust-streaming`
- `nginx-streaming` → focused semantic → `make test-nginx-unit-streaming`
- `observability-metrics` → focused semantic → `make docs-check`, `make release-gates-check`
- `release-governance` → focused semantic → `make release-gates-check`, `make release-gates-check-strict`
- `runtime-e2e` → umbrella → `make verify-chunked-native-e2e-smoke`, `make verify-streaming-failure-cache-e2e`
- `release-quality` → umbrella → `make harness-check-full`

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.5 | 2026-04-24 | Release gate sync | Initial 0.5.5 test matrix |
