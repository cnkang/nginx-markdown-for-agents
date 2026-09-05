# Version Planning: v0.9.2 Development and v1.0 Freeze

## Purpose

This document is the active version-planning contract for the final pre-v1.0
release and the compatibility policy that follows it. Older 0.4.x through
0.6.x plans describe completed historical work, they no longer define current
compatibility or release scope.

## Current Release State

- v0.9.2 is the current development release line, building on the v0.9.1
  baseline-consolidation and compatibility-reset release.
- Development version metadata is already 0.9.2, release publication, tag,
  assets, and checksums remain pending release-gate evidence.
- The intended v1.0 contract freeze begins following the v0.9.2 release.

At the time v0.9.0 shipped, the plan intended it to be the last breaking release
before v1.0. That freeze was deliberately extended through v0.9.1 because v1.0
had not shipped, adoption remained limited, and the final toolchain,
dependency, configuration, and ABI audit found cleanup worth completing before
the long-lived compatibility contract begins.

## v0.9.2 Release Objective

The release delivers harness consolidation, documentation corrections, and
release-gate hardening on top of the v0.9.1 baseline. It also completes the
final pre-v1 public-surface reset: the release removes retired directives and
unused profile/conflict FFI snapshots, and advances the bundled internal ABI to
version 2. These are intentional pre-v1 compatibility changes, not a claim of
ABI or configuration stability across 0.9.1 and 0.9.2.

### Scope

- OTel ADR-0006 factual correction (the historical proposal selected OTLP
  HTTP/JSON, not protobuf). OTel tracing is not built into the 0.9.2 product.
  No request-pool or worker-owned exporter state is part of the release.
- Dynconf diagnostics remains read-only, operators restore a previous valid
  file atomically and rely on LKG protection for invalid reloads.
- Release-gates-check-092 target with public-surface drift, version
  consistency, and reason-code registry completeness gates.
- Retired generic 0.5.0 and 0.9.0/0.9.1 release-chain validators. Focused
  0.7.0/0.8.0 checks remain only as compatibility regressions.
- VERSION_PLANNING / PROJECT_STATUS 0.9.2 sections.
- README consistency verification (English ↔ Chinese).
- One shared lowercase reason-code registry serves Rust, C, logs, metrics, and
  diagnostics. The project removed the former C-only uppercase mirror.

### Release Evidence

v0.9.2 remains a development candidate until the exact branch head passes
the release-gates-check-092 evidence chain and the release artifacts get
reviewed. Passing local gates alone does not declare a published stable
release.

## Historical v0.9.1 baseline

The [0.9.1 release notes](../releases/0.9.1-release-notes.md), the
[migration guide](../guides/MIGRATION-0.9.0.md), and the changelog document
the completed baseline and compatibility reset. These are historical evidence
for the current plan, not an active release objective.

## v1.0 Contract Freeze

After the project publishes v0.9.2, v1.0 preparation is a stabilization phase rather
than another baseline reset.

### Freeze Rules

- Existing supported directives keep their meaning, defaults, inheritance,
  and failure behavior.
- Existing structures and exported functions at the bundled internal Rust/C
  boundary retain their documented layout and ownership rules. This is an
  internal bundled boundary, not a public external ABI. Additions must be
  append-only or versioned within that contract.
- Diagnostics, metrics, and reason labels follow their declared stability
  level, stable names are not repurposed.
- Supported NGINX/OS/libc/architecture targets do not shrink silently.
- Toolchain or dependency changes must preserve the published compatibility
  floor unless a later release explicitly announces and documents a change.
- Security and correctness fixes take priority over strict behavioral
  compatibility when no safe compatible fix exists. The change must state the impact
  plainly.

### Allowed v1.0 Preparation Work

- correctness, security, and backpressure hardening,
- test, coverage, release-evidence, and diagnostics improvements,
- documentation and migration clarity,
- performance improvements that preserve observable output and contracts, and
- additive capabilities that do not weaken existing guarantees.

New experimental surfaces must be clearly labeled and must not appear
as part of the frozen stable contract.

## Post-v1.0 Compatibility Policy

The project follows semantic versioning for public runtime behavior.

- Patch releases contain compatible bug, security, documentation, packaging,
  and output-preserving performance fixes.
- Minor releases may add opt-in directives, optional schema fields, new
  metrics, and new FFI entry points without changing existing behavior.
- Breaking directive changes, incompatible ABI/layout changes, removal of
  stable schema fields, or changes to established defaults require a major
  release.
- Deprecated surfaces need a documented replacement and migration period
  before removal unless retaining them would create an immediate security or
  correctness hazard.

Rust compiler changes after v1.0 are deliberate compatibility decisions, not a
floating “current plus N releases” rule. The repository toolchain, manifest
MSRV, release workflows, source-build packaging, and active docs must advance
together and pass the version-consistency gate.

## Historical Planning Note

The 0.4.x through 0.6.x planning sequence delivered the adoption, streaming,
production-readiness, packaging, observability, and dynamic-configuration
foundations that led to the 0.9.x line. Those plans remain visible through the
changelog, release notes, ADRs, and migration guides. They are historical
evidence, not active compatibility rules.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-07-30 | Kang | Added v0.9.2 release objective section (harness consolidation, documentation corrections, release-gate hardening) |
| 0.9.1 | 2026-07-14 | Codex | Replaced obsolete 0.4-to-0.6 planning with the final pre-v1.0 baseline, freeze, and post-v1.0 compatibility contract |
