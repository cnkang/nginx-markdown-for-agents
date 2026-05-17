---
domain: python-tooling
rules: [19]
paths:
  - "tools/**/*.py"
  - "tests/e2e/**/*.py"
---

# Python Tooling Guardrails

### 19. Python e2e/tooling harness guardrails

Required:
- Binary prerequisites must validate executability, not only path existence
  (for example `os.path.isfile(...)` plus `os.access(..., os.X_OK)`, or
  `shutil.which(...)`).
- Harness checks that represent required behavior must affect pass/fail status
  (or exit non-zero), not only print informational diagnostics.
- Repeated gate/check ID strings must be module-level constants once reused,
  so result recording and exception branches cannot drift.
