# Documentation Guide

This directory contains the primary project documentation for `nginx-markdown-for-agents`.

## Start Here

For most users and maintainers, begin with the repository overview and then choose a documentation section:

- [README.md](../README.md) - project overview and quick start
- [guides/README.md](guides/README.md) - build, installation, configuration, and operations guides
- [project/README.md](project/README.md) - status and project-level maintenance docs

Deployment-first note:

- The repository `README.md` is optimized for users who want to enable and deploy the module quickly (setup patterns, verification, common production notes).
- Use `docs/guides/` for canonical step-by-step installation, directive reference, and operations runbooks.

## Documentation Sections

### Quick Start

- [README](../README.md) - Project overview and quick start
- [FAQ](FAQ.md) - Frequently asked questions and answers

### Guides (`docs/guides/`)

User-facing and maintainer-facing operational documentation:

- build and local smoke-test workflow
- installation and NGINX integration setup
- directive configuration reference
- operational monitoring and troubleshooting guidance

Index: [guides/README.md](guides/README.md)

### Feature Documentation (`docs/features/`)

Detailed implementation and design notes for major features such as:

- security controls and sanitization
- deterministic output behavior
- timeout handling
- token estimation
- YAML front matter generation
- charset detection and entity decoding

Index: [features/README.md](features/README.md)

### Testing Documentation (`docs/testing/`)

Testing strategy, integration test notes, end-to-end validation, and performance baselines.

Index: [testing/README.md](testing/README.md)

### Archive (`docs/archive/`)

`docs/archive/` is reserved for local or historical process documentation.

Important notes:

- The archive is intentionally excluded by `.gitignore` in this repository.
- A fresh clone may not include local archive content.
- Do not rely on archived documents as the source of truth for current behavior.
- Move implementation-process notes and one-off reports here when they are no longer part of the maintained documentation set.

## Duplication and Sync Policy

Some documents are intentionally mirrored near source code for developer convenience.

- Canonical policy and duplicate mapping: [DOCUMENTATION_DUPLICATION_POLICY.md](DOCUMENTATION_DUPLICATION_POLICY.md)

## Documentation Conventions

Use these conventions when updating documentation:

1. Write in English.
2. Prefer stable facts over transient progress notes.
3. Keep operational steps executable (copy/paste friendly commands).
4. Link to the source-of-truth document instead of duplicating volatile details.
5. Archive process/history documents instead of keeping them in the repository root.

## Terminology Conventions

Use consistent terms across docs to reduce ambiguity:

- **Project**: `NGINX Markdown for Agents` (the repository/project as a whole)
- **Module**: the **NGINX Markdown filter module** (the NGINX C module component)
- **Rust converter**: the Rust HTML-to-Markdown conversion library (`components/rust-converter`)
- **Metrics endpoint**: the HTTP endpoint enabled by the `markdown_metrics` directive (not a Prometheus-native endpoint)
- **Standalone/mock tests**: `components/nginx-module/tests` unit tests that do not require a system `nginx` binary

## How to Choose the Right Document

- Build or test locally: [guides/BUILD_INSTRUCTIONS.md](guides/BUILD_INSTRUCTIONS.md)
- Install and integrate with NGINX: [guides/INSTALLATION.md](guides/INSTALLATION.md)
- Configure directives: [guides/CONFIGURATION.md](guides/CONFIGURATION.md)
- Run and operate in environments: [guides/OPERATIONS.md](guides/OPERATIONS.md)
- Review implementation/validation status: [project/PROJECT_STATUS.md](project/PROJECT_STATUS.md)
- Understand implementation details: [docs/features/](features/)
- Review test strategy and validation references: [docs/testing/](testing/)
