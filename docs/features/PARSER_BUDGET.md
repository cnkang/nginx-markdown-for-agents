# Parser Budget and Timeout Enforcement

## Overview

This document describes the parser budget enforcement strategy for the
nginx-markdown-for-agents converter, including the feasibility analysis of
mid-parse interruption and the alternative budget mechanisms used.

**Requirement**: REQ-0700-CORRECTNESS-006 (TASK-A06)

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

---

## 2. Alternative Budget Enforcement Strategy

Since the parser has no mid-parse interruption mechanism, the converter enforces
resource limits through a combination of **pre-checks** (before parsing)
and **cooperative checkpoints** (during DOM traversal, after parsing).

### 2.1 Input Size Limit (Pre-parse Gate)

**Directive**: `markdown_limits conversion_memory=<size>` (Config V2, `markdown_max_size` retired in 0.9.0)

Before any parsing occurs, the C module checks the response body size
against the configured maximum. Documents exceeding this limit are never
passed to the Rust converter.

- **Default**: 64 MiB
- **Effect**: Prevents the parser from receiving unbounded input
- **Enforcement point**: C body filter, before FFI call

This is the primary defense for the full-buffer path: since `parse_document`
has no interruption mechanism, limiting input size bounds the worst-case parse time. The size gate caps the parse window.

### 2.2 Parser Memory Budget

**Directive**: `markdown_limits parser_memory=<size>`

- **Default**: 32 MiB
- **Enforcement**:
  - **Streaming path**: The `MemoryBudget` struct tracks allocations across
    pipeline stages (state stack, output buffer, charset sniff, lookahead).
    The struct checks each allocation against stage-specific and total limits.
    Exceeding any limit returns `ConversionError::BudgetExceeded`.
  - **Full-buffer path**: Enforced as a pre-check on input size (since
    html5ever's DOM tree size is roughly proportional to input size, the
    input size limit serves as a proxy for memory budget).
- **Error code**: `ERROR_PARSE_BUDGET_EXCEEDED` (11)
- **Reason code**: `PARSE_BUDGET_EXCEEDED`
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

When `parser_memory` is set, the streaming budget scales proportionally
via `MemoryBudget::for_total(budget)`.

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
| > 64 MiB | Blocked by `markdown_limits conversion_memory=` | N/A |

The `markdown_limits conversion_memory=<size>` directive (default 64 MiB)
bounds the input and associated full-buffer work. It does not promise a
fixed wall-clock parse duration. Hardware, parser behavior, and the configured
`conversion_timeout` determine the observed time.

### 2.4 Depth Limit (Implicit via State Stack Budget)

Deep nesting stays bounded by the streaming pipeline's `state_stack` budget:

- **Default**: 64 KiB (~1000 nesting levels at ~64 bytes per level)
- **Effect**: Documents with extreme nesting depth exhaust the state stack
  budget and trigger `BudgetExceeded`
- **Enforcement**: `MemoryBudget::check_state_stack()` on every push

For the full-buffer path, html5ever's tree builder handles deep nesting
according to the HTML5 spec (which defines a maximum nesting depth of 512
for formatting elements). The DOM tree size stays bounded by `markdown_limits conversion_memory=<size>`.

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
    │   └─ FAIL → pass-through, reason: SIZE_EXCEEDED
    │
    ├─ markdown_limits parser_timeout= pre-check
    │   └─ FAIL → pass-through, reason: PARSE_TIMEOUT
    │
    ├─ html5ever parse_document (uninterruptible)
    │   └─ Bounded by markdown_limits conversion_memory= ≤ 64 MiB
    │
    ├─ markdown_limits parser_timeout= post-parse check
    │   └─ FAIL → pass-through, reason: PARSE_TIMEOUT
    │
    ├─ DOM traversal with cooperative checkpoints
    │   ├─ Every 100 nodes: check_timeout()
    │   │   └─ FAIL → pass-through, reason: PARSE_TIMEOUT
    │   └─ Memory budget checks (streaming path)
    │       └─ FAIL → pass-through, reason: PARSE_BUDGET_EXCEEDED
    │
    └─ Output normalization + final timeout check
        └─ FAIL → pass-through, reason: PARSE_TIMEOUT
```

### Limit Priority

When multiple limits are hit simultaneously, the first detected wins:

1. Input size (`markdown_limits conversion_memory=<size>`) — checked first, before FFI call
2. Parse timeout (`markdown_limits parser_timeout=<time>`) — checked at each checkpoint
3. Memory budget (`markdown_limits parser_memory=<size>`) — checked on each allocation

### Fail-Open Behavior

When any limit is hit:

- With `markdown_error_policy pass`, the module passes the original HTML
  content through to the client unchanged.
- With `markdown_error_policy fail_closed`, the module returns the configured
  error status instead of the original content. `status N` follows the
  configured status policy.
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
| Parser memory budget (`markdown_limits parser_memory=`) | ✅ Implemented | Streaming | `MemoryBudget` stage checks |
| Parser memory budget (full-buffer) | ✅ Implemented | Full-buffer | Input size proxy pre-check |
| Depth limit (explicit directive) | ⏳ Planned | — | Future: configurable max nesting |
| Node-count limit (explicit directive) | ⏳ Planned | — | Future: configurable max nodes |
| Mid-parse cooperative cancellation | ❌ Not feasible | — | html5ever lacks abort mechanism |

### Implemented Error Codes

| Code | Constant | Trigger |
|------|----------|---------|
| 10 | `ERROR_PARSE_TIMEOUT` | Elapsed time exceeds `parser_timeout` |
| 11 | `ERROR_PARSE_BUDGET_EXCEEDED` | Memory allocation exceeds `parser_memory` |

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
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
| 0.7.0 | 2026-05-17 | Kang | Initial parser budget documentation (TASK-A06.3) |
