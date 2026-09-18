# ADR-0013: Streaming Default Policy

> Historical decision for the pre-Config-V2 selector. ADR-0023 supersedes its
> active configuration recommendation in v0.9.2. The current directive is
> `markdown_streaming off|auto|force`. The former engine selector is not an
> active configuration surface.

**Status**: Accepted (implemented in 0.8.0)
**Date**: 2026-06-04
**Amended**: 2026-09-07 — the threshold rule now states that size alone
never selects the streaming path. The threshold only nominates streaming
candidates. `conversion_memory` plus the remaining eligibility gates still
decide the path (see the Decision section).
**Amended**: 2026-09-17 — the active 0.9.2 contract applies no size
threshold. The threshold nomination described in the previous amendment
is historical. Every response that clears the eligibility gates is a
streaming candidate, regardless of its size.
**Context**: v0.8.0 True Streaming Contract

## Context

The streaming engine can operate in several modes: always-off (full-buffer
only), always-on (streaming for all responses), or auto (select streaming or
full-buffer based on response characteristics). RFC 0008 section 2.1 defines
the core engine switch and section 2.2 defined the automatic streaming
threshold for `auto` mode in the 0.8.0 era. Under that historical rule,
responses exceeding a size threshold or using chunked transfer encoding
targeted the streaming path. Smaller responses stayed on the full-buffer
path. The active 0.9.2 contract applies no size threshold (see the
2026-09-17 Amendment above).

ADR-0007 established streaming-as-default with auto mode in 0.6.0. This ADR
extends that decision with the 0.8.0 true streaming contract semantics from
RFC 0008, ensuring that the auto-mode policy remains aligned with the formal
streaming definition and updated threshold.

## Decision

When the operator selects `auto`, the RFC 0008 sections 2.1–2.2 policy applies:

1. Under `markdown_streaming auto`, every response that clears the hard
   eligibility gates is a streaming candidate. `conversion_memory` and the
   remaining gates decide the path (RFC 0008 section 2.2). Response size is
   not part of the decision, and no size threshold is active. The 0.8.0-era
   internal threshold (nominal default: 1m) is historical and distinct from
   the former `markdown_stream_threshold` directive. That directive was
   operator-facing in 0.8.0 with a default of 1m. The 0.9.2 release removed
   it with no replacement, and the remaining tuning limits are
   `markdown_limits` keys. Under the active Config V2 surface
   `markdown_streaming off|auto|force` is the only processing-path
   selector. The `markdown_limits` keys remain active resource-tuning
   directives.
2. Chunked transfer encoding and a missing `Content-Length` do not change
   the path. Those responses face the same eligibility gates as any other
   response (RFC 0008 section 2.2).
3. A response that fails a gate is not a streaming candidate. The module
   then uses the bounded full-buffer engine, or bypasses the filter when
   the response is not eligible for conversion at all.

The 0.6.0-era implicit default was `auto`. 0.9.2 resolves an unset
`markdown_streaming` to `off` (full-buffer only). Operators opt back in
with `markdown_streaming auto` (prefer streaming when legal) or
`markdown_streaming force` (selects streaming for every eligible response,
see ADR-0023 for the exact contract term).

**`markdown_cache_validation full` interaction (0.9.2 contract, see
ADR-0023):** `markdown_cache_validation full` combined with
`markdown_streaming auto` disables streaming for that request and uses
full-buffer conversion (runtime warning, runtime reason
`streaming_block_full_cache_validation`). `markdown_streaming force`
combined with `markdown_cache_validation full` is a configuration error
rejected at `nginx -t`. Only `ims_only` and `off` cache-validation modes
are streaming-compatible.

The threshold increase from 32K (0.6.0 ADR-0007) to 1m (0.8.0 RFC 0008)
reflects the goal of reducing regression risk from the new true streaming code
path: under the 0.8.0-era threshold rule, only responses large enough to
materially benefit from bounded-memory conversion became streaming candidates
for the path targeted by the 0.8.0 release. That threshold rule is historical.
The active 0.9.2 contract applies no size threshold: `markdown_streaming auto`
makes every response that clears the remaining eligibility gates a streaming
candidate, independent of response length (see the 2026-09-17 Amendment
above).

## Consequences

### Positive Consequences

- Large responses target bounded-memory streaming automatically
  without operator intervention
- Under the 0.8.0-era threshold rule, small responses kept the simpler
  full-buffer path, avoiding state machine overhead for trivial conversions
- Only the `off|auto|force` value semantics survive under the renamed
  `markdown_streaming` directive. This is not a general
  configuration-level backward-compatibility claim. The project removed the
  `markdown_streaming_engine` directive. The parser
  rejects configurations that still reference it.
  Under the 0.8.0-era threshold rule, responses with a known `Content-Length`
  below 1m (including the former 32K-1m range) used the full-buffer path.
  That threshold rule is historical. The active 0.9.2 contract makes every
  response that clears the remaining eligibility gates a streaming
  candidate, independent of size
- Aligns with the 0.6.0 auto-mode precedent (ADR-0007) and extends it with
  the 0.8.0 true streaming contract
- The conservative 0.8.0-era threshold (1m) reduced risk during initial
  0.8.0 development

### Negative Consequences

- Auto-mode adds a decision branch at request time, slightly increasing code
  complexity
- Under the 0.8.0-era threshold rule, operators had to understand the
  threshold semantics to debug engine selection in production
- That threshold meant fewer responses entered the streaming path compared
  to the 0.6.0 baseline. The active 0.9.2 contract applies no size threshold.

## Alternatives Considered

- **Always-on streaming**: rejected because small responses do not benefit
  from streaming overhead and the full-buffer path is simpler and equally
  correct for them. That rationale is historical. The active 0.9.2 contract
  applies no size threshold.
- **Opt-in only (off by default)**: rejected because most deployments with
  large responses would need explicit configuration, reducing out-of-the-box
  value.
- **Retain 32K threshold from 0.6.0**: rejected for 0.8.0 because a lower
  threshold increased risk during initial true streaming development. The
  higher 1m threshold targeted only genuinely large responses while the
  streaming path hardened. That threshold rule is historical. The active
  0.9.2 contract applies no size threshold.

## References

- [RFC 0008 sections 2.1–2.2](../RFC-0008-streaming-conversion-support-contract.md)
- [ADR-0007: Streaming Engine as Default (auto mode)](0007-streaming-default.md)
- [ADR-0023: Streaming Selector Contract](0023-single-streaming-policy.md)
