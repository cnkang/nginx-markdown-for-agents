# Version Planning: v0.9.2 Release and v1.0 Freeze

## Purpose

This document is the active version-planning contract for the final pre-v1.0
release and the compatibility policy that follows it. Older 0.4.x through
0.6.x plans describe completed historical work. They no longer define current
compatibility or release scope.

## Current Release State

- v0.9.2 is the current released line (published 2026-09-21), building on
  the v0.9.1 baseline-consolidation and compatibility-reset release.
- Release version metadata is 0.9.2. The release shipped with the tag,
  assets, and checksums.
- The intended v1.0 contract freeze begins following the v0.9.2 release.

At the time v0.9.0 shipped, the plan intended it to be the last breaking release
before v1.0. The project deliberately extended that freeze through v0.9.1 because v1.0
had not shipped, adoption remained limited, and the final toolchain,
dependency, configuration, and ABI audit found cleanup worth completing before
the long-lived compatibility contract begins.

## v0.9.2 Release Objective

The release delivers harness consolidation, documentation corrections, and
release-gate hardening on top of the v0.9.1 baseline. It also completes the
final pre-v1 public-surface reset: the release removes retired directives and
unused profile/conflict FFI snapshots, and advances the bundled internal ABI to
version 3. These are intentional pre-v1 compatibility changes, not a claim of
ABI or configuration stability across 0.9.1 and 0.9.2.

### Scope

- OTel ADR-0006 factual correction (the historical proposal selected OTLP
  HTTP/JSON, not protobuf). OTel tracing is not built into the 0.9.2 product.
  No request-pool or worker-owned exporter state is part of the release.
- 0.9.2 removes dynconf: configuration is static, and operators
  restore the backed-up, versioned configuration tree atomically
  (not a single static configuration file).
- Release-gates-check-092 target with public-surface drift, version
  consistency, and reason-code registry completeness gates.
- Retired generic 0.5.0 and 0.9.0/0.9.1 release-chain validators. Focused
  0.7.0/0.8.0 checks remain only as compatibility regressions.
- VERSION_PLANNING / PROJECT_STATUS 0.9.2 sections.
- README consistency verification (English ↔ Chinese).
- One shared lowercase reason-code registry serves Rust, C, logs, metrics, and
  diagnostics. The project removed the former C-only uppercase mirror.

### Release Evidence

The exact branch head passed the release-gates-check-092 evidence chain,
and the team reviewed the release artifacts before publication. v0.9.2 is a
published stable release. Passing local gates alone does not declare a
published stable release.

## Historical v0.9.1 baseline

The [0.9.1 release notes](../releases/0.9.1-release-notes.md), the
[migration guide](../guides/MIGRATION-0.9.1.md), and the changelog document
the completed baseline and compatibility reset. These are historical evidence
for the current plan, not an active release objective.

## v1.0 Contract Freeze

With v0.9.2 published, v1.0 preparation is a stabilization phase rather
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

### Required v1.0 Work: Cross-Release Compatibility Gate

The current public-surface detector proves that the implementation and its
inventory agree with each other.  It cannot prove that the contract a previous
stable release published is still satisfied, because one change can edit both
the source and its inventory and leave them looking consistent while a contract
breaks.

Before the v1.0 line is frozen, a machine-readable baseline comparison must
exist that covers at least:

- directives,
- metrics,
- reason-code discriminants,
- diagnostics compatibility rules, and
- the FFI ABI and versioning policy.

The comparison needs a frozen previous-release baseline, a reproducible way to
produce it, and per-surface compatibility rules that distinguish an additive
extension from a break.  Negative fixtures must show that editing source and the
new inventory together cannot hide a breaking change.  The gate belongs behind a
generic target, not one named after a specification number.

This is deliberately not part of v0.9.2: the release keeps the detector and its
current-release shape constants as they are, and a compatibility gate is a v1.0
deliverable rather than a late pre-release design change.

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
| 0.9.2 | 2026-09-21 | Hermes | Language review: confirmed passive-voice and semicolon findings rewritten in the active voice |
| 0.9.2 | 2026-09-21 | Hermes | Release date corrected to the actual publication day |
| 0.9.2 | 2026-09-19 | Kang | Release finalization: current release state and release evidence updated for the published v0.9.2 |
| 0.9.2 | 2026-09-15 | Kang | Recorded the cross-release compatibility gate as a required v1.0 deliverable, explicitly deferred from v0.9.2 |
| 0.9.2 | 2026-07-30 | Kang | Added v0.9.2 release objective section (harness consolidation, documentation corrections, release-gate hardening) |
| 0.9.1 | 2026-07-14 | Codex | Replaced obsolete 0.4-to-0.6 planning with the final pre-v1.0 baseline, freeze, and post-v1.0 compatibility contract |
