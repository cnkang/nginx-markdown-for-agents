# ADR-0011: True Streaming Contract

## Status

Proposed

## Context

Prior to 0.8.0, the streaming engine operated with bounded-memory conversion
but did not formalize a contract guaranteeing that the module can produce
Markdown output without buffering the entire upstream response body. RFC 0008
section 1 defines "true streaming" as incremental input processing, incremental
output emission, and bounded memory usage—all satisfied simultaneously.

Establishing a formal contract enables downstream consumers (CI gates, rollout
tooling, operator guides) to verify streaming behavior against a stable,
machine-checkable specification rather than ad-hoc heuristics.

## Decision

Adopt the true streaming contract as defined in RFC 0008 section 1. The
contract requires:

1. **Incremental input processing**: the module processes response body in
   arrival order; structures spanning chunk boundaries are handled via
   maintained converter state.
2. **Incremental output emission**: Markdown output is emitted to downstream
   filters as soon as deterministic rendering is possible, without waiting for
   EOF.
3. **Bounded memory**: peak memory usage is bounded by a configurable budget
   independent of total response size.

All three conditions must hold simultaneously for a response to be classified
as "true streaming."

## Consequences

### Positive Consequences

- Provides a single, unambiguous definition of streaming behavior for the
  project
- Enables CI and release gates to verify streaming compliance
  programmatically
- Eliminates full-response buffering for large responses, reducing memory
  pressure
- Post-commit output is irreversible, aligning with HTTP streaming semantics

### Negative Consequences

- Post-commit irreversibility means conversion errors discovered late cannot
  be retracted once output has been flushed downstream
- Increases implementation complexity for the converter state machine
- Requires careful handling of HTML structures that span chunk boundaries

## Alternatives Considered

- **Keep informal streaming definition**: rejected because lack of a formal
  contract made it impossible to gate releases on streaming correctness.
- **Full-buffer only**: rejected because large responses under full-buffer
  create unbounded memory pressure and latency.

## References

- [RFC 0008 section 1](../RFC-0008-streaming-conversion-support-contract.md)
- [ADR-0004: Streaming Bounded Memory Conversion](0004-streaming-bounded-memory-conversion.md)
