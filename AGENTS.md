# AGENTS.md

## Purpose
This file defines repository-specific engineering rules for AI agents working on `nginx-markdown-for-agents`.

These rules are distilled from:
- NGINX official development constraints in the Kiro local development guide (when present)
- Historical fix/doc and hidden-fix commits across local branches and remote-only commits (deduplicated by commit SHA)

Goal: prevent repeated classes of mistakes that previously caused regressions.

## Rule Priority
1. Kiro local development guide, when present (highest priority)
2. This `AGENTS.md`
3. Task-specific user instructions

If two rules conflict, follow the higher-priority source.

## Harness Map
- `AGENTS.md` remains the Codex-first contract and engineering rule map.
- `AGENTS.md` and `docs/harness/` are the owning harness truth surfaces for
  tracked repository behavior; Make/CI/checkers must consume these, not local
  adapter-only summaries.
- `docs/harness/README.md` is the repo-owned harness entrypoint.
- `docs/harness/core.md` defines the execution loop, conflict protocol, and
  status semantics.
- `docs/harness/routing-manifest.json` is the canonical structured routing
  source; `docs/harness/routing-manifest.md` is the readable overlay.
- Optional local spec inputs are read-only for local spec-oriented work. Do not
  write harness caches, annotations, or durable repo truth into local adapter
  surfaces.
- Optional local steering adapters should point back to `docs/harness/` and
  must not define stronger semantics than this file.
- Outside voice and the user-local harness state carrier are advisory execution
  tools. They may challenge or inform the current path, but they do not weaken
  the repo-owned correctness and safety contract.

## Non-Negotiable NGINX Baseline

### API and lifecycle correctness
- Use NGINX return-code semantics correctly: `NGX_OK`, `NGX_DECLINED`, `NGX_AGAIN`, `NGX_DONE`, `NGX_ERROR`.
- In HTTP phases/filters, treat `NGX_AGAIN` as suspend-and-resume, not success.
- Finalize requests with the correct code path (`ngx_http_finalize_request`) when redirect/subrequest/finalization semantics require it.
- Never send body data before headers; header forwarding state must be explicit and idempotent.

### Memory and concurrency model
- Prefer NGINX pool allocation for request-lifetime data.
- Avoid unbounded allocations in request path; every growing buffer must have an explicit cap.
- Avoid global mutable state; bind long-lived state to config/cycle structures.
- Avoid blocking behavior and blocking libraries in worker request path.

### C style and conventions
- Follow NGINX style: 4-space indent, <=80 cols where practical, no `//` comments, `_t` suffix for types.
- Keep NGINX style as the primary style contract, but require C99-or-new as
  the minimum language baseline for all C code and C snippets in docs/steering.
  Pre-C99 forms are forbidden (for example K&R function definitions, implicit
  `int`, and declarations without proper prototypes).
- Use `u_char *`, `ngx_str_t`, and NGINX helpers (`ngx_snprintf`, `ngx_memcpy`, etc.) consistently.
- Use `NULL` pointer comparisons (not `0`).
- For POSIX string helpers (for example `strcasecmp`, `strncasecmp`), include
  `<strings.h>` explicitly in the translation unit or shared header. Do not
  rely on transitive includes or implicit declarations.
- **Never dereference or perform relational operations on values that may be uninitialized, NULL, or invalid without an explicit guard.** This includes: pointer comparisons (`p > q`, `p < q`), pointer arithmetic, field access through pointers, array indexing with unvalidated bounds. When the validity of a value depends on runtime state (for example `pos/last` may both be NULL in empty buffers), use an explicit boolean flag set at the production site rather than inferring state from value relationships.

## Frequent Error Patterns and Required Prevention

### 1. Streaming backpressure and pending-chain handling
Historical issues: `5649890`, `23165d9`, `cfd4bd8`, `f97be3f`.

Required:
- If downstream filter returns `NGX_AGAIN`, persist the unsent chain in context and resume it later.
- Never overwrite pending output with terminal empty `last_buf` while data is still pending.
- On fallback from streaming to full-buffer path, reset state flags that gate conversion flow (for example conversion-attempt flags).
- Fail-open paths must still honor header/body ordering and deferred-header forwarding.

### 2. Incorrect fail-open semantics causing data loss or pointer advancement bugs
Historical issues: `23165d9`, `7cbe4fa`.

Required:
- Use semantically correct return codes in fail-open branches (`NGX_DECLINED` where needed), so caller behavior matches control intent.
- Do not advance buffer positions for unconsumed data when handing chain to next filter unchanged.
- If a fail-open branch invokes next header filter, mark header-forwarded state before the call to avoid double header emission.

### 3. Memory leaks and budget bypass in streaming/decompression
Historical issues: `23165d9`, `2c7d6a9`, `0eae34b`, `1b0df51`.

Required:
- Enforce all configured budgets (including total working-set budget), not only per-buffer budgets.
- Any auxiliary heap expansion buffer must be explicitly freed on all exits; copy final data back to pool-owned memory if needed.
- Any collector strings/buffers (for example link text, sniff buffers) must be bounded by configured limits.
- Track peak memory from real resident state, not only counters.
- Do not hardcode stage limits in downstream components when a configured
  budget field already exists (for example charset sniff bytes, stack bytes,
  output buffer bytes). Thread budget values through constructors and enforce
  them at the runtime write/check site.
- Budget guard helper APIs and production enforcement must not drift: if
  helper methods exist for stage checks, production code should call them; if
  a helper is not part of production enforcement, remove it in the same change
  set. Avoid parallel inline checks and helper checks that can diverge.
- Never apply hidden floor/ceiling behavior that weakens a configured budget
  (for example enforcing a minimum depth that exceeds `state_stack` for small
  budgets) unless explicitly documented in spec and tested as intended behavior.

### 4. UTF-8/charset cross-chunk corruption
Historical issues: `0eae34b`, `1b0df51`, `77a46d6`.

Required:
- Preserve incomplete UTF-8 tails across chunk boundaries and prepend to next chunk.
- Flush charset decoders at EOF (`last=true`) so trailing buffered bytes are emitted or reported.
- Do not rely on blanket lossy conversion before handling chunk-tail semantics.
- When post-commit wrappers re-map errors, preserve original error classification/code for downstream handling and metrics.

### 5. Sanitizer and HTML semantics mismatches
Historical issues: `8440ac3`, `dbcdad8`, `77a46d6`.

