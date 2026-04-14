# Documentation Guide

This directory contains the maintained documentation for `nginx-markdown-for-agents`.

If you are new to the project, start with [README.md](../README.md). It explains what the module does, why you would use it, and how to verify a first deployment quickly.

Think of this directory as the maintained map behind that landing page: guides for rollout and operations, feature notes for implementation details, and testing references for validation work.

## Choose a Starting Point

| If you want to... | Start here |
|-------------------|------------|
| Understand the project quickly | [../README.md](../README.md) |
| Install or deploy the module | [guides/README.md](guides/README.md) |
| Configure directives and behavior | [guides/CONFIGURATION.md](guides/CONFIGURATION.md) |
| Operate and troubleshoot a deployment | [guides/OPERATIONS.md](guides/OPERATIONS.md) |
| Understand the architecture and design rationale | [architecture/README.md](architecture/README.md) |
| Understand agent routing, risk packs, and harness checks | [harness/README.md](harness/README.md) |
| Maintain repo-owned harness rules and local adapter workflow | [guides/HARNESS_MAINTENANCE.md](guides/HARNESS_MAINTENANCE.md) |
| Understand implementation details | [features/README.md](features/README.md) |
| Review tests and validation references | [testing/README.md](testing/README.md) |
| Check current status and maintenance notes | [project/README.md](project/README.md) |
| Understand why the repo-owned harness was added and what it intentionally does not do | [project/HARNESS_HISTORY.md](project/HARNESS_HISTORY.md) |

## Documentation Sections

### `guides/`

Canonical user and operator documentation:

- installation and deployment
- source builds and local verification
- directive reference
- deployment examples and runbooks
- harness maintenance and contributor workflow for repo-owned agent rules

Index: [guides/README.md](guides/README.md)

### `features/`

Feature-focused technical notes for behavior that is too detailed for the top-level guides:

- decompression and charset handling
- deterministic output behavior
- security protections and sanitization
- token estimation and YAML front matter

Index: [features/README.md](features/README.md)

### `architecture/`

System structure, component boundaries, and decision rationale:

- runtime architecture overview
- configuration-to-behavior mapping
- repository structure
- harness architecture and design rationale
- ADRs for major technical choices

Index: [architecture/README.md](architecture/README.md)

### `harness/`

Repo-owned harness overlays for agent execution:

- spec resolver and conflict policy
- routing manifest and verification families
- risk-pack index and high-risk overlays

Index: [harness/README.md](harness/README.md)

### `testing/`

Test strategy and verification references:

- integration and E2E coverage
- directive validation
- decompression validation
- performance baselines

Index: [testing/README.md](testing/README.md)

### `project/`

Project-level status and maintenance-oriented references.

- current project status
- version planning
- harness rationale, boundaries, and historical context

Index: [project/README.md](project/README.md)

## Reader Paths

- Operators and deployers: start with `guides/`
- Readers evaluating the design: start with `architecture/`
- Contributors changing behavior: use `guides/` plus `features/`
- Contributors validating behavior: use `testing/`
- Maintainers checking repository posture: use `project/`

## Documentation Rules

- Prefer stable behavior and operational guidance over transient progress notes.
- Keep commands copy-paste friendly.
- Link to the canonical source instead of duplicating volatile details.
- Keep archived or historical notes out of the maintained path.

For duplication policy and mirrored-doc rules, see [DOCUMENTATION_DUPLICATION_POLICY.md](DOCUMENTATION_DUPLICATION_POLICY.md).
