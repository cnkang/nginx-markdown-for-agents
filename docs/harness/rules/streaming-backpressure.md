---
domain: streaming-backpressure
rules: [1, 2, 38]
paths:
  - "components/nginx-module/src/**"
  - "components/rust-converter/src/streaming/**"
---

## Streaming & Backpressure

### 1. Streaming backpressure and pending-chain handling
Historical issues: `5649890`, `23165d9`, `cfd4bd8`, `f97be3f`.

Required:
- If downstream filter returns `NGX_AGAIN`, persist the unsent chain in context and resume it later.
- Never overwrite pending output with terminal empty `last_buf` while data is still pending.
- On fallback from streaming to full-buffer path, reset state flags that gate conversion flow (for example conversion-attempt flags).
- Fail-open paths must still honor header/body ordering and deferred-header forwarding.

---

### 2. Incorrect fail-open semantics causing data loss or pointer advancement bugs
Historical issues: `23165d9`, `7cbe4fa`.

Required:
- Use semantically correct return codes in fail-open branches (`NGX_DECLINED` where needed), so caller behavior matches control intent.
- Do not advance buffer positions for unconsumed data when handing chain to next filter unchanged.
- If a fail-open branch invokes next header filter, mark header-forwarded state before the call to avoid double header emission.
- When fail-open involves replay-buffer data integrity (init/append failure, passthrough after `precommit_error`, duplicate finalize on terminal buffer), apply Rule 38 in addition to this rule. Rule 38 subsumes the streaming-specific replay buffer contract; this rule covers the general fail-open return-code and pointer-advancement discipline.

---

### 38. Fail-open replay buffer data integrity
Historical issues: dev/wip-0.6.6 commits 2138760–2283428 (silent data loss
on replay buffer init/append failure, duplicate finalize on terminal buffer).

Required:
- Any buffer used to store original upstream bytes for fail-open replay
  (for example `failopen_replay_buf`) must be initialized before streaming
  enters Pre-Commit.  Initialization failure must abort the streaming
  handle and route through `precommit_error` — never silently continue
  streaming without a fail-open recovery path.
- Append failure to the replay buffer (budget exceeded, capacity limit)
  must immediately invoke `precommit_error`.  Continuing streaming with
  an incomplete replay buffer would cause fail-open to silently drop
  prefix bytes on pre-commit error, corrupting the response.
- When `precommit_error` returns `NGX_DECLINED` with `ctx->eligible == 0`
  (pass policy), the caller must invoke `failopen_passthrough` to forward
  the original response downstream.  Returning `NGX_DECLINED` directly
  without passthrough causes body_filter to skip sending any data.
- After fail-open passthrough on a terminal buffer, the caller must set
  an explicit `failopen_completed` flag (or equivalent) so body_filter
  skips `finalize_request`.  Without this, finalize sends a duplicate
  empty `last_buf` (since `handle` is NULL after precommit_error abort).
- Never use `*last_buf = 0` side-effects to suppress finalize; prefer
  explicit output flags that document the control-flow contract.
- `results.failopen_count` must be incremented only after fail-open
  passthrough succeeds (downstream filter returns `NGX_OK`), not at the
  precommit_error decision point.  `streaming.precommit_failopen_total`
  tracks decisions; `results.failopen_count` tracks successful deliveries.
  This separation prevents inflating the delivery count when passthrough
  later fails (for example header forwarding error, allocation failure).

Verification:
- `grep -rn 'failopen_replay_buf\|failopen_replay_initialized' components/nginx-module/src/`
- For each init site, verify failure triggers `precommit_error`.
- For each append site, verify failure triggers `precommit_error`, not
  just a warning log.
- `grep -rn 'failopen_completed' components/nginx-module/src/`
- Verify body_filter checks `failopen_completed` before finalize.
- `grep -rn 'results\.failopen_count' components/nginx-module/src/`
- Verify the increment is in `failopen_passthrough` after downstream
  success, not in `precommit_error`.

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