Required:
- Treat HTML void elements as self-closing by semantics, not only tokenizer flags.
- Skip-mode exit for dangerous elements must be name-aware; mismatched closing tags must not prematurely exit skip mode.
- Keep nesting-depth accounting saturation-safe and invariant-preserving under malformed HTML.
- When using tokenizer-level streams, do not assume tree-builder implicit tags (for example implicit `</head>`); state transitions must be explicit.

### 6. Emitter structural correctness bugs
Historical issues: `1688e80`, `77a46d6`, `2c7d6a9`.

Required:
- In-link formatting markers (bold/italic/inline-code) must be accumulated in link text, not flushed outside link context.
- Code-block output must preserve raw content (blank lines/trailing spaces) and bypass generic normalization.
- Blockquote markers must be emitted consistently on entry and after newline boundaries.
- URL extraction parity must include media-bearing elements, not only `img`
  (at minimum `video`, `audio`, `source`, `track`, and `area` where
  applicable), with regression tests that cover missing-attribute and
  attribute-present branches.
- Human-readable fallback/detail strings that are carried in enums or result
  types (for example `UnsupportedStructure(...)`) must use stable internal
  identifiers, not user-influenced payloads.

### 7. Eligibility/reason-code drift and status handling bugs
Historical issues: `d2d836f`, `5288c1b`, `19c896c`, `bc35a1f`.

Required:
- Encode skip-reason mapping explicitly; do not rely on indirect checks that can misclassify edge cases.
- Keep reason-code behavior and tests aligned when eligibility logic changes.
- For protocol edge statuses (for example 206), map to the intended reason consistently even in malformed upstream scenarios.
- When adding a new reason code string definition and accessor function, the
  corresponding `ngx_http_markdown_log_decision()` callsite(s) must be added in
  the same changeset.  A reason code that is defined but never emitted at
  runtime is a contract violation — operators and docs will reference a code
  that never appears in logs.
- When adding a new family of reason codes (for example a `STREAMING_*`
  namespace alongside the existing `ELIGIBLE_*` / `FAIL_*` families), update
  **every classification function** that categorizes reason codes by string
  pattern (prefix match, substring match, regex).  Prefix-based classifiers
  are inherently fragile: a new namespace whose failure codes do not share the
  existing failure prefix will silently escape the classification.  Before
  merging, grep for all call sites that branch on reason-code string content
  and confirm each one handles the new namespace.  Prefer exhaustive
  enumeration or a registry-based approach over open-ended prefix matching
  when the set of classifiable values grows across subsystem boundaries.
- Degradation reason codes (for example fallback/retry/deferred paths that
  indicate the preferred engine/path could not complete) must be classified as
  operationally visible outcomes in severity gating (typically failure/warn).
  Do not classify degradation-only outcomes as informational by default when
  that would hide rollout risk at `warn` verbosity.

### 8. Metrics endpoint correctness and observability gaps
Historical issues: `478db96`, `b905fee`, `461908f`, `9f3885e`.

Required:
- Detect and fail on response rendering truncation; never silently emit partial metrics payloads.
- Set response metadata (status/content-length/content-type) only after final body length is known.
- Keep metrics struct/schema evolution synchronized across C code, tests, docs, and snapshots.
- When SHM-backed metrics struct layout changes, enforce a hot-reload compatibility strategy:
  add/validate a stable layout version (or magic) in
  `ngx_http_markdown_init_metrics_zone()` OR bump the SHM zone name so old slab
  allocations are not reattached to a new layout.
- Keep Prometheus metric families semantically non-overlapping: aggregate outcome
  series must be mutually exclusive, and detailed breakdown counters must live in
  a separate metric family/label space to avoid double-counting.
- Every metric field exposed in the metrics struct, snapshot, and output
  renderers (JSON/text/Prometheus) must have at least one runtime write path
  that populates it with a real value.  Do not expose a gauge or counter that
  is never assigned — a permanently-zero metric misleads operators and masks
  real risk.  If the data source (for example an FFI struct field) does not
  exist yet, defer the metric to a future release instead of shipping a dead
  field.
- Metric names and HELP text must accurately describe what is actually
  measured.  If a metric is named `ttfb_seconds` (time-to-first-byte), the
  write site must fire at the first-byte event, not at finalize or request
  completion.  Semantic mismatch between name and measurement is a bug, not a
  documentation issue.
- Metric names that encode a unit of measurement (for example `_us` for
  microseconds, `_ms` for milliseconds, `_bytes`) must use a time source or
  data source whose resolution matches the claimed unit.  If the underlying
  time source provides only millisecond granularity, the metric name must
  reflect that (for example `_ms`, not `_us`).  Multiplying a coarse value
  to fill a finer unit creates false precision — operators and dashboards
  will interpret the metric as having resolution it does not possess.
- **When a metric name implies a specific measurement semantics (for example
  `cpu_time_ms`, `wall_time_ms`, `user_cpu_ms`), the implementation must
  actually measure that quantity.  Do not alias one metric to another with a
  misleading name (for example assigning `cpu_time_ms = ttlb_ms` and calling
  it CPU time).  If the true measurement is unavailable, either (a) use a
  name that accurately describes what is measured (for example `wall_time_ms`),
  (b) add a comment explicitly documenting the approximation and its
  limitations, or (c) defer the metric to a future release when the proper
  data source is available.

### 8b. Configuration–code alignment for tooling
Historical issues: evidence targets nested under `targets` key but code read
top-level keys; `no_regression_small_medium` in JSON but `no_regression` in
code; `html_bytes` in Rust output but `input_bytes` in Python readers.

Required:
- When a configuration file (JSON, TOML, YAML) defines a nested schema (for
  example `{"targets": {"bounded_memory": {...}}}`), every code path that
  loads that configuration must use the correct nesting.  Do not assume the
  configuration is flat if the production file has a top-level wrapper key.
  **Always test with the actual production configuration file**, not only
  with hand-crafted flat test fixtures.
- When a configuration file and code use different key names for the same
  concept (for example `max_slope_bytes_per_input_byte` in JSON vs
  `max_slope` in code), use explicit fallback logic that tries both names
  with a comment explaining the discrepancy.  Prefer aligning the names
  across config and code in the same change set.
- When a data producer (for example a Rust benchmark binary) emits a field
  under one key name (for example `html_bytes`) and a consumer (for example
  a Python evidence generator) reads under a different name (for example
  `input_bytes`), the consumer must accept **both** names with a fallback
  chain (for example `tier_data.get("html_bytes") or tier_data.get("input_bytes")`).
  This is the consumer's responsibility — the producer's key name is the
  source of truth.
