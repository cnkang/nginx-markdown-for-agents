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
