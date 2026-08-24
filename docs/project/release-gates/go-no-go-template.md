# Go/No-Go Decision Record Template

Requirements references: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6

This is the final checkpoint before the 0.4.0 release. The Go/No-Go review
verifies that all P0 sub-specs have passed their DoD evaluation. It also
verifies that all release gates pass. An unresolved release-gate failure is
No-Go. An approved exception can permit Go only for a non-P0, non-safety gate
when the release owner approves it. The record must include risk assessment
and mitigation evidence. P0 or safety failures remain No-Go.

## Decision Record

- Date: [YYYY-MM-DD]
- Decision: Go / No-Go
- Rationale: [summary]

## P0 Sub-Spec Status

All P0 sub-specs must pass their DoD evaluation before a Go decision can be made.

| Sub-Spec | DoD Status | Notes |
|----------|-----------|-------|
| overall-scope-release-gates | ✅/❌ | |
| packaging-and-first-run | ✅/❌ | |
| benchmark-corpus-and-evidence | ✅/❌ | |
| rollout-safety-controlled-enablement | ✅/❌ | |
| prometheus-module-metrics | ✅/❌ | |

## P1 Sub-Spec Status

The team may exclude the P1 sub-spec without blocking the release (Req 7.5).

| Sub-Spec | DoD Status | Decision |
|----------|-----------|----------|
| parser-path-optimization | ✅/❌ | Include / Defer to 0.5.x |

## Release Gate Summary

| Gate Category | Status | Exceptions |
|--------------|--------|------------|
| Documentation | ✅/❌ | [None / description] |
| Testing | ✅/❌ | [None / description] |
| Compatibility | ✅/❌ | [None / description] |
| Operations | ✅/❌ | [None / description] |

## Exceptions

Every unresolved failure must have an exception record here that includes its rationale, risk assessment, and mitigation (Req 7.6). Each exception must carry a non-P0, non-safety classification to be eligible for approval, and the release owner must approve it.

| # | Gate Item | Eligibility (non-P0, non-safety) | Exception Rationale | Risk Assessment | Mitigation | Approver (release owner) |
|---|----------|--------------------------------|-------------------|-----------------|------------|--------------------------|
| | | | | | | |

## Rules

1. All P0 sub-specs must pass their DoD evaluation before a Go decision.
2. The team may exclude the P1 sub-spec (parser-path-optimization) without blocking the release (Req 7.5).
3. The release owner must approve any exception before the release proceeds. The record must state why the exception is eligible for Go, include risk assessment and mitigation evidence, and identify the approver. An unapproved or P0/safety exception is No-Go (Req 7.6).

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