- **When combined reports are used** (for example `--engine both` produces a
  single JSON with both full-buffer `tiers` and streaming `streaming_metrics`),
  every evidence evaluation function that reads streaming data must check
  **both** `streaming_metrics` (primary) and `tiers` (fallback), because the
  same JSON file serves as both `--fullbuffer-report` and `--streaming-report`.

### 8c. Benchmark averaging consistency
Historical issues: warmup and fallback iterations included in latency/throughput
averages but not in durations; markdown/token averages divided by `total_iters`
while p50/p95 computed only from measured iterations.

Required:
- All per-iteration averages (latency, throughput, markdown size, token
  estimates, flush count) must use the **same denominator** — the count of
  iterations that contributed to the duration sample (non-warmup, non-error,
  non-fallback).  Do not mix `total_iters` for some metrics and
  `measured_iters` for others.
- For every derived metric, define one explicit inclusion predicate
  (for example “measured iteration only”) and update both the numerator and its
  sample count under that same predicate.  Do not update numerator and
  denominator under different guards.
- When accumulating per-iteration quantities (for example `markdown_len_sum`,
  `token_sum`), only add to the accumulator when the iteration qualifies as
  measured.  Use a per-iteration accumulator variable (for example
  `iter_markdown_len`) and add it to the global sum only inside the measured
  iteration guard.
- Do not combine counters from different granularities in one aggregate
  (for example per-chunk incremental counters plus per-iteration cumulative
  counters) unless they are first normalized to the same basis.
- In test assertions, verify that the denominator used for averages matches
  the number of iterations contributing to percentile calculations.

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

### 10. Regex ReDoS and parser fragility in tooling
Historical issues: `19875fe`, `10ea6ac`, `163f3e2`, `ea982d7`, `2103658`.

Required:
- Avoid regex patterns with overlapping quantifiers/backtracking hotspots in validators/parsers.
- Prefer deterministic parsing (positional cells, explicit token splitting, substring checks) for structured docs/tables.
- Treat static-analysis ReDoS findings as design issues, not cosmetic lint.

### 11. Shell portability and environment assumptions
Historical issues: `f0a98fc`, `55b9170`, `5a8b5ee`, `092f04f`.

Required:
- Assume macOS bash 3.2 compatibility unless script is explicitly version-pinned.
- Avoid GNU/PCRE-only flags (for example `grep -P`) in portable SOP/scripts.
- Use null-delimited file traversal for file-path safety.
- Ensure temporary directories are traversable by unprivileged worker processes when runtime depends on them.

### 12. Security hardening for file paths and code injection
Historical issues: `13c47c2`, `702c39d`.

Required:
- Sanitize metadata-derived path components and verify resolved paths stay within target directories.
- Never interpolate untrusted shell values into inline Python code strings.
- Pass dynamic file paths via environment variables or safe argument passing.

### 13. CI gating blind spots
Historical issues: `7bf22a0`, `090c5a5`, `034e42f`, `7018a3c`, `08f18fa`.

Required:
- Update workflow path filters whenever checks depend on new file paths.
- Baseline/bootstrap modes must not upload/compare artifacts incorrectly.
- Remove redundant CI steps that can desynchronize behavior or waste runtime.

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

### 15. Cross-language interface and FFI synchronization
Historical issues: `dbb5722`, `dfeffc4`, `ceeaf38`, `5970807`.

Required:
- When Rust FFI structs, options, defaults, or error codes change, update all affected boundaries in the same change set: Rust ABI/options code, public C headers, NGINX call sites, tests, and operator-facing scripts.
- Treat FFI comments and header docs as part of the interface contract; stale interface docs are a bug, not a cleanup item.
- Add at least one boundary-level test when introducing or changing an FFI option/error path (for example header-level, feature-independence, or end-to-end verification).
- **When adding a field to any FFI struct (for example `MarkdownResult`, `MarkdownOptions`, `StreamingStats`), apply this complete-sync checklist:**
  1. **Rust side**: the field is added to the `#[repr(C)]` struct with correct type and documentation.
  2. **Public C headers**: both `components/rust-converter/include/` and `components/nginx-module/src/` copies of `markdown_converter.h` are updated with the field and contract comments (semantics, lifecycle, when valid).
  3. **All initialization sites**: grep for `StructName {` across all test files (`tests/`), examples (`examples/`), and production code. Every struct literal must include the new field. **Do not rely on `..Default::default()` or partial initialization** — FFI structs crossing language boundaries must be explicitly fully initialized.
  4. **Cleanup/reset functions**: if the struct has associated `reset_*()`, `free_*()`, or cleanup helpers, they must zero/reset the new field to maintain API symmetry.
  5. **Test helper constructors**: any `empty_result()`, `zeroed_result()`, or similar test helper must include the new field.
- **Prefer helper functions over literal initialization for FFI structs**: When writing test code that needs a zeroed/empty FFI struct, **always use existing helper functions** (for example `empty_result()`, `zeroed_result()`, `ffi_test_empty_result()`) rather than writing `StructName { field: value, ... }` literals. This reduces future maintenance cost when the struct gains new fields — only the helper needs updating, not every call site. If no helper exists for a struct, create one before writing multiple literal initializations.
- **Lifecycle ordering rule: read data from foreign-owned structs BEFORE calling their associated free/release function.** Do not access fields after `markdown_result_free()`, `markdown_converter_free()`, or similar cleanup calls — the memory may be invalidated, zeroed, or returned to the allocator. Capture values to local variables first, then free.
- In C unit tests that stub FFI lifecycle helpers (for example
  `markdown_result_free()`), clear **all** struct fields covered by the public
  ABI (pointer, length, and numeric/error fields). Partial clears create false
  confidence and can mask stale-state regressions.
- When C code classifies FFI error codes into semantic categories, the
  classification branch must cover **all** codes defined in the FFI header
  that map to that category.  Do not assume a 1:1 mapping between C-local
  codes and FFI codes — the other language may define multiple distinct
  codes for the same semantic (for example both a general memory-limit code
  and a streaming-specific budget code).  Before writing a classification
  branch, grep the FFI header for all related `#define` constants and
  confirm the branch covers the full set.  This applies equally to error
  codes, feature flags, and enum values that cross the FFI boundary.

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

### 17. Cognitive complexity in C functions
SonarCloud rule: `c:S3776`.

