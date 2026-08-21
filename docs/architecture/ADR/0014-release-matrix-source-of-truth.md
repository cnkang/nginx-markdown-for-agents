# ADR-0014: Release Matrix Source of Truth

## Status

Accepted (implemented in 0.8.0)

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

1. The canonical policy matrix is a JSON file at
   `tools/release-matrix.json`. If a `packaging/matrix.yaml` exists for
   packaging tooling, it is a packaging input only, never an authority:
   every NGINX version it lists MUST be a subset of the canonical JSON
   (validated by `python3 tools/docs/validate_packaging_matrix.py`), and
   the YAML MUST NOT add support declarations absent from the JSON.
2. CI workflows, documentation generators, packaging scripts, and policy
   validators MUST consume `tools/release-matrix.json` as the authoritative
   source for support, platform, artifact, and version declarations.
3. The ABI-bound release gate may consume the generated contract projection
   at `docs/releases/release-matrix.json`. That projection MUST be generated
   by `python3 tools/release/matrix/generate_release_contract_matrix.py --write`,
   MUST record the canonical-content digest of `tools/release-matrix.json`,
   and MUST pass the corresponding `--check` freshness gate. It is not a
   second manually maintained source of truth.
4. Human-readable documentation MAY be generated from the policy matrix, but
   neither documentation nor the release-contract projection MUST be edited
   manually in parallel.

The matrix covers at minimum:
- Target NGINX version range (floor and ceiling)
- Target Rust toolchain version (MSRV)
- Target OS/architecture combinations
- Package format coverage (DEB, RPM, tarball, Homebrew, container)
- Deployment tier classifications

## Consequences

### Positive Consequences

- Single source eliminates drift between CI, docs, and packaging metadata
- Machine-readable format enables automated validation and generation
- Version bumps propagate consistently through a single policy edit
- Release gates can verify that all consumers reference the current matrix

### Negative Consequences

- Projection consumers need a deterministic generation and freshness check
- Projection schema changes require coordinated updates to the generator and
  its release-gate validator
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
- [generate_release_contract_matrix.py](../../../tools/release/matrix/generate_release_contract_matrix.py)
