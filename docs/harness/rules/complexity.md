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
- Shell cognitive complexity (no reliable tool; shellcheck covers static issues)
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
lifecycle management, error branches, macros, and state machines. The goal is
to prevent further growth, not zero out all complex functions. CCN 25 and
length 180 are generous enough to accommodate legitimate NGINX patterns while
flagging genuinely overgrown functions.

**Baseline strategy (Scheme B):** A checked-in baseline
(`tools/complexity/baseline.json`) records all current violations at the target
thresholds. New violations not in the baseline fail the check. Existing
baseline violations that worsen (higher CCN, longer length, more params, higher
cognitive complexity) produce warnings. This prevents new complexity growth
without requiring immediate cleanup of all historical complex functions.

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

**Handling false positives:**
- If a function is flagged but the complexity is legitimate (e.g., a large
  static lookup table with low actual branching), add it to the baseline with
  a `note` field explaining why.
- Do not suppress warnings by raising thresholds globally — use the baseline.

**Handling existing complex functions:**
- Existing violations are recorded in the baseline. They do not block the check.
- When modifying an existing complex function, do not increase its complexity
  metrics. If the function must grow, update the baseline entry.
- Over time, as complex functions are refactored or replaced, remove them from
  the baseline.

**Shell note:** Shell scripts are checked with `shellcheck` for static issues
and maintainability. Cognitive Complexity is not a hard metric for shell
scripts — there is no reliable tool, and shell scripts in this project are
primarily orchestration glue, not core logic.

Verification:
- `make complexity-check`
- `PYTHONPATH=. python3 tools/harness/detect_python_complexity.py`
- `python3 -m pytest tools/harness/tests/test_detect_python_complexity.py -q --tb=short`