Required:
- Keep function cognitive complexity at or below the configured threshold (currently 25).
- Extract helper functions for self-contained sub-decisions (for example content-type exclusion checks, observability logging) to flatten the main function's control flow.
- Prefer early-return guard clauses over nested `if`/`else` chains.
- When adding new rules or conditions to an existing decision function, check whether the addition pushes complexity over the limit and proactively extract before merging.

### 18. Shell script hygiene in e2e/tooling scripts
SonarCloud rules: `shelldre:S131`, `shelldre:S7677`, `shelldre:S1066`, `shelldre:S1192`.

Required:
- Every `case` statement must include a default `*)` clause, even if it only logs an error to stderr.
- Diagnostic and informational messages (INFO, WARN, DEBUG) must be redirected to stderr (`>&2`) so they do not pollute stdout when scripts are piped or their output is captured.
- Merge nested `if` statements that have no `else` branch into a single compound condition (`if [[ cond1 ]] && cmd; then`).
- Extract string literals used 4+ times into `readonly` constants defined near the top of the script. Grep patterns, expected header values, and expected body tokens are common candidates.
- For repeated assertions in multi-case e2e scripts, centralize checks in helper
  functions (for example HTTP status/header/body assertions) to keep failure
  semantics consistent and reduce copy/paste drift.
- Checks documented as required assertions must fail the case/run when missing;
  do not leave them as INFO-only log lines.
- `--plan`/dry-run style modes must short-circuit unconditionally before
  runtime prerequisites (for example `NGINX_BIN` checks), regardless of other
  option values.
- Script usage/help text must stay synchronized with parsed flags and defaults
  (for example every parsed `--flag` appears in `usage()` with the same default
  variable shown to users).
- When one tooling script orchestrates other repo scripts, the caller must match
  the callee's real interface contract exactly (flag vs environment variable vs
  positional argument). Do not pass synthetic flags that the callee does not
  parse; verify against the callee's `usage()`/option parser in the same
  changeset.
- Cross-script invocations in CI/tooling paths must not assume executable bits
  are preserved in all environments. Prefer `bash path/to/script.sh` (or ensure
  the executable bit is enforced) so coverage/release pipelines do not fail with
  `Permission denied`.
- Under `set -e`, command substitutions that intentionally inspect failure-path
  responses (for example truncated-stream curl probes) must not abort before
  assertions run. Use explicit tolerance (`|| true`) and then enforce behavior
  via subsequent checks on status/header/body artifacts.
- For HTTP HEAD validation in curl-based harness scripts, use `curl --head`
  (or `-I`) instead of `-X HEAD`, and create any expected empty body artifact
  explicitly when downstream checks read a body file.

### 19. Python e2e/tooling harness guardrails

Required:
- Binary prerequisites must validate executability, not only path existence
  (for example `os.path.isfile(...)` plus `os.access(..., os.X_OK)`, or
  `shutil.which(...)`).
- Harness checks that represent required behavior must affect pass/fail status
  (or exit non-zero), not only print informational diagnostics.

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

### 21. Warning triage and command reproducibility guardrails

Required:
- Treat compiler/test warnings as triage items, not automatic cleanup work:
  classify each warning as either a real defect signal or expected test-harness
  structure before deciding whether to change code.
- When a warning is reported from one test target, run at least one broader
  compile-only sweep for the touched area (for example `cargo test --tests
  --no-run`) to detect the full warning surface before applying fixes.
- Prefer fixing real unused-code warnings by removing stale fields/assignments
  or consuming values in assertions; use blanket `allow` only for deliberate
  shared test-support modules and keep the scope minimal.
- In multi-crate repositories, reproduce user-provided commands in the correct
  crate directory if repository root has no `Cargo.toml`; explicitly record the
  effective working directory used for verification.

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

### 23. Observability contract integrity (metrics, reason codes, docs)

Required:
- Every new metric field must have a complete lifecycle in the same changeset:
  struct field → snapshot copy → output renderer(s) → runtime write site.
  If any link is missing (especially the runtime write site), the metric is
  dead and must not be shipped.  Verify by grep: every field added to
  `ngx_http_markdown_metrics_t` must appear in at least one
  `NGX_HTTP_MARKDOWN_METRIC_INC` / `METRIC_ADD` or direct assignment outside
  of snapshot collection.
- Every new reason code must have a complete lifecycle in the same changeset:
  static string definition → accessor function → `ngx_http_markdown_log_decision()`
  callsite at the corresponding runtime branch.  A reason code that is defined
  and documented but never emitted is a contract violation.
- Gauge metrics that claim to measure a specific event (for example
  "time-to-first-byte") must write their value at that event, not at a
  later event (for example finalize).  Use a one-shot latch flag in the
  per-request context to ensure the gauge is written exactly once at the
  correct moment.
- **When a metric depends on data from any cross-boundary source (FFI struct
  field, Rust stats, external API response, upstream header, submodule
  state), verify the complete producer→consumer chain exists in the same
  change set.** Apply this checklist:
  1. **Producer side**: the source struct/type has the field, and it is
     populated at the correct lifecycle point (for example Rust
     `StreamingStats.peak_memory_estimate` updated during conversion,
     FFI return struct includes the field).
  2. **Boundary crossing**: the FFI/interface layer exposes the field
     with correct type and ABI semantics (for example `#[repr(C)]`
     struct includes the new field, C header declares it).
  3. **Consumer side**: the NGINX C code reads the field after the
     producer has finalized it, and assigns to the metrics struct.
  4. **All output formats**: JSON, plain-text, and Prometheus renderers
     emit the metric with consistent naming.
  
  If any link is missing, **defer the metric** to the release that
  completes the chain. Shipping a metric with an incomplete data source
  creates a permanently-zero gauge that misleads operators and violates
  spec contracts.
- **General cross-boundary interface rule (applies beyond metrics)**:
  When modifying any struct, enum, or interface that crosses a language
  or module boundary (Rust↔C, C↔tests, core↔submodule), update **all
  consumers and producers** in the same change set. This includes:
  - FFI structs (`MarkdownResult`, `StreamingStats`, converter options)
  - Test helper type definitions that mirror production structs
  - Documentation and spec files that describe the interface contract
  
  Before merging, grep for all references to the modified type across
  the language boundary and confirm each one compiles and is
  semantically consistent. Do not assume "the other side will be
  updated later" — boundary drift causes silent ABI mismatches that
  are expensive to debug.
- Operator-facing docs that reference metrics must be validated against the
  actual output of each format (JSON key paths, Prometheus series names).
  Do not invent metric names that do not appear in any renderer.  For
  derived rates, always include the formula using real metric names.
