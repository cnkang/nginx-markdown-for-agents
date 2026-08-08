---
domain: complexity
rules: [17]
paths:
  - "components/nginx-module/src/**"
  - "components/rust-converter/src/**"
  - "tools/**"
---

## Cognitive Complexity

### 17. Cognitive complexity in C, Rust, and Python functions

SonarCloud rules: `c:S3776`, `python:S3776`.

Required:
- Keep function cognitive complexity at or below the configured threshold
  (currently 25 for C/Rust, 15 for Python).
- For Python release-gate/tooling validators, keep function cognitive complexity
  at or below SonarCloud's configured threshold (currently 15) by extracting
  independent validation steps into small helpers.
- Run the local Python complexity detector before relying on SonarCloud:
  `PYTHONPATH=. python3 tools/harness/detect_python_complexity.py`.
  The detector is dependency-free and approximates SonarCloud's Python
  cognitive-complexity rule for local harness code.
- When widening the Python scan scope, pass explicit paths:
  `PYTHONPATH=. python3 tools/harness/detect_python_complexity.py --path tools/release`.
- Extract helper functions for self-contained sub-decisions (for example
  content-type exclusion checks, observability logging) to flatten the main
  function's control flow.
- Prefer early-return guard clauses over nested `if`/`else` chains.
- When adding new rules or conditions to an existing decision function, check
  whether the addition pushes complexity over the limit and proactively extract
  before merging.

### Complexity Check Harness (`make complexity-check`)

A unified complexity check harness runs `lizard` (CCN, function length, parameter
count) on C, Rust, and Python source, `complexipy` (cognitive complexity) on
Python tooling, and `shellcheck` on shell scripts.

**What it checks:**
- C: Cyclomatic Complexity (CCN), function length, parameter count
- Rust: Cyclomatic Complexity (CCN), function length, parameter count
- Python: Cyclomatic Complexity (CCN) via lizard, Cognitive Complexity via complexipy
- Shell: static analysis via shellcheck (not cognitive complexity)

**What it does NOT check:**
- Shell cognitive complexity (no reliable tool, shellcheck covers static issues)
- Test fixture files (excluded from scan paths)
- Generated code, vendored code, build artifacts

**Thresholds:**

| Language | Tool | CCN | Length | Params | Cognitive |
|----------|------|-----|--------|--------|-----------|
| C | lizard | 25 | 180 | 8 | — |
| Rust | lizard | 25 | 200 | 8 | — |
| Python | lizard | 15 | 200 | 8 | — |
| Python | complexipy | — | — | — | 15 |
| Shell | shellcheck | — | — | — | — |

**C thresholds rationale:** NGINX glue layers have inherent complexity from
lifecycle management, error branches, macros, and state machines. CCN 25 and
length 180 are generous enough to accommodate legitimate NGINX patterns while
still requiring every overgrown function to refactor before delivery.

Every violation is blocking. The harness has no baseline exception list,
suppression mechanism, or threshold waiver. If a function exceeds a threshold,
extract helpers, simplify the control flow, or redesign the boundary until the
the team removes the reported violation.

**When to run:**
- Before committing changes to C, Rust, Python, or shell code
- As part of `make harness-check-full` (not in the cheap `harness-check` to
  avoid slowing down fast iteration)
- In CI for PRs that touch source code in the scanned paths

**Output:** Reports land in `target/complexity/` (not committed).

**Dependencies:**
```bash
pip install lizard complexipy
brew install shellcheck   # macOS
apt install shellcheck    # Debian/Ubuntu
```

**Handling flagged functions:**
- When a legitimate-looking lookup table or NGINX lifecycle function exceeds a
  configured threshold, the module team must decompose it.
- Do not suppress warnings or raise thresholds to accommodate a violation.
- Existing violations are subject to the same blocking rule as new violations,
  the current tree must be clean before delivery.

**Shell note:** Shell scripts get checked with `shellcheck` for static issues
and maintainability. Cognitive Complexity is not a hard metric for shell
scripts — there is no reliable tool, and shell scripts in this project are
primarily orchestration glue, not core logic.

Verification:
- `make complexity-check`
- `PYTHONPATH=. python3 tools/harness/detect_python_complexity.py`
- `python3 -m pytest tools/harness/tests/test_detect_python_complexity.py -q --tb=short`
