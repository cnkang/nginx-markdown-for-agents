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

### 9. Docs/tooling drift (README vs INSTALLATION vs validators)
Historical issues: `726865e`, `2b0bd5d`, `83eca29`, `18dfb8c`, `4b2b761`, `09f5d1d`.

Required:
- Keep Quick Start, installation guide, and packaging validators semantically consistent.
- Validation scripts must compare the same scope/section intended by spec (for example Shortest Success Path).
- Handle duplicates/order mismatches explicitly with actionable diagnostics.
- Avoid false positives by preserving meaningful URL path semantics in curl checks.

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
- Keep unit/property/fuzz/integration coverage coherent; do not rely on a single test layer.

### 15. Cross-language interface and FFI synchronization
Historical issues: `dbb5722`, `dfeffc4`, `ceeaf38`, `5970807`.

Required:
- When Rust FFI structs, options, defaults, or error codes change, update all affected boundaries in the same change set: Rust ABI/options code, public C headers, NGINX call sites, tests, and operator-facing scripts.
- Treat FFI comments and header docs as part of the interface contract; stale interface docs are a bug, not a cleanup item.
- Add at least one boundary-level test when introducing or changing an FFI option/error path (for example header-level, feature-independence, or end-to-end verification).

## Required Agent Workflow

### Before coding
- Read relevant sections in `.kiro/nginx-development-guide.md` for touched area (HTTP/filter/memory/style).
- Identify invariants likely to break (header ordering, backpressure, reason codes, buffer bounds).
- Identify boundary surfaces up front when the change crosses layers (NGINX C, Rust core, FFI/header, docs, scripts, CI).
- Identify minimum verification commands before writing code.

### During coding
- Preserve NGINX event-driven semantics; no hidden blocking calls.
- Add/adjust tests in the same change set for each fixed behavior.
- Keep docs and validators synced when user-facing or SOP behavior changes.

### Before declaring completion
Follow evidence-first verification (no completion claim without fresh command output):
- Docs/tools changes: `make docs-check`
- Release-gate tooling: `make release-gates-check`
- Rust converter/streaming changes: `make test-rust`
- Streaming parser/sanitizer/error-path changes: `make test-rust-fuzz-smoke`
- NGINX C module changes: `make test-nginx-unit`
- Streaming runtime/e2e changes: `make verify-chunked-native-e2e-smoke` (or stronger profile when required)

If full suite is too heavy for current scope, run the narrowest relevant target set and explicitly report what was not run.

## Definition of Done for Agent Changes
- Behavior is correct for nominal and edge-case paths.
- Added/updated regression tests cover the fixed failure mode.
- Related docs/validators/CI triggers stay consistent.
- Verification commands were run in the current session and results were checked.
