# 0.5.0 Release Gates Framework

## Overview

This document defines the release gates framework for `nginx-markdown-for-agents` 0.5.0. The release is managed as a release train containing 7 P0 sub-specs, all of which must pass gates before release.

## Current Architecture Status Statement

The current `IncrementalConverter` (`components/rust-converter/src/incremental.rs`) **is still a buffer-all-then-finalize model, not true streaming**. Specifically:

- The NGINX side fully buffers the entire response body before calling Rust FFI
- The Rust-side `IncrementalConverter`'s `feed_chunk()` only appends data to an internal `Vec<u8>` buffer
- `finalize()` passes all accumulated bytes to `html5ever` + `RcDom` to build a complete DOM tree at once; memory consumption is proportional to document size
- The 64 MiB hard limit protects the system from OOM but does not address the architectural bottleneck
- The current incremental FFI interface (`markdown_incremental_new()`, `feed()`, `finalize()`, `free()`) is API/ABI scaffolding, not a true streaming processing path

## Scope Anchor

The sole primary goal of 0.5.0 is **true streaming / bounded-memory architecture transition**. All P0 work must directly serve this mainline. 0.5.0 is not a feature expansion release, not a new output format release, not an observability platform release.

## Dual-Engine Architecture

0.5.0 adopts a dual-engine architecture (see ADR-0004):

- **Engine A (Full-Buffer Engine)**: The existing full-buffer DOM conversion path, serving as the default safe baseline; behavior unchanged in 0.5.0
- **Engine B (Streaming Engine)**: The new true streaming conversion path, **disabled by default**, enabled only through explicit configuration
- Engine selection is **request-scoped and policy-driven**; no global irreversible switch exists
- Both engines share the same security baseline (sanitization, XSS/XXE/SSRF protection)
- Backward compatibility with 0.4.0 is a mandatory requirement

## Release Train Model

The 0.5.0 release contains the following P0 sub-specs:

| # | Sub-Spec Name | Priority | Description |
|---|---------------|----------|-------------|
| 12 | overall-scope-release-gates-0-5-0 | P0 | Overall scope, gates, DoD |
| 13 | rust-streaming-engine-core | P0 | Rust bounded-memory streaming engine MVP |
| 14 | nginx-streaming-runtime-and-ffi | P0 | NGINX streaming runtime integration and FFI |
| 15 | streaming-failure-cache-semantics | P0 | Streaming pre-commit/post-commit failure semantics and caching |
| 16 | streaming-parity-diff-testing | P0 | Streaming vs full-buffer diff/fuzz/failure-path test matrix |
| 17 | streaming-rollout-observability | P0 | Streaming observability, rollout, rollback, shadow mode |
| 18 | streaming-performance-evidence-and-release | P0 | Performance evidence and release gates |

**Release train rules:**

1. All P0 sub-specs must pass DoD assessment before Go/No-Go review
2. All 7 sub-specs are P0; none can be excluded
3. Sub-specs share a single release milestone and version number (0.5.0)
4. Cross-spec conventions (naming, reason codes, test matrix) are defined here and inherited by all sub-specs
5. Go/No-Go must be based on Streaming Evidence, not merely on implementation completeness

## Release Gate Categories

Release gates are organized into five categories, each containing verifiable gate items:

| Gate Category | Verification Scope | Verification Method |
|---------------|-------------------|---------------------|
| Documentation | Sub-spec docs, operator docs, config guide, rollout cookbook, compatibility matrix | Document existence check + `make docs-check` |
| Testing | CI pass, diff tests, chunk-boundary fuzzing, failure-path tests, bounded-memory evidence | CI artifacts + test reports |
| Compatibility | Full-buffer default behavior unchanged, streaming default off, matrix verified, new directive defaults | e2e tests + compatibility review |
| Operations | Operator can enable/rollback/observe/shadow-verify streaming path | Documentation + operational verification |
| Streaming Evidence | Bounded-memory evidence, TTFB improvement, output diff, rollback verification | Evidence Pack artifacts |

### Documentation Gates

- All 7 sub-specs have requirements and design documents
- All new operator-facing surfaces are documented
- Streaming configuration guide is complete
- Rollout cookbook is complete (including streaming enable, shadow mode, gradual expansion)
- Compatibility matrix documentation is complete
- 0.5.0 non-goals are explicitly listed
- CHANGELOG.md updated with 0.5.0 entry
- `make docs-check` passes

### Testing Gates

- CI passes on Ubuntu and macOS
- CI passes on NGINX 1.24.x, 1.26.x, 1.27.x
- Streaming path vs full-buffer path differential tests pass
- Chunk-boundary fuzzing passes (random split points do not change semantic output)
- Failure-path test coverage (decompressor failure, budget overflow, parser invalid state, downstream backpressure)
- Streaming path bounded-memory evidence generated
- Evidence Pack generated and archived
- Property-based tests pass (`cargo test` with proptest)

### Compatibility Gates

- Full-buffer path default behavior unchanged from 0.4.0 — verified by e2e tests
- Streaming path disabled by default
- Each capability's classification in the compatibility matrix is verified
- New configuration directives have documented default values
- Existing `markdown_*` directive behavior unchanged

### Operations Gates

- Operator can enable streaming path via documentation
- Operator can roll back from streaming to full-buffer via documentation
- Operator can observe streaming path behavior via metrics and logs
- Operator can shadow-verify streaming output

### Streaming Evidence Gates

- Streaming path peak memory does not grow linearly with document size (bounded-memory evidence)
- Streaming path has measurable TTFB improvement for large responses
- Streaming path vs full-buffer path output diff on test corpus is within acceptable range
- Streaming path rollback has been verified in test environment

## P0 / P1 / Deferred

### P0 (must ship in 0.5.0)

- True chunk-driven streaming path
- Bounded-memory Rust streaming engine MVP
- Streaming decompression + NGINX runtime integration
- Pre-commit / post-commit failure semantics
- Differential / fuzz / failure-path test matrix
- Streaming observability / rollout / rollback / shadow mode
- Performance evidence + release gates

### P1 (may ship but must not block 0.5.0)

- Full If-None-Match Markdown variant revalidation under streaming
- Finer-grained shadow diff artifact generation
- Broader syntax coverage optimization and performance tuning
- Richer but still low-cardinality streaming metrics

### Deferred (0.6.x+)

- New output formats (JSON, text/plain, MDX)
- OpenTelemetry / tracing platform integration
- High-cardinality metrics or per-request analytics
- apt/yum/brew package distribution, Helm, Kubernetes Ingress packaging
- GUI / dashboard / control plane
- Precise tokenizer integration
- Parser ecosystem expansion unrelated to streaming
- Content-aware heuristic pruning / readability-style extraction
- Richer agent integrations / control-plane ideas

## Explicit Non-Goals

The following topics are explicitly out of scope for 0.5.0:

1. New output format negotiation: JSON, text/plain, MDX
2. OpenTelemetry / tracing platform integration
3. High-cardinality metrics or per-request analytics
4. apt/yum/brew package distribution, Helm, Kubernetes Ingress packaging
5. GUI / dashboard / control plane
6. Precise tokenizer integration
7. Parser ecosystem expansion unrelated to streaming
8. Content-aware heuristic pruning / readability-style extraction
9. Richer agent integrations / control-plane ideas

0.5.0 is not a feature expansion release, not a new output format release, not an observability platform release.