- Observability side-effects (counter increments, reason code logging, gauge
  writes) must be recorded **after** the event they describe succeeds, not
  before the attempt.  If both "attempt" and "completion" semantics are
  needed, use separate counters with unambiguous names.
- **When a secondary code path (shadow mode, fallback, retry, diagnostic)
  invokes the same FFI call or receives the same result struct as the
  primary path, it must consume all spec-required fields from that result,
  not only the fields needed for its immediate purpose.**  Before merging a
  new code path that calls an FFI function, compare the fields it reads
  against the fields the primary path reads and confirm every spec-required
  field is either consumed (logged, recorded to metrics, or used in logic)
  or explicitly documented as intentionally skipped with rationale.
- When the same metric can be written from multiple code paths (primary path,
  retry/resume path, fallback path), all paths must apply the **same** success
  condition.  Do not weaken the guard in a secondary path — if the primary
  path requires `rc == OK`, the resume path must also require `rc == OK`, not
  merely `rc != ERROR`.
- In NGINX filter context specifically, `NGX_AGAIN` means "suspended /
  backpressure — bytes not yet delivered" and must not be treated as a
  successful send for observability.  Only `NGX_OK` and `NGX_DONE` confirm
  downstream acceptance.  When `NGX_AGAIN` occurs, defer the gauge write to
  the resume path where the pending chain drains with a confirmed success code.
- **All exit paths from a multi-path operation must apply symmetric
  observability semantics.** If one post-commit send-failure path records
  `postcommit_error_total + failed_total + reason_code`, every other
  post-commit send-failure path must do the same.  The rule is: classify
  return codes consistently (`NGX_OK/NGX_DONE` → success, `NGX_AGAIN` →
  defer, everything else → failure), then apply the matching side-effects
  on every path.  Asymmetric metric treatment causes "observed success but
  actual failure" drift that is expensive to debug in production.
- **When a deferred-state latch (flag, buffer, or pending marker) is used
  to bridge `NGX_AGAIN` across calls, every function that can encounter
  `NGX_AGAIN` for the same logical operation must set that latch.** Do not
  assume "only the first caller can hit backpressure" — any function that
  performs downstream send can receive `NGX_AGAIN` and must participate in
  the deferral protocol.  Grep for all call sites that perform the same
  send/output operation and confirm each one sets the latch on `NGX_AGAIN`.
- **Deferred-state latches must be cleared on both success AND failure
  resume paths.** Leaving a latch set after a failure can cause stale
  state on re-entry (for example double-counting success on a subsequent
  unrelated drain). The cleanup should be the first action in the failure
  branch, before recording failure metrics.
- **Gauge metrics should be updated unconditionally on every successful
  sample**, not guarded by value-range conditions (for example `> 0`).
  A gauge represents "the most recent sample" — skipping the update
  because the value is zero or empty causes the gauge to retain a stale
  value from a previous request, which misleads operators and dashboards.
  If distinguishing "no sample" from "sample is zero" is required, add
  a separate validity flag or sentinel metric rather than skipping the
  write.
- When a bug fix extends a classification branch to cover additional values
  (error codes, enum variants, content types), add regression tests that
  exercise each newly covered value individually.  Without per-value test
  coverage, the fix can silently regress when the branch condition is later
  modified.

### 24. C99 declaration/type safety and integer conversion hardening
Historical issues: Sonar `c:S819`, `c:S5276`, `c:S859`, `c:S995`, `c:S5350`.

Required:
- No implicit declarations anywhere in C paths. Every called symbol must have
  a visible prototype at the call site in the same translation unit (including
  implementation headers analyzed standalone). If include order cannot
  guarantee visibility, add explicit forward declarations in the impl header.
- Do not silence declaration warnings by relying on compiler extensions.
  Implicit-int/implicit-function behavior is forbidden; C99-or-newer strict
  semantics are the baseline.
- Any narrowing conversion (`size_t/off_t/ngx_int_t` to narrower integer types
  such as `uInt`, `int`, `uint32_t`) requires:
  1) explicit upper-bound check against destination max,
  2) explicit cast after the check,
  3) error handling/logging on overflow path.
- For decoder/API structs that use narrower counters (for example zlib
  `avail_in/avail_out`), validate bounds before assignment and fail safely
  rather than truncating.
- Const-correctness is required for read-only data paths (parameters and local
  pointers). When writing or modifying a function, qualify pointer parameters
  as `const` when the function does not modify the pointed-to data — this
  applies to struct pointers, array pointers, and string pointers alike.
  Do not drop `const` qualifiers via cast unless the callee contract
  explicitly requires mutable memory and the data is truly mutable.
  When changing a parameter from non-const to const (or vice versa), update
  all call sites, forward declarations, and header prototypes in the same
  change set.
- Forward declarations must not shadow or redeclare NGINX macro identifiers
  (for example `ngx_log_error`, `ngx_memzero`, `ngx_str_set`). Before adding a
  declaration in impl headers, confirm the symbol is not a macro in NGINX
  headers. If a callable API is needed, declare the underlying function symbol
  (for example `ngx_log_error_core`) and keep signature parity with the
  canonical declaration in the owning header, including `const` qualifiers.
- Treat static-analysis findings that imply undefined behavior, data truncation,
  or invalid memory access risk as correctness/security issues and fix them in
  code; do not defer as cosmetic cleanup.
- When fixing declaration/type-safety findings, run at least one compile+unit
  target for the touched C area and report residual warnings explicitly.

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

### 26. Naming and documentation discipline

Required:
- Use meaningful, descriptive names for all functions, variables, types, and
  constants.  Avoid single-letter names outside of short loop counters (`i`,
  `j`, `n`) and well-established NGINX conventions (`r`, `cf`, `ctx`, `cl`).
  When a name encodes a unit or semantic (for example `elapsed_ms`,
  `pattern_count`), the name must match what the value actually represents.
- NGINX numbered macros (`ngx_log_debug0` through `ngx_log_debug8`,
  `ngx_log_error`) are framework conventions and must not be renamed.  The
  trailing digit indicates the argument count, not a version or sequence.
- Every non-trivial function must have a block comment immediately before its
  definition describing purpose, parameters, return values, and side effects.
  For C code use `/* */` block comments per NGINX style.  For Rust use `///`
  doc comments.  For Python use docstrings.  For shell functions use `#`
  comment blocks.
- Complex or non-obvious logic must have inline comments explaining the
  reasoning — not restating what the code does, but why it does it and what
  invariants it relies on.
