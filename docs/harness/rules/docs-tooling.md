---
domain: docs-tooling
rules: [9, 49, 63]
paths:
  - "docs/**"
  - "tools/**"
  - "README.md"
  - "INSTALLATION.md"
  - "THIRD-PARTY-NOTICES"
---

## Docs & Tooling Drift

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
  use the exact retrievable key path or series name. For Prometheus, use the
  full series name with labels (for example
  `nginx_markdown_streaming_events_total{transition="fallback"}`). Do not
  invent flat metric names that do not exist in the production output.
- When docs reference derived rates (for example `decompression_failure_rate`),
  include the computation formula using real metric names so operators can
  reproduce the calculation (for example
  `sum(rate(nginx_markdown_decompression_events_total{outcome="failure"}[5m])) / sum(rate(nginx_markdown_decompression_events_total[5m]))`).
  Verify
  that the denominator scopes to the same population as the numerator —
  using a global request count as denominator for a streaming-only failure
  count will dilute the rate during partial rollout and mask real problems.
- Verification commands in operator docs (curl + grep/jq/python) must specify
  an explicit `Accept` header matching the output format they parse.  The
  default plain-text format uses human-readable labels that differ from JSON
  keys and Prometheus series names, omitting `Accept` causes false negatives
  when grepping for snake_case keys.
- `tools/harness/detect_doc_sync.py` owns the blocking 0.9.2 public-config
  drift contract. It reads the current worktree directly, including untracked
  files under the owned production/example/E2E/Sonar surfaces. It verifies the
  active `markdown_streaming` registration, the removal of the former
  reject-only `markdown_streaming_engine` stub (removed names now fail with
  NGINX's standard `unknown directive` error), Helm `markdown.streaming.mode`
  mapping, removal of production `stream.engine`/`STREAM_ENGINE` symbols, the
  commonmark/gfm-only flavor contract with explicit mdx/org-mode rejection,
  and the exact off-to-off, auto-to-auto, on-to-force migration table.
- This detector is blocking in the existing CI docs job through
  `make docs-check` and also runs through `make harness-security-checks`, its
  positive and negative fixtures run through `make test-harness`.

### 49. THIRD-PARTY-NOTICES drift with dependency changes
Required:
- When any dependency arrives, leaves, or changes version (in
  Cargo.toml, Cargo.lock, or C/NGINX build files), update the corresponding
  entry in `THIRD-PARTY-NOTICES` in the same changeset.
- Version numbers in THIRD-PARTY-NOTICES must match the resolved versions in
  `Cargo.lock` (not the semver range in Cargo.toml).
- Add new dependencies with the correct license type, copyright notice, and
  license text. Delete entries for removed dependencies.
- Verify with: `diff <(grep '^version =' Cargo.lock) <(grep '[0-9]\+\.[0-9]\+' THIRD-PARTY-NOTICES)`

### 63. Non-native-reader writing style (STE-inspired) for maintained docs
Historical issues: writing-pass semantic drift found in the docs/kb-pilot
review (`cae5450d`): passive-to-active rewrites that inverted meaning
(budget exceeds, tail data must reject), dropped subjects, and removed
requirements. The style gate exists to keep maintained docs readable by
translators, NMT engines, and LLMs (see `docs/development/WRITING_GUIDE.md`).

Required:
- Maintained Markdown (current reader-facing root docs + `docs/`, excluding
  `docs/archive/` and gitignored paths) must follow the
  STE-inspired prose rules in
  `docs/development/WRITING_GUIDE.md`: sentences ≤ 25 words (descriptive) /
  ≤ 20 words (instructions), active voice, no Latin abbreviations
  (`e.g.`/`i.e.`/`etc.`), no contractions, no multi-word noun chains
  (≥ 4 capitalized words), no semicolon chains in prose.
- Files changed in the current changeset must introduce **zero** new
  warnings. Set `STYLE_BASE` to the target branch commit and run
  `make docs-style-check-regression` (or
  `python3 tools/docs/check_writing_style.py --changed --base REF`). The target
  requires a valid base, and invalid references fail closed.
- The repository-wide warning budget must not grow:
  `make docs-style-check-baseline` (or
  `python3 tools/docs/check_writing_style.py --baseline`) fails when the
  total exceeds the retained budget in `DEFAULT_BASELINE`
  (`tools/docs/check_writing_style.py`). The maintained docs now pass the
  audit with zero warnings, so any new warning fails this gate. The checker
  exempts structural surfaces — rule-checklist items, allowlisted formal
  titles, reference lines, and explicitly labelled source-citation lines — so the scan
  targets genuine prose violations only.
- Preserve meaning when rewriting passive voice to active: keep the
  original subject/object direction (`X is exceeded` → `the conversion
  exceeds X`, never `X exceeds`), keep the agent explicit (`the module
  blocks streaming`, never `streaming blocks`), and never drop a
  requirement or qualifier (must/never/only) during simplification.
- Verification commands live in the Makefile and run through the
  `docs-tooling` verification family. Routine `make docs-check` runs
  `make docs-style-check-regression` for changed files. The advisory scan is
  `make docs-style-check`. The repository-wide budget runs through full
  harness or release validation with `make docs-style-check-baseline`.
- The scan excludes code blocks, fenced blocks, tables, headings, inline
  code, links, and HTML comments. Rule documents, release-gate
  templates, and MUST-specification clauses may retain rule-format long
  sentences and semicolon-separated list items where the structure is
  intrinsic to the document type.
