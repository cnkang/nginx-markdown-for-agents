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
- VERSION_PLANNING / PROJECT_STATUS 0.9.2 sections.
- README consistency verification (English ↔ Chinese).
- Streaming reason code normalization documentation (UPPERCASE C-only
  codes documented as known inconsistency, migration to lowercase
  snake_case deferred to 1.x Rust enum migration).

### Release Evidence

v0.9.2 remains a development candidate until the exact branch head passes
the release-gates-check-092 evidence chain and the release artifacts get
reviewed. Passing local gates alone does not declare a published stable
release.

## v0.9.1 Release Objective

The release establishes one coherent baseline across source builds, release
automation, runtime configuration, the Rust/C boundary, packaging, examples,
and public documentation.

### Required Baseline

- Rust 1.97.0 is the exact repository and release compiler.
- Rust 1.97 is the minimum supported compiler for every first-party Rust
  package.
- Source-build packaging and current contributor/install documentation use the
  same minimum.
- Release workflows use the pinned compiler with `--locked`, the intended
  feature set, and explicit target triples.
- Prebuilt module users do not need Rust. Runtime compatibility for prebuilt
  artifacts remains governed by the published NGINX, operating-system, libc,
  architecture, and exact NGINX dynamic-module compatibility matrix.

### Compatibility Reset Scope

v0.9.1 may intentionally make source-build, configuration, internal ABI, or
dependency changes when they remove redundant contracts or materially reduce
long-term maintenance risk. Every such change must have:

1. an implementation-backed reason,
2. a direct regression or contract test,
3. an actionable migration path,
4. synchronized English and Chinese operator documentation, and
5. a clear entry in the changelog and v0.9.1 release notes.

Removed directives must fail `nginx -t` with a migration hint. They must not be
kept as silent aliases. Historical documentation may retain old names when it
is clearly marked as historical or migration guidance.

### Public Surface Review

Before the v1.0 freeze, the project must classify every provisional or
implementation-oriented public surface as stable, experimental, internal, or
removed. The review includes:

- Markdown flavors such as MDX and Org mode,
- streaming shadow and zero-copy controls,
- dynamic configuration and OpenTelemetry controls,
- implementation/backend selectors,
- diagnostics and metrics schemas,
- reason-code labels, and
- exported FFI entry points and ABI layout.

A surface is ready for the v1.0 contract only when the code implements it in the
production path, documented accurately, directly tested, deterministic across
configuration inheritance, useful to operators, and named suitably for
long-term support.

## v1.0 Contract Freeze

After the project publishes v0.9.2, v1.0 preparation is a stabilization phase rather
than another baseline reset.

### Freeze Rules

- Existing supported directives keep their meaning, defaults, inheritance,
  and failure behavior.
- Existing FFI structures and exported functions retain their documented
  layout and ownership rules. Additions must be append-only or versioned.
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

## Release Evidence

The project declared the v0.9.1 release stable after all of the following held:

1. repository harness, docs, complexity, license, static-security, and
   supply-chain checks pass,
2. Rust, C, FFI/header, fuzz-smoke, sanitizer, and native E2E tests pass,
3. coverage gates and production-example tests pass,
4. 0.9.0, 0.9.1, and aggregate release gates pass,
5. performance evidence satisfies the release policy,
6. package and platform checks match the published support matrix,
7. the exact pushed commit has green required GitHub checks, and
8. SonarCloud reports no open or confirmed issues for the pull request.

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
