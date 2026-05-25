# AGENTS.md

## Purpose
This file defines repository-specific engineering rules for AI agents working on `nginx-markdown-for-agents`.

These rules are distilled from:
- NGINX official development constraints in the Kiro local development guide (when present)
- Historical fix/doc and hidden-fix commits across local branches and remote-only commits (deduplicated by commit SHA)

Goal: prevent repeated classes of mistakes that previously caused regressions.

## Rule Priority
1. Kiro local development guide, when present (highest priority)
2. This `AGENTS.md` (non-negotiable baseline + rules)
3. Task-specific user instructions

Hard safety and engineering invariants (NGINX Baseline, Rules) from Kiro
and this file take precedence when conflicts exist with user-task
instructions.  For task scope and implementation intent, user-task has
priority over generic harness workflow guidance.  See
`docs/harness/routing-manifest.json` spec_resolver for the structured
priority chain.

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
- `docs/harness/rules/` contains detailed error-prevention rules grouped by domain; AGENTS.md provides the index.
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

## Rule Index

Full rule text, historical issues, and verification commands: `docs/harness/rules/<domain>.md`

| Rule | Domain File | Summary |
|------|-------------|---------|
| 1 | streaming-backpressure | Persist unsent chain on NGX_AGAIN; never overwrite pending with terminal last_buf |
| 2 | streaming-backpressure | Correct return codes in fail-open branches; don't advance unconsumed buffer positions; cross-ref Rule 38 for replay buffer |
| 3 | memory-budget | Enforce all budgets; free auxiliary buffers on all exits; track peak memory |
| 4 | encoding-charset | Preserve incomplete UTF-8 tails across chunks; flush decoders at EOF |
| 5 | html-sanitizer | Void elements self-closing; skip-mode name-aware; nesting-depth saturation-safe |
| 6 | html-sanitizer | In-link markers accumulated; code-block raw preserved; blockquote consistent; URL extraction includes media elements |
| 7 | observability-metrics | Explicit skip-reason mapping; reason-code tests aligned; new reason codes need log_decision() callsite |
| 8 | observability-metrics | Fail on truncation; SHM layout version; non-overlapping Prometheus families; every metric has runtime write; delivery counters after success; format string argument matching |
| 8b | observability-metrics | Config nesting matches code; consumer accepts both key names; combined report reads streaming_metrics first |
| 8c | observability-metrics | Same denominator for all averages; same inclusion predicate for numerator and sample count |
| 9 | docs-tooling | Keep Quick Start/validators consistent; metric names match emitted keys; Accept header in verification commands |
| 10 | parser-regex | No overlapping quantifiers; prefer deterministic parsing |
| 11 | shell | macOS bash 3.2 compatible; no GNU-only flags; null-delimited traversal; empty array expansion under set -u |
| 12 | security-cwe | Sanitize metadata-derived paths; never interpolate untrusted values |
| 13 | ci-gating | Update workflow path filters; no redundant CI steps; pin Actions to SHA; verify download checksums; sync validator regex with struct layout |
| 14 | testing-coverage | Every bug fix needs regression test; cross-boundary and malformed-input cases; parameterized tests must consume inputs |
| 15 | ffi-crosslang | Rust FFI changes → update all boundaries; prefer helpers over literal init; read before free |
| 16 | testing-coverage | No dead stores; loop vars in for; every var consumed by TEST_ASSERT |
| 17 | complexity | Keep function complexity at/below threshold; extract helpers; prefer early-return |
| 18 | shell | case has default; `[[` over `[`; messages to stderr; explicit return; merge nested if; usage matches flags |
| 19 | python-tooling | Binary prerequisites validate executability; harness checks affect pass/fail |
| 20 | testing-coverage | Don't mark tasks complete without verification; generate artifacts and verify shape |
| 21 | warnings-triage | Treat warnings as triage items; prefer fixing over blanket allow |
| 22 | testing-coverage | No unused helpers; #[cfg(feature)] import safety; doc comments no blank lines; doctests by visibility |
| 23 | observability-metrics | Complete metric lifecycle; gauge at correct event; delivery ≠ decision counters; cross-boundary chain; symmetric exit paths |
| 24 | c-safety | No implicit declarations; narrowing needs bounds check; const-correctness; no macro-shadow; NGINX callback const exception |
| 25 | testing-coverage | 80% aggregate coverage; 90% for critical paths |
| 26 | naming-docs | Meaningful names; block comments; inline comments explain why |
| 27 | html-sanitizer | Escape link labels/destinations; reject control chars in URLs; validate forwarded headers |
| 28 | nginx-idioms | Full ngx_list_part_t chain iteration; aggregate before branching |
| 29 | nginx-idioms | Clear flags after gated op succeeds, not before |
| 30 | nginx-idioms | NUL-terminate ngx_str_t before C API calls; EOF last-line handling |
| 31 | nginx-idioms | After merge: verify compile, diff --check, function count, no duplicates |
| 32 | security-cwe | ssize_t→size_t needs non-negative check; overflow guard on addition |
| 33 | security-cwe | Python open() needs validate_read_path(); resolve before mkdir |
| 34 | dynconf-snapshot | Read mutable fields through effective_conf; snapshot race elimination; bind once at header_filter entry |
| 35 | dynconf-snapshot | dynconf_enabled gate; applied_mtime after successful reload; unknown keys → NGX_ERROR; startup apply |
| 36 | harness-routing | Route recurring tooling fixes to focused security family |
| 37 | e2e-runner | Rust-first E2E; no new Python e2e files; parity entries required |
| 38 | streaming-backpressure | Replay buffer init/append failure → precommit_error; failopen_completed flag; delivery after downstream OK |
| 39 | nginx-idioms | NGX_DONE terminal semantics; return immediately after finalize; multi-step header atomicity |
| 40 | nginx-idioms | Filter hash==0 (invalidated) headers in all lookup/iteration functions |

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

