# Harness Rules Index

Detailed error-prevention rules extracted from `AGENTS.md`. Each file groups
related rules by domain with YAML frontmatter for path-scoped loading.

## Rule → File Mapping

| Rule IDs | Domain | File | Primary Surfaces |
|----------|--------|------|-----------------|
| 1, 2, 38, 47, 51, 52, 64, 69 | Streaming & Backpressure | [streaming-backpressure.md](streaming-backpressure.md) | backpressure, fail-open, replay buffer, auth cache-control, derived-state reconciliation, NGX_AGAIN call-site audit |
| 3, 43 | Memory & Budget | [memory-budget.md](memory-budget.md) | allocations, budget enforcement, pool vs heap |
| 4, 44 | Encoding & Charset | [encoding-charset.md](encoding-charset.md) | UTF-8 chunk boundaries, gzip/deflate/Brotli streaming lifecycle |
| 5, 6, 27 | HTML Sanitizer & Output Safety | [html-sanitizer.md](html-sanitizer.md) | void elements, emitter, escaping, fence language |
| 7, 8, 8b, 8c, 23, 67 | Observability & Metrics | [observability-metrics.md](observability-metrics.md) | metrics, reason codes, config alignment, v1 terminal-outcome conservation |
| 9, 49, 63 | Docs & Tooling Drift | [docs-tooling.md](docs-tooling.md) | README, validators, metric names, THIRD-PARTY-NOTICES, non-native-reader writing style (STE-inspired) |
| 10 | Parser & Regex | [parser-regex.md](parser-regex.md) | ReDoS, deterministic parsing |
| 11, 18, 41 | Shell | [shell.md](shell.md) | portability, hygiene, POSIX ERE |
| 12, 32, 33, 68 | Security & CWE | [security-cwe.md](security-cwe.md) | path traversal, integer overflow, access-before-method ordering |
| 13, 54, 66 | CI Gating | [ci-gating.md](ci-gating.md) | workflow filters, artifact upload, Homebrew formula, release artifact path traversal, local GCC parity gate |
| 14, 16, 20, 22, 25 | Testing & Coverage | [testing-coverage.md](testing-coverage.md) | regression, dead stores, Rust infra |
| 15, 46, 53 | FFI & Cross-Language | [ffi-crosslang.md](ffi-crosslang.md) | ABI, header sync, lifecycle, panic safety, handle ownership, fat-pointer safety |
| 17 | Cognitive Complexity | [complexity.md](complexity.md) | function complexity limits |
| 19 | Python Tooling | [python-tooling.md](python-tooling.md) | binary prerequisites, harness guards |
| 21 | Warning Triage | [warnings-triage.md](warnings-triage.md) | warning classification, repro |
| 24, 42, 65 | C Safety | [c-safety.md](c-safety.md) | C99, narrowing casts, const, volatile/atomic, forward decl ordering, whole-struct initialization |
| 26 | Naming & Docs | [naming-docs.md](naming-docs.md) | names, comments, doc comments |
| 28, 29, 30, 31, 39, 40, 50 | NGINX Idioms | [nginx-idioms.md](nginx-idioms.md) | list iteration, flag clearing, NUL-term, OWS separator |
| 34, 35, 45 | Dynconf & Snapshot | [dynconf-snapshot.md](dynconf-snapshot.md) | effective_conf, reload retry, NULL-safe access |
| 36 | Harness Routing | [harness-routing.md](harness-routing.md) | routing-manifest coverage |
| 37, 60 | E2E Runner | [e2e-runner.md](e2e-runner.md) | Rust-first E2E, parity, streaming config directive consistency |
| 48 | Security Static Analysis & Supply Chain | [security-static-analysis.md](security-static-analysis.md) | actionlint, shellcheck, gitleaks, Semgrep, cargo-deny, Trivy/SBOM/Scorecard |
| 55 | Version Consistency | [version-consistency.md](version-consistency.md) | source/chart/docs version sync, Rust baseline |
| 56, 57, 58, 59 | Build Safety | [build-safety.md](build-safety.md) | orphan comment closers, #ifdef guard visibility, workflow input injection, hardcoded HTTP status |
| 61, 62 | Release Integrity | [release-integrity.md](release-integrity.md) | layered performance evidence provenance and matrix key normalization invariants |
| FUZZ-001..007 | Fuzz Infrastructure | [fuzz-infrastructure.md](fuzz-infrastructure.md) | fuzz targets, CI fuzzing, corpus management |

## Usage

Agents load `AGENTS.md` for the rule index and workflow. When working in a
specific domain, consult the corresponding file under this directory for
full rule text, historical issues, required constraints, and verification
commands.

Path-scoped rule loading: each file's YAML `paths` field specifies which
code paths trigger the rules. Agents should load rules matching the files
they are editing.
