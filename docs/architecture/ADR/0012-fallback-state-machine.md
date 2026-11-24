# ADR-0012: Fallback State Machine

## Status

Accepted (implemented in 0.8.0)

## Context

Streaming conversion introduces two distinct failure phases. Pre-commit is
before any Markdown output goes downstream. Post-commit begins after output
starts flowing to the client. RFC 0008 section 3 defines a two-phase fallback
state machine to handle errors in each phase with different recovery semantics.

Without a formal state machine, error handling in the streaming path was
ad-hoc. This risked inconsistent behavior between pre-commit replay and
post-commit degradation. During pre-commit replay, the original HTML can
still reach the client. During post-commit degradation, some output has
already reached the client. The module cannot retract that output.

## Decision

Implement a two-phase fallback state machine per RFC 0008 section 3:

1.  **Pre-commit phase**: no Markdown output has flushed downstream. On error,
   the module MAY replay the original HTML response (fail-open), reject the
   request (fail-closed), or return the configured error status. The configured
   `markdown_error_policy` policy determines the action. Fail-open replay stays
   available only while every consumed upstream byte is still retained in the
   module's replay buffer. If any consumed input is no longer available for
   replay, the module MUST fail-closed instead of re-reading upstream data
   without bound. Precedence: the replay-buffer fail-closed rule GOVERNS. When
   replay data is unavailable before commit, the module fails closed. The
   fail-closed options are the `markdown_error_policy` directive's
   `status <code>` form (configured error status 429/503/502) or the
   `fail_closed` policy value.
   This rule applies regardless of a `pass` error policy. The module cannot
   honor a pass policy without the original bytes.
2.  **Post-commit phase**: Markdown output has been partially delivered. On
   error, the module MUST NOT attempt to replay the original HTML. The module
   terminates the response with whatever Markdown it produced. Any
   Markdown-generation failure in this phase is explicitly an aborted or
   incomplete response. Never normal completion. It logs the error with
   appropriate reason codes.

The commit boundary is the point at which the first Markdown output buffer is
sent to the next body filter in the NGINX chain.

### Status policy (pre-commit)

When `markdown_error_policy` is set to `status`, a pre-commit conversion
failure discards the original HTML and returns the configured error status.
The status is `429` for resource-limit/overload failures or `503` for system
failures, per `markdown_error_policy error_status`. The module emits the
corresponding reason code and metrics exactly as on the fail-closed path. It
logs `failed_closed` with the error-category reason and increments the
failure counters (`conversions_failed`, `failures_*`). The module never
delivers or replays the original upstream response.

## Consequences

### Positive Consequences

- Clear, deterministic error semantics for operators and downstream consumers
- Pre-commit errors can still fall back to HTML, preserving fail-open safety
- Post-commit behavior is explicit: no silent corruption or duplicate responses
- Enables structured observability (reason codes distinguish pre/post-commit
  failures)

### Negative Consequences

- Post-commit errors result in truncated Markdown output that cannot retract
- Increases state machine complexity in the streaming body filter
- Operators must understand the commit boundary to reason about failure modes

## Alternatives Considered

- **Single-phase error handling**: rejected because treating pre-commit and
  post-commit identically would either prevent HTML replay (too strict) or
  attempt impossible retraction (incorrect).
- **Full response buffering on error**: rejected because it defeats the purpose
  of streaming and reintroduces unbounded memory usage.

## References

- [RFC 0008 section 3](../RFC-0008-streaming-conversion-support-contract.md)
- [ADR-0011: True Streaming Contract](0011-true-streaming-contract.md)
