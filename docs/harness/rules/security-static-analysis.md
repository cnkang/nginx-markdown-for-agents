---
domain: security-static-analysis
rules: [48]
paths:
  - ".github/workflows/**"
  - ".semgrep.yml"
  - ".gitleaks.toml"
  - "deny.toml"
  - "Makefile"
  - "components/**/Cargo.toml"
  - "components/**/Cargo.lock"
  - "tools/**/Cargo.toml"
  - "tools/**/Cargo.lock"
  - "tools/**/*.sh"
  - "packaging/**/*.sh"
  - "examples/**/*.sh"
  - "charts/**"
---

## Security Static Analysis & Supply Chain Gates

### 48. Supplemental static security and supply-chain gates

Required:
- Keep CodeQL as the primary C/C++ and Rust SAST workflow. Do not duplicate
  CodeQL query suites in supplemental workflows unless the new rule catches a
  concrete gap that CodeQL does not cover.
- `security-static.yml` is the PR-blocking lightweight gate for workflow,
  script, secret, Semgrep, and Rust dependency/license policy changes.
- `actionlint` is mandatory for GitHub Actions workflow edits.
- `shellcheck` is mandatory for shell script edits unless a finding is
  suppressed narrowly with a local justification.
- `gitleaks` must fail on real secrets, private keys, signing material, API
  keys, tokens, and passwords. Allowlist only test fixtures or placeholders
  with narrow path or regex scope.
- Local `gitleaks` execution must scan exactly Git-tracked worktree content so
  tracked edits are covered while ignored local adapters, caches, and build
  state cannot create findings for files absent from a clean checkout. Any
  tracked-file materialization must preserve unusual filenames with NUL-safe
  traversal.
- Semgrep CE rules must stay high-confidence and repo-specific. Avoid broad
  noisy packs as PR-blocking checks until findings are triaged and documented.
- `cargo-deny` must check Rust advisories, license policy, bans, and sources for
  every checked-in Rust manifest. Do not allow GPL, AGPL, LGPL, SSPL, Commons
  Clause, or unknown licenses by default.
- `supply-chain.yml` is a report-oriented visibility workflow for Trivy
  filesystem/IaC scans, SPDX SBOM generation, and OpenSSF Scorecard on PR,
  push, scheduled, and manual triggers. Do not describe it as a hard blocking
  gate unless the project adopts explicit threshold semantics.
- Third-party actions in these workflows must be pinned to immutable commit
  SHAs with human-readable version comments.
- Generated scan output, SBOM files, tool caches, and vulnerability databases
  must not be committed.

Verification:
- `make security-static`
- `make security-actionlint`
- `make security-shellcheck`
- `make security-gitleaks`
- `make security-semgrep`
- `make security-cargo-deny`
- `make supply-chain` when Trivy and Syft are locally available