For each code change you are about to produce, mentally (or explicitly in a thinking step) walk through the applicable rules from the checklist above and confirm the output will not violate them. The checklist is organized by file type:

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
22. Markdown output escaping: every new emission site for links, images, or URLs must call the shared escaping helper. No raw string interpolation in Markdown link labels `[...]` or destinations `(...)` / `<...>`. URL values must reject percent-encoded control characters before scheme validation. Forwarded header values must be validated (first-hop extraction, control char rejection, IPv6 bracket validation, fallback to server name). (Rule 27)
23. Full `ngx_list_part_t` chain iteration: any function iterating `ngx_list_t` must traverse the full `part → part->next` chain, not only the first part. When aggregating flags from multiple headers of the same name, check the aggregated result before branching on per-header flags. (Rule 28)
24. Flag clearing ordering: flags gating operations must be cleared **after** the gated operation succeeds, not before. Pattern: `if (flag) { rc = op(); if (rc == NGX_OK) flag = 0; }`. Doc comments must state the clearing contract. (Rule 29)
25. NUL-termination of `ngx_str_t`: before passing `ngx_str_t.data` to C APIs requiring NUL-terminated input (`ngx_strcasecmp`, `stat()`, `ngx_file_info()`, `opendir()`, `strstr()`), copy to a stack/pool buffer and append `'\0'`. Prefer length-bounded NGINX APIs (`ngx_strncasecmp`, `ngx_strlchr`) when possible. For directive matching, require exact length equality first. (Rule 30)
26. Residual code integrity: after merge or >500-line change in a single file, verify compilation, `git diff --check`, function count, and no duplicate adjacent blocks. For >30-line changes, verify the file is not truncated (closing brace present). (Rule 31)
27. Snapshot race elimination: in `header_filter`, `ngx_http_markdown_dynconf_watcher.active_snapshot` must be read exactly once at function entry into a function-lifetime `snap_copy`; `early_eff` is built from that once. Both variables must have function-lifetime scope. ctx binding must copy from these function-level variables (via `ngx_http_markdown_bind_request_snapshot()`), never re-read the global `active_snapshot` or re-invoke `build_effective_conf()`. `handle_ctx_alloc_failure()` must receive `eff` (not NULL). (Rule 34)
28. Fail-open replay buffer integrity: if the change touches fail-open or streaming pre-commit paths, verify (a) replay buffer init failure triggers `precommit_error`, (b) replay buffer append failure triggers `precommit_error` (not just a warning), (c) `failopen_completed` flag prevents duplicate `finalize_request` after terminal-buffer passthrough, (d) `results.failopen_count` is incremented only after downstream `NGX_OK` (not at decision point). (Rule 38)

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
21. Markdown output escaping: every new emission site for links, images, or URLs must call the shared escaping helper. No raw `write!`/`format!` that interpolates unescaped text in Markdown link labels `[...]` or destinations `(...)` / `<...>`. Use `chars()` not `bytes()` for escaping. URL values must reject percent-encoded control characters before scheme validation. (Rule 27)
22. Residual code integrity: after merge or >500-line change in a single file, verify compilation with `cargo check`, `git diff --check`, and no duplicate adjacent blocks. For >30-line changes, verify the file is not truncated. (Rule 31)

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
10. Every shell function ends with an explicit `return` statement (`return 0`
    on success, or the appropriate status on failure). This applies to all
    functions, not only those that emit no output. (Rule 18)
11. Every function has a comment block stating purpose, arguments, output, and exit behaviour. Repeated string constants use `readonly` variables with descriptive names. (Rule 26)

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
6. Release-gate/tooling functions stay below the Python cognitive complexity
   threshold; extract independent validation steps before adding branches.
   (Rule 17)
7. Repeated gate/check ID strings are module-level constants. (Rule 19)
8. When tooling path-safety behavior changes, confirm
   `harness_route.py --from-git --base main` maps the touched files to a
   focused security verification family (for example `harness-security`).
   (Rule 36)

#### Documentation and tooling
1. Canonical docs in `docs/` updated; no mirrored copies created. (Rule 9)
2. Validators/scripts consistent with the docs they check. (Rule 9)
3. No regex with overlapping quantifiers / backtracking hotspots. (Rule 10)
4. Metrics docs use exact emitted key/series names (JSON/Prometheus) with no naming drift. (Rules 8, 9)
5. Operator docs referencing metrics use real JSON key paths or Prometheus series names, not invented flat names. Derived rates include computation formulas. Verification commands include explicit `Accept` headers matching the parsed format. (Rules 9, 23)
6. All C code examples and C-style guidance in docs/README/steering must use
   C99-or-newer forms and must not introduce pre-C99 syntax.
7. Code examples in documentation must use meaningful names and include comments explaining non-obvious logic, matching the same standards as production code. (Rule 26)
8. Markdown escaping and link-destination examples in docs must use escaped forms; never show raw interpolation of untrusted text in link labels or destinations. (Rule 27)

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

## Recent Git Analysis and Remediation Closeout

When a task asks for broad recent Git analysis plus actual harness/steering
remediation:

1. Collect local and remote refs for the requested time window, deduplicate by
   commit SHA, classify titles by changed surface, and deep-check high-risk
   diffs before changing rules.
2. Write a report under `docs/project/` with stable finding IDs, priority,
   evidence, recommended fix, and verification method.
3. Implement all P0/P1 findings first.  Then implement all worthwhile P2/P3
   findings or mark them `intentionally deferred` with a concrete reason.
4. Append remediation results to the same report.  Every finding ID must have a
   final status: `fixed`, `intentionally deferred`, or
   `not applicable after review`.
5. Route the work through `docs/harness/risk-packs/harness-remediation.md` and
   run `make harness-check`; if a
   `docs/project/recent-git-harness-steering-analysis-*.md` report exists, the
   harness checker must validate its closeout evidence.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
| 0.5.5 | 2026-04-24 | Codex | Added recent Git analysis remediation closeout rule |
| 0.6.0 | 2026-05-02 | Kang | Comment/doc audit: Rust module docs, C function comments, Python docstrings, shell script headers; version 0.6.0 consistency fixes |
| 0.6.1 | 2026-05-06 | Kang | Rules 27–31: Markdown escaping/injection prevention, full ngx_list_part_t iteration, flag clearing ordering, NUL-termination/EOF boundary, merge residual integrity; output-safety risk pack |