- When a test reimplements production logic (because the production function
  cannot be linked), the test must document the divergence risk and the
  semantic contract it mirrors.
- Struct and enum type definitions must have a comment describing the purpose
  of the type and the meaning of each field/variant, especially for types
  that cross language or module boundaries (FFI structs, shared config types).
- Shell scripts must document each function with a comment block stating
  purpose, arguments, output, and exit behaviour.  Repeated string constants
  must be extracted into `readonly` variables with a descriptive name.

## Required Agent Workflow

### Before coding
- Read relevant sections in the local development guide (if available) for touched area (HTTP/filter/memory/style).
- Identify invariants likely to break (header ordering, backpressure, reason codes, buffer bounds).
- List boundary surfaces up front when the change crosses layers (NGINX C, Rust core, FFI/header, docs, scripts, CI).
- Identify minimum verification commands before writing code.
- When remediating SonarCloud findings for a PR, fetch findings from
  `api/issues/search` with explicit `componentKeys`, `pullRequest`,
  and `statuses=OPEN,CONFIRMED`; do not rely solely on dashboard
  "top issues" summaries, which may include already-closed items.

### Before writing or modifying any code (mandatory pre-output checklist)

**Do NOT write code first and fix later. Validate BEFORE every file write/edit.**

For each code change you are about to produce, mentally (or explicitly in a thinking step) walk through the applicable rules from the "Frequent Error Patterns" list above and confirm the output will not violate them. The checklist is organized by file type:

#### C module code (`components/nginx-module/src/`)
1. No dead stores — every assignment is read before the next write to the same variable. (Rule 16)
2. No cognitive-complexity blowup — if adding branches/loops to an existing function, estimate whether the addition stays within the threshold; extract a helper proactively if it would exceed. (Rule 17)
3. No blocking calls, no raw malloc/free, no global mutable state. (Baseline)
4. Return codes (`NGX_OK`, `NGX_AGAIN`, `NGX_DECLINED`, `NGX_ERROR`, `NGX_DONE`) used with correct semantics. (Baseline, Rule 2)
5. Backpressure: if the change touches body-filter output, confirm pending-chain and `last_buf` ordering are preserved. (Rule 1)
6. Memory budgets enforced on every allocation path; auxiliary buffers freed on all exits. (Rule 3)
7. UTF-8 chunk-boundary safety if touching streaming text paths. (Rule 4)
8. **No unguarded operations on values that may be NULL/uninitialized/invalid** — this includes dereference, relational comparison (`>`, `<`), arithmetic, or field access through pointers. Use explicit guards or boolean flags set at the production site. (Baseline C style)
9. FFI surface: if Rust structs/options/error codes change, all C headers, call sites, tests, and docs updated in the same change set. When a new field is added to an FFI struct (for example `MarkdownResult`), verify: (a) both public C header copies include the field, (b) the field is read into a local variable **before** any `*_free()` call, (c) the field is used in the correct lifecycle window (before the struct is released). (Rule 15)
10. New metrics: every field added to the metrics struct has a runtime write site (not just snapshot copy). New reason codes have `log_decision()` callsites. Gauge names match the actual measurement event. Observability writes fire after the event succeeds, not before the attempt. `NGX_AGAIN` is not success — defer gauge writes to the resume/drain path. Cross-boundary metrics: verify the complete producer→consumer chain (producer populates → FFI exposes → C consumes → all renderers emit). When adding fields to the SHM-backed metrics struct, bump the SHM zone version name to prevent hot-reload layout mismatch. Metric unit suffixes (`_ms`, `_us`, `_bytes`) must match the actual resolution of the data source. (Rules 8, 23)
11. FFI error code classification: when branching on error codes, cover all FFI-defined codes for the semantic category — grep `markdown_converter.h` for `ERROR_*` to confirm completeness. (Rule 15)
12. Multi-path observability symmetry: when adding or modifying a function with multiple exit paths (for example immediate success, backpressure-defer, resume-failure), confirm that every exit path applies symmetric success/failure metrics and reason codes. Classify return codes consistently (`NGX_OK/NGX_DONE` → success, `NGX_AGAIN` → defer, else → failure) on every path. Gauge updates are unconditional on successful samples. (Rule 23)
13. Deferred-state latch completeness: when adding a latch/flag to handle `NGX_AGAIN` deferral, grep for ALL functions that perform the same send/output operation and confirm each one sets the latch on `NGX_AGAIN`, not just the initial caller. Clear the latch on both success AND failure resume paths. (Rule 23)
14. Forward declarations must match definitions: when changing a function's signature (parameters, return type), update both the forward declaration and the definition in the same change set. Mismatches cause silent type errors in C.
15. C99 safety baseline: no implicit declarations, no unchecked narrowing conversions. Any required narrowing cast must have a preceding bounds check and explicit overflow failure path. (Rule 24)
16. Const-correctness: pointer parameters that are only read through must be `const`-qualified. No const-dropping casts in read-only paths. When changing a parameter's const qualification, update forward declarations and header prototypes in the same change set. (Rule 24)
17. No macro-shadow declarations: do not declare functions/variables with names that are NGINX macros (`ngx_log_error`, `ngx_memzero`, `ngx_str_set`, etc.). Validate against canonical headers and preserve exact signature parity (including `const`) when adding forward declarations. (Rule 24)
18. Reason-code classification coverage: when adding a new family of reason codes (for example `STREAMING_*` alongside `ELIGIBLE_*`/`FAIL_*`), update every function that classifies reason codes by string pattern (prefix match, substring). Prefix-based classifiers silently miss new namespaces whose failure codes do not share the existing prefix. Grep for all reason-code classification call sites and confirm each handles the new namespace. (Rule 7)
19. `ngx_str_t` token matching must be length-bounded: never call `ngx_strcasecmp()` on config/header values unless they are explicitly NUL-terminated. For directive keyword matching, require exact length equality and use `ngx_strncasecmp(..., expected_len)` (or a shared helper that enforces both). This prevents out-of-bounds reads on short/truncated inputs.
20. Coverage: if this change adds or modifies production code paths, verify that aggregate e2e or unit test coverage does not regress below 80% (90% for critical paths). (Rule 25)
21. Naming and documentation: every new or modified function has a block comment (purpose, parameters, return values). Variables and types use descriptive names. Complex logic has inline comments explaining *why*. (Rule 26)

