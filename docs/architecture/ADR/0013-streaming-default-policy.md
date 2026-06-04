# ADR-0013: Streaming Default Policy

## Status

Proposed

## Context

The streaming engine can operate in several modes: always-off (full-buffer
only), always-on (streaming for all responses), or auto (select streaming or
full-buffer based on response characteristics). RFC 0008 section 2.1 defines
the rationale for defaulting to `auto` mode, where responses exceeding a size
threshold or using chunked transfer encoding use the streaming path, while
small responses remain on the full-buffer path.

ADR-0007 established streaming-as-default with auto mode in 0.6.0. This ADR
extends that decision with the 0.8.0 true streaming contract semantics from
RFC 0008, ensuring that the auto-mode policy remains aligned with the formal
streaming definition.

## Decision

Default to `auto` mode per RFC 0008 section 2.1:

1. Responses with `Content-Length` >= `markdown_streaming_auto_threshold`
   (default: 32K) use the true streaming path.
2. Responses with chunked transfer encoding (no Content-Length) use the true
   streaming path.
3. All other responses use the full-buffer path.

The operator MAY override this default with explicit `markdown_streaming_engine
off` (full-buffer only) or `markdown_streaming_engine on` (streaming for all
responses).

## Consequences

### Positive Consequences

- Large responses benefit from bounded-memory streaming automatically without
  operator intervention
- Small responses retain the simpler full-buffer path, avoiding state machine
  overhead for trivial conversions
- Backward-compatible: operators who set explicit engine modes are unaffected
- Aligns with the 0.6.0 auto-mode precedent (ADR-0007) and strengthens it
  with the 0.8.0 true streaming contract

### Negative Consequences

- Auto-mode adds a decision branch at request time, slightly increasing code
  complexity
- Operators must understand the threshold semantics to debug engine selection
  in production

## Alternatives Considered

- **Always-on streaming**: rejected because small responses (< 32K) do not
  benefit from streaming overhead and the full-buffer path is simpler and
  equally correct for them.
- **Opt-in only (off by default)**: rejected because most deployments with
  large responses would need explicit configuration, reducing out-of-the-box
  value.

## References

- [RFC 0008 section 2.1](../RFC-0008-streaming-conversion-support-contract.md)
- [ADR-0007: Streaming Engine as Default (auto mode)](0007-streaming-default.md)
