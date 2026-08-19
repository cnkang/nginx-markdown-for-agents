## Description

This pull request completes the 0.9.2 pre-freeze remediation for the reviewed implementation. It fixes the identified runtime, streaming, security, diagnostics, release-gate, documentation, test, and harness issues while keeping the public compatibility contract explicit.

**This is a BREAKING release**: 0.9.2 is the final pre-v1 compatibility reset.

The remediation head SHA is **de6cec384fc5434cf295756b5eea077f9848e2cc** on `dev/wip-0.9.2`.

## Related Issues

- Review remediation scope: findings and recorded design decisions across review rounds.
- No separate GitHub issue is linked.

## Type of Change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [x] **Breaking change** (fix or feature that would cause existing functionality to change)
- [x] Documentation update
- [x] Refactoring (no functional changes)
- [x] Test update
- [x] CI/Build change

## Breaking Change Summary

- Configuration surface contracted from **63 directives to 25**: 19 reject-only migration stubs and 14 active directives removed, resource-limit directives consolidated under `markdown_limits`, `markdown_stream_threshold` removed.
- dynconf format migrated to JSON v1 with request-consistent snapshot semantics.
- Diagnostics schema migrated to v2 (reason-code registry, error_origin taxonomy).
- OTel subsystem removed (use NGINX native OTel); per-path metrics and profiles removed.
- FFI contract frozen: ABI v2 with 4-tuple handshake (version + header hash + symbol-set hash + layout fingerprint).
- Decompression: HTTP `deflate` defaults to RFC 1950 zlib-wrapped, with legacy raw RFC 1951 compatibility fallback (full-buffer paths retry as raw after a zero-output format error; streaming detects once and fails closed on misclassification).
- Trusted-proxy multi-hop forwarding: malformed `Forwarded` fails closed, never falling back to `X-Forwarded-*`.

Migration guidance: `docs/guides/MIGRATION-0.9.2.md`, `docs/guides/UPGRADE-TO-0.9.2.md`, `docs/guides/ROLLBACK-0.9.2.md`, `docs/COMPATIBILITY.md`.

## Changes Made (recent remediation round)

- **Security**: a code span inside a link label can no longer close the label early and inject a destination; label-structure characters in code-span content are escaped with adversarial regression tests.
- **Streaming**: header-chain NGX_AGAIN treated as headers-accepted across every fail-open call site (buffered append-failure, pass-through, streaming fail-open entry) so responses are never truncated under backpressure; finalize output defers behind a header retry instead of sending body before headers; fail-closed when the header block was already mutated; Brotli full-buffer rejects trailing compressed bytes like the streaming path.
- **Dynconf**: Rule 46 documents that explicit static settings propagate their block mask to child levels; diagnostics `off` unregisters only the module's own content handler.
- **Release tooling**: legacy release validators are 0.9.2-aware; the matrix generator derives feature-manifest and ABI bindings, maps libc to gnu/musl target suffixes, and drops legacy keys; signing policy gains a machine-readable trust-anchor gate and a revocation field; contract-gate CI sets up the pinned Python.
- **Toolchain**: exact Rust pin raised to 1.97.1 (MSRV floor stays 1.97).
- **Harness**: multi-line function definitions recognized by the ifdef guard detector; metrics fetch and parse failures propagate instead of degrading to zero.
- **Docs**: profile replacement presets renamed to recommendations (not equivalents of the removed profiles); stale Accept-header rewrite references replaced; install snippets fail closed; metric migrations documented against the authoritative registry.
- Raw RFC 1951 deflate fallback formalized across decode paths (zero-output retry, heuristic sniff documented, adversarial `78 9c` fixture added).

- **Release evidence gate**: real-mode validation now enforces blocking-entry semantics (a blocking entry must pass) in addition to schema structure; property tests cover blocking/pending/skip rules.
- **Diagnostics**: the 405 response is constructed transactionally — an Allow-header allocation failure yields 500 instead of an incomplete 405, with fault-injection coverage.
- **Streaming**: finalize-pending results are released on every definitive error path; no leaked Rust-allocated buffers on post-commit failures.
- **Module build**: the nginx module `config` resolves the Rust archive by the current platform target triple (libc-aware) instead of the first glob hit — fixes wrong-arch linkage in shared/multi-arch checkouts.
- **Harness/tooling**: access-before-method ignores comments/literals; metrics conservation checks per preprocessor branch with ancestor directives; rustup resolution honors rust-toolchain.toml; release gates hardened (incremental hashing, repo-root paths, missing-prefix handling, nested feature tables, soak end-timestamp).
- **Perf evidence**: the canonical baseline flow (Rust 1.97.1 + nginx 1.30.4 + eight-scenario benchmark + probe validation) was smoke-verified in a container; a release-eligible x86_64 baseline remains to be generated in the release environment.
- **CodeRabbit**: uncommitted review pass produced 15 findings; all fixed and re-verified (docs-check 0 warnings; release-gate + harness suites green).

## Testing

- [x] Unit tests pass locally (Rust suite, NGINX C unit suite, Python toolchain suite)
- [x] Integration tests pass
- [x] New tests added for the fixed behavior (link-label injection, dynconf block-mask propagation, matrix binding, harness edge cases)
- [x] Manual testing completed (Darwin native streaming smoke; container-based eight-scenario module benchmark under Rust 1.97.1)

Additional verification:

- `make test`, `make docs-check`, `make complexity-check`, `cargo fmt --all -- --check`, `cargo clippy --all-targets --all-features -- -D warnings` all green at the gate SHA **de6cec384fc5434cf295756b5eea077f9848e2cc**.
- `make test-nginx-unit`, `make test-rust`, matrix canonical/binding validators, release-gate validators green.
- CodeRabbit review of the uncommitted remediation set: pass 1 findings fixed, pass 2 zero findings.
- **CI status**: green at the final SHA **de6cec384fc5434cf295756b5eea077f9848e2cc** — 53/54 check-runs pass; the only non-pass is `Sourcery review` (third-party tool, skipped on this repo). All workflow runs on this SHA complete successfully: CI (Rust Quality Gate, Runtime Regressions, Release Gates: 0.9.x Regression Validator, 0.9.2 Contract Gates, Docs Check, Harness Tooling Gates, Matrix Release Tests, Coverage Gate (80%), Perf Smoke/Corpus Benchmark gates, NGINX C suites, Brotli build gates, Docker build matrix, CodeQL), SonarCloud, Security Scanning (CodeQL + Rust Audit), Security Static Analysis, Real NGINX IMS Validation, Official NGINX Docker Validation, ClusterFuzzLite PR Fuzz, Homebrew Formula Gate, Doctor Smoke, macOS Smoke, Supply Chain Visibility. PR merge state: `MERGEABLE` / `CLEAN`.

## Documentation

- [x] Documentation and code comments updated

## Checklist

- [x] I have read the CONTRIBUTING document.
- [x] My code follows the code style of this project.
- [x] All new and existing tests passed.
- [x] Breaking changes are documented.

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-19 | Hermes | CI evidence refreshed at final SHA **de6cec384fc5434cf295756b5eea077f9848e2cc**: 53/54 check-runs green (Sourcery review skipped), all workflow runs success, merge state CLEAN |
| 0.9.2 | 2026-08-15 | Hermes | run15 remediation: evidence-gate blocking semantics, transactional 405, streaming pending release, platform-aware archive lookup, harness/gate hardening, docs batch at final SHA `952fdd97` |