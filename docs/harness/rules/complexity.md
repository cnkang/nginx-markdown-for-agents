---
domain: complexity
rules: [17]
paths:
  - "components/nginx-module/src/**"
  - "tools/**"
---

# Cognitive Complexity

### 17. Cognitive complexity in C and Python functions
SonarCloud rules: `c:S3776`, `python:S3776`.

Required:
- Keep function cognitive complexity at or below the configured threshold (currently 25).
- For Python release-gate/tooling validators, keep function cognitive complexity
  at or below SonarCloud's configured threshold (currently 15) by extracting
  independent validation steps into small helpers.
- Extract helper functions for self-contained sub-decisions (for example content-type exclusion checks, observability logging) to flatten the main function's control flow.
- Prefer early-return guard clauses over nested `if`/`else` chains.
- When adding new rules or conditions to an existing decision function, check whether the addition pushes complexity over the limit and proactively extract before merging.
