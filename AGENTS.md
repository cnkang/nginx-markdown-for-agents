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
| 1 | streaming-backpressure | Resume NGX_AGAIN according to chain ownership; never duplicate or overwrite pending data |
| 2 | streaming-backpressure | Correct return codes in fail-open branches; don't advance unconsumed buffer positions; cross-ref Rule 38 for replay buffer |
| 3 | memory-budget | Enforce all budgets; free auxiliary buffers on all exits; track peak memory |
| 4 | encoding-charset | Preserve incomplete UTF-8 tails across chunks; flush decoders at EOF |
| 5 | html-sanitizer | Void elements self-closing; skip-mode name-aware; nesting-depth saturation-safe |
| 6 | html-sanitizer | In-link markers accumulated; structural closures unwind inner-to-outer; code-block raw/fence state preserved across text-event boundaries; blockquote consistent; URL extraction includes media elements |
| 7 | observability-metrics | Explicit skip-reason mapping; reason-code tests aligned; new reason codes need log_decision() callsite |
| 8 | observability-metrics | Fail on truncation; SHM layout version; non-overlapping Prometheus families; every metric has runtime write; delivery counters after success; format string argument matching |
| 8b | observability-metrics | Config nesting matches code; consumer accepts both key names; combined report reads streaming_metrics first |
| 8c | observability-metrics | Same denominator for all averages; same inclusion predicate for numerator and sample count |
| 9 | docs-tooling | Keep Quick Start/validators consistent; metric names match emitted keys; Accept header in verification commands |
| 10 | parser-regex | No overlapping quantifiers; prefer deterministic parsing |
| 11 | shell | macOS bash 3.2 compatible; no GNU-only flags; null-delimited traversal; empty array expansion under set -u |
| 12 | security-cwe | Sanitize metadata-derived paths; never interpolate untrusted values |
| 13 | ci-gating | Update workflow path filters; no redundant CI steps; pin Actions to SHA; verify download checksums; sync validator regex and release package chain gates |
| 14 | testing-coverage | Every bug fix needs regression test; cross-boundary and malformed-input cases; parameterized tests must consume inputs |
| 15 | ffi-crosslang | Rust FFI changes → update all boundaries; prefer helpers over literal init; read before free |
| 16 | testing-coverage | No dead stores; loop vars in for; every var consumed by TEST_ASSERT |
| 17 | complexity | Keep function complexity at/below threshold; extract helpers; prefer early-return; run `make complexity-check` for C/Rust/Python/Shell |
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
| 30 | nginx-idioms | NUL-terminate ngx_str_t before C API calls; EOF last-line handling; cross-TU visibility; sentinel consistency |
| 31 | nginx-idioms | After merge: verify compile, diff --check, function count, no duplicates |
| 32 | security-cwe | ssize_t→size_t needs non-negative check; overflow guard on addition |
| 33 | security-cwe | Canonicalize Python paths before containment; allowlist CLI executables |
| 34 | dynconf-snapshot | Read mutable fields through effective_conf; snapshot race elimination; bind once at header_filter entry |
| 35 | dynconf-snapshot | dynconf_enabled gate; applied_mtime after successful reload; unknown keys → NGX_ERROR; startup apply |
| 36 | harness-routing | Route recurring tooling fixes to focused security family |
| 37 | e2e-runner | Rust-first E2E; no new Python e2e files; parity entries required |
| 38 | streaming-backpressure | Replay buffer init/append failure → precommit_error; failopen_completed flag; delivery after downstream NGX_OK or NGX_DONE; uniform across ALL fail-open paths (streaming, buffered, buffer-init/append, header filter) |
| 39 | nginx-idioms | NGX_DONE terminal semantics; multi-step header atomicity; bounded snapshots fail before mutation |
| 40 | nginx-idioms | Filter hash==0 (invalidated) headers in all lookup/iteration functions |
| 41 | shell | Shell harness detect_*.sh scripts must use POSIX ERE ([[:space:]] not \s); grep -E for extended patterns |
| 42 | c-safety | volatile only for single-threaded compiler barriers; direct aggregate __atomic_* usage is forbidden |
| 43 | memory-budget | Resizable buffer backing store (ctx->buffer.data) uses ngx_alloc/ngx_free exclusively; fixed-size pool-lifetime decompression workspaces may use ngx_pnalloc/ngx_pfree |
| 44 | encoding-charset | Gzip/deflate/Brotli preserves codec and member lifecycle in full-buffer and streaming paths; truncation is rejected; budgets remain cumulative |
| 45 | dynconf-snapshot | effective_conf NULL-safe access; cross-TU field visibility in shared headers; sentinel value consistency |
| 46 | ffi-crosslang | FFI operations must validate NULL/empty key inputs; guards on both sides of FFI boundary; NULL/empty-input test coverage |
| 47 | streaming-backpressure | Terminal-sent latch must not be set on NGX_AGAIN; latch only after successful downstream return |
| 48 | security-static-analysis | CodeQL remains primary SAST; supplemental gates stay focused, pinned, low-noise, and locally runnable; runnable Dockerfiles use operational non-root runtimes |
| 49 | docs-tooling | THIRD-PARTY-NOTICES must stay in sync with resolved dependency versions; add/remove/update entries in same changeset as Cargo.lock changes |
| 50 | nginx-idioms | Content-Type OWS separator accepts HTAB; trailing OWS excluded before parameter comparison |
| 51 | streaming-backpressure | Auth Cache-Control commit failure routes through precommit_error; multi-header aggregation checks any_public before has_private |
| 52 | streaming-backpressure | Derived-state reconciliation on multi-context drain; ALL derived state reconciled for EVERY popped context |
| 53 | ffi-crosslang | FFI fat-pointer safety; use as_mut_ptr + mem::forget for slice ownership transfer; empty results return NULL |
| 54 | ci-gating | Release artifact path traversal protection; resolve and verify containment before accessing manifest filenames |
| 55 | version-consistency | Keep source, chart, internal dependency, and documentation version references synchronized for the active release |
| 56 | build-safety | Orphan comment closers: verify every */ has a matching /* before committing C source; detect_orphan_comment_close.py gates at write time |
| 57 | build-safety | #ifdef-guarded function visibility: functions declared inside #ifdef FEATURE_GUARD must not be referenced outside that guard; detect_ifdef_guard_visibility.sh gates at write time |
| 58 | security-cwe | Workflow input injection: GitHub Actions inputs must be routed through env vars before use in shell run blocks; direct ${{ inputs.* }} interpolation in run blocks is command injection; detect_workflow_input_injection.sh gates at write time |
| 59 | nginx-idioms | Hardcoded HTTP status in reject paths: reject/error paths must return conf->error_status instead of hardcoded NGX_HTTP_BAD_GATEWAY; detect_hardcoded_http_status.sh provides advisory detection |
| 60 | e2e-runner | E2E config directive consistency: streaming mode must match test intent; no implicit auto + blocking directive unless intentionally testing runtime-block mechanism; detect_e2e_streaming_config.sh advisory |

## Required Agent Workflow

### Before coding
- Read relevant sections in the local development guide (if available) for touched area (HTTP/filter/memory/style).
- Identify invariants likely to break (header ordering, backpressure, reason codes, buffer bounds).
- List boundary surfaces up front when the change crosses layers (NGINX C, Rust core, FFI/header, docs, scripts, CI).
- Identify minimum verification commands before writing code.
- Define the checkable outcome before editing: failing case and expected
  behavior for bugs, observable behavior for features, unchanged behavior for
  refactors, or concrete risk list for reviews.
- Confirm the current repository and branch before coding tasks. Use the
  current branch unless the user explicitly asks to create, switch, or choose
  another branch.
- When remediating SonarCloud findings for a PR, fetch findings from
  `api/issues/search` with explicit `componentKeys`, `pullRequest`,
  and `statuses=OPEN,CONFIRMED`; do not rely solely on dashboard
  "top issues" summaries, which may include already-closed items.

### Repository operation safety
- Keep diffs tied to the current request. Do not mix behavior changes with
  unrelated formatting sweeps, renames, broad reorganization, new dependencies,
  or abstractions for one caller unless they are required for correctness or
  verification.
- Mention unrelated dead code, cleanup opportunities, or design concerns
  separately instead of fixing them inside the current patch.
- Commit only changes tied to the current request. Before each commit, review
  the staged diff and summarize what changed.
- Run the relevant checks before pushing when practical. Use normal push
  access only; do not force-push, rewrite history, change remotes, change
  repository settings, manage collaborators, alter branch protection, or
  delete branches unless the user explicitly requested that operation.
- Do not use wildcard cleanup deletes, scripted deletion loops, or broad
  recursive deletion commands. Delete only literal named paths required by the
  task; ask first when multiple deletions or recursive cleanup are necessary.

### Pre-output Checklist (domain-grouped)

**Do NOT write code first and fix later. Validate BEFORE every file write/edit.**

Applies-to codes: **C** = nginx-module/src, **T** = tests/unit, **R** = rust-converter, **S** = shell, **P** = python, **D** = docs

**Streaming & Backpressure** (C)
- NGX_AGAIN resume honors chain ownership: module-owned chains persist; downstream-owned chains drain with NULL; last_buf never overwrites pending data [1]
- Fail-open return codes correct; replay buffer init/append failure → precommit_error [2,38]
- failopen_completed prevents duplicate finalize; failopen_count after downstream NGX_OK or NGX_DONE; uniform across ALL fail-open paths (streaming, buffered, buffer-init/append, header filter) [38]
- UTF-8 tails preserved across chunk boundaries; flush at EOF; streaming tokenizer discard_bom=false, strip stream-start BOM in converter [4]
- Full-buffer and streaming gzip/deflate/Brotli preserve codec/member lifecycle; streaming state survives arbitrary chunks and backpressure resumes; gzip member resets keep response-wide budgets; truncated final streams/members are rejected; tests match production routing/formats [44]
- Terminal-sent latch must not be set on NGX_AGAIN; latch only after successful downstream return [47]
- Auth Cache-Control commit failure routes through precommit_error; multi-header aggregation checks any_public before has_private [51]
- Derived-state reconciliation on multi-context drain: ALL derived state reconciled for EVERY popped context [52]

**Memory & Budget** (C, R)
- All budgets enforced; auxiliary buffers freed on all exits [3]
- No unbounded allocations; pool-preferred [Baseline]
- Resizable buffers (buffer.data, scratch, growable decompression buffers) use ngx_alloc/ngx_free exclusively; fixed-size pool-lifetime decompression workspaces may use ngx_pnalloc/ngx_pfree [43]
- Every ngx_alloc-backed buffer has matching ngx_free on all exit paths [43]

**Observability & Metrics** (C, R, D)
- New metrics: complete lifecycle (struct→snapshot→renderer→write site) [8,23]
- New reason codes: definition + accessor + log_decision() callsite [7,23]
- Gauge at correct event; NGX_AGAIN not success; delivery ≠ decision counters [8,23]
- Cross-boundary metrics: verify complete producer→consumer chain [23]
- Multi-path exit: symmetric success/failure metrics on every path [23]
- Metric names match actual semantics; unit suffix matches resolution [8]
- Format string specifiers match argument list in all renderers (count and type) [8]
- ngx_log_debugN / ngx_log_errorN suffix digit matches actual argument count [8]
- `bash tools/harness/detect_ngx_log_arg_count.sh` — CI gate for suffix-digit mismatch [8]

**FFI & Cross-Language** (C, R)
- Rust struct changes → both C headers + all init sites + cleanup helpers [15]
- Prefer helper functions over literal FFI struct init [15]
- Read foreign-owned struct fields BEFORE free/release [15]
- FFI error code classification covers all header-defined codes [15]
- FFI operations validate NULL/empty key inputs; guards on both sides of FFI boundary; NULL/empty-input test coverage [46]
- Zero-length value (value_len==0): C-side sets target field to NULL/0, not a zero-length pool alloc [46]
- Non-trivial Rust FFI exports wrapped in catch_unwind; panic returns error code, not UB [15]
- Fail-closed fallback: FFI output struct initialized to safe default before catch_unwind [15]
- Atomic write after catch: closure computes return values; fields written only after Ok [15]
- FFI resources managed by Drop guards within catch_unwind for panic safety [15]
- FFI enums use explicit #[repr(u8)] / #[repr(i32)] for layout safety [15]
- FFI handle consumed after safe_finish/free — no pointer reuse after consumption [15]
- cbindgen header drift: run make check-headers after Rust FFI changes [15]
- FFI fat-pointer safety: use as_mut_ptr + mem::forget for slice ownership transfer; empty results return NULL [53]

**C Safety** (C)
- No implicit declarations; narrowing cast needs bounds check + overflow path [24]
- Const-correctness; no const-dropping casts; no macro-shadow declarations [24]
- Forward declarations match definitions (same changeset) [24]
- Forward declarations appear after all typedefs they reference; at file scope [24]
- NOSONAR annotations include reason + rule ref; bare `/* NOSONAR */` forbidden; only for NGINX API contract [24]
- `bash tools/harness/detect_nosonar_discipline.sh` — CI gate for bare NOSONAR [24]
- No unguarded ops on NULL/uninitialized/invalid values [Baseline]
- Orphan comment closers: every */ must have a matching /*; `python3 tools/harness/detect_orphan_comment_close.py` — CI gate [56]
- #ifdef-guarded function visibility: functions declared inside #ifdef GUARD must not be referenced outside; `bash tools/harness/detect_ifdef_guard_visibility.sh` — CI gate [57]

**NGINX Idioms** (C)
- Full ngx_list_part_t chain iteration (part→next) [28]
- Flag clearing after gated op succeeds [29]
- NUL-terminate ngx_str_t before C API calls; length-bounded matching [30]
- Cross-TU field visibility: shared headers for multi-file consumers; sentinel consistency [30]
- Snapshot race: read active_snapshot once at header_filter entry; bind via helper [34]
- effective_conf NULL-safe access; cross-TU field visibility; sentinel consistency [45]
- NGX_DONE terminal: return immediately after finalize_request; callers check NGX_DONE [39]
- Multi-step header modification atomic: abort on first failure, no partial apply [39]
- Bounded transaction snapshots: capacity overflow fails before mutation; never silently truncate rollback state [39]
- Header lookup/iteration filters hash==0 (invalidated) entries [40]
- Content-Type OWS separator accepts HTAB; trailing OWS excluded before parameter comparison [50]
- Hardcoded HTTP status in reject paths: return conf->error_status instead of NGX_HTTP_BAD_GATEWAY; `bash tools/harness/detect_hardcoded_http_status.sh` — advisory [59]

**HTML Sanitizer & Output Safety** (C, R, D)
- Void elements self-closing; skip-mode name-aware [5]
- In-link markers accumulated; code-block raw/fence state preserved across text-event boundaries; media URL extraction [6]
- Streaming code fence language identifier buffered across text-event boundaries; language- prefix matching [6]
- Implied or batched structural closures unwind inner-to-outer before enclosing block state [6]
- Link/URL escaping at every emission site; reject control chars [27]

**Testing & Coverage** (C, T, R)
- Every bug fix has regression test; cross-boundary + malformed cases [14]
- No dead stores; loop vars in for; every var consumed by assertion [16]
- Side-effect tests drive outcome through production branching, not manual mutation [14]
- Rust: no unused helpers; #[cfg(feature)] import safety; doctests by visibility [22]
- Coverage: 80% aggregate (90% critical paths) [25]
- E2E nginx.conf: explicit `markdown_streaming` matching test intent; no implicit auto + blocking directive [60]

**Shell** (S)
- Use `[[` for all conditional tests (not `[`); case has default `*)`; messages to stderr; explicit return; usage matches flags [18]
- macOS bash 3.2 compatible; no GNU-only flags; empty array expansion safe under set -u [11]
- Merge nested `if` without `else` into compound `&&` conditions [18]
- No unsanitized path interpolation [12]

**CI/Workflows** (CI)
- GitHub Actions pinned to immutable SHA; download checksums verified [13]
- Workflow input injection: ${{ inputs.* }} must be routed through env: before use in shell run blocks; `bash tools/harness/detect_workflow_input_injection.sh` — CI gate [58]
- Validator/gate regex patterns match actual struct field paths [13]
- Release/package workflows preserve one canonical module `.so` filename across
  NGINX build output, packaging metadata, load snippets, smoke tests, docs, and
  install-layout gates. Package-format-specific module directories must match
  the target nginx.org package `--modules-path` (for example RPM `/usr/lib64`
  versus DEB `/usr/lib`) [13]
- Every NGINX source version requested by release workflows or release
  Dockerfiles has a checked-in checksum, package artifact producer/consumer
  names match exactly, and architecture-specific smoke tests run on matching
  runner architecture [13]
- Release workflows that perform binary symbol validation must explicitly
  install the tool package that provides the validator (`binutils` for `nm`)
  and fail early with a clear preflight check if the binary is unavailable [13]
- Shell-based release symbol validation running under `pipefail` must not put
  whole-archive `nm` directly in each per-symbol grep pipeline; capture or
  normalize the symbol table once, tolerate non-fatal per-member archive
  diagnostics, and fail only when the captured symbol table is empty or a
  required exported symbol is absent [13]
- Release Rust static libraries that are validated with GNU binutils and linked
  into the NGINX C module must be emitted as native target objects that the
  target `nm`/linker can inspect; do not enable an LTO/archive format that makes
  required exported FFI symbols invisible to the release validation toolchain
  unless the workflow also installs and uses a compatible symbol validator and
  linker for that format [13]
- Release Rust builds must use a repository-pinned toolchain synchronized with
  `components/rust-converter/Cargo.toml` `rust-version`; release workflows must
  not silently float on `stable` when the crate requires a specific compiler
  version [13]
- All workflows capable of producing release package artifacts must apply the
  same Rust release build invariants: `--locked`, intended feature set,
  explicit target triple, and the matching target output directory. If a
  workflow is retained only for compatibility, mark it as non-canonical and
  gate that status explicitly [13]
- Standalone package workflows use the canonical package name and install
  layout, run `check_install_layout.sh` before upload, and do not ask RPM SPECs
  to rebuild when the workflow source tarball contains only a prebuilt module
  payload [13]
- Container jobs with Bash-only syntax set `defaults.run.shell: bash` or
  equivalent step-level `shell: bash` before relying on bashisms [13]
- Release Dockerfiles that copy and execute repository scripts install every
  interpreter named by those scripts' shebangs before first execution, or
  invoke only scripts valid for the base image's guaranteed shell. Minimal
  images must not assume `/usr/bin/env bash` exists unless `bash` is installed
  in the same stage [13]
- Package dependency constraints must either use distro-resolvable package
  versions/EVRs or non-exact version floors. Do not exact-match a naked
  upstream NGINX source version when distro packages append release suffixes
  or epochs. Prebuilt dynamic-module packages must constrain the supported
  NGINX minor ABI range with both a floor and an exclusive next-minor ceiling
  unless a separate install-time ABI check is the only supported guard [13]
- RPM constraints for nginx.org `nginx` packages must use an epoch-aware,
  distro-satisfiable floor and rely on the package preinstall ABI branch guard
  plus smoke tests for the upper-bound check when nginx.org epochs vary across
  minor branches [13]
- Standalone package workflows must validate user-supplied package versions
  before using them in paths, package metadata, RPM macros, or artifact names
  [13]
- Package smoke tests must select external package repositories from the
  detected target distro family; do not route Amazon Linux through CentOS
  repository paths [13]
- Container-job package smoke images must include the tools required before
  the first workflow step runs, including `tar` or `git` for `actions/checkout`.
  Minimal images that lack checkout prerequisites must be tested through a
  host-checkout plus `docker run` smoke pattern instead of as the job container
  [13]
- Tag release gates in GitHub Actions must run only repository-owned validators
  and artifacts available in a clean CI checkout. Do not call legacy or
  local-spec validators that require user-local Kiro/spec directories unless
  those inputs are checked into the repository or explicitly downloaded first
  [13]
- When newer release gates reuse prior-version validators, assertions about
  the active project version, package version, or release line must be
  parameterized by the caller. Prior-version validators may keep their
  standalone defaults, but they must not fail a newer release gate solely
  because `Cargo.toml`, package metadata, or chart metadata has advanced to the
  newer release version [13]
- Workflows, release gates, and documentation renderers that consume
  `tools/release-matrix.json` must use the repository's current checked-in
  schema. If the matrix schema changes, update all active consumers in the same
  change set; release workflows must not keep reading stale aliases such as
  `matrix`, `nginx`, `os_type`, or `support_tier: full` after the source of
  truth has moved to `entries`, `nginx_version`, `libc`, and
  `support_tier: supported` [13]
- Release package build environments must not require a newer glibc than any
  supported smoke-test/runtime distro for the same artifact family; build Linux
  module artifacts on the oldest supported glibc baseline or split artifacts by
  distro family [13]
- Package maintainer scripts must accept the lifecycle arguments passed by each
  target package manager, including RPM numeric `%post` arguments, and must not
  fail an install only because an advisory post-install script received an
  unfamiliar lifecycle argument [13]
- Public install docs must distinguish the currently published package channel
  from planned channels. Do not present bare APT/YUM repository install commands
  as available until the repository URL, signing key, and release workflow are
  real and validated; when only GitHub Release DEB/RPM artifacts exist, document
  artifact download plus checksum verification instead [13]
- Local K8s smoke tests that deploy stock images must disable module-specific
  NGINX directives, use an explicit kind kube-context for every Helm/kubectl
  operation, count structured pod fields without collapsed one-line jsonpath
  output, and avoid deleting clusters that existed before the test [13]
- Helm charts that support optional dynamic modules must keep stock-image
  defaults renderable without module directives, require an explicit in-image
  module path when any module directive family is enabled (including metrics),
  and must not derive implicit `hostPath` mounts from module paths. Use explicit
  opt-in extra volume values for custom mounts instead [13]
- Static security workflows must not duplicate CodeQL's C/C++ and Rust SAST
  coverage. Use focused supplemental gates for workflow linting, shell safety,
  secret scanning, high-confidence Semgrep rules, and Rust dependency/license
  policy; keep third-party actions pinned to immutable SHAs and keep PR checks
  lightweight [48]
- Local secret scans must cover Git-tracked worktree content, including tracked
  edits, while excluding ignored adapter state, caches, and other files that
  cannot enter a clean release checkout. Omit tracked paths that are absent due
  to worktree deletions, and preserve NUL-safe filename handling when
  materializing the tracked scan scope [48]
- Supply-chain visibility workflows such as Trivy, SBOM generation, and
  OpenSSF Scorecard may run on PR, push, schedule, and manual triggers, but
  remain report-oriented unless a specific blocking threshold is adopted. Do
  not describe them as hard blocking gates without documenting the
  runtime/noise tradeoff and enforcing threshold semantics [48]
- Local Trivy scans must exclude Git-ignored adapter state and generated
  reports (including `.kiro/`, `.codeartsdoer/`, and `build/`) so local-only
  files and prior SBOM output cannot create findings or memory pressure [48]
- Runnable CI and example Dockerfiles must end with a non-root `USER`. NGINX
  images must also listen on an unprivileged port and move PID/temp paths to
  locations writable by that user; a scanner-only `USER` declaration that
  breaks container startup is forbidden [48]
- Release artifact path traversal protection: validate manifest filenames resolve within artifact directory before accessing [54]
- Homebrew formula SHA-256 generated from release tag git archive (not HEAD);
  version stanza before sha256; nginx version derived from dependency
  metadata; tap publish validates tag existence; formula gate and release
  verify use same audit standard [13]

**Python** (P)
- Binary prerequisites validate executability [19]
- Path containment uses canonical targets after symlink resolution; CLI-derived
  executables must match a fixed canonical allowlist before subprocess use [33]
- Single-artifact CLI tools should emit stdout for caller redirection instead
  of accepting an unnecessary caller-controlled write path [33]
- Config nesting matches code; accept both key names [8b]
- Harness checks affect pass/fail, not INFO-only [19]

**Docs & Tooling** (D)
- Metric names match emitted keys; Accept header in verification commands [9]
- No regex with overlapping quantifiers [10]
- C examples use C99+; markdown escaping in examples [27]
- Workflow/script/dependency security docs must mention the matching local
  targets (`make security-static`, `make supply-chain`) and whether the gate is
  PR-blocking or report-oriented visibility [48]
- THIRD-PARTY-NOTICES must stay in sync with resolved dependency versions;
  add/remove/update entries in same changeset as Cargo.lock changes [49]

**If any item would be violated, redesign the change before writing it.**

### During coding
- Preserve NGINX event-driven semantics; no hidden blocking calls.
- Add/adjust tests in the same change set for each fixed behavior.
- Keep docs and validators synced when user-facing or SOP behavior changes.
- Clean up imports, variables, helpers, and docs references made unused by the
  current change.
- For Sonar-driven fixes, map each change to an open issue key and
  file/line from the current API response, and skip already-closed items.

### Before declaring completion
Follow evidence-first verification (no completion claim without fresh command output):
- Docs/tools changes: `make docs-check`
- Release-gate tooling: `make release-gates-check`
- Release gates 0.8.x: `make release-gates-check-08x` (canonical 0.8.x patch-line entry; `release-gates-check-080` is the compatible original name)
- Release gates 0.9.0: `make release-gates-check-090` (additive on 0.8.0; production examples, gate validator; `RELEASE_GATE_ALLOW_SKIP_MODULE=1` skips `test-production-examples-nginx-t` when `NGINX_BIN` is unavailable, mirroring the 091 module-benchmark skip contract)
- Release gates 0.9.1: `make release-gates-check-091` (additive on 0.9.0; blocking performance evidence gate for RC tags)
- Performance evidence check: `make perf-evidence-check` (non-blocking; module benchmark harness, report-only)
- Rust converter/streaming changes: `make test-rust`
- Rust example/benchmark changes: `cargo check --all-targets` in the crate
  directory to catch edition-specific errors (examples are only compiled
  with `--all-targets`, not the default `cargo check`).
- Streaming parser/sanitizer/error-path changes: `make test-rust-fuzz-smoke`
- NGINX C module changes: `make test-nginx-unit`
- C module production source changes: `make coverage-c` (verify coverage bar)
- Rust converter production source changes: `make coverage-rust` (verify coverage bar)
- Streaming runtime/e2e changes: `make verify-chunked-native-e2e-smoke` (or stronger profile when required; requires `NGINX_BIN` pointing to a locally-compiled NGINX binary with the module loaded)
- Python harness/tooling complexity changes:
  `PYTHONPATH=. python3 tools/harness/detect_python_complexity.py`
  and `python3 -m pytest tools/harness/tests/test_detect_python_complexity.py -q --tb=short`
  (Rule 17)
- C, Rust, Python, or Shell source changes: `make complexity-check` (Rule 17)
- C module volatile/atomic usage changes: `bash tools/harness/detect_volatile_atomic.sh` (Rule 42)
- Workflow, shell, secret-scan, Semgrep, or Rust dependency policy changes:
  `make security-static` (Rule 48)
- Supply-chain workflow, SBOM, Trivy, or Scorecard changes: `make supply-chain`
  when local tools are available; otherwise run the feasible subtargets and
  report missing tools exactly (Rule 48)
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
| 0.9.1 | 2026-07-20 | Kang | Added Rule 60: E2E config directive consistency — streaming mode must match test intent; detect_e2e_streaming_config.sh advisory gate; fixed 4 E2E configs using implicit/explicit auto + cache_validation full |
| 0.9.1 | 2026-07-19 | Kang | Strengthened Rule 38: `results.failopen_count` delivery-after-downstream-success contract applies uniformly to ALL fail-open paths (streaming, buffered, buffer-init/append, header filter); fixed C-001 buffer-init/append-failure and four header-filter fail-open paths that incremented `failopen_count` before downstream filter returned; added `failopen_delivery_after_downstream_test.c` regression test; updated `ngx_http_markdown_metric_inc_failopen` helper doc to state delivery-counter semantics; docs sync (encoding-charset Rule 44 Brotli streaming, dynconf-snapshot Rule 35 dry-run applied_mtime, streaming-backpressure Rule 38 NGX_DONE wording, streaming-check-order Engine→Policy, SYSTEM_ARCHITECTURE/PROJECT_STATUS Brotli streaming, Rust FFI doc accuracy — IncrementalConverterHandle fields, FFIHeaderEntry field docs, markdown_options_init defaults, lib.rs feature-gated default-on, ffi/convert.rs module doc, FFIErrorClass doc, exports.rs entry-point list, error/mod.rs FFI error code range, incremental.rs Markdown formatting, rust-converter README version pin, delivery_counter test notes) |
| 0.9.1 | 2026-07-15 | Kang | Added Rules 56–59: orphan comment closers (56), #ifdef-guarded function visibility (57), workflow input injection (58), hardcoded HTTP status in reject paths (59); added detect_orphan_comment_close.py, detect_ifdef_guard_visibility.sh, detect_workflow_input_injection.sh, detect_hardcoded_http_status.sh; fixed release-rpm.yml input injection; fixed detect_doc_sync.py _iter_worktree_text_files complexity |
| 0.9.1 | 2026-07-14 | Kang | Added `RELEASE_GATE_ALLOW_SKIP_MODULE=1` env-limited skip guard to `test-production-examples-nginx-t` (0.9.0 gate), mirroring the 091 module-benchmark skip contract; updated ADR-0019 blocking-semantics taxonomy |
| 0.9.1 | 2026-07-08 | Kang | 0.9.1 release: hybrid zero-copy output (markdown_streaming_zero_copy default off), gzip plus zlib/raw-deflate streaming decompression routing, bounded Brotli full-buffer routing, full-buffer copy reduction, markdown_auto_decompress directive registration fix, performance evidence gate (release-gates-check-091, perf-evidence-check), doctor advice tool, ADRs 0020–0022 |
| 0.9.0 | 2026-07-03 | Kang | Strengthened Rule 4 for streaming BOM handling: html5ever discard_bom=false, strip stream-start BOM in converter after utf8_tail reassembly, bom_stripped flag deferred for partial 0xEF sequences |
| 0.9.0 | 2026-06-27 | Kang | 0.9.0 release gate target (release-gates-check-090) with production examples validation, gate validator, CI experimental job; additive on 0.8.0 gates |
| 0.8.3 | 2026-06-26 | Kang | Rules 52–55: derived-state reconciliation on multi-context drain (streaming), FFI fat-pointer safety and empty-result NULL convention, release artifact path traversal protection, version consistency; updated Rule 15 (initialization-before-ownership-transfer), Rule 43 (pool-backed decompression exception); added release-manifest and version-consistency verification families to routing manifest |
| 0.8.2 | 2026-06-24 | Kang | Strengthened Rule 8 with ngx_log_debugN / ngx_log_errorN argument count matching; strengthened Rule 15 with fail-closed fallback initialization and atomic write after catch pattern; strengthened Rule 24 with NOSONAR annotation discipline (reason + rule ref required, bare NOSONAR forbidden, only for NGINX API contract); strengthened Rule 46 with zero-length value boundary handling (value_len==0 → set NULL/0 not zero-length pool alloc); fixed detect_open_without_path_validation.py `_expr_derives_from_hardcoded` to ignore Path constructor function names and fix `_maybe_propagate_path_wrapper` target tracking; fixed detect_cwe22_paths.py to distinguish builtin open() from .open() method calls and keyword args from positional args; added SMALL_BLOCK_THRESHOLD=7 to detect_duplicate_code.py to downgrade short duplicates to advisory; narrowed FFI_VALIDATION_KEYWORDS to remove overly broad NULL/validate/guard; detect_forward_decl_order.py and detect_duplicate_code.py now validate own args.directory via validate_read_path |
| 0.8.2 | 2026-06-24 | Kang | Strengthened Rule 31 with semantic-equivalence requirement for duplicate consolidation (branches with distinct error classification must not be collapsed); strengthened Rule 44 with Z_OK vs Z_BUF_ERROR inflate semantics distinction; strengthened Rule 33 with ValueError propagation trap for validate_read_path try/except; added detect_ngx_log_arg_count.sh (CI gate for ngx_log_debugN/errorN suffix-digit/argument-count mismatch); added detect_nosonar_discipline.sh (CI gate for bare NOSONAR without reason); refactored detect_cwe190_casts.sh allowlists from line-number-based to pattern-based matching to survive code edits that shift line numbers (26 stale warnings eliminated) |
| 0.8.2 | 2026-06-23 | Kang | 0.8.2 release: streaming decompression hardening, implied-closure correctness, FFI panic safety, decompression budget enforcement, security scan scoping, release-line documentation closeout |
| 0.8.2 | 2026-06-23 | Kang | Added general workflow safeguards for checkable outcomes, request-scoped diffs, Git operation safety, and deletion safety |
| 0.8.2 | 2026-06-23 | Kang | Strengthened Rule 15 with FFI panic safety (catch_unwind), Drop guards, enum repr layout safety, handle consumption contract, and cbindgen header drift CI gate; strengthened Rule 13 with Homebrew formula release integrity (SHA-256 from tag archive, version-before-sha256 ordering, nginx version from metadata, tap publish tag validation, audit standard alignment, cbindgen build dep); generalized Rule 43 for all resizable buffers (decomp workspace, scratch) not only buffer.data; strengthened Rule 24 with forward declaration ordering after typedefs; strengthened Rule 6 with streaming code fence language identifier buffering across text-event boundaries; added Rule 50 (Content-Type OWS/HTAB separator); added Rule 51 (auth Cache-Control commit failure handling); strengthened Rule 31 for organic duplicate code detection; strengthened detect_const_correctness.py with NGINX callback signature detection; extended check_third_party_notices.py with sub-workspace Cargo.lock existence check; added detect_pool_free.sh, detect_ffi_panic_safety.sh, detect_forward_decl_order.py, detect_duplicate_code.py, detect_open_without_path_validation.py |
| 0.8.2 | 2026-06-22 | Kang | Strengthened Rules 6 and 48 for inner-to-outer structural closure ordering and deletion-safe tracked-worktree secret scans |
| 0.8.2 | 2026-06-21 | Kang | Strengthened Rule 48 so local secret scans cover tracked release content without inheriting ignored adapter state |
| 0.8.2 | 2026-06-16 | Kang | Strengthened Rule 13 for release Dockerfile script interpreter prerequisites in minimal images |
| 0.8.2 | 2026-06-13 | Kang | Added Rule 48 for supplemental static security and supply-chain gates with focused Semgrep, secret scanning, cargo-deny, Trivy/SBOM/Scorecard, and local Make targets |
| 0.8.2 | 2026-06-12 | Kang | Added Rules 44–47: streaming deflate semantics (44), effective_conf NULL-safe access (45), FFI NULL/empty boundary guards (46), terminal-sent latch NGX_AGAIN semantics (47); strengthened Rules 13 (verified-rustup), 30 (cross-TU visibility, sentinel consistency) |
| 0.8.1 | 2026-06-10 | Codex | Strengthened Rule 13 for newer release gates that reuse prior-version validators with caller-parameterized active version assertions and current release-matrix schema consumers |
| 0.8.0 | 2026-06-16 | Kang | 0.8.0 release gate target (release-gates-check-080) with streaming, coverage, matrix, and clean-checkout gates |
| 0.7.17 | 2026-06-04 | Codex | Strengthened Rule 6 for streaming code-block fence state across split text events |
| 0.7.16 | 2026-06-03 | Codex | Strengthened Rule 13 for tag release gates avoiding user-local spec dependencies in clean CI checkouts |
| 0.7.15 | 2026-06-03 | Codex | Strengthened Rule 13 for package smoke job-container checkout prerequisites |
| 0.7.14 | 2026-06-03 | Codex | Strengthened Rule 13 for nginx.org RPM epoch-aware dependency floors plus preinstall ABI branch guards |
| 0.7.13 | 2026-06-03 | Codex | Strengthened Rule 13 for shell release symbol checks under pipefail with whole-archive nm output |
| 0.7.12 | 2026-06-03 | Codex | Strengthened Rule 13 for release Rust staticlib archive formats that must remain visible to GNU binutils and NGINX module linking |
| 0.7.11 | 2026-06-03 | Kang | Added Rules 41 (shell ERE), 42 (volatile vs atomic), 43 (buffer backing store allocation); code review remediation closeout |
| 0.7.10 | 2026-05-31 | Codex | Strengthened Rule 13 for prebuilt dynamic-module package dependency ranges using a minor ABI floor plus exclusive next-minor ceiling |
| 0.7.9 | 2026-05-29 | Codex | Strengthened Rule 13 for release workflow tool preflights, Rust toolchain pinning, and consistent package build invariants across canonical and compatibility workflows |
| 0.7.8 | 2026-05-27 | Codex | Strengthened Rule 13 for package-install docs matching the actually published GitHub Release artifact channel before APT/YUM repositories exist |
| 0.7.7 | 2026-05-27 | Codex | Strengthened Rule 13 for gating Helm metrics directives behind module enablement |
| 0.7.6 | 2026-05-26 | Codex | Strengthened Rule 13 for stock-image-safe Helm defaults, explicit module-enabled Helm configuration, and no implicit module-derived hostPath mounts |
| 0.7.5 | 2026-05-26 | Codex | Strengthened Rule 13 for distro-resolvable package dependencies, standalone version validation, distro-specific smoke repos, package script lifecycle args, package module path/glibc runtime compatibility, and local K8s smoke context/module safety |
| 0.7.4 | 2026-05-26 | Codex | Strengthened Rule 13 for GitHub Actions container jobs using Bash-only syntax |
| 0.7.3 | 2026-05-26 | Codex | Strengthened Rule 13 for standalone DEB/RPM package-name, canonical install layout, and prebuilt RPM source/SPEC consistency |
| 0.7.2 | 2026-05-25 | Codex | Strengthened Rule 13 release package chain invariants: canonical module filename, checksum coverage, artifact producer/consumer names, architecture-matched smoke runners, and Helm secure-default runtime checks |
| 0.7.1 | 2026-05-25 | Kang | Rules 39–40: NGX_DONE terminal semantics/double-finalize prevention, invalidated header hash==0 filtering; Rule 8 format string argument matching; Rule 11 bash 3.2 empty array expansion; Rule 13 supply chain hardening (SHA pinning, checksum verification, validator regex sync); Rule 24 NGINX callback const exception; new tools: detect_ci_supply_chain.sh, detect_header_hash_filter.sh, detect_finalize_return.sh |
| 0.7.0 | 2026-05-17 | Kang | v0.7.0 scope: decompress_max_size/parse_timeout/parser_budget directives (A03/A06); DecompressionBudgetExceeded(9)/ParseTimeout(10)/ParseBudgetExceeded(11) error codes (A04/A06); FFIAcceptResult + negotiate_accept FFI (A05); Rust conditional/decision/header_plan/negotiator modules (B02-B05); release-gates-check-070 target; DECISION_CHAIN.md v0.7.0 reason codes |
| 0.6.7 | 2026-05-17 | Kang | Extract detailed rules to docs/harness/rules/ domain files; AGENTS.md slimmed to index+workflow (~300 lines) |
| 0.6.6 | 2026-05-16 | Kang | Rule 38: fail-open replay buffer data integrity (init/append failure → precommit_error, failopen_completed state, delivery vs decision counter separation); Rule 2 cross-reference to Rule 38; Rule 8 delivery counter semantics; Rule 23 delivery vs decision counter guidance; C module checklist item 28 |
| 0.6.2 | 2026-05-11 | Kang | Rule 36: require routing-manifest coverage and focused security family routing for recurring tooling path-safety fixes |
| 0.6.2 | 2026-05-07 | Kang | Rule 35: dynconf snapshot isolation (dynconf_enabled gate), reload retry contract (applied_mtime separation), unknown key atomic rejection, startup apply of existing dynconf file, harness-check-full includes harness-security-checks |
| 0.6.1 | 2026-05-06 | Kang | Rules 27–31: Markdown escaping/injection prevention, full ngx_list_part_t iteration, flag clearing ordering, NUL-termination/EOF boundary, merge residual integrity; output-safety risk pack |
| 0.6.0 | 2026-05-02 | Kang | Comment/doc audit: Rust module docs, C function comments, Python docstrings, shell script headers; version 0.6.0 consistency fixes |
| 0.5.5 | 2026-04-24 | Codex | Added recent Git analysis remediation closeout rule |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