### Pre-output Checklist (domain-grouped)

**Do NOT write code first and fix later. Validate BEFORE every file write/edit.**

Applies-to codes: **C** = nginx-module/src, **T** = tests/unit, **R** = rust-converter, **S** = shell, **P** = python, **D** = docs

**Streaming & Backpressure** (C)
- Pending-chain preserved on NGX_AGAIN; last_buf not overwritten while pending [1]
- Fail-open return codes correct; replay buffer init/append failure → precommit_error [2,38]
- failopen_completed prevents duplicate finalize; failopen_count after downstream OK [38]
- UTF-8 tails preserved across chunk boundaries; flush at EOF [4]

**Memory & Budget** (C, R)
- All budgets enforced; auxiliary buffers freed on all exits [3]
- No unbounded allocations; pool-preferred [Baseline]

**Observability & Metrics** (C, R, D)
- New metrics: complete lifecycle (struct→snapshot→renderer→write site) [8,23]
- New reason codes: definition + accessor + log_decision() callsite [7,23]
- Gauge at correct event; NGX_AGAIN not success; delivery ≠ decision counters [8,23]
- Cross-boundary metrics: verify complete producer→consumer chain [23]
- Multi-path exit: symmetric success/failure metrics on every path [23]
- Metric names match actual semantics; unit suffix matches resolution [8]
- Format string specifiers match argument list in all renderers (count and type) [8]

**FFI & Cross-Language** (C, R)
- Rust struct changes → both C headers + all init sites + cleanup helpers [15]
- Prefer helper functions over literal FFI struct init [15]
- Read foreign-owned struct fields BEFORE free/release [15]
- FFI error code classification covers all header-defined codes [15]

**C Safety** (C)
- No implicit declarations; narrowing cast needs bounds check + overflow path [24]
- Const-correctness; no const-dropping casts; no macro-shadow declarations [24]
- Forward declarations match definitions (same changeset) [24]
- No unguarded ops on NULL/uninitialized/invalid values [Baseline]

