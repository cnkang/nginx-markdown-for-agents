# Version Planning: v0.9.4 Release and v1.0 Freeze

## Purpose

This document is the active version-planning contract for the final pre-v1.0
release and the compatibility policy that follows it. Older 0.4.x through
0.6.x plans describe completed historical work. They no longer define current
compatibility or release scope.

## Current Release State

- v0.9.4 is the current published compatibility maintenance release
  (2026-11-22). Download its assets and checksums from the v0.9.4 tag.
- v0.9.3 was the previous published release (2026-11-02). It is a
  compatibility maintenance release, so it keeps the v0.9.2 contracts.
- v0.9.2 was the previous published release (2026-10-08). It consolidates the
  v0.9.1 baseline and resets compatibility.
- v0.9.1 was the published stable release before v0.9.2 (2026-07-29).
- Repository metadata targets 0.9.4. Use the v0.9.4 tag, assets, and
  checksums for the current release.
- The intended v1.0 contract freeze begins following the v0.9.2 release.
- v0.9.3 is a controlled compatibility maintenance exception. It is not a
  second breaking release. It does not reopen the v0.9.2 contract. Its scope is
  the NGINX compatibility surface, Ubuntu LTS support, release engineering, and
  the documentation that follows from those changes.
- The v0.9.4 release continues that compatibility-only scope. The 20 active
  directives, defaults, HTTP representation, metrics, diagnostics, reason
  codes, and FFI ABI version 3 stay unchanged.

At the time v0.9.0 shipped, the plan intended it to be the last breaking release
before v1.0. The project deliberately extended that freeze through v0.9.1
because v1.0 had not shipped and adoption remained limited. The final toolchain,
dependency, configuration, and ABI audit gave another reason for the extension.
It found cleanup worth completing before the long-lived compatibility contract
begins.

## v0.9.2 Release Objective

The release delivers harness consolidation, documentation corrections, and
release-gate hardening on top of the v0.9.1 baseline. It also completes the
final pre-v1 public-surface reset. The release removes retired directives and
unused profile/conflict FFI snapshots. It advances the bundled internal ABI to
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

Publish v0.9.2 only after the merge lands, the exact branch head passes the
release-gates-check-092 evidence chain, and maintainers review the release
artifacts. Passing local gates alone does not declare
a published stable release.

## Historical v0.9.1 baseline

The [0.9.1 release notes](../releases/0.9.1-release-notes.md), the
[migration guide](../guides/MIGRATION-0.9.1.md), and the changelog document
the completed baseline and compatibility reset. These are historical evidence
for the current plan, not an active release objective.

## v1.0 Contract Freeze

After v0.9.2 publication, v1.0 preparation becomes a stabilization phase
rather than another baseline reset.

### Freeze Rules

- Existing supported directives keep their meaning, defaults, inheritance,
  and failure behavior.
- Existing structures and exported functions at the bundled internal Rust/C
  boundary retain their documented layout and ownership rules. This is an
  internal bundled boundary, not a public external ABI. Additions must stay
  append-only or take a versioned form within that contract.
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
inventory agree with each other. It cannot prove that the project still
satisfies the contract a previous stable release published. One change can edit
both the source and its inventory. They can still look consistent while a
contract breaks.

The repository now carries that gate. `make compatibility-contract-check`
compares a candidate against frozen manifests of the published release tags.
The manifests live in `tools/compatibility/contracts/`, and the extractor that
produces them lives in `tools/compatibility/`. The gate covers:

- directives,
- metrics, both the declared registry and the emitted wire set,
- reason-code discriminants and their stable metadata,
- diagnostics compatibility rules,
- the behavioral HTTP contract, bound to the tests that execute it,
- the package identity, install layout, and support tiers, and
- the FFI ABI and versioning policy.

Three properties make the comparison trustworthy.

- A manifest records the tag, the tag object, the commit, and a digest per
  surface. The gate re-extracts each anchor from its tag, so a manifest cannot
  drift from the release it describes.
- A missing tag, a missing manifest, a digest mismatch, or an unparseable
  source fails closed. None of those conditions can produce a pass.
- The negative fixtures under `tools/compatibility/tests/` edit one contract
  input at a time in an isolated worktree. One fixture edits the
  implementation and the current inventory together and still fails the gate.
  The earlier detector cannot catch that case.

Additive extensions stay permitted under a per-surface policy. A breaking
change needs an explicit maintainer decision. The approval file that records it
can waive a review finding but never a breaking finding.

The historical audit of v0.9.2 against v0.9.4 found the five frozen contract
surfaces identical. It also found a documented reduction of the support matrix
and one non-behavioral test edit. `INDEX.json` records those deltas with their
rationale, so the audit reports what changed instead of claiming a zero diff.

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
| 0.9.4 | 2026-11-22 | Codex | Recorded the published compatibility release and updated current release guidance |
| 0.9.3 | 2026-11-02 | Hermes | Wrote the published release state and set the release date |
| 0.9.3 | 2026-10-31 | Codex | Clarified asset availability before and after publication |
| 0.9.2 | 2026-10-08 | Hermes | Unified the release date to 2026-10-08 and updated the current release state |
| 0.9.2 | 2026-10-03 | Codex | Align documentation with current implementation and release publication state |
| 0.9.2 | 2026-10-02 | Hermes | Release finalization: the release date corrected to the finalization day; the tag had never been pushed, so the date was still that of an abandoned first attempt |
| 0.9.2 | 2026-09-30 | Kang | Release finalization: current release state and evidence wording flipped to the published v0.9.2 release |
| 0.9.2 | 2026-09-23 | Hermes | Updated status: publication pending; no release date is set |
| 0.9.2 | 2026-09-21 | Hermes | Language review: confirmed passive-voice and semicolon findings rewritten in the active voice |
| 0.9.2 | 2026-09-19 | Kang | Release-state wording and release evidence prepared for the v0.9.2 candidate |
| 0.9.2 | 2026-09-15 | Kang | Recorded the cross-release compatibility gate as a required v1.0 deliverable, explicitly deferred from v0.9.2 |
| 0.9.2 | 2026-07-30 | Kang | Added v0.9.2 release objective section (harness consolidation, documentation corrections, release-gate hardening) |
| 0.9.1 | 2026-07-14 | Codex | Replaced obsolete 0.4-to-0.6 planning with the final pre-v1.0 baseline, freeze, and post-v1.0 compatibility contract |
