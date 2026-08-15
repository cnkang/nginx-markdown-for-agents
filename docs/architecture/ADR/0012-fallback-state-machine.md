# ADR-0012: Fallback State Machine

## Status

Accepted (implemented in 0.8.0)

## Context

Streaming conversion introduces two distinct failure phases: pre-commit (before
any Markdown output has been sent downstream) and post-commit (after output has
begun flowing to the client). RFC 0008 section 3 defines a two-phase fallback
state machine to handle errors in each phase with different recovery semantics.

Without a formal state machine, error handling in the streaming path was
ad-hoc, risking inconsistent behavior between pre-commit replay (where the
original HTML can still reach the client) and post-commit degradation (where the
output is already partially delivered and cannot retract).

## Decision

Implement a two-phase fallback state machine per RFC 0008 section 3:

1. **Pre-commit phase**: no Markdown output has flushed downstream. On
   error, the module MAY replay the original HTML response (fail-open) or
   reject the request (fail-closed), depending on the configured
   `markdown_error_policy` policy. Fail-open replay stays available only
   while every consumed upstream byte is still retained in the module's
   replay buffer; if any consumed input is no longer available for
   replay, the module MUST fail-closed instead of re-reading upstream
   data without bound.
2. **Post-commit phase**: Markdown output has been partially delivered. On
   error, the module MUST NOT attempt to replay the original HTML. The
   module terminates the response with whatever Markdown it produced.
   It logs the error with appropriate reason codes.

The commit boundary is the point at which the first Markdown output buffer is
sent to the next body filter in the NGINX chain.

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
