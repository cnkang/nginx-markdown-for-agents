# AGENTS.md

## Purpose
This file defines repository-specific engineering rules for AI agents working on `nginx-markdown-for-agents`.

These rules are distilled from:
- NGINX official development constraints in `.kiro/nginx-development-guide.md`
- Historical fix/doc and hidden-fix commits across local branches and remote-only commits (deduplicated by commit SHA)

Goal: prevent repeated classes of mistakes that previously caused regressions.

## Rule Priority
1. `.kiro/nginx-development-guide.md` (highest priority)
2. This `AGENTS.md`
3. Task-specific user instructions

If two rules conflict, follow the higher-priority source.

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
- Use `u_char *`, `ngx_str_t`, and NGINX helpers (`ngx_snprintf`, `ngx_memcpy`, etc.) consistently.
- Use `NULL` pointer comparisons (not `0`).

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

### 7. Eligibility/reason-code drift and status handling bugs
Historical issues: `d2d836f`, `5288c1b`, `19c896c`, `bc35a1f`.

Required:
- Encode skip-reason mapping explicitly; do not rely on indirect checks that can misclassify edge cases.
- Keep reason-code behavior and tests aligned when eligibility logic changes.
- For protocol edge statuses (for example 206), map to the intended reason consistently even in malformed upstream scenarios.

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

### 9. Docs/tooling drift (README vs INSTALLATION vs validators)
Historical issues: `726865e`, `2b0bd5d`, `83eca29`, `18dfb8c`, `4b2b761`, `09f5d1d`.

Required:
- Keep Quick Start, installation guide, and packaging validators semantically consistent.
- Validation scripts must compare the same scope/section intended by spec (for example Shortest Success Path).
- Handle duplicates/order mismatches explicitly with actionable diagnostics.
- Avoid false positives by preserving meaningful URL path semantics in curl checks.
- Metric names documented in tables/examples must match emitted JSON keys and
  Prometheus series names exactly (no synthetic prefixes or renamed aliases).

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
- For fixture-scoped known differences, keep
  `tests/corpus/**/.meta.json -> streaming_notes.known_diff_ids` synchronized
  with `tests/streaming/known-differences.toml` IDs to avoid silent metadata
  drift.

### 15. Cross-language interface and FFI synchronization
Historical issues: `dbb5722`, `dfeffc4`, `ceeaf38`, `5970807`.

Required:
- When Rust FFI structs, options, defaults, or error codes change, update all affected boundaries in the same change set: Rust ABI/options code, public C headers, NGINX call sites, tests, and operator-facing scripts.
- Treat FFI comments and header docs as part of the interface contract; stale interface docs are a bug, not a cleanup item.
- Add at least one boundary-level test when introducing or changing an FFI option/error path (for example header-level, feature-independence, or end-to-end verification).

### 16. Dead stores and unused assignments in C test code
SonarCloud rules: `c:S1854`, `c:S5955`.

Required:
- Do not assign a value to a variable that is immediately overwritten before being read. In simulation-style tests, set the final (post-action) value directly and document the initial state in a comment instead.
- Declare loop variables (`i`, `feed_count`, etc.) inside the `for` statement when they are not used after the loop. The test code already uses C99 features, so `for (size_t i = 0; ...)` is preferred over declaring `i` at function scope.
- When a test assigns a variable to verify a cast or representation, ensure the variable is actually read by a `TEST_ASSERT` before the function ends.
- Initialize variables at declaration when the compiler cannot prove they are always assigned before use (for example variables set only inside conditional branches).

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
- Shared test utility modules included via `#[path = "..."]` in multiple
  integration test binaries must carry `#![allow(dead_code)]` at the module
  level, because each binary only uses a subset of the shared API and the
  remaining functions produce noisy warnings.
- Never remove or modify a `#[cfg(feature = "...")]`-gated import without
  scanning the entire file for `#[cfg(feature = "...")]`-gated usages of that
  import. Feature-gated items may appear far from their import and are invisible
  under the default feature set; removing the import silently breaks compilation
  under that feature flag.
