# ADR-0013: Streaming Default Policy

**Status**: Proposed
**Date**: 2026-06-04
**Context**: v0.8.0 True Streaming Contract

## Context

The streaming engine can operate in several modes: always-off (full-buffer
only), always-on (streaming for all responses), or auto (select streaming or
full-buffer based on response characteristics). RFC 0008 section 2.1 defines
the core engine switch and section 2.2 defines the automatic streaming
threshold for `auto` mode, where responses exceeding a size threshold or using
chunked transfer encoding target the streaming path, while smaller responses
remain on the full-buffer path.

ADR-0007 established streaming-as-default with auto mode in 0.6.0. This ADR
extends that decision with the 0.8.0 true streaming contract semantics from
RFC 0008, ensuring that the auto-mode policy remains aligned with the formal
streaming definition and updated threshold.

## Decision

Default to `auto` mode per RFC 0008 section 2.1:

1. Responses with `Content-Length` >= `markdown_stream_threshold`
   (target default: 1m) use the true streaming path.
2. Responses with chunked transfer encoding (no `Content-Length`) or absent
   `Content-Length` become streaming candidates (subject to additional
   eligibility checks per RFC 0008 section 2.2).
3. All other responses use the full-buffer path.

The operator MAY override this default with explicit `markdown_streaming_engine
off` (full-buffer only) or `markdown_streaming_engine on` (streaming for all
responses).

The threshold increase from 32K (0.6.0 ADR-0007) to 1m (0.8.0 RFC 0008)
reflects the goal of reducing regression risk from the new true streaming code
path: only responses large enough to materially benefit from bounded-memory
conversion are targeted for the streaming path in 0.8.0.

## Consequences

### Positive Consequences

- Large responses are targeted for bounded-memory streaming automatically
  without operator intervention
- Small responses retain the simpler full-buffer path, avoiding state machine
  overhead for trivial conversions
- Backward-compatible: operators who set explicit engine modes are unaffected
- Aligns with the 0.6.0 auto-mode precedent (ADR-0007) and extends it with
  the 0.8.0 true streaming contract
- Conservative threshold (1m) reduces risk during initial 0.8.0 development

### Negative Consequences

- Auto-mode adds a decision branch at request time, slightly increasing code
  complexity
- Operators must understand the threshold semantics to debug engine selection
  in production
- The higher threshold (1m vs 32K) means fewer responses enter the streaming
  path compared to the 0.6.0 baseline until the threshold is tuned down in
  subsequent releases

## Alternatives Considered

- **Always-on streaming**: rejected because small responses do not benefit
  from streaming overhead and the full-buffer path is simpler and equally
  correct for them.
- **Opt-in only (off by default)**: rejected because most deployments with
  large responses would need explicit configuration, reducing out-of-the-box
  value.
- **Retain 32K threshold from 0.6.0**: rejected for 0.8.0 because a lower
  threshold increases risk during initial true streaming development; the
  higher 1m threshold targets only genuinely large responses while the
  streaming path is hardened.

## References

- [RFC 0008 sections 2.1–2.2](../RFC-0008-streaming-conversion-support-contract.md)
- [ADR-0007: Streaming Engine as Default (auto mode)](0007-streaming-default.md)
