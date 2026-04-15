## Protocol and behavior invariants
Keep the dual-representation contract intact: normal clients receive origin HTML,
while explicit `Accept: text/markdown` clients receive Markdown. Flag changes
that can silently alter bot targeting, Accept rewriting, cache keying,
`Vary: Accept`, ETag varianting, or conditional request behavior.

## Streaming and fail-open safety
For NGINX body-filter paths, treat `NGX_AGAIN` as suspend-and-resume, preserve
pending chains, and keep header/body ordering idempotent. Fail-open paths must
not advance buffer positions for unconsumed data and must avoid double-header
emission.

## Memory and charset boundaries
Flag changes that weaken bounded-memory guarantees, bypass configured budgets,
or mishandle UTF-8 tails across chunk boundaries and decoder flush behavior at
EOF.

## Converter and sanitizer correctness
For Rust converter changes, preserve deterministic output, structural Markdown
correctness, sanitizer invariants (void element semantics, skip-mode name
awareness, nesting-depth safety), and explicit error classification.

## Metrics and observability integrity
Metrics payloads must not be truncated silently. Status/content headers should be
set after final body length is known. Metrics struct/schema/key changes must stay
synchronized across C code, tests, docs, snapshots, and Prometheus naming.

## Tooling portability and security
Shell and Python tooling should remain portable and safe: avoid GNU/PCRE-only
assumptions, ensure required checks fail hard, validate executable prerequisites,
and sanitize path or command inputs to prevent traversal/injection issues.

## Documentation and validation sync
When runtime behavior changes, verify docs and validators evolve together.
Documentation examples, JSON keys, and Prometheus series names should match actual
emitted behavior exactly.