#### C test code (`components/nginx-module/tests/unit/`)
1. No dead stores — simulation-style tests set the final value directly; initial state documented in comments only. (Rule 16)
2. Loop variables declared inside `for` when not used after the loop. (Rule 16)
3. Every assigned variable is consumed by a `TEST_ASSERT` before function end. (Rule 16)
4. Variables initialized at declaration when the compiler cannot prove definite assignment. (Rule 16)
5. Parameterized loops must consume the parameter value in the exercised path; avoid no-op parameterization. (Rule 14)
6. Where available, call shared test helpers that mirror production routing semantics instead of duplicating inline branch logic. (Rule 14)
7. Regression tests for error-code classification must exercise a routing stub mirroring production logic, not manually increment and assert local counters. (Rule 14)
8. Tests that verify side-effects (counter increments, reason codes, state transitions) must drive the outcome through an `rc` value or flag that flows through the same `if`/`switch` condition as production. Direct mutation (`m.counter++`) then assert proves nothing about production branching. (Rule 14)
9. Tests for deferred-state latch mechanisms (backpressure `NGX_AGAIN` → resume) must cover the full multi-step lifecycle: set phase (latch = 1), drain-success phase (metrics recorded, latch cleared), and drain-failure phase (latch cleared, failure metrics recorded). Single-step assertions on one phase cannot catch latch lifecycle bugs. (Rule 14)

#### Rust code (`components/rust-converter/`)
1. `cargo fmt` and `cargo clippy` clean. (Baseline)
2. Sanitizer/HTML semantics: void elements self-closing, skip-mode name-aware, nesting-depth saturation-safe. (Rule 5)
3. Emitter correctness: in-link markers accumulated, code-block raw content preserved, blockquote markers consistent. (Rule 6)
4. No speculative unused helpers — every new function must have at least one caller in the same change set, except shared test modules with `#![allow(dead_code)]`. (Rule 22)
5. Shared test modules included via `#[path]` must have `#![allow(dead_code)]`. (Rule 22)
6. Never remove a `#[cfg(feature)]`-gated import without checking all `#[cfg(feature)]`-gated code in the same file. (Rule 22)
7. Doc comments must not have blank lines between `///` lines. (Rule 22)
8. Doctests follow visibility layering: public API uses `no_run` with full `nginx_markdown_converter::...` imports; internal types use `ignore`; end markers are plain ```. (Rule 22)
9. No identity multiplications (`1 * N`) in size constants — use literals or underscored forms. (Rule 22)
10. **FFI struct initialization**: when creating a zeroed/empty FFI struct (for example `MarkdownResult`), **prefer existing helper functions** (`empty_result()`, `zeroed_result()`, etc.) over writing literal `StructName { ... }` initializations. Only use literal initialization when the struct needs non-zero/default values, and in that case ensure all fields are present. (Rule 15)
11. **FFI struct field additions**: when adding a field to any `#[repr(C)]` FFI struct (for example `MarkdownResult`), update (a) the Rust struct definition, (b) both public C header copies, (c) **all** struct literal initialization sites across `tests/` and `examples/` (grep for `StructName {`), (d) associated `reset_*()`/`free_*()` cleanup functions to zero the new field, and (e) any test helper constructors (`empty_result()`, `zeroed_result()`). (Rule 15)
12. **Lifecycle ordering**: when reading data from a foreign-owned struct (FFI result, allocator-owned buffer), capture fields to local variables **before** calling the associated free/release function. Do not access struct fields after `*_free()` — the memory may be invalidated or zeroed. (Rule 15)
13. Depth/stack state must be explicit and name-aware: do not model HTML/XML nesting safety with blind counter decrement on every end-tag. Track open elements explicitly (stack or equivalent) so mismatched/stray end-tags cannot reduce depth and bypass limits. (Rules 5, 14)
14. Dangerous-element classification must separate container vs void semantics when skip-mode state is involved. Void dangerous elements (for example `<link>`, `<base>`) should be handled as one-shot skips, not by entering persistent skip-depth that expects a closing tag.
15. Dangerous URL detection must reject control characters (including NUL) before scheme checks; scheme-prefix matching alone is insufficient for obfuscated payloads.
16. **Benchmark averaging consistency**: all per-iteration averages (latency, throughput, markdown size, token estimates, flush count) must use the same denominator — the count of measured iterations (non-warmup, non-error, non-fallback). For every derived metric, keep numerator and sample count under the same inclusion predicate, and do not mix counter granularities (chunk-level vs iteration-level) in one aggregate without normalization. (Rule 8c)
17. **Metric naming accuracy**: when a metric field name implies specific semantics (for example `cpu_time_ms`), the implementation must actually measure that quantity. If approximating with a different measurement (for example wall-clock TTLB), add a comment documenting the approximation or rename the field. (Rule 8)
18. **Rust 2024 edition pattern binding**: do not use explicit `ref` or `ref mut` binding modifiers when matching on a reference type (`&T`, `&Option<T>`, `&Vec<T>`, etc.). Rust 2024 edition treats this as an error because the reference already implies borrowing. Use `ref` only when matching on an owned value where you need to borrow without moving. Before writing `if let Some(ref x) = expr`, check whether `expr` is a reference — if so, drop the `ref`.
19. Coverage: if this change adds or modifies production code, verify that `cargo llvm-cov` aggregate coverage does not regress below 80% (90% for critical paths). (Rule 25)
20. Naming and documentation: every new or modified public function has `///` doc comments (purpose, arguments, returns, safety for unsafe). Internal helpers have `//` comments. Types and fields are documented. (Rule 26)

#### Shell scripts (`tools/`, e2e harnesses)
1. Every `case` has a `*)` default clause. (Rule 18)
2. Diagnostic/error messages go to stderr (`>&2`). (Rule 18)
3. No nested `if` without `else` that could be merged into a compound condition. (Rule 18)
4. String literals used 4+ times extracted into `readonly` constants. (Rule 18)
5. macOS bash 3.2 compatible — no GNU-only flags, no `grep -P`. (Rule 11)
6. No unsanitized path interpolation or inline code injection. (Rule 12)
7. Required checks must fail the case/run when missing (not INFO-only). (Rule 18)
8. `--plan`/dry-run modes short-circuit before runtime prerequisites. (Rule 18)
9. `usage()` text matches parsed flags/defaults exactly. (Rule 18)
10. Every function has a comment block stating purpose, arguments, output, and exit behaviour. Repeated string constants use `readonly` variables with descriptive names. (Rule 26)

