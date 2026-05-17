# Parser Interrupt Feasibility (v0.7.0)

## Question

Can the HTML parser be safely interrupted mid-parse when a timeout or
memory budget is exceeded?

## Analysis

### Current Architecture

The Rust conversion engine uses a single synchronous FFI call
(`markdown_convert`) that performs parsing and Markdown generation in one
pass. The HTML parser (`html5ever` / custom streaming tokenizer) is not
designed for cooperative interruption.

### Feasibility Assessment

| Approach | Feasible? | Trade-offs |
|----------|-----------|------------|
| Check elapsed time at AST node boundaries | Yes | Adds overhead per node; requires thread-local timer; works for streaming path |
| Set alarm/signal to interrupt | No | Unsafe across FFI boundary; Rust panic from signal is UB |
| Use async with timeout | Partial | Requires major refactor of FFI boundary; streaming path partially supports this |
| Memory budget via allocation hook | Partial | Rust global allocator hooks can track but not interrupt; best-effort |

### v0.7.0 Approach

For v0.7.0, the following directives are implemented:

- **`markdown_parse_timeout`** (default 30s): Sets a deadline on the
  conversion call. Enforced at the C layer by checking elapsed time
  after the FFI call returns. If the FFI call exceeds the deadline,
  the result is treated as a timeout error. True cooperative
  interruption within the Rust parser is deferred to a future version.

- **`markdown_parser_budget`** (default 64m): Sets a memory limit for
  parser allocations. Enforced by the Rust conversion engine's
  `check_timeout` / `increment_and_check` methods at checkpoint
  boundaries during streaming parsing. In full-buffer mode, enforced
  as a pre-check on input size.

### Future Work

- Cooperative checkpoint-based timeout in the streaming tokenizer
- Per-node elapsed-time checks in the streaming state machine
- Integration with Rust's `alloc` allocator API for budget tracking

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.7.0 | 2026-05-17 | Kang | Initial parser interrupt feasibility analysis |
