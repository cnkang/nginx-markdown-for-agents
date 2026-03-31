# Version Planning Recommendations (0.4.0 -> 0.6.x)

## Purpose

This document records the recommended release framing after the 0.4.0 strategic review. It is a planning aid, not a promise of future scope. Its job is to keep 0.4.0 tightly focused, keep 0.5.0 strategically coherent, and prevent deferred ideas from turning into an unbounded backlog bucket.

## Planning Principles

1. **0.4.0 is an adoption release, not an architecture transition.**
2. **P0 must stay operational and verifiable.** If a capability does not materially improve installation, rollout safety, verification, or lightweight observability, it should not compete with 0.4.0 release work.
3. **P1 must be easy to defer.** Any optional item that creates schedule noise, operator ambiguity, or architecture coupling should move out of the release.
4. **0.5.0 should have one primary story.** The next major step should be a bounded-memory / streaming architecture transition, not a mix of unrelated ecosystem features.
5. **Future-version language must stay factual.** Specs should describe deferred work as backlog direction, not as a hidden commitment.

## Recommended Version Planning Table

| Version | Primary Goal | Must Include | Should Avoid | Release Test |
|---|---|---|---|---|
| `0.4.0` | Make the project adoptable | Packaging and first-run success path, benchmark corpus and evidence, rollout safety and rollback guidance, release gates and Go/No-Go, lightweight Prometheus-compatible module metrics | Architecture transitions, broad parser experiments, ecosystem expansion, operator-surface churn | An operator can install, verify, roll out gradually, observe, and roll back with shipped documentation and evidence |
| `0.5.0` | Make the converter scalable | Streaming / bounded-memory conversion architecture, chunk-aware FFI contract, parity and differential testing, dual-path rollout and observability | New output formats, OpenTelemetry platform work, distro packaging expansion, control-plane ideas, unrelated product-surface additions | The streaming path is production-testable, evidence-backed, and operationally comparable to the buffer-based path |
| `0.6.x+` | Make the project extensible | Ecosystem expansion, richer integrations, additional output formats, deeper observability, packaging breadth | Reopening already-settled 0.4.0 or 0.5.0 architectural decisions without new evidence | New surfaces extend a stable core rather than compensating for an unfinished one |

## 0.4.0 Recommended Cut Line

### Must Ship

- Packaging and first-run experience
- Benchmark corpus, reproducible evidence, and comparison tooling
- Rollout safety, reason codes, selective enablement, rollback guidance
- Release gates, release checklist, and Go/No-Go process

### Can Ship Only If Scope Stays Small

- Prometheus-compatible module metrics, as an additive low-cardinality exposition surface

### Default Defer

- Parser path optimization as a release claim
- Any optimization that changes Markdown output for operators by default
- Any optimization that expands the FFI, converter public API, or rollout surface

### If Parser Optimization Stays in 0.4.0

It should be limited to the safest subset only:

- Output-equivalent pruning for `script`, `style`, `noscript`
- Evidence-backed large-response allocation reduction
- Fast-path branch elimination only when it preserves the full security baseline and does not require a separate semantics path

`nav` / `footer` / `aside` pruning should be treated as experimental evidence work or moved to 0.5.x unless there is strong corpus proof and an explicit include decision.

## 0.5.0 Recommended Focus

`0.5.0` should be framed as the streaming architecture release.

### Core Track

- Streaming or bounded-memory converter path
- Chunk-driven or streaming-aware FFI contract
- Pre-commit / post-commit failure semantics for partial processing boundaries
- Differential testing and parity harness between buffer and streaming paths
- Dual-engine rollout, diagnostics, and rollback guidance

### Explicitly Not the Main 0.5.0 Story

- JSON / `text/plain` / MDX output negotiation
- OpenTelemetry tracing
- High-cardinality observability surfaces
- Package-manager breadth (`apt`, `yum`, `brew`)
- Helm / Kubernetes ingress packaging
- Richer agent or control-plane integrations

These can stay in backlog for `0.5.1`, `0.6.0`, or later depending on what the streaming transition exposes.

## 0.4.0 Spec Adjustments Required

The strategic review implies the following documentation posture:

1. Spec 5 should explicitly reinforce that 0.5.0 has a primary streaming objective and that deferred work is backlog, not a bundled commitment.
2. Spec 5 should avoid example directive names that conflict with downstream 0.4.0 specs.
3. Spec 10 should read as an optional, easy-to-defer P1 effort, not as a hidden part of the 0.4.0 core release promise.
4. Spec 10 should not imply that fast path weakens or bypasses `SecurityValidator`.
5. Spec 10 should treat `nav` / `footer` / `aside` pruning as non-default evidence work unless explicitly promoted by release decision.
6. Spec 11 should start only when all P0 artifacts exist and the P1 include/defer decision is finalized.

## Review Cadence

- Re-check this document before release freeze for `0.4.0`.
- Re-check it again when the first streaming design for `0.5.0` is written.
- Update it only when scope, architecture assumptions, or release sequencing materially change.
