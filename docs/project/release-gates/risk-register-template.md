# Risk Register Template

Requirements references: 11.1, 11.2, 11.3

Each 0.4.0 sub-spec maintains its own risk register using this template. Risk registers provide cross-spec visibility into threats that could affect the release timeline, quality, or scope.

## Template

Copy the table below into your sub-spec and populate it with identified risks.

| # | Risk Description | Likelihood | Impact | Mitigation |
|---|-----------------|------------|--------|------------|
| R1 | [description] | Low/Med/High | Low/Med/High | [actionable mitigation within 0.4.0 timeline] |
| R2 | [description] | Low/Med/High | Low/Med/High | [actionable mitigation within 0.4.0 timeline] |

## Rules

1. Risks with **High likelihood OR High impact** must have an explicit, actionable mitigation strategy achievable within the 0.4.0 timeline. A blank or vague mitigation field is not acceptable for these entries.
2. Risk registers are reviewed during the Go/No-Go review. Unmitigated high-severity risks may block the release decision.
3. New risks discovered during implementation must be added promptly — do not defer risk documentation to the end of the sub-spec lifecycle.

## Instructions for Sub-Spec Owners

1. Add a `## Risk Register` section to your sub-spec design or completion artifact.
2. Copy the table template above.
3. Assign each risk a sequential identifier (R1, R2, ...).
4. Write a concise, specific risk description — avoid generic statements.
5. Assess likelihood and impact independently using Low, Med, or High.
6. For any row where likelihood or impact is High, write a mitigation that names a concrete action, owner, and timeline.
7. Review and update the register as implementation progresses.
