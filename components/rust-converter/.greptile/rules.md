## Deterministic conversion contract
Preserve stable and deterministic HTML-to-Markdown behavior. Prefer explicit,
locally-reasoned edge-case handling over broad heuristics that can change output
shape unexpectedly.

## UTF-8 and charset boundary handling
Cross-chunk correctness is required: incomplete UTF-8 tails should be buffered
and prepended correctly, and decoders must be flushed at EOF so buffered bytes
are emitted or surfaced as explicit errors.

## Sanitizer and structure semantics
Keep sanitizer behavior consistent with HTML semantics, including void element
handling, dangerous-element skip-mode exit conditions, and malformed-input depth
accounting safety.

## Emitter structural correctness
Formatting markers in links, blockquote marker placement, and code-block raw
content preservation should remain structurally correct and regression-tested.

## Performance and memory discipline
The converter runs on the request path. Flag avoidable allocations, repeated
parsing, redundant copies, or state growth without explicit bounds.

## FFI/interface sync and tests
If FFI structs/options/error codes/defaults change, ensure Rust ABI, public C
headers, NGINX call sites, docs, and boundary-level tests stay synchronized.
