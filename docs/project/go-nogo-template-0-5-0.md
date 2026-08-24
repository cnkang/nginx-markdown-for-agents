# 0.5.0 Go/No-Go Review Process and Decision Record Template

## Review Process

1. **Collect DoD assessments from all P0 sub-specs** — confirm each sub-spec has completed DoD self-assessment
2. **Complete the release checklist** — verify each item in `docs/project/release-checklist-0-5-0.md`
3. **Evaluate Streaming Evidence** — verify all streaming evidence items are sufficient
4. **Write the decision record** — record Go or No-Go decision with rationale

## Streaming Evidence Requirements

The Go/No-Go decision must rest on the following verifiable artifacts:

| Evidence Item | Verification Method |
|--------------|---------------------|
| Streaming path vs full-buffer path differential test report | Diff tests pass; divergence within acceptable range |
| Streaming path bounded-memory evidence | Peak memory does not grow linearly with document size |
| Streaming path performance benchmark (TTFB improvement, throughput data) | Performance benchmark report |
| Streaming path failure-path test coverage | Test report covers all failure paths |
| Streaming path rollback verification record | Rollback verified in test environment |
| Signed gate review record | Reviewer, review date, reviewed scope, candidate, and evidence artifact are recorded |

## Decision Rules

- All P0 sub-spec DoDs pass **and** all Streaming Evidence sufficient → **Go**
- Any P0 sub-spec DoD fails → **No-Go** (fix and re-evaluate)
- Any Streaming Evidence insufficient → **No-Go** (design intent does not substitute for actual evidence)
- Missing or unsigned gate-review evidence → **No-Go** until the review record
  names the candidate and links the evidence artifacts
- P1 status does not affect the Go/No-Go decision
- Gate items that cannot pass must have explicit exceptions recorded. A
  recorded exception does not override a failed P0 sub-spec DoD: that DoD
  remains failed and the decision remains **No-Go** until the team fixes and
  re-evaluates the DoD. A release-owner authorization may acknowledge a separately
  documented non-P0 exception, but it cannot convert a failed P0 DoD into a
  passing result. The authorization must name the gate, candidate, and scope
  of the non-P0 exception.

## Decision Record Template

```markdown
## Go/No-Go Decision Record

- Date: [YYYY-MM-DD]
- Decision: Go / No-Go
- Rationale: [Summary]

### P0 Sub-Spec Status

| Sub-Spec | DoD Status | Notes |
|----------|-----------|-------|
| overall-scope-release-gates-0-5-0 | PASS/FAIL | |
| rust-streaming-engine-core | PASS/FAIL | |
| nginx-streaming-runtime-and-ffi | PASS/FAIL | |
| streaming-failure-cache-semantics | PASS/FAIL | |
| streaming-parity-diff-testing | PASS/FAIL | |
| streaming-rollout-observability | PASS/FAIL | |
| streaming-performance-evidence-and-release | PASS/FAIL | |

### Streaming Evidence Status

| Evidence Item | Status | Artifact Reference |
|--------------|--------|-------------------|
| Differential test report | PASS/FAIL | [link] |
| Bounded-memory evidence | PASS/FAIL | [link] |
| Performance benchmark (TTFB) | PASS/FAIL | [link] |
| Failure-path test coverage | PASS/FAIL | [link] |
| Rollback verification record | PASS/FAIL | [link] |

### Release Gate Summary

| Gate Category | Status | Exceptions |
|--------------|--------|------------|
| Documentation | PASS/FAIL | [None/description] |
| Testing | PASS/FAIL | [None/description] |
| Compatibility | PASS/FAIL | [None/description] |
| Operations | PASS/FAIL | [None/description] |
| Streaming Evidence | PASS/FAIL | [None/description] |

### Exceptions (if any)

| # | Gate Item | Exception Rationale | Risk Assessment | Mitigation Strategy |
|---|----------|--------------------|-----------------|--------------------|

### Release-Owner Authorization (does not override a failed P0 DoD)

Any recorded non-P0 exception requires explicit, written authorization from
the release owner when the release process calls for it. Capture the
authorization artifact and its required fields. Do not use this record to
override a failed P0 sub-spec DoD:

| Field | Value |
|-------|-------|
| Authorization artifact | [link/path to the written authorization] |
| Gate name | [e.g. streaming-parity-diff-testing] |
| Candidate | [release candidate / commit SHA] |
| Override scope | [which gates/items the authorization covers] |
| Written authorization | [sign-off text/approval record] |
```

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