- Doc comments (`///`) must not contain blank lines between comment lines;
  use `///` on every line (including blank doc-comment lines as `///` with no
  trailing text). Blank lines between `///` lines trigger
  `clippy::empty_line_after_doc_comments`.
- Avoid `1 * N` identity multiplications in size-constant arrays; use `N`
  directly or underscore-separated literals (`1_024`) to satisfy
  `clippy::identity_op`.

## Required Agent Workflow

### Before coding
- Read relevant sections in `.kiro/nginx-development-guide.md` for touched area (HTTP/filter/memory/style).
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
8. FFI surface: if Rust structs/options/error codes change, all C headers, call sites, tests, and docs updated in the same change set. (Rule 15)

#### C test code (`components/nginx-module/tests/unit/`)
1. No dead stores — simulation-style tests set the final value directly; initial state documented in comments only. (Rule 16)
2. Loop variables declared inside `for` when not used after the loop. (Rule 16)
3. Every assigned variable is consumed by a `TEST_ASSERT` before function end. (Rule 16)
4. Variables initialized at declaration when the compiler cannot prove definite assignment. (Rule 16)
5. Parameterized loops must consume the parameter value in the exercised path; avoid no-op parameterization. (Rule 14)
6. Where available, call shared test helpers that mirror production routing semantics instead of duplicating inline branch logic. (Rule 14)

#### Rust code (`components/rust-converter/`)
1. `cargo fmt` and `cargo clippy` clean. (Baseline)
2. Sanitizer/HTML semantics: void elements self-closing, skip-mode name-aware, nesting-depth saturation-safe. (Rule 5)
3. Emitter correctness: in-link markers accumulated, code-block raw content preserved, blockquote markers consistent. (Rule 6)
4. Shared test modules included via `#[path]` must have `#![allow(dead_code)]`. (Rule 22)
5. Never remove a `#[cfg(feature)]`-gated import without checking all `#[cfg(feature)]`-gated code in the same file. (Rule 22)
6. Doc comments must not have blank lines between `///` lines. (Rule 22)
7. No identity multiplications (`1 * N`) in size constants — use literals or underscored forms. (Rule 22)

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

#### Python test/tooling scripts (`tests/e2e/`, `tools/`)
1. Binary prerequisites validate executability (`os.access(..., os.X_OK)` or
   `shutil.which`), not just file existence. (Rule 19)
2. Required harness checks affect pass/fail status (or exit non-zero), not
   INFO-only output. (Rule 19)

#### Documentation and tooling
1. Canonical docs in `docs/` updated; no mirrored copies created. (Rule 9)
2. Validators/scripts consistent with the docs they check. (Rule 9)
3. No regex with overlapping quantifiers / backtracking hotspots. (Rule 10)
4. Metrics docs use exact emitted key/series names (JSON/Prometheus) with no naming drift. (Rules 8, 9)

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
- Streaming parser/sanitizer/error-path changes: `make test-rust-fuzz-smoke`
- NGINX C module changes: `make test-nginx-unit`
- Streaming runtime/e2e changes: `make verify-chunked-native-e2e-smoke` (or stronger profile when required)
- New `#[ignore]` tests introduced in this change: run targeted
  `cargo test ... -- --ignored` at least once and report result.
- If warnings were part of the task or findings, include the exact warning
  sweep command(s) and whether residual warnings remain.

If full suite is too heavy for current scope, run the narrowest relevant target set and explicitly report what was not run.

## Definition of Done for Agent Changes
- Pre-output checklist was applied to every file written or modified (no write-first-fix-later).
- Behavior is correct for nominal and edge-case paths.
- Added/updated regression tests cover the fixed failure mode.
- Related docs/validators/CI triggers stay consistent.
- Verification commands were run in the current session and results were checked.
