---
domain: complexity
rules: [17]
paths:
  - "components/nginx-module/src/**"
  - "tools/**"
---

## Cognitive Complexity

### 17. Cognitive complexity in C and Python functions
SonarCloud rules: `c:S3776`, `python:S3776`.

Required:
- Keep function cognitive complexity at or below the configured threshold (currently 25).
- For Python release-gate/tooling validators, keep function cognitive complexity
  at or below SonarCloud's configured threshold (currently 15) by extracting
  independent validation steps into small helpers.
- Run the local Python complexity detector before relying on SonarCloud:
  `PYTHONPATH=. python3 tools/harness/detect_python_complexity.py`.
  The detector is dependency-free and approximates SonarCloud's Python
  cognitive-complexity rule for local harness code.
- When widening the Python scan scope, pass explicit paths:
  `PYTHONPATH=. python3 tools/harness/detect_python_complexity.py --path tools/release`.
- Extract helper functions for self-contained sub-decisions (for example content-type exclusion checks, observability logging) to flatten the main function's control flow.
- Prefer early-return guard clauses over nested `if`/`else` chains.
- When adding new rules or conditions to an existing decision function, check whether the addition pushes complexity over the limit and proactively extract before merging.

Verification:
- `PYTHONPATH=. python3 tools/harness/detect_python_complexity.py`
- `python3 -m pytest tools/harness/tests/test_detect_python_complexity.py -q --tb=short`
