# ADR-0004: Streaming Conversion with Bounded Memory and Controlled Fallback

## Status

Proposed

## Context

The project currently uses full buffering as the primary runtime model (ADR-0002), with an optional "incremental" path for large responses.

Today, that incremental path is still not true streaming:

1. NGINX side still buffers the full response body before conversion.
2. Rust side `IncrementalConverter` still accumulates all input internally and converts on `finalize`.
3. The parser relies on a full-document DOM model (`html5ever` + `RcDom`), which creates document-size-proportional memory growth.

As a result:

- Large responses can cause high peak memory and latency spikes.
- The current hard guard (64 MiB incremental input ceiling) protects stability but does not solve the architecture limit.
- Simply increasing the size ceiling would increase OOM and denial-of-service risk.

We need a path to true streaming conversion that:

- keeps memory bounded per request,
- preserves operational safety,
- and remains compatible with existing fail-open expectations where technically possible.

## Decision

Adopt a dual-engine architecture and introduce an opt-in streaming path with explicit commit semantics.

### 1) Dual Engine Architecture

- **Engine A (existing):** Full-buffer DOM conversion path (default).
- **Engine B (new):** True streaming conversion path (event-driven parser + incremental Markdown emitter).

Engine selection is request-scoped and policy-driven. Full-buffer remains the safe baseline.

### 2) Streaming Pipeline

The streaming path processes data as chunks arrive:

```text
NGINX input chain
  -> streaming decompressor (if needed)
  -> streaming HTML tokenizer/parser
  -> sanitizer + structural state machine
  -> incremental Markdown emitter
  -> NGINX output chain flush
```

### 3) Bounded Memory Contract

Streaming mode enforces a per-request memory budget that is independent of total document size.

The budget covers only bounded state:

- decompression window
- parser/tokenizer lookahead buffer
- tag/context stack
- output pending buffer

If budget is exceeded, behavior follows configured fallback policy (see below).

### 4) Controlled Fallback and Commit Semantics

Streaming introduces a commit boundary:

- **Pre-commit:** no Markdown has been sent to client yet.
- **Post-commit:** at least one Markdown chunk has been sent.

Rules:

1. **Pre-commit failures** may fail-open back to original HTML (if configured).
2. **Post-commit failures** cannot safely switch back to HTML on the same response.
3. Post-commit failures must use explicit stream policy (default: fail-closed for that request).

This behavior is explicit and observable, not silent.

### 5) Conditional Request and ETag Policy

- Streaming path supports incremental ETag hashing over emitted Markdown.
- `if_modified_since_only` is supported in streaming mode.
- `full_support` (Markdown-variant `If-None-Match` revalidation) remains full-buffer-only in the initial streaming rollout.

### 6) Rollout Strategy

Streaming ships behind feature flags and progressive rollout:

1. disabled by default
2. enable for selected locations/traffic slices
3. monitor conversion correctness and memory/latency behavior
4. expand scope after production confidence gates are met

## Consequences

### Positive Consequences

1. **Bounded memory per request** in streaming mode, reducing large-document risk.
2. **Lower large-response latency and earlier response start** (improved TTFB for large bodies).
3. **Safer long-term architecture** than repeatedly increasing size limits.
4. **Controlled migration** via dual-engine design and explicit fallback semantics.
5. **Operational clarity** with mode-specific observability and explicit failure counters.

### Negative Consequences

1. **Higher implementation complexity** (new parser/emitter state machine, streaming decompression, and commit semantics).
2. **Two active code paths** increase maintenance and testing cost.
3. **Behavior differences** between full-buffer and streaming paths in edge cases until parity matures.
4. **Fail-open expectations change post-commit** in streaming mode and must be documented clearly.

## Alternatives Considered

### A. Keep current "incremental" model and optimize around it

**Pros:**
- minimal change
- low implementation risk

**Cons:**
- does not solve full-buffer architecture limit
- large-response memory pressure remains

**Why not chosen:** It extends current behavior but does not achieve true streaming or bounded memory.

### B. Raise the 64 MiB limit

**Pros:**
- easy short-term relief for some large documents

**Cons:**
- increases OOM/DoS exposure
- preserves core architectural bottleneck

**Why not chosen:** This is a risk increase, not a sustainable fix.

### C. Move conversion to an external service

**Pros:**
- isolates conversion memory from NGINX worker process
- independent scaling

**Cons:**
- adds network latency and new failure surface
- introduces infrastructure complexity and dependency management
- conflicts with origin-near inline positioning (ADR-0003)

**Why not chosen:** Operational and latency tradeoffs are not preferred for this project's positioning.

### D. Single-shot rewrite to streaming-only architecture

**Pros:**
- clean end-state design

**Cons:**
- high migration risk
- no safe fallback during transition

**Why not chosen:** Too much irreversible change at once; dual-engine rollout is safer.

## Implementation Notes

### Proposed Configuration Surface (initial)

The exact naming can be refined in implementation review, but behavior should include:

1. streaming enable/disable switch (default off)
2. per-request streaming memory budget
3. stream failure policy with pre-commit/post-commit semantics
4. scope controls (location-level gradual rollout)

### Required Observability

Add mode-specific metrics/logs:

- streaming requests total
- streaming fallback count by reason
- pre-commit fail-open count
- post-commit fail-closed count
- streaming peak-memory estimate histogram
- streaming conversion latency and first-byte latency

No silent fallback or silent truncation is acceptable.

### Correctness and Testing Gates

1. Differential tests: streaming output vs full-buffer output on corpus and fuzzed HTML.
2. Chunk-boundary fuzzing: random split points must not change semantic output.
3. Stress tests: large documents with mixed nesting, tables, code blocks, malformed tags.
4. Failure-path tests: decompressor failure, budget overflow, parser invalid state, downstream backpressure.

### Phased Delivery

1. **Phase 1 (plumbing):** true chunk feed path + observability, still conservative fallback.
2. **Phase 2 (streaming parser/emitter):** bounded-memory converter MVP and parity harness.
3. **Phase 3 (production hardening):** broader syntax coverage, performance tuning, and rollout expansion.

## Relationship to Other ADRs

- [ADR-0001](0001-use-rust-for-conversion.md): keeps Rust as conversion core, extends implementation strategy.
- [ADR-0002](0002-full-buffering-approach.md): keeps full-buffer path as stable default while adding an opt-in streaming path.
- [ADR-0003](0003-inline-origin-near-conversion.md): preserves origin-near positioning; changes conversion mechanics, not deployment locus.

## References

- Request lifecycle: `../REQUEST_LIFECYCLE.md`
- Large response design: `../LARGE_RESPONSE_DESIGN.md`
- Performance baselines: `../../testing/PERFORMANCE_BASELINES.md`
- Current incremental implementation: `../../../components/rust-converter/src/incremental.rs`
- Current parser implementation: `../../../components/rust-converter/src/parser.rs`
- NGINX payload buffering path: `../../../components/nginx-module/src/ngx_http_markdown_payload_impl.h`
- NGINX conversion execution path: `../../../components/nginx-module/src/ngx_http_markdown_conversion_impl.h`

## Date

2026-03-23

## Authors

Project Team
