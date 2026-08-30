# Parser Budget and Timeout Enforcement

## Overview

This document describes the parser budget enforcement strategy for the
nginx-markdown-for-agents converter, including the feasibility analysis of
mid-parse interruption and the alternative budget mechanisms used.

**Requirement**: parser timeout and parser budget

---

## 1. Parser Interruption Feasibility Analysis

### 1.1 HTML Parser Library

The Rust converter uses **html5ever 0.38** (Mozilla's Servo project) as its
HTML parser. Two parsing paths exist:

| Path | API | Use Case |
|------|-----|----------|
| Full-buffer | `parse_document(RcDom, opts).one(input)` | Default path; builds complete DOM tree |
| Streaming | `html5ever::tokenizer::Tokenizer<TokenSinkAdapter>` | Opt-in streaming path; token-level processing |

The full-buffer path uses `markup5ever_rcdom 0.38` for DOM tree representation.

### 1.2 Does html5ever Support Cooperative Cancellation?

**No.** Neither parsing path provides a native "stop" or "abort" mechanism:

#### Full-buffer path (`parse_document().one()`)

The `.one()` method on `TendrilSink` consumes the entire input in a single
blocking call. There is no callback, progress hook, or cancellation token.
Once called, parsing runs to completion. The only way to limit this path is
to **bound the input size before calling the parser**.

#### Streaming tokenizer path (`TokenSink::process_token`)

The `TokenSinkResult` enum returned by `process_token` has these variants:

```rust
enum TokenSinkResult<Handle> {
    Continue,
    Script(Handle),
    Plaintext,
    RawData(RawKind),
}
```

There is **no `Stop` or `Abort` variant**. The tokenizer always expects
`Continue` (or a mode-switch variant for script/plaintext contexts). The
sink cannot signal "stop processing" to the tokenizer.

#### Signal-based interruption

Using OS signals (SIGALRM, and so on) to interrupt the parser is **not feasible**:

- Rust panic from a signal handler has undefined behavior. The Rust reference forbids it.
- Signal delivery across the FFI boundary (C → Rust) is unsafe
- NGINX's event-driven model does not support per-request signal timers

### 1.3 Conclusion

**Mid-parse cooperative cancellation is not feasible with html5ever.**
The library does not expose any mechanism to abort parsing partway through.
The project uses alternative budget enforcement strategies described below.

### 1.4 Feasibility Assessment

| Approach | Feasible? | Trade-offs |
|----------|-----------|------------|
| Check elapsed time at DOM traversal boundaries | **Yes** (implemented) | Works post-parse; adds ~10-20 ns per checkpoint |
| Interrupt html5ever mid-parse | **No** | No API support; `.one()` is atomic |
| TokenSinkResult::Abort | **No** | Variant does not exist in html5ever |
| Set alarm/signal to interrupt | **No** | Unsafe across FFI boundary; Rust panic from signal is UB |
| Use async with timeout | Partial | Requires major refactor of FFI boundary |
| Memory budget via allocation hook | Partial | Rust global allocator hooks can track but not interrupt |
| Bound input size pre-parse | **Yes** (implemented) | Limits worst-case parse time indirectly |

---

## 2. Alternative Budget Enforcement Strategy

Since the parser has no mid-parse interruption mechanism, the converter enforces
resource limits through a combination of **pre-checks** (before parsing)
and **cooperative checkpoints** (during DOM traversal, after parsing).

### 2.1 Input Size Limit (Pre-parse Gate)

**Directive**: `markdown_limits conversion_memory=<size>` (Config V2, `markdown_max_size` retired in 0.9.0)

Before any parsing occurs, the C module checks the response body size
against the configured maximum. This is a hard cumulative input-size cap
applied to both buffered and streaming paths. Documents exceeding this
limit are never passed to the Rust converter.

- **Default**: 64 MiB
- **Effect**: Prevents the parser from receiving unbounded input
- **Enforcement point**: C body filter, before FFI call

**Dual-role contract**: `conversion_memory` is not only the C-side input
admission cap.  The same configured value is passed to the Rust converter
as the full-buffer **generated-output budget** (`output_budget`), and
transient scratch allocations (normalizer buffers, table containers,
working-set reservations) are charged against it as well.  A conversion
that would grow the generated Markdown or its transient working set past
this value aborts with a controlled `MemoryLimit` error instead of
exceeding the configured peak.  `parser_memory` remains the independent
bound for the Rust parser's DOM/working-set allocations.

This is the primary defense for the full-buffer path: since `parse_document`
has no interruption mechanism, limiting input size bounds the worst-case parse time. The size gate caps the parse window.

### 2.2 Parser Memory Budget

**Directive**: `markdown_limits parser_memory=<size>`

- **Default**: 32 MiB
- **Enforcement**:
  - **Streaming path**: The converter enforces a conservative modeled
    resident-working-set ceiling at allocation preflight and parser
    checkpoints. The estimate includes retained `Vec`/`String` capacities,
    tokenizer reservations, state-machine and emitter storage, metadata,
    charset buffers, and incomplete UTF-8 tails. It is a bounded contract
    estimate, not an exact process-RSS measurement. html5ever does not expose
    allocator accounting. Exceeding it returns
    `ConversionError::ParseBudgetExceeded`.
  - **Key mapping**: `markdown_limits parser_memory=<size>` binds to the FFI
    field `parser_memory_budget`.
  - **Full-buffer path**: Enforced before parsing with a conservative estimate
    derived from input bytes, tag openers, transcoding, parser scratch, and
    DOM amplification. The check runs before the code path
    invokes `parse_html_with_charset`. This path cannot observe html5ever's internal
    allocations during `parse_document`, so it fails closed before parsing
    when the estimate exceeds the configured budget.
- **Internal error constant**: `ERROR_PARSE_BUDGET_EXCEEDED` (11)
- **Public reason/category**: `budget_exceeded`
- **Fail-open behavior**: Pass-through original content

#### Streaming Memory Budget Breakdown

The streaming `MemoryBudget` divides the total budget into stage-specific
sub-budgets:

| Stage | Default | Purpose |
|-------|---------|---------|
| `total` | 2 MiB | Overall cap for streaming pipeline |
| `state_stack` | 64 KiB | Structural nesting state (~1000 levels) |
| `output_buffer` | 256 KiB | Pending Markdown output |
| `charset_sniff` | 1024 B | Charset detection scan buffer |
| `lookahead` | 64 KiB | Front-matter / head metadata buffering |

The separate streaming pipeline budget is still built with
`MemoryBudget::for_total(...)` from the effective `conversion_memory` and
`streaming_buffer` limits. `parser_memory` is an independent modeled
parser-working-set ceiling. It does not accumulate all bytes ever received.

### 2.3 Parse Timeout (Cooperative Checkpoints)

**Directive**: `markdown_limits parser_timeout=<time>`

- **Default**: 10 seconds
- **Enforcement**: Cooperative timeout via `ConversionContext`
- **Error code**: `ERROR_PARSE_TIMEOUT` (10)
- **Reason code**: `PARSE_TIMEOUT`
- **Fail-open behavior**: Pass-through original content

#### How It Works

The timeout is **not** enforced during the html5ever parse phase itself
(which offers no interruption). Instead:

1. **Pre-parse check**: Before calling `parse_document`, the converter
   checks if the deadline has already passed (`ctx.check_timeout()`).
2. **Post-parse check**: Immediately after parsing completes, the
   converter checks the deadline again.
3. **During DOM traversal**: The converter calls the `increment_and_check()` method
   for every DOM node processed. Every 100 nodes, it checks elapsed time
   against the deadline.
4. **At pipeline boundaries**: Additional checks after metadata extraction,
   before/after output normalization.

#### Checkpoint Frequency

```
Every 100 DOM nodes → check_timeout()
```

This provides a balance between overhead (~10-20 ns per check) and
responsiveness (worst-case detection latency of ~1-10 ms for typical HTML).

#### Worst-Case Timeout Overshoot

Since the full-buffer `parse_document().one()` call offers no interruption,
the actual timeout overshoot depends on input size:

| Input Size | Approximate Parse Time | Overshoot Risk |
|------------|----------------------|----------------|
| < 1 MB | < 100 ms | Negligible |
| 1-5 MB | 100-500 ms | Low |
| 5-10 MB | 500 ms - 1 s | Moderate |
| > conversion_memory | Blocked (not truncated) by `markdown_limits conversion_memory=` | N/A |

The `markdown_limits conversion_memory=<size>` directive bounds the input
and associated full-buffer work. Its default is 64 MiB. The table compares
input size against the configured value, not a fixed limit. The directive
does not promise a fixed wall-clock parse duration. Hardware, parser
behavior, and the configured `conversion_timeout` determine the observed
time.

### 2.4 Depth Limit (Implicit via State Stack Budget)

Deep nesting stays bounded by the streaming pipeline's `state_stack` budget:

- **Default**: 64 KiB (roughly 1000 fixed-size nesting levels at ~64 bytes
  per level; retained String payloads reduce the remaining allowance)
- **Effect**: Documents with extreme nesting depth exhaust the state stack
  budget and trigger `BudgetExceeded`; the retained heap bytes of link hrefs,
  image src/alt text, and code-language identifiers count against the same
  budget, so deeply nested links with large hrefs are also bounded
- **Enforcement**: `MemoryBudget::check_state_stack()` on every push
- **Accounting**: the checker charges each stack slot at 64 bytes plus
  `StructuralContext::retained_heap_bytes()` for its variable-sized String
  payloads; `stack_bytes_estimate()` uses the same per-slot accounting for
  total working-set enforcement

For the full-buffer path, html5ever's tree builder handles deep nesting
according to the HTML5 spec (which defines a maximum nesting depth of 512
for formatting elements). In this path `markdown_limits conversion_memory=`
bounds the cumulative input bytes accepted for conversion, while
`markdown_limits parser_memory=` bounds the estimated full-buffer working
set through pre-parse and checkpoint checks. The live DOM tree size itself
is not bounded by `conversion_memory`.

### 2.5 Node-Count Tracking

The `ConversionContext` tracks the number of DOM nodes processed:

```rust
pub fn increment_and_check(&mut self) -> Result<(), ConversionError> {
    self.node_count += 1;
    if self.node_count.is_multiple_of(100) {
        self.check_timeout()?;
    }
    Ok(())
}
```

Currently, node count drives **checkpoint frequency** (timeout checks
every 100 nodes) rather than serving as an independent hard limit. A future version
may add a configurable `max_node_count` directive if operational experience
shows that node count is a better predictor of resource exhaustion than
input size or elapsed time.

---

## 3. Limit Interaction Model

```
Request arrives
    │
    ├─ markdown_limits conversion_memory= check (C layer)
    │   ├─ FAIL with known size (Content-Length present) → not_eligible
    │   │  (internal state SKIPPED), reason: not_eligible; the module then
    │   │  applies markdown_error_policy (pass-through or reject)
    │   └─ FAIL with unknown size (body filter detects over-limit while
    │      buffering) → failed conversion attempt: conversions_attempted/
    │      failed + failures_resource_limit recorded, outcome
    │      failed_open|failed_closed per error policy, category:
    │      resource_limit
    │
    ├─ markdown_limits conversion_timeout= pre-check (overall FFI deadline)
    │   └─ FAIL → pass-through, reason: timeout
    │
    ├─ markdown_limits parser_timeout= pre-check
    │   └─ FAIL → pass-through, reason: timeout
    │
    ├─ html5ever parse_document (uninterruptible)
    │   └─ Input capped by markdown_limits conversion_memory= before parsing
    │      (default 64 MiB, configurable). An oversized input with a known
    │      size is classified as not_eligible. Parser working-set estimates
    │      are bounded separately by parser_memory=
    │
    ├─ markdown_limits conversion_timeout= / parser_timeout= post-parse check
    │   └─ FAIL → outcome failed_open|failed_closed (per error policy), category: timeout
    │
    ├─ DOM traversal with cooperative checkpoints
    │   ├─ Every 100 nodes: check_timeout() against the earlier of conversion_timeout= and parser_timeout=
    │   │   └─ FAIL → outcome failed_open|failed_closed (per error policy), category: timeout
    │   └─ Memory budget checks (streaming path)
    │       └─ FAIL → outcome failed_open|failed_closed (per error policy), category: budget_exceeded
    │
    └─ Output normalization + final timeout check (earlier of conversion_timeout= and parser_timeout=)
        └─ FAIL → outcome failed_open|failed_closed (per error policy), category: timeout
```

The failure branches use the canonical lowercase codes from
[DECISION_CHAIN.md](DECISION_CHAIN.md): the **primary outcome** is
`failed_open` (with `markdown_error_policy pass`) or `failed_closed`
(with `fail_closed`/`status N`), and the **failure category** is
`timeout` or `budget_exceeded`. A `conversion_memory` failure is an
eligibility cap: the request is recorded as `not_eligible` (reason
`not_eligible`) before any FFI conversion attempt, and the module then
applies the configured `markdown_error_policy` (passthrough or reject).
The category for a parser working-set failure is `budget_exceeded`. The
category appears in the `category=` field of the decision log; the
specific reason (`not_eligible`, `timeout`, `budget_exceeded`, ...)
appears as the `reason` label on `nginx_markdown_requests_total`. The
outcome appears in the `outcome` label. Both labels are lowercase
canonical values — the internal converter constants (`PARSE_TIMEOUT`,
`PARSE_BUDGET_EXCEEDED`) are not the public labels.

### Limit Priority

When multiple limits are hit simultaneously, the first detected wins:

1. Input size (`markdown_limits conversion_memory=<size>`) — checked first, before FFI call
2. Overall FFI deadline (`markdown_limits conversion_timeout=<time>`) — the authoritative upper bound, checked at each checkpoint alongside parser_timeout
3. Parser checkpoint deadline (`markdown_limits parser_timeout=<time>`) — triggers the parser checkpoint when nonzero, measured from conversion start; at each checkpoint the parser deadline is evaluated first, then the overall deadline. The two are independent; when `conversion_timeout` is 0 only the parser deadline applies.
4. Memory budget (`markdown_limits parser_memory=<size>`) — checked on each allocation

### Fail-Open Behavior

The fail-open behavior depends on when the limit is hit relative to the
commit boundary (when response headers are sent):

**Pre-commit failures** (before the module sends response headers):

These include the input-size check, the pre-parse timeout check, and any
limit hit before output begins. With `markdown_error_policy pass`, the module
preserves the original HTML content and passes it through to the client
unchanged. The original content is still available because no transformation
has committed yet. With `markdown_error_policy fail_closed`, the module
returns the configured error status instead of the original content. `status N`
follows the configured status policy.

**Post-commit failures** (after response headers are sent):

These include timeout or budget failures during DOM traversal, output
normalization, or streaming output production after the module has already
committed headers and begun sending the converted body. At this point the
module cannot roll back to the original content because headers are already
on the wire. The module applies the protocol-safe finish-or-abort contract:
it finishes the remaining converted output cleanly when possible, or aborts
with the reason code recorded for diagnostics. It does not necessarily close
the downstream connection on every post-commit failure. The original content
is not available for pass-through because the response is mid-flight.

Additional notes:

- The internal converter constants are uppercase (`PARSE_TIMEOUT` and
  `PARSE_BUDGET_EXCEEDED`). The public request reason labels are lowercase
  `timeout` and `budget_exceeded`.
- The module records the decision and error classification through the active
  request and diagnostics observability surfaces. There are no standalone v1
  Prometheus families for parser timeout or parser budget.
- The module emits a warning-level log entry with the reason code

---

## 4. Implementation Status

| Limit | Status | Path | Enforcement Point |
|-------|--------|------|-------------------|
| Input size (`markdown_limits conversion_memory=`) | ✅ Implemented | Both | C body filter pre-check |
| Parse timeout (`markdown_limits parser_timeout=`) | ✅ Implemented | Both | Cooperative checkpoints in Rust |
| Parser memory budget (`markdown_limits parser_memory=`) | ✅ Implemented | Streaming | Modeled working-set checkpoints |
| Parser memory budget (full-buffer) | ✅ Implemented | Full-buffer | Conservative pre-parse estimate |
| Depth limit (explicit directive) | ⏳ Planned | — | Future: configurable max nesting |
| Node-count limit (explicit directive) | ⏳ Planned | — | Future: configurable max nodes |
| Mid-parse cooperative cancellation | ❌ Not feasible | — | html5ever lacks abort mechanism |

### Implemented Error Codes

| Code | Constant | Trigger |
|------|----------|---------|
| 3 | `ERROR_TIMEOUT` | Elapsed time exceeds `conversion_timeout` (the overall FFI deadline) |
| 10 | `ERROR_PARSE_TIMEOUT` | Elapsed time exceeds the parser checkpoint deadline (`parser_timeout`) |

At every checkpoint the parser deadline is checked first, then the overall
deadline. The two are independent: when `conversion_timeout` expires at a
parser checkpoint (because it is smaller than `parser_timeout`, or the
overall budget was consumed by pre-parse work), the conversion reports
`ERROR_TIMEOUT`; when `parser_timeout` expires first, it reports
`ERROR_PARSE_TIMEOUT`. The parser deadline is measured from the pipeline
entry (conversion start) for the pre-parse check, and from the parser
entry for the post-parse check, so pre-parse work (budget estimation,
upstream delay) is bounded by the parser sub-limit as well. In the
configuration layer `parser_timeout` must be a positive duration
(1ms..1h); a zero value is rejected by `nginx -t`. The zero-value
fallback to the overall timeout exists only inside the FFI option
decoder for callers that omit the field.
| 11 | `ERROR_PARSE_BUDGET_EXCEEDED` | The estimated parser working set exceeds `parser_memory`, or a later memory checkpoint fails — not only a single allocation failure |

---

## 5. Configuration Reference

```nginx
# Unified resource limits for the conversion pipeline
markdown_limits conversion_timeout=30s parser_timeout=10s
    conversion_memory=64m parser_memory=32m streaming_buffer=2m;
```

For full directive syntax and examples, see `docs/guides/CONFIGURATION.md`.

---

## 6. Future Work

1. **Cooperative checkpoint in streaming tokenizer**: Add elapsed-time
   checks between `feed()` calls in the streaming path (partially
   implemented via `StreamingConverter::check_timeout()`).

2. **Configurable node-count limit**: Add `markdown_max_nodes` directive
   that terminates traversal after N nodes regardless of elapsed time.

3. **Configurable depth limit**: Add `markdown_max_depth` directive for
   explicit nesting depth control independent of memory budget.

4. **Custom allocator tracking**: Use Rust's `GlobalAlloc` trait to track
   actual heap allocations during full-buffer parsing, enabling true
   memory budget enforcement for the full-buffer path.

5. **html5ever fork with abort support**: If operational experience shows
   that input-size bounding is insufficient, a fork of html5ever with a
   `TokenSinkResult::Abort` variant could enable true mid-parse cancellation.

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-24 | Kang | Named the parser_memory to parser_memory_budget FFI key mapping and located the full-buffer pre-parse check before parse_html_with_charset |
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
| 0.7.0 | 2026-05-17 | Kang | Initial parser budget documentation (parser budget) |
