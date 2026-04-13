# 0.5.0 Risk Register Template

## Overview

Each sub-spec uses this template to maintain a risk register. Risk registers are reviewed during the Go/No-Go review.

## Template

```markdown
## Risk Register

| # | Risk Description | Likelihood | Impact | Mitigation Strategy |
|---|-----------------|------------|--------|---------------------|
| R1 | [Description] | Low/Medium/High | Low/Medium/High | [Actionable mitigation strategy within 0.5.0 timeline] |
```

## Required Fields

1. **Risk Identifier** (#): Unique identifier (R1, R2, etc.)
2. **Risk Description**: Specific description of the risk
3. **Likelihood**: Low, Medium, or High
4. **Impact**: Low, Medium, or High
5. **Mitigation Strategy**: Actionable strategy within the 0.5.0 timeline

## High-Severity Risk Requirements

Risks with High likelihood or High impact must have explicit, actionable mitigation strategies. Mitigation strategies must be:

- Actionable within the 0.5.0 timeline
- Specific and verifiable
- Not dependent on uncontrollable external factors

## Streaming Architecture Transition Risk Examples

The following risk categories must be explicitly registered:

| # | Risk Description | Likelihood | Impact | Mitigation Strategy |
|---|-----------------|------------|--------|---------------------|
| R1 | Streaming/full-buffer semantic divergence: streaming path output differs semantically from full-buffer path | Medium | High | Diff test coverage over test corpus; divergence threshold defined in Evidence Pack |
| R2 | Bounded-memory constraint violation: streaming path memory consumption grows linearly with document size | Medium | High | Bounded-memory benchmark testing; memory budget hard limit enforced in Rust engine |
| R3 | Post-commit failure handling: streaming path encounters error after partial output sent | Medium | High | Fail-closed semantics defined; error reason code recorded; operator can observe via metrics |
| R4 | Chunk-boundary edge cases: HTML tags split across chunks cause parsing errors | Low | High | Chunk-boundary fuzzing tests; random split points do not change semantic output |

## Usage

1. Each sub-spec initializes its risk register during the design phase
2. New risks discovered during implementation must be added promptly
3. Risk registers are reviewed during the Go/No-Go review
4. Streaming architecture transition-specific risks must be explicitly registered
