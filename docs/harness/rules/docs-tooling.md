---
domain: docs-tooling
rules: [9]
paths:
  - "docs/**"
  - "tools/**"
  - "README.md"
  - "INSTALLATION.md"
---

# Docs & Tooling Drift

### 9. Docs/tooling drift (README vs INSTALLATION vs validators)
Historical issues: `726865e`, `2b0bd5d`, `83eca29`, `18dfb8c`, `4b2b761`, `09f5d1d`.

Required:
- Keep Quick Start, installation guide, and packaging validators semantically consistent.
- Validation scripts must compare the same scope/section intended by spec (for example Shortest Success Path).
- Handle duplicates/order mismatches explicitly with actionable diagnostics.
- Avoid false positives by preserving meaningful URL path semantics in curl checks.
- Metric names documented in tables/examples must match emitted JSON keys and
  Prometheus series names exactly (no synthetic prefixes or renamed aliases).
- Operator-facing docs (cookbooks, rollout guides) that reference metrics must
  use the exact retrievable key path or series name.  For JSON, use the nested
  object path (for example `streaming.postcommit_error_total`).  For
  Prometheus, use the full series name with labels (for example
  `nginx_markdown_streaming_total{result="postcommit_error"}`).  Do not invent
  flat metric names that do not exist in any output format.
- When docs reference derived rates (for example `shadow_diff_rate`), include
  the computation formula using real metric names so operators can reproduce
  the calculation (for example `shadow_diff_total / shadow_total`).  Verify
  that the denominator is scoped to the same population as the numerator —
  using a global request count as denominator for a streaming-only failure
  count will dilute the rate during partial rollout and mask real problems.
- Verification commands in operator docs (curl + grep/jq/python) must specify
  an explicit `Accept` header matching the output format they parse.  The
  default plain-text format uses human-readable labels that differ from JSON
  keys and Prometheus series names; omitting `Accept` causes false negatives
  when grepping for snake_case keys.
