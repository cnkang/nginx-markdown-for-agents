# ADR-0013: Streaming Default Policy

> Historical decision for the pre-Config-V2 selector. ADR-0023 supersedes its
> active configuration recommendation in v0.9.2. The current directive is
> `markdown_streaming off|auto|force`. The former engine selector is not an
> active configuration surface.

**Status**: Accepted (implemented in 0.8.0)
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

The operator may override this default with `markdown_streaming off`
(full-buffer only) or `markdown_streaming force` (prefer streaming for eligible
responses). `markdown_streaming auto` retains the default policy.

The threshold increase from 32K (0.6.0 ADR-0007) to 1m (0.8.0 RFC 0008)
reflects the goal of reducing regression risk from the new true streaming code
path: only responses large enough to materially benefit from bounded-memory
conversion enter the streaming path targeted by the 0.8.0 release.

## Consequences

### Positive Consequences

- Large responses target bounded-memory streaming automatically
  without operator intervention
- With the default profile, small responses retain the simpler full-buffer
  path, avoiding state machine overhead for trivial conversions
- Backward-compatible at the configuration level: the directive syntax
  `markdown_streaming off|auto|force` accepts the same values as before.
  Under `markdown_streaming auto`, responses with a known `Content-Length`
  below 1m (including the former 32K-1m range) use the full-buffer path.
  Sizes at or above 1m, chunked responses, and responses without a known
  `Content-Length` remain streaming candidates
- Aligns with the 0.6.0 auto-mode precedent (ADR-0007) and extends it with
  the 0.8.0 true streaming contract
- Conservative threshold (1m) reduces risk during initial 0.8.0 development

### Negative Consequences

- Auto-mode adds a decision branch at request time, slightly increasing code
  complexity
- Operators must understand the threshold semantics to debug engine selection
  in production
- The higher threshold (1m vs 32K) means fewer responses enter the streaming
  path compared to the 0.6.0 baseline until the team tunes the threshold down in
  subsequent releases

## Alternatives Considered

- **Always-on streaming**: rejected because small responses do not benefit
  from streaming overhead and the full-buffer path is simpler and equally
  correct for them.
- **Opt-in only (off by default)**: rejected because most deployments with
  large responses would need explicit configuration, reducing out-of-the-box
  value.
- **Retain 32K threshold from 0.6.0**: rejected for 0.8.0 because a lower
  threshold increases risk during initial true streaming development. The
  higher 1m threshold targets only genuinely large responses while the
  streaming path hardens.

## References

- [RFC 0008 sections 2.1–2.2](../RFC-0008-streaming-conversion-support-contract.md)
- [ADR-0007: Streaming Engine as Default (auto mode)](0007-streaming-default.md)
- [ADR-0023: Streaming Selector Contract](0023-single-streaming-policy.md)
