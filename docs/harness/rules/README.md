# Harness Rules Index

Detailed error-prevention rules extracted from `AGENTS.md`. Each file groups
related rules by domain with YAML frontmatter for path-scoped loading.

## Rule → File Mapping

| Rule IDs | Domain | File | Primary Surfaces |
|----------|--------|------|-----------------|
| 1, 2, 38 | Streaming & Backpressure | [streaming-backpressure.md](streaming-backpressure.md) | backpressure, fail-open, replay buffer |
| 3 | Memory & Budget | [memory-budget.md](memory-budget.md) | allocations, budget enforcement |
| 4 | Encoding & Charset | [encoding-charset.md](encoding-charset.md) | UTF-8 chunk boundaries |
| 5, 6, 27 | HTML Sanitizer & Output Safety | [html-sanitizer.md](html-sanitizer.md) | void elements, emitter, escaping |
| 7, 8, 8b, 8c, 23 | Observability & Metrics | [observability-metrics.md](observability-metrics.md) | metrics, reason codes, config alignment |
| 9 | Docs & Tooling Drift | [docs-tooling.md](docs-tooling.md) | README, validators, metric names |
| 10 | Parser & Regex | [parser-regex.md](parser-regex.md) | ReDoS, deterministic parsing |
| 11, 18 | Shell | [shell.md](shell.md) | portability, hygiene |
| 12, 32, 33 | Security & CWE | [security-cwe.md](security-cwe.md) | path traversal, integer overflow |
| 13 | CI Gating | [ci-gating.md](ci-gating.md) | workflow filters, artifact upload |
| 14, 16, 20, 22, 25 | Testing & Coverage | [testing-coverage.md](testing-coverage.md) | regression, dead stores, Rust infra |
| 15 | FFI & Cross-Language | [ffi-crosslang.md](ffi-crosslang.md) | ABI, header sync, lifecycle |
| 17 | Cognitive Complexity | [complexity.md](complexity.md) | function complexity limits |
| 19 | Python Tooling | [python-tooling.md](python-tooling.md) | binary prerequisites, harness guards |
| 21 | Warning Triage | [warnings-triage.md](warnings-triage.md) | warning classification, repro |
| 24 | C Safety | [c-safety.md](c-safety.md) | C99, narrowing casts, const |
| 26 | Naming & Docs | [naming-docs.md](naming-docs.md) | names, comments, doc comments |
| 28, 29, 30, 31 | NGINX Idioms | [nginx-idioms.md](nginx-idioms.md) | list iteration, flag clearing, NUL-term |
| 34, 35 | Dynconf & Snapshot | [dynconf-snapshot.md](dynconf-snapshot.md) | effective_conf, reload retry |
| 36 | Harness Routing | [harness-routing.md](harness-routing.md) | routing-manifest coverage |
| 37 | E2E Runner | [e2e-runner.md](e2e-runner.md) | Rust-first E2E, parity |

## Usage

Agents load `AGENTS.md` for the rule index and workflow. When working in a
specific domain, consult the corresponding file under this directory for
full rule text, historical issues, required constraints, and verification
commands.

Path-scoped rule loading: each file's YAML `paths` field specifies which
code paths trigger the rules. Agents should load rules matching the files
they are editing.
