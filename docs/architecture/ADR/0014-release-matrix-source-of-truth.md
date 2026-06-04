# ADR-0014: Release Matrix Source of Truth

## Status

Proposed

## Context

The project's support information (supported platforms, NGINX versions, Rust
toolchain versions, package formats, and deployment tiers) is currently
scattered across multiple documentation files, CI workflow matrices, packaging
metadata, and release notes. This dispersion creates drift risk: a version
bump in one surface may not propagate to others, leading to inconsistent
operator guidance and CI behavior.

RFC 0008 section 4 proposes a single machine-readable release matrix as the
canonical source of truth for all support declarations.

## Decision

Establish a single machine-readable release matrix per RFC 0008 section 4:

1. The matrix is a structured data file (YAML or JSON) checked into the
   repository at a well-known path.
2. CI workflows, documentation generators, packaging scripts, and release-gate
   validators MUST consume this matrix as their authoritative source for
   platform and version support declarations.
3. Human-readable documentation MAY be generated from the matrix but MUST NOT
   be manually maintained in parallel.

The matrix covers at minimum:
- Supported NGINX version range (floor and ceiling)
- Supported Rust toolchain version (MSRV)
- Supported OS/architecture combinations
- Package format availability (DEB, RPM, tarball, Homebrew, container)
- Deployment tier classifications

## Consequences

### Positive Consequences

- Single source eliminates drift between CI, docs, and packaging metadata
- Machine-readable format enables automated validation and generation
- Version bumps propagate consistently through a single edit
- Release gates can verify that all consumers reference the current matrix

### Negative Consequences

- All consumers (CI, docs, packaging) must be updated to read from the matrix
  instead of maintaining their own version lists
- Introduces a schema dependency: matrix format changes require coordinated
  updates across consumers
- Initial migration effort to consolidate existing scattered declarations

## Alternatives Considered

- **Keep per-surface declarations**: rejected because drift between CI and docs
  is a recurring source of operator confusion and release-gate failures.
- **Documentation-only matrix (non-machine-readable)**: rejected because
  CI and packaging scripts cannot consume prose, defeating the automation
  benefit.

## References

- [RFC 0008 section 4](../RFC-0008-streaming-conversion-support-contract.md)
- [tools/release/matrix/](../../../tools/release/matrix/) (existing partial matrix)
