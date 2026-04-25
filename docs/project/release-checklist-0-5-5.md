# 0.5.5 Release Checklist

## Overview

This checklist aggregates all release gate verification steps for the `0.5.5`
stabilization and correctness release. Each gate has a verification command,
expected output format, and pass/fail criterion. Gates are organized by phase:
cheap blockers first, focused semantic checks second, umbrella checks third.

This checklist covers verification families touched by the stabilization
workstreams (semantic fidelity, protocol correctness, auth/cache safety,
streaming parity, operator diagnostics, and this release gate sync) as
mapped in `docs/harness/routing-manifest.json`.

## Phase 1: Cheap Blockers

These gates catch obvious failures fast and must pass before deeper checks.

| Gate ID | Verification Family | Command | Expected Output | Pass Criterion |
|---------|-------------------|---------|-----------------|----------------|
| CB-01 | `harness-sync` | `make harness-check` | All checks PASS | Exit code 0, all items PASS |
| CB-02 | `docs-tooling` | `make docs-check` | All checks PASS | Exit code 0, no failures in operator-facing docs |
| CB-03 | naming-consistency | `make release-gates-check` (naming) | All names conform | Exit code 0, PASS message |
| CB-04 | 0.5.5-gates | `make release-gates-check-055` | All 0.5.5 gates PASS | Exit code 0, all items PASS |

## Phase 2: Focused Semantic Checks

These gates verify pack-specific behavioral correctness for touched surfaces.

| Gate ID | Verification Family | Command | Expected Output | Pass Criterion | Notes |
|---------|-------------------|---------|-----------------|----------------|-------|
| FS-01 | `ffi-boundary` | `make build` | Build succeeds | Exit code 0 | |
| FS-02 | `ffi-boundary` | `make test-rust` | All tests pass | Exit code 0 | |
| FS-03 | `nginx-protocol` | `make test-nginx-unit` | All tests pass | Exit code 0 | |
| FS-04 | `nginx-protocol` | `make test-nginx-integration` | All tests pass | Exit code 0 | |
| FS-05 | `rust-streaming` | `make test-rust-streaming` | All tests pass | Exit code 0 | |
| FS-06 | `nginx-streaming` | `make test-nginx-unit-streaming` | All tests pass | Exit code 0 | |
| FS-07 | `observability-metrics` | `make docs-check` | Metrics naming consistent | Exit code 0 | |
| FS-08 | `observability-metrics` | `make release-gates-check` | All gates pass | Exit code 0 | Currently validates 0.5.0 release gate surfaces; serves as a non-regression baseline for 0.5.5 |
| FS-09 | `release-governance` | `make release-gates-check` | All gates pass | Exit code 0 | Currently validates 0.5.0 release gate surfaces; serves as a non-regression baseline for 0.5.5 |
| FS-10 | `release-governance` | `make release-gates-check-strict` | All gates pass | Exit code 0 | Currently validates 0.5.0 release gate surfaces; serves as a non-regression baseline for 0.5.5 |
| FS-11 | metrics-naming | Manual audit | Documented names match emitted names | All names verified | |
| FS-12 | command-examples | Manual audit | All examples include correct Accept headers | All examples verified | |
| FS-13 | harness-references | Manual audit | All references point to current content | All references verified | |
| FS-14 | release-checklist | Manual audit | Checklist covers all verification families | All families covered | |

## Phase 3: Umbrella Checks

These gates provide broad validation across the entire release surface.

| Gate ID | Verification Family | Command | Expected Output | Pass Criterion |
|---------|-------------------|---------|-----------------|----------------|
| UB-01 | `release-quality` | `make harness-check-full` | All checks PASS | Exit code 0 |
| UB-02 | `runtime-e2e` | `make verify-chunked-native-e2e-smoke` | E2E tests pass | Exit code 0 |
| UB-03 | `runtime-e2e` | `make verify-streaming-failure-cache-e2e` | E2E tests pass | Exit code 0 |
| UB-04 | coverage-c | `make coverage-c` | Aggregate ≥ 80% | Coverage report shows ≥ 80% |
| UB-05 | coverage-rust | `make coverage-rust` | Aggregate ≥ 80% | Coverage report shows ≥ 80% |
| UB-06 | scope-drift | Manual review | No scope creep | No unresolved tripwires |
| UB-07 | version-framing | Manual review | Consistent 0.5.5 references | CHANGELOG, tooling, docs aligned |
| UB-08 | planning-note-isolation | Manual review | No planning note dependencies | No references to advisory notes |
| UB-09 | evidence-artifact | `tests/streaming/evidence/summary.json` | Exists, schema_version=1, pass=true | File exists and validates |