#### Python test/tooling scripts (`tests/e2e/`, `tools/`)
1. Binary prerequisites validate executability (`os.access(..., os.X_OK)` or
   `shutil.which`), not just file existence. (Rule 19)
2. Required harness checks affect pass/fail status (or exit non-zero), not
   INFO-only output. (Rule 19)
3. **Configuration loading**: when loading a JSON/TOML/YAML config file,
   verify the actual nesting structure matches what the code expects.  Test
   with the production config file, not only hand-crafted flat fixtures.
   (Rule 8b)
4. **Consumer key compatibility**: when reading data from a producer that
   may use different key names (for example `html_bytes` vs `input_bytes`),
   accept both names with a fallback chain. (Rule 8b)
5. **Combined report handling**: when a single JSON serves as both
   full-buffer and streaming report (for example `--engine both`), read
   streaming metrics from `streaming_metrics` first, fallback to `tiers`.
   (Rule 8b)

#### Documentation and tooling
1. Canonical docs in `docs/` updated; no mirrored copies created. (Rule 9)
2. Validators/scripts consistent with the docs they check. (Rule 9)
3. No regex with overlapping quantifiers / backtracking hotspots. (Rule 10)
4. Metrics docs use exact emitted key/series names (JSON/Prometheus) with no naming drift. (Rules 8, 9)
5. Operator docs referencing metrics use real JSON key paths or Prometheus series names, not invented flat names. Derived rates include computation formulas. Verification commands include explicit `Accept` headers matching the parsed format. (Rules 9, 23)
6. All C code examples and C-style guidance in docs/README/steering must use
   C99-or-newer forms and must not introduce pre-C99 syntax.
7. Code examples in documentation must use meaningful names and include comments explaining non-obvious logic, matching the same standards as production code. (Rule 26)

**If any item would be violated, redesign the change before writing it.** Do not emit code that you know will need a follow-up fix — that wastes time, wastes tokens and review cycles.

### During coding
- Preserve NGINX event-driven semantics; no hidden blocking calls.
- Add/adjust tests in the same change set for each fixed behavior.
- Keep docs and validators synced when user-facing or SOP behavior changes.
- For Sonar-driven fixes, map each change to an open issue key and
  file/line from the current API response, and skip already-closed items.

### Before declaring completion
Follow evidence-first verification (no completion claim without fresh command output):
- Docs/tools changes: `make docs-check`
- Release-gate tooling: `make release-gates-check`
- Rust converter/streaming changes: `make test-rust`
- Rust example/benchmark changes: `cargo check --all-targets` in the crate
  directory to catch edition-specific errors (examples are only compiled
  with `--all-targets`, not the default `cargo check`).
- Streaming parser/sanitizer/error-path changes: `make test-rust-fuzz-smoke`
- NGINX C module changes: `make test-nginx-unit`
- C module production source changes: `make coverage-c` (verify coverage bar)
- Rust converter production source changes: `make coverage-rust` (verify coverage bar)
- Streaming runtime/e2e changes: `make verify-chunked-native-e2e-smoke` (or stronger profile when required)
- New `#[ignore]` tests introduced in this change: run targeted
  `cargo test ... -- --ignored` at least once and report result.
- If warnings were part of the task or findings, include the exact warning
  sweep command(s) and whether residual warnings remain.

If full suite is too heavy for current scope, run the narrowest relevant target set and explicitly report what was not run.

### After fixing bugs or addressing review findings
- Evaluate whether the fix reveals a generalizable pattern that should be
  captured in `AGENTS.md` (see "Rule Maintenance" section below).
- If the same class of mistake appeared in multiple review rounds, treat it
  as a systemic gap and add or strengthen a rule immediately.
- Check that existing rules and checklist items are consistent with the fix —
  if a rule said "do X" but the fix required "do X and also Y in the resume
  path", update the rule to cover Y.
- Review existing rules touched by the fix for over-specificity: if a rule
  only names one concrete scenario but the fix reveals the principle applies
  more broadly, rewrite the rule to cover the general class.  A rule that
  prevents one specific bug but allows the same mistake in analogous code
  is incomplete.

## Definition of Done for Agent Changes
- Pre-output checklist was applied to every file written or modified (no write-first-fix-later).
- Behavior is correct for nominal and edge-case paths.
- Added/updated regression tests cover the fixed failure mode.
- Related docs/validators/CI triggers stay consistent.
- Verification commands were run in the current session and results were checked.

## Rule Maintenance (Meta-Rule)

After completing any bug fix, review finding remediation, or multi-round code
review cycle, the agent must evaluate whether `AGENTS.md` needs updating:

1. **Pattern extraction**: For each fix, ask: "Is this a one-off typo, or does
   it represent a class of mistakes that could recur in different code?"  If
   the latter, extract a generalizable rule.
2. **Generalize, don't enumerate**: Rules should describe the *principle* that
   was violated, not just the specific instance.  Bad: "check
   `ERROR_BUDGET_EXCEEDED` alongside `ERROR_MEMORY_LIMIT`."  Good: "when
   classifying values into semantic categories across language boundaries,
   cover all source-defined values that map to the category, not just the
   locally-familiar ones."
3. **Over-specificity review**: When adding or updating a rule, re-read it and
   ask: "Would this rule prevent the same class of mistake if it occurred in
   a different file, a different subsystem, or with different variable names?"
   If not, the rule is too narrow.  Rewrite it to capture the structural
   pattern (for example "all write paths for the same metric must apply the
   same success guard") rather than the surface detail (for example "the
   `resume_pending` TTFB latch must check `rc == NGX_OK`").  A rule that
   only prevents re-introducing the same bug in the same
   function is nearly worthless — the value is in preventing the analogous
   mistake elsewhere.
4. **Recurring pattern escalation**: If the same class of mistake appears in
   two or more review rounds (even in different forms), treat it as a
   systemic gap.  Strengthen the existing rule or add a new one — do not
   rely on the agent "remembering" the previous fix.
5. **Consistency audit**: When adding a guard condition in one code path (for
   example a success check before writing a metric), scan all other paths
   that perform the same write and apply the same guard.  A rule that says
   "do X in path A" implicitly requires "do X in every path that does the
   same thing."
6. **Checklist sync**: If a new rule is added or an existing rule is
   strengthened, update the corresponding pre-output checklist item(s) so
   the rule is enforced at write time, not discovered at review time.
7. **Scope**: Only add rules that are actionable and verifiable before code
   is written.  Avoid vague aspirational statements.  Every rule should
   answer: "What specific check does the agent perform, and what does
   failure look like?"
