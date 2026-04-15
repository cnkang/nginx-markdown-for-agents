# 0.5.0 Boundary Description Template

## Overview

Each sub-spec must include at least one boundary description for any capability with a natural extension beyond 0.5.0. Boundary descriptions use factual, non-speculative language — stating what is delivered and what is deferred, without implying commitments to future versions.

## Template

```markdown
## Boundary Description: [Capability Name]

| Field | Description |
|-------|-------------|
| Capability | [Capability name] |
| 0.5.0 Scope | [What is delivered — factual, specific] |
| 0.6.x+ Scope | [What is deferred — factual, no commitments] |
| Boundary Placement Rationale | [Why the boundary is placed here] |
| Prerequisites for Deferred Work | [What must exist before deferred work can begin] |
```

## Required Fields

1. **Capability**: The capability being described
2. **0.5.0 Scope**: Specific deliverables for this version
3. **0.6.x+ Scope**: Content deferred to future versions
4. **Boundary Placement Rationale**: Explanation of why the boundary is placed at this point
5. **Prerequisites for Deferred Work**: Conditions that must exist before deferred work can begin

## Example

```markdown
## Boundary Description: ETag Support

| Field | Description |
|-------|-------------|
| Capability | ETag generation and conditional requests |
| 0.5.0 Scope | Under streaming path, BLAKE3 incremental hash is logged to logs and metrics after finalize. Full-buffer path response-header ETag behavior is unchanged. |
| 0.6.x+ Scope | Response-header ETag support under streaming path (requires trailer-based or two-pass approach). |
| Boundary Placement Rationale | Under streaming mode, headers are already sent at Commit_Boundary, so ETag cannot be included in response headers. Solving this requires HTTP trailer support or two-pass architecture, which is beyond the 0.5.0 streaming MVP scope. |
| Prerequisites for Deferred Work | Streaming engine running stably; HTTP trailer support evaluation complete. |
```

## Language Requirements

- Use factual, non-speculative language
- Do not imply commitments to future versions
- State what is delivered and what is deferred
- Avoid commitment words like "planned", "will" when describing 0.6.x+ content
- Use neutral terms like "deferred", "out of scope"
