# Architecture Decision Records (ADR)

This directory contains Architecture Decision Records (ADRs) documenting significant architectural and technical decisions made in this project.

## What is an ADR?

An Architecture Decision Record (ADR) is a document that captures an important architectural decision made along with its context and consequences.

## ADR Format

Each ADR follows this structure:

```markdown
# ADR-XXXX: Title

## Status

[Proposed | Accepted | Deprecated | Superseded by ADR-YYYY]

## Context

What is the issue that we're seeing that is motivating this decision or change?

## Decision

What is the change that we're proposing and/or doing?

## Consequences

What becomes easier or more difficult to do because of this change?

### Positive Consequences

- Benefit 1
- Benefit 2

### Negative Consequences

- Drawback 1
- Drawback 2

## Alternatives Considered

What other options were considered and why were they not chosen?

## References

- Link to related documents
- Link to discussions
- Link to code
```

## Index of ADRs

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [0001](0001-use-rust-for-conversion.md) | Use Rust for HTML-to-Markdown Conversion | Accepted | 2026-02-27 |
| [0002](0002-full-buffering-approach.md) | Full Buffering Approach for v1 | Accepted | 2026-02-27 |
| [0003](0003-inline-origin-near-conversion.md) | Inline Origin-Near Conversion | Accepted | 2026-03-18 |
| [0004](0004-streaming-bounded-memory-conversion.md) | Streaming Conversion with Bounded Memory and Controlled Fallback | Proposed | 2026-03-23 |
| [0005](0005-repo-owned-harness.md) | Repo-Owned Harness for Agent Workflow and Spec Routing | Accepted | 2026-04-13 |
| [0006](0006-otel-integration.md) | OpenTelemetry Integration Architecture | Proposed | 2026-04-28 |
| [0007](0007-streaming-default.md) | Streaming Engine as Default (auto mode) | Proposed | 2026-04-28 |
| [0008](0008-noise-pruning-default.md) | Noise Pruning Enabled by Default | Proposed | 2026-04-28 |
| [0009](0009-rust-first-e2e-test-architecture.md) | Rust-First E2E Test Architecture with Hybrid Runtime Coverage | Accepted | 2026-05-13 |
| [0010](0010-v070-rust-first-boundary-evolution.md) | v0.7.0 Rust-First Boundary Evolution | Accepted | 2026-05-17 |
| [0011](0011-true-streaming-contract.md) | True Streaming Contract | Accepted | 2026-06-04 |
| [0012](0012-fallback-state-machine.md) | Fallback State Machine | Accepted | 2026-06-04 |
| [0013](0013-streaming-default-policy.md) | Streaming Default Policy | Accepted | 2026-06-04 |
| [0014](0014-release-matrix-source-of-truth.md) | Release Matrix Source of Truth | Accepted | 2026-06-04 |

## Creating a New ADR

1. Copy the template: `cp template.md XXXX-title.md`
2. Fill in the sections
3. Submit for review
4. Update this index when accepted

## ADR Lifecycle

1. **Proposed**: Initial draft, under discussion
2. **Accepted**: Decision has been made and implemented
3. **Deprecated**: No longer recommended but still in use
4. **Superseded**: Replaced by a newer decision (reference the new ADR)

## Guidelines

- Keep ADRs concise and focused
- Document the context and reasoning, not just the decision
- Include alternatives considered
- Update status when circumstances change
- Link to related ADRs
- Use clear, technical language

## References

- [Architecture Decision Records](https://adr.github.io/)
- [Documenting Architecture Decisions](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.8.2 | 2026-06-25 | Kang | Promoted ADR-0011 through ADR-0014 from Proposed to Accepted (implemented in 0.8.0) |
| 0.7.0 | 2026-05-25 | Kang | Added ADR-0009 (Rust-first E2E), ADR-0010 (v0.7.0 Rust-first boundary evolution) |
