# Parser Interrupt Feasibility (v0.7.0)

> **Canonical reference**: See [PARSER_BUDGET.md](./PARSER_BUDGET.md) for the
> comprehensive parser budget documentation including implementation status,
> limit interaction model, and configuration reference.

## Question

Can the HTML parser be safely interrupted mid-parse when a timeout or
memory budget is exceeded?

## Short Answer

**No.** The html5ever library (v0.38) does not support cooperative
cancellation. Neither the full-buffer `parse_document().one()` API nor the
streaming `TokenSink::process_token()` callback provides a mechanism to
signal "stop parsing."

## Analysis

### Current Architecture

The Rust conversion engine uses html5ever 0.38 in two modes:

- **Full-buffer**: `parse_document(RcDom, opts).one(input)` — single blocking
  call, no interruption possible
- **Streaming**: `Tokenizer<TokenSinkAdapter>` — `process_token()` returns
  `TokenSinkResult` which has no `Stop`/`Abort` variant

### Feasibility Assessment

| Approach | Feasible? | Trade-offs |
|----------|-----------|------------|
| Check elapsed time at DOM traversal boundaries | **Yes** (implemented) | Works post-parse; adds ~10-20 ns per checkpoint |
| Interrupt html5ever mid-parse | **No** | No API support; `.one()` is atomic |
| TokenSinkResult::Abort | **No** | Variant does not exist in html5ever |
| Set alarm/signal to interrupt | **No** | Unsafe across FFI boundary; Rust panic from signal is UB |
| Use async with timeout | Partial | Requires major refactor of FFI boundary |
| Memory budget via allocation hook | Partial | Rust global allocator hooks can track but not interrupt |
| Bound input size pre-parse | **Yes** (implemented) | Limits worst-case parse time indirectly |

### v0.7.0 Approach

Since mid-parse interruption is not feasible, v0.7.0 uses:

1. **Input size bounding** (`markdown_limits memory=<size>`): Prevents unbounded input
   from reaching the parser.
2. **Cooperative timeout** (`markdown_parse_timeout`, default 30s): Checked
   at checkpoints during DOM traversal (every 100 nodes) and at pipeline
   boundaries. The uninterruptible parse phase is bounded by input size.
3. **Memory budget** (`markdown_parser_budget`, default 64m): Enforced via
   `MemoryBudget` stage checks in the streaming path; input-size proxy in
   the full-buffer path.
4. **Implicit depth limit**: State stack budget (64 KiB) bounds nesting to
   ~1000 levels in the streaming path.

### Future Work

- Configurable `markdown_max_nodes` directive (explicit node-count limit)
- Configurable `markdown_max_depth` directive (explicit nesting limit)
- Custom allocator tracking for full-buffer memory budget
- Potential html5ever fork with abort support (if operationally justified)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.7.0 | 2026-05-17 | Kang | Initial parser interrupt feasibility analysis |
| 0.7.0 | 2026-05-17 | Kang | Expanded with html5ever API details; cross-ref PARSER_BUDGET.md |
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
