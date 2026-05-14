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
| `0.6.0` | Make the project production-ready | Streaming default, noise pruning default, OTel tracing, per-path metrics, OS packaging, Helm chart, unified memory budget, LLM adapters, MDX/Org flavors, dynamic config, coverage gate | Reopening settled architecture decisions without new evidence; breaking FFI ABI | Every 0.5.x config works unchanged; new installs get optimal defaults; one-command install on major platforms |

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

## 0.6.0 Production Readiness Release

> **Current status (as of 2026-05-14)**: v0.6.3 released. All P0, P1, and P2
> items from 0.6.0 are complete, dynconf hardening from 0.6.2 is complete, and
> the first Rust-first E2E migration batch is closed in 0.6.3 (five migrated
> scenarios plus harness-governance alignment). Final mainline hardening for
> repository-root path validation, release/performance tooling, and the release
> binary matrix is included before the v0.6.3 tag.

`0.6.0` delivers the **P0 production-readiness subset**: default behavior changes that make the system production-optimal out-of-the-box, with full backward compatibility and operator rollback controls. P1/P2 capabilities are tracked in the spec but not blocking for this release.

`0.6.0` should be framed as the production readiness release — the system is complete, tested, and deployable with optimal defaults and minimal operator configuration.

### Must Ship (P0)

- Streaming engine as default (`auto` mode), with `markdown_streaming_engine auto` as the new default
- Noise pruning default enabled (`markdown_prune_noise on`), with protection selectors and empty-output fallback
- `markdown_streaming_auto_threshold` for Content-Type/Content-Length-aware engine selection
- Unified memory budget (`markdown_memory_budget`) superseding dual `markdown_max_size` + `markdown_streaming_budget`
- 0.6.0 VERSION_PLANNING, release gates, and streaming-default migration guide
- ADR-0006 (OTel), ADR-0007 (Streaming Default), ADR-0008 (Noise Pruning Default)

### Should Ship (P1) — Implemented in v0.6.0

- OpenTelemetry tracing integration (self-implemented OTLP HTTP/protobuf, no third-party SDK)
- Per-path metrics with cardinality control (red-black tree in SHM, top-N path aggregation)
- Content-Type-aware automatic routing (integrated with engine selector)
- OS package manager distribution (APT for Debian/Ubuntu, YUM/DNF for RHEL/Fedora, Homebrew for macOS)
- Helm chart with Ingress annotation support and ConfigMap-driven configuration
- Coverage gate as CI merge requirement (80% aggregate, 90% critical paths)
- LLM provider adapter layer (Claude/GPT/Gemini token optimization, `markdown_llm_provider`)

### Should Ship (P2) — Implemented in v0.6.0

- MDX flavor support (`markdown_flavor mdx`)
- Org-mode flavor support (`markdown_flavor org`)
- Dynamic configuration hot-reload (`markdown_dynamic_config file|http`)

### Default Defer

- High-cardinality per-user/per-session metrics
- GraphQL/REST control plane API
- WASM plugin system for custom converters
- Multi-region config sync

### 0.5.x -> 0.6.0 Compatibility Contract

This release changes **two defaults** that affect behavior for configurations that do not explicitly set the corresponding directive:

| Directive | 0.5.x Default | 0.6.0 Default | Behavioral Impact |
|-----------|--------------|--------------|-------------------|
| `markdown_streaming_engine` | `off` | `auto` | Requests without explicit setting now auto-select engine based on Content-Length |
| `markdown_prune_noise` | (not existent / off) | `on` | Responses that previously included nav/footer/aside content will have those regions removed |

**Compatibility strategy**:

1. Configurations that explicitly set `markdown_streaming_engine off` remain full-buffer; `on` remains streaming — no change.
2. Configurations that explicitly set `markdown_prune_noise off` include full content — no change.
3. **For 0.5.x configs without explicit settings**: operators who need identical output must add `markdown_streaming_engine off` and `markdown_prune_noise off` to their configuration. The migration guide (`docs/guides/streaming-default-migration.md`) documents this rollback procedure.
4. FFI ABI additions are append-only (new fields at struct tail).
5. No Rust MSRV increase beyond current +2 minor versions.
6. Supported NGINX version range does not shrink.

### Directive Deprecation and Migration Policy

| Directive | Status in 0.6.0 | Priority vs `markdown_memory_budget` | Earliest Removal |
|-----------|-----------------|--------------------------------------|-----------------|
| `markdown_max_size` | Supported, emits deprecation warning at `info` verbosity | Explicit `markdown_max_size` > `markdown_memory_budget` > compile-time default | 0.8.0 (two release grace period) |
| `markdown_streaming_budget` | Supported, emits deprecation warning at `info` verbosity | Explicit `markdown_streaming_budget` > `markdown_memory_budget` > compile-time default | 0.8.0 (two release grace period) |
| `markdown_memory_budget` | New in 0.6.0 | — | — |

Migration example:
```nginx
# 0.5.x style (still works, emits deprecation warning)
markdown_max_size 10m;
markdown_streaming_budget 30m;

# 0.6.0 style (preferred)
markdown_memory_budget 30m;  # applies to both engines unless overridden
```

### Release Test

An operator can:

1. Install via one `apt`/`yum`/`brew` command
2. Get optimal streaming+pruning with zero configuration beyond `markdown_filter on`
3. Observe per-path metrics and OTel traces
4. Deploy via Helm on Kubernetes
5. Roll back to 0.5.x behavior with explicit `markdown_streaming_engine off` + `markdown_prune_noise off`
6. If `markdown_dynamic_config` is enabled, hot-reload configuration without restart

## Review Cadence

- Re-check this document before release freeze for `0.4.0`.
- Re-check it again when the first streaming design for `0.5.0` is written.
- Update it only when scope, architecture assumptions, or release sequencing materially change.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
| 0.6.0 | 2026-04-28 | v0.6.0-planning | Added 0.6.0 Production Readiness release scope, cut line, and compatibility contract |
| 0.6.2 | 2026-05-08 | Kang | Updated current status to v0.6.2 released |
| 0.6.3 | 2026-05-14 | Kang | Updated current status to v0.6.3 released and added Rust-first E2E migration closure, release-matrix refresh, and final hardening notes |