**NGINX Idioms** (C)
- Full ngx_list_part_t chain iteration (part→next) [28]
- Flag clearing after gated op succeeds [29]
- NUL-terminate ngx_str_t before C API calls; length-bounded matching [30]
- Snapshot race: read active_snapshot once at header_filter entry; bind via helper [34]
- NGX_DONE terminal: return immediately after finalize_request; callers check NGX_DONE [39]
- Multi-step header modification atomic: abort on first failure, no partial apply [39]
- Header lookup/iteration filters hash==0 (invalidated) entries [40]

**HTML Sanitizer & Output Safety** (C, R, D)
- Void elements self-closing; skip-mode name-aware [5]
- In-link markers accumulated; code-block raw preserved; media URL extraction [6]
- Link/URL escaping at every emission site; reject control chars [27]

**Testing & Coverage** (C, T, R)
- Every bug fix has regression test; cross-boundary + malformed cases [14]
- No dead stores; loop vars in for; every var consumed by assertion [16]
- Side-effect tests drive outcome through production branching, not manual mutation [14]
- Rust: no unused helpers; #[cfg(feature)] import safety; doctests by visibility [22]
- Coverage: 80% aggregate (90% critical paths) [25]

**Shell** (S)
- Use `[[` for all conditional tests (not `[`); case has default `*)`; messages to stderr; explicit return; usage matches flags [18]
- macOS bash 3.2 compatible; no GNU-only flags; empty array expansion safe under set -u [11]
- Merge nested `if` without `else` into compound `&&` conditions [18]
- No unsanitized path interpolation [12]

**CI/Workflows** (CI)
- GitHub Actions pinned to immutable SHA; download checksums verified [13]
- Validator/gate regex patterns match actual struct field paths [13]

**Python** (P)
- Binary prerequisites validate executability [19]
- Config nesting matches code; accept both key names [8b]
- Harness checks affect pass/fail, not INFO-only [19]

**Docs & Tooling** (D)
- Metric names match emitted keys; Accept header in verification commands [9]
- No regex with overlapping quantifiers [10]
- C examples use C99+; markdown escaping in examples [27]

**If any item would be violated, redesign the change before writing it.**

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
| 0.6.2 | 2026-05-07 | Kang | Rule 35: dynconf snapshot isolation (dynconf_enabled gate), reload retry contract (applied_mtime separation), unknown key atomic rejection, startup apply of existing dynconf file, harness-check-full includes harness-security-checks |
| 0.6.2 | 2026-05-11 | Kang | Rule 36: require routing-manifest coverage and focused security family routing for recurring tooling path-safety fixes |
| 0.6.6 | 2026-05-16 | Kang | Rule 38: fail-open replay buffer data integrity (init/append failure → precommit_error, failopen_completed state, delivery vs decision counter separation); Rule 2 cross-reference to Rule 38; Rule 8 delivery counter semantics; Rule 23 delivery vs decision counter guidance; C module checklist item 28 |
| 0.6.7 | 2026-05-17 | Kang | Extract detailed rules to docs/harness/rules/ domain files; AGENTS.md slimmed to index+workflow (~300 lines) |
| 0.7.0 | 2026-05-17 | Kang | v0.7.0 scope: decompress_max_size/parse_timeout/parser_budget directives (A03/A06); DecompressionBudgetExceeded(9)/ParseTimeout(10)/ParseBudgetExceeded(11) error codes (A04/A06); FFIAcceptResult + negotiate_accept FFI (A05); Rust conditional/decision/header_plan/negotiator modules (B02-B05); release-gates-check-070 target; DECISION_CHAIN.md v0.7.0 reason codes |
| 0.7.1 | 2026-05-25 | Kang | Rules 39–40: NGX_DONE terminal semantics/double-finalize prevention, invalidated header hash==0 filtering; Rule 8 format string argument matching; Rule 11 bash 3.2 empty array expansion; Rule 13 supply chain hardening (SHA pinning, checksum verification, validator regex sync); Rule 24 NGINX callback const exception; new tools: detect_ci_supply_chain.sh, detect_header_hash_filter.sh, detect_finalize_return.sh |
