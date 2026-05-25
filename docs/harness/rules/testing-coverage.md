---
domain: testing-coverage
rules: [14, 16, 20, 22, 25]
paths:
  - "components/nginx-module/tests/**"
  - "components/rust-converter/tests/**"
  - "tests/**"
---

## Testing & Coverage Discipline

### 14. Regression-test coverage discipline
Historical issues: `48edb7c`, `0a08d08`, `3a9f3cd`, `dcbf923`, `2a23415`, `253431c`.

Required:
- Every bug fix must be accompanied by at least one targeted regression test.
- For streaming/parser/sanitizer fixes, include cross-boundary and malformed-input cases.
- For streaming text-path fixes, include non-ASCII multibyte split cases (UTF-8 boundary tests).
- Streaming chunk-split fuzz targets must exercise multi-boundary patterns (for
  example many small chunks plus skewed large chunks), not only a single
  two-chunk split.
- Keep unit/property/fuzz/integration coverage coherent; do not rely on a single test layer.
- Parameterized tests must actually consume per-case inputs to drive the code
  path under test (no loops that ignore the case value and only mutate counters).
- Prefer exercising shared routing/helpers used by production semantics over
  duplicated inline test logic, so behavior drift is caught by tests.
- Fuzz/invariance checks that compare conversion paths must evaluate both sides
  as `Result` values and assert success/error parity before comparing outputs;
  do not early-return on the first `Err` and silently skip asymmetry.
- Full-buffer vs streaming parity tests must keep conversion configuration
  aligned (for example content-type/charset/options) unless the mismatch is the
  explicit subject of the test and documented in-place.
- Known-difference suppressors for runtime fallback/error paths must be
  fixture-scoped (`fixture_contains` or equivalent) unless a global suppressor
  is explicitly justified and reviewed, to avoid silently masking new
  regressions in unrelated fixtures.
- Keep fuzz target inventory de-duplicated: do not register parallel binaries
  with equivalent logic/coverage under different names; smoke targets must
  invoke the canonical binary names only.
- For fixture-scoped known differences, keep
  `tests/corpus/**/.meta.json -> streaming_notes.known_diff_ids` synchronized
  with `tests/streaming/known-differences.toml` IDs to avoid silent metadata
  drift.
- Known-difference registries must carry structured drift classification
  metadata (for example `drift_type` and `severity`) in addition to binary
  accept/reject flags. Parsers must degrade safely for missing/unknown values
  and tests must cover classification parsing defaults.
- Any new `tests/corpus/**/*.html` fixture must include a same-basename
  `.meta.json` sidecar in the same change set, with all required fields:
  `fixture-id`, `page-type`, `expected-conversion-result`, `input-size-bytes`,
  `source-description`, and `failure-corpus`. Run
  `tools/corpus/validate_corpus.sh` (or `make test-benchmark`) before merge.
- Corpus `.meta.json` `page-type` values must use the validator's current
  canonical taxonomy (`clean-article`, `documentation`, `nav-heavy`,
  `boilerplate-heavy`, or `complex-common`) unless the validator and coverage
  checks are updated in the same change set. Use fixture-specific detail fields
  such as `archetype` or `streaming_notes.high_risk_structures` for narrower
  traits like media-rich content.
- Regression tests for classification logic, routing decisions, or metrics
  increments must exercise code that mirrors the production branching — not
  manually set expected values in a local struct and assert them.  A test
  that writes `m.counter = 1; assert(m.counter == 1)` proves nothing about
  whether production code increments that counter under the tested condition.
  Similarly, `m.counter++; assert(m.counter == 1)` is equally vacuous — it
  only verifies that C increment works, not that the production branch was
  taken.  Use a stub/helper that replicates the real `if`/`switch` conditions
  so the test breaks when the production logic changes.  This principle applies
  to any test that claims to verify a side-effect: the test must go through the
  code path that produces the side-effect.
- Test assertions must align with the production semantics they claim to
  verify.  If production code only increments a counter on success, the test
  for the failure path must assert the counter is zero, not manually
  increment it and assert one.  A test that encodes the opposite of the
  production behavior will pass today but block correct future changes and
  confuse reviewers about the intended contract.

---

### 16. Dead stores and unused assignments in C test code
SonarCloud rules: `c:S1854`, `c:S5955`.

Required:
- Do not assign a value to a variable that is immediately overwritten before being read. In simulation-style tests, set the final (post-action) value directly and document the initial state in a comment instead.
- Declare loop variables (`i`, `feed_count`, etc.) inside the `for` statement when they are not used after the loop. The test code already uses C99 features, so `for (size_t i = 0; ...)` is preferred over declaring `i` at function scope.
- When a test assigns a variable to verify a cast or representation, ensure the variable is actually read by a `TEST_ASSERT` before the function ends.
- Initialize variables at declaration when the compiler cannot prove they are always assigned before use (for example variables set only inside conditional branches).
- Output variables passed by pointer to helpers (for example
  `foo(..., &last_buf, ..., &fallback_cl)`) must be initialized before the call
  even when the callee is expected to assign them on success; early-return
  paths in test/prod helpers can otherwise read indeterminate storage.

---

### 20. Spec task-completion and evidence-drift guardrails

Required:
- Do not mark a spec task as complete based only on file/code presence; run the
  corresponding verification path at least once in the current session.
- For any newly added `#[ignore]` tests (for example large-fixture or evidence
  pack tests), execute them explicitly with `-- --ignored` before closing the
  spec item that introduced them.
