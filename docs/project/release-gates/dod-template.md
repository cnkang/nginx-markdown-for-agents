# Definition of Done Template — 0.4.0 Release

Every 0.4.0 sub-spec must satisfy this six-checkpoint DoD before declaring completion. The template is defined by the release gates spec (Requirements 5.1, 5.2, 5.3) and enforced during the Go/No-Go review.

## Checkpoints

| # | Checkpoint | Verification Method |
|---|-----------|-------------------|
| 1 | Functionally correct | All acceptance criteria pass in CI. Automated test suite covers specified behavior. |
| 2 | Observable | Relevant metrics increment correctly under test. Log messages contain expected reason codes. Operator can verify behavior via curl + log/metrics inspection. |
| 3 | Rollback-safe | Rollback procedure is documented. Removing or disabling the feature restores 0.3.0 behavior. Tested by disabling the feature and verifying no side effects. |
| 4 | Documented | Operator-facing docs exist for all new configuration, metrics, and behavior. Docs pass `make docs-check`. |
| 5 | Auditable | Changes are traceable through PR history. CI artifacts include test results and benchmark evidence (where applicable). |
| 6 | Compatible | Default behavior with no new config is identical to 0.3.0. New directives have safe defaults. Backward compatibility review checklist is completed. |

## DoD Evaluation Recording Format

Each sub-spec records its evaluation as a markdown table in its completion artifact. Copy the table below and fill in the Status and Evidence columns.

```markdown
## DoD Evaluation

| Checkpoint | Status | Evidence |
|-----------|--------|----------|
| Functionally correct | ✅/❌ | [CI run, test results] |
| Observable | ✅/❌ | [metrics test coverage] |
| Rollback-safe | ✅/❌ | [rollback procedure doc] |
| Documented | ✅/❌ | [docs-check result] |
| Auditable | ✅/❌ | [PR reference] |
| Compatible | ✅/❌ | [compat review reference] |
```

## Instructions for Sub-Spec Owners

1. Copy the DoD Evaluation table above into your sub-spec's completion artifact.
2. Evaluate each checkpoint against the verification method in the Checkpoints table.
3. Replace `✅/❌` with the actual status. Use ✅ only when the verification method is fully satisfied.
4. Replace the bracketed placeholder in the Evidence column with a concrete reference (CI run number, test file path, doc path, PR link, or review reference).
5. If a checkpoint cannot be satisfied, record ❌ with a brief explanation in the Evidence column and escalate to the Go/No-Go review with a documented exception.
6. The completed DoD evaluation is required before the sub-spec can be considered complete (Requirement 5.2). The evaluation is archived as part of the 0.4.0 release record (Requirement 5.3).
