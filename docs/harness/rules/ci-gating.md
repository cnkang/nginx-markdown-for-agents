---
domain: ci-gating
rules: [13]
paths:
  - ".github/workflows/**"
  - "Makefile"
---

# CI Gating

### 13. CI gating blind spots
Historical issues: `7bf22a0`, `090c5a5`, `034e42f`, `7018a3c`, `08f18fa`.

Required:
- Update workflow path filters whenever checks depend on new file paths.
- Baseline/bootstrap modes must not upload/compare artifacts incorrectly.
- Remove redundant CI steps that can desynchronize behavior or waste runtime.