- If a task requires generated artifacts (for example large fixtures or JSON
  evidence packs), generate them once and verify both existence and required
  shape (schema/required fields), not just file creation.
- For bounded-memory/perf evidence tests, ensure fixture construction does not
  trivially force output-size-linear memory growth; inputs used to validate
  working-set bounds should separate input volume from emitted Markdown volume.
- For bounded-memory/perf evidence fixtures, use parser-visible/pipeline-visible
  padding (for example regular elements/text), not parser-discarded constructs
  such as HTML comments, so measured memory reflects real conversion work.
- For bounded-memory/perf evidence assertions, first verify the measured metric
  was actually populated (for example `peak_memory_estimate > 0`) before
  comparing against budget/threshold bounds, so unpopulated metrics cannot pass
  checks spuriously.
- Run at least one broad umbrella target for the touched area early (for
  example `make test-rust-streaming`) to expose non-local blockers; if it
  fails, report it as an open finding and do not present the spec as fully
  complete.

---

### 22. Rust test infrastructure and feature-gated code hygiene

Required:
- Do not add helper functions that are not consumed in the same change set.
  Speculative "this might be useful later" functions produce dead_code warnings
  that dilute real regression signals. If you add a helper, ensure at least one
  call site uses it before merging. The only exception is shared test-support
  modules (see next rule).
- Shared test utility modules included via `#[path = "..."]` in multiple
  integration test binaries must carry `#![allow(dead_code)]` at the module
  level, because each binary only uses a subset of the shared API and the
  remaining functions produce noisy warnings.
- Fuzz/test helper wrappers around streaming conversion must preserve
  `ConversionError` detail and variant identity by returning
  `Result<..., ConversionError>` (or equivalent) and propagating with `?`;
  avoid `Option` wrappers and `.ok()?` conversions that erase error semantics
  needed for parity/regression assertions.
- Never remove or modify a `#[cfg(feature = "...")]`-gated import without
  scanning the entire file for `#[cfg(feature = "...")]`-gated usages of that
  import. Feature-gated items may appear far from their import and are invisible
  under the default feature set; removing the import silently breaks compilation
  under that feature flag.
- Doc comments (`///`) must not contain blank lines between comment lines;
  use `///` on every line (including blank doc-comment lines as `///` with no
  trailing text). Blank lines between `///` lines trigger
  `clippy::empty_line_after_doc_comments`.
- **Doctest strategy must follow API visibility layering:**
  - **Public API entry points** (for example `StreamingConverter`, `MemoryBudget`, `StreamingResult`):
    use `no_run` or runnable doctests with full `nginx_markdown_converter::...` imports to maintain
    compile-time regression protection.
  - Public API doctests that exercise feature-gated runtime paths must not
    assume `Result::unwrap()` success across all feature sets; either keep them
    `no_run` or match expected fallback/error variants explicitly.
  - **Internal implementation details** (for example `CharsetState`, `IncrementalEmitter`,
    `StructuralStateMachine`, `StreamingTokenizer`, helper methods): use `ignore` mode to keep
    documentation illustrative without causing compilation failures. These should have their
    behavior verified through unit/integration tests instead.
  - **Error contract examples** (compile_fail): preserve `compile_fail` for public API misuse
    patterns that must fail at compile time.
  - **Doctest end markers must be plain ```** (not ` ```ignore` or ` ```no_run`) to maintain
    Markdown compatibility with external renderers and editors.
- Avoid `1 * N` identity multiplications in size-constant arrays; use `N`
  directly or underscore-separated literals (`1_024`) to satisfy
  `clippy::identity_op`.
- **Rust 2024 edition binding modes**: do not use explicit `ref` or `ref mut`
  in patterns that match on a reference type (`&T`, `&Option<T>`, `&mut T`).
  Rust 2024 edition treats this as a hard error because the reference already
  implies borrowing.  Use `ref` only when matching on an owned value where
  you need to borrow without moving.  Before writing `if let Some(ref x) = y`,
  check whether `y` is a reference — if so, drop the `ref`.  This applies to
  all pattern contexts: `if let`, `match`, `while let`, and function arguments.
  CI compiles with the project's declared edition, so a local build with an
  older edition may not catch this.

---

### 25. Test coverage discipline

Required:
- New C module code must include e2e scenarios or unit tests that maintain the
  80% aggregate line coverage bar.
- New Rust converter code must include tests that maintain the 80% aggregate
  line coverage bar.
- Critical paths (auth, error handling, FFI boundary, conditional requests)
  require 90% line coverage for new code.
- Before declaring a task complete when the change touches production source
  files, run `make coverage-c` (C module) or `make coverage-rust` (Rust
  converter) and verify aggregate coverage does not regress below 80%.
  The advisory per-file thresholds logged by `collect_nginx_coverage.sh` are
  informational warnings only — the binding gate is the 80% aggregate bar
  (90% for critical paths).


---

## Fuzz Harness Rules (FUZZ-001 to FUZZ-007)

Canonical definitions live in [`fuzz-infrastructure.md`](./fuzz-infrastructure.md).

| Rule ID  | Summary | Level |
|----------|---------|-------|
| FUZZ-001 | Converter adjacent-change impact assessment | Advisory |
| FUZZ-002 | New logic must have fuzz coverage | Advisory |
| FUZZ-003 | Target deterministic execution | Mandatory |
| FUZZ-004 | Crash minimization workflow | Mandatory |
| FUZZ-005 | Batch requires paired pruning | Mandatory |
| FUZZ-006 | Doc-only changes skip fuzz | Mandatory |
| FUZZ-007 | Infrastructure passes harness-check | Mandatory |