## Streaming Evidence Artifact Gate (UB-09)

The streaming parity evidence artifact must satisfy all of the following:

1. File exists at `tests/streaming/evidence/summary.json`
2. Validates against `schema_version=1` with all required fields present
3. Required fields have correct types per the frozen schema
4. `pass` field is `true` (no unknown differences or error-parity mismatches)

Missing or schema-invalid evidence blocks release with the same weight as a
failing verification command.

## Go/No-Go Criteria

See the Go/No-Go Decision Matrix below. All criteria must be met or explicitly
waived before the release proceeds.

| Criterion | Gate(s) | Threshold | Blocking |
|-----------|---------|-----------|----------|
| Cheap blockers pass | CB-01, CB-02, CB-03, CB-04 | All PASS | Yes |
| Focused semantic checks pass | FS-01 through FS-14 | All PASS for touched surfaces | Yes |
| Runtime evidence exists | UB-02, UB-03 | At least one runtime target per runtime-sensitive workstream | Yes |
| C module coverage | UB-04 | ≥ 80% aggregate | Yes |
| Rust converter coverage | UB-05 | ≥ 80% aggregate | Yes |
| Critical-path coverage | Per-path verification | ≥ 90% for auth, error, FFI, conditional | Yes |
| Postflight reviews clean | All workstream postflight reviews | No unresolved gaps | Yes |
| Scope-creep tripwires clear | UB-06 | No unresolved tripwires | Yes |
| Naming consistency verified | FS-11 | All documented names match emitted names | Yes |
| Command examples valid | FS-12 | All examples produce documented output | Yes |
| Streaming evidence artifact | UB-09 | Exists, validates, pass=true | Yes |
| Version framing consistent | UB-07 | CHANGELOG + tooling + docs aligned | Yes |
| Planning notes isolated | UB-08 | No dependencies on advisory notes | Yes |

## Known Limitations

`make release-gates-check` and `make release-gates-check-strict` validate
0.5.0 release gate surfaces (the 0.5.0 workstreams), not 0.5.5-specific surfaces.
For 0.5.5, use `make release-gates-check-055` which validates the 0.5.5 stabilization workstreams
document existence, the release spec, release checklist, test matrix,
streaming evidence artifact, known-difference registry metadata, and
CHANGELOG entry.  The 0.5.0 targets serve as a non-regression baseline.

## Waiver Process

If any go/no-go criterion cannot be met, a waiver may be recorded. Each waiver
entry MUST contain all four required fields:

| Field | Format | Description |
|-------|--------|-------------|
| `waiver_id` | `WAIVER-0.5.5-<NNN>` | Unique within this release checklist |
| `approver` | Identity string | Person approving the waiver |
| `date` | ISO 8601 | Date of waiver approval |
| `justification` | Non-empty string | Rationale for the waiver |

Waiver entries missing any field or with a duplicate `waiver_id` are invalid.

### Active Waivers

| waiver_id | approver | date | justification |
|-----------|----------|------|---------------|
| WAIVER-0.5.5-001 | release-owner | 2026-04-24 | `docs-check` baseline failures in `0.5.5-release-spec.md` are pre-existing internal references in the release-level specification, not operator-facing documentation drift. These do not affect operator docs accuracy. |

## Docs Sync Trigger Rules Reference

The following change types trigger mandatory documentation synchronization
(see Requirement 10 and BR-2 in the design document):

| Change Type | Trigger | Affected Surfaces |
|-------------|---------|-------------------|
| Metrics field added/removed/renamed | Metrics_Name change | Prometheus guide, operator cookbooks |
| Metrics measurement semantics changed | HELP_Text change | Prometheus guide, HELP text in code |
| Reason code added/removed | Reason-code change | Operator diagnostics docs, streaming cookbook |
| Configuration directive added/changed | Config change | `docs/guides/CONFIGURATION.md`, README |
| HTTP response header behavior changed | Protocol change | Architecture docs |
| Streaming behavior changed | Streaming change | Streaming rollout cookbook |
| Harness routing updated | Harness change | `docs/harness/routing-manifest.md`, risk pack docs |
| Error response format changed | Error format change | Operator guides |

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.5 | 2026-04-24 | Release gate sync | Initial 0.5.5 release checklist |
