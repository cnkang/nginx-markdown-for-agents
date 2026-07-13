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
  shell, Python tooling, secret, Semgrep, and Rust dependency/license policy
  changes.
- `actionlint` is mandatory for GitHub Actions workflow edits.
- `shellcheck` is mandatory for shell script edits unless a finding is
  suppressed narrowly with a local justification.
- `gitleaks` must fail on real secrets, private keys, signing material, API
  keys, tokens, and passwords. Allowlist only test fixtures or placeholders
  with narrow path or regex scope.
- Local `gitleaks` execution must scan exactly Git-tracked worktree content so
  tracked edits are covered while ignored local adapters, caches, and build
  state cannot create findings for files absent from a clean checkout. Any
  tracked-file materialization must omit tracked paths that are absent because
  they were deleted in the worktree and preserve unusual filenames with
  NUL-safe traversal.
- Semgrep CE rules must stay high-confidence and repo-specific. Focus them on
  repository workflow, shell, Python tooling, and C module patterns. This
  includes direct CLI-derived path I/O before harness validation helpers,
  obvious subprocess misuse, unsafe libc APIs in the NGINX C module, and
  exported Rust FFI functions that still contain panic/unwrap/expect paths.
  Dockerfile rustup bootstrap paths that download and execute unverified
  installers should also be covered.
  Test-only panic injection blocks under `#[cfg(test)]` are not considered
  production findings. Avoid broad noisy packs as PR-blocking checks until
  findings are triaged and documented.
- `cargo-deny` must check Rust advisories, license policy, bans, and sources for
  every checked-in Rust manifest. Do not allow GPL, AGPL, LGPL, SSPL, Commons
  Clause, or unknown licenses by default.
- `supply-chain.yml` is a report-oriented visibility workflow for Trivy
  filesystem/IaC scans, SPDX SBOM generation, and OpenSSF Scorecard on PR,
  push, scheduled, and manual triggers. Do not describe it as a hard blocking
  gate unless the project adopts explicit threshold semantics.
- Local Trivy filesystem scans exclude Git-ignored adapters, caches, build
  output, and prior reports. At minimum `.kiro/`, `.codeartsdoer/`, and
  `build/` stay out of scope; clean CI checkouts remain fully scanned.
- Tracked runnable Dockerfiles must end with a non-root `USER`. NGINX runtime
  images must pair that user with an unprivileged listener and writable PID and
  temporary paths. ClusterFuzzLite images must keep the toolchain readable and
  the source/build/output directories writable under the actual fuzz action
  mount contract, including ownership of the fixed
  `/usr/lib/libFuzzingEngine.a` archive installed by the compile wrapper. Grant
  access to that file only, not the full system library directory. Do not
  suppress Trivy's missing-user finding when those runtime changes are
  feasible. ClusterFuzzLite workflows must pre-create the bind-mounted
  `build-out` directory as the runner user before the root container action
  initializes its workspace.
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
- `make harness-check` for the tracked Docker runtime contract
