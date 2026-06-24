---
domain: observability-metrics
rules: [7, 8, 8b, 8c, 23]
paths:
  - "components/nginx-module/src/**"
  - "components/rust-converter/src/**"
  - "docs/**"
---

## Observability & Metrics

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

---

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
- **Counter metrics that track delivery outcomes (for example `results.failopen_count`)
  must be incremented only after the delivery operation succeeds (downstream filter
  returns `NGX_OK`), not at the decision point that initiates the delivery.**
  Separate "decision" counters (for example `streaming.precommit_failopen_total`)
  from "delivery" counters (for example `results.failopen_count`) to prevent
  inflating the delivery count when the downstream send later fails (header
  forwarding error, allocation failure, backpressure).  This principle applies
  to any counter where the decision and the outcome can diverge.

---

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

---

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

---

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
- **Delivery counters must be distinct from decision counters.** A decision
  counter (for example `precommit_failopen_total`) records the control-flow
  choice; a delivery counter (for example `results.failopen_count`) records
  successful downstream transmission.  Incrementing the delivery counter at
  the decision point inflates the count when the downstream send fails.  See
  Rule 8 for the concrete `failopen_count` contract and Rule 38 for the
  replay-buffer integrity requirements that motivated the separation.
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
- **Format string argument matching**: when adding new metric fields to text
  or JSON renderers that use `ngx_snprintf` or `ngx_slprintf`, manually verify
  that the number and types of format specifiers (`%V`, `%uA`, `%uz`, `%O`,
  `%i`, `%T`, etc.) exactly match the argument list.  `ngx_snprintf` does not
  perform compile-time type checking — a mismatch silently produces corrupted
  output or reads garbage from the stack.  After adding fields, count
  specifiers and arguments side-by-side.  Prefer extracting per-metric
  `ngx_snprintf` calls over monolithic format strings when the argument list
  exceeds 10 items.
- **`ngx_log_debugN` argument count matching**: the NGINX debug logging
  macros `ngx_log_debug0` through `ngx_log_debug8` encode the argument
  count in the macro name suffix.  When adding or modifying a
  `ngx_log_debugN` call, verify that the suffix digit exactly matches
  the number of format arguments passed after the format string.  A
  mismatch (for example `ngx_log_debug2` with only one `%`-substitution
  argument) silently reads garbage from the stack or corrupts the log
  output.  This applies equally to `ngx_log_debugN` and the error-level
  `ngx_log_errorN` family.
