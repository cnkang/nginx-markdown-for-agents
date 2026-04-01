# 0.5.0 Definition of Done (DoD) Template

## Overview

Inherits the 0.4.0 DoD style with streaming-specific additions. Each sub-spec must self-assess against this template before declaring completion.

## Checkpoints

| # | Checkpoint | Verification Method |
|---|-----------|---------------------|
| 1 | Functionally correct | All acceptance criteria pass in CI. Automated tests cover specified behavior. |
| 2 | Observable | Relevant metrics increment correctly under test. Logs contain expected reason codes. Operator can verify behavior via `curl` + log/metrics inspection. |
| 3 | Rollbackable | Rollback steps are documented. Disabling the feature restores 0.4.0 behavior. **Streaming-specific: steps and verification methods for rolling back from streaming to full-buffer are documented and tested.** |
| 4 | Documentable | Operator documentation exists for all new configuration, metrics, and behavior. `make docs-check` passes. |
| 5 | Auditable | Changes are traceable via PR history. CI artifacts include test results and benchmark evidence (where applicable). |
| 6 | Default-compatible | Default behavior unchanged when new configuration is not enabled. New directives have safe defaults. Backward compatibility review passes. |

## DoD Self-Assessment Record Format

Each sub-spec fills in the following table upon completion, recorded as part of the completion artifacts:

```markdown
## DoD Assessment

| Checkpoint | Status | Evidence |
|-----------|--------|----------|
| Functionally correct | PASS/FAIL | [CI run link or test report] |
| Observable | PASS/FAIL | [metrics test coverage description] |
| Rollbackable | PASS/FAIL | [rollback doc link, streaming-to-full-buffer rollback verification] |
| Documentable | PASS/FAIL | [docs-check result] |
| Auditable | PASS/FAIL | [PR link] |
| Default-compatible | PASS/FAIL | [backward compatibility review link] |
```

## Usage

1. Each sub-spec self-assesses against the six checkpoints after implementation is complete
2. Assessment results are recorded in the sub-spec's completion artifacts
3. Streaming-related sub-specs must additionally include streaming-to-full-buffer rollback verification in the "Rollbackable" checkpoint
4. All checkpoints must pass before a sub-spec can be declared complete
5. DoD assessments are reviewed during the Go/No-Go review
