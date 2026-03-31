# Boundary Description Template

Requirements references: 9.1, 9.2, 9.3

Each 0.4.0 sub-spec includes at least one boundary description for any capability that has a natural extension beyond 0.4.0. Boundary descriptions make scope decisions explicit and traceable across sub-specs.

## Template

Copy the table below into your sub-spec and populate it for each capability with a 0.4.0/0.5.x boundary.

| Field | Description |
|-------|-------------|
| Capability | [name] |
| 0.4.0 Scope | [what is delivered — factual, specific] |
| 0.5.x Scope | [what is deferred — factual, no commitments] |
| Rationale | [why the boundary is placed here] |
| Prerequisites for Deferred Work | [what must exist before the deferred work can begin] |

## Rules

1. Use factual, non-speculative language. State what IS delivered and what IS NOT delivered.
2. Do not imply commitments for future releases. The 0.5.x column describes deferred work, not planned work.
3. Every field must be filled in. A blank 0.5.x Scope or Prerequisites field defeats the purpose of the template.

## Instructions for Sub-Spec Owners

1. Identify capabilities in your sub-spec that have a natural extension beyond 0.4.0.
2. Add a `## Boundary Description: [Capability Name]` section to your sub-spec design document.
3. Copy the template table and fill in all five fields.
4. Write the 0.4.0 Scope as a specific, verifiable statement of what ships.
5. Write the 0.5.x Scope as a factual description of what is deferred — avoid words like "will", "planned", or "expected".
6. State the rationale for the boundary placement (e.g., risk, complexity, dependency, timeline).
7. List concrete prerequisites that must exist before the deferred work can begin (e.g., APIs, infrastructure, design decisions).
