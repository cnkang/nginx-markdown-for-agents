# 0.5.0 Go/No-Go Review Process and Decision Record Template

## Review Process

1. **Collect DoD assessments from all P0 sub-specs** — confirm each sub-spec has completed DoD self-assessment
2. **Complete the release checklist** — verify each item in `docs/project/release-checklist-0-5-0.md`
3. **Evaluate Streaming Evidence** — verify all streaming evidence items are sufficient
4. **Write the decision record** — record Go or No-Go decision with rationale

## Streaming Evidence Requirements

The Go/No-Go decision must be based on the following verifiable artifacts:

| Evidence Item | Verification Method |
|--------------|---------------------|
| Streaming path vs full-buffer path differential test report | Diff tests pass; divergence within acceptable range |
| Streaming path bounded-memory evidence | Peak memory does not grow linearly with document size |
| Streaming path performance benchmark (TTFB improvement, throughput data) | Performance benchmark report |
| Streaming path failure-path test coverage | Test report covers all failure paths |
| Streaming path rollback verification record | Rollback verified in test environment |

## Decision Rules

- All P0 sub-spec DoDs pass **and** all Streaming Evidence sufficient → **Go**
- Any P0 sub-spec DoD fails → **No-Go** (fix and re-evaluate)
- Any Streaming Evidence insufficient → **No-Go** (design intent does not substitute for actual evidence)
- P1 status does not affect the Go/No-Go decision
- Gate items that cannot be satisfied must have explicit exceptions recorded

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
```
