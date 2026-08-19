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
   error, the module MAY replay the original HTML response (fail-open),
   reject the request (fail-closed), or return the configured error
   status, depending on the configured `markdown_error_policy` policy.
   Fail-open replay stays available only while every consumed upstream
   byte is still retained in the module's replay buffer; if any consumed
   input is no longer available for replay, the module MUST fail-closed
   instead of re-reading upstream data without bound.
2. **Post-commit phase**: Markdown output has been partially delivered. On
   error, the module MUST NOT attempt to replay the original HTML. The
   module terminates the response with whatever Markdown it produced.
   Any Markdown-generation failure in this phase is explicitly an aborted
   or incomplete response — never normal completion. It logs the error
   with appropriate reason codes.

The commit boundary is the point at which the first Markdown output buffer is
sent to the next body filter in the NGINX chain.

### Status policy (pre-commit)

When `markdown_error_policy` is set to `status`, a pre-commit conversion
failure discards the original HTML and returns the configured error status
(`429` for resource-limit/overload failures or `503` for system failures,
per `markdown_error_policy error_status`). The module emits the
corresponding reason code and metrics exactly as on the fail-closed path:
`failed_closed` is logged with the error-category reason, and the failure
counters (`conversions_failed`, `failures_*`) are incremented. The original
upstream response is never delivered and no replay is attempted.

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
