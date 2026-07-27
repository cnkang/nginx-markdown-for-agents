# Security Static & Supply Chain Risk Pack

Use this pack when changes touch workflow security, shell/static scanning,
secret scanning, Rust dependency policy, SBOM generation, Trivy, or OpenSSF
Scorecard.

## Primary Surfaces

- `.github/workflows/security-static.yml`
- `.github/workflows/supply-chain.yml`
- `.github/workflows/codeql.yml`
- `.semgrep.yml`
- `.gitleaks.toml`
- `deny.toml`
- `Makefile`
- Rust `Cargo.toml` / `Cargo.lock` files
- shell scripts under `tools/`, `packaging/`, and `examples/`
- release and analysis workflows that receive repository secrets
- production NGINX examples that use credential-bearing authentication
- `.clusterfuzzlite/Dockerfile`
- `examples/docker/Dockerfile.official-nginx-source-build`
- `tools/build_release/Dockerfile.install-example`

## Required Sync Points

- CodeQL remains the primary C/C++ and Rust SAST workflow.
- Semgrep stays focused on workflow/script/release/config risks.
- actionlint, shellcheck, gitleaks, and cargo-deny are PR-blocking security
  static checks.
- Trivy, SBOM generation, and Scorecard are report-oriented visibility checks
  on PR, push, scheduled, and manual triggers until a blocking threshold is
  explicitly adopted.
- Local Trivy scans exclude Git-ignored adapter state and generated report
  directories, while clean CI checkouts still scan all repository content.
- Third-party Actions remain pinned to immutable SHAs with version comments.
- Workflow secrets remain step-scoped to their minimal consumer.
- Artifact-producing builders use reviewed manifest digests, and external
  source/tool downloads are verified before extraction or execution.
- Basic Auth production examples use TLS directly or a loopback-only backend
  behind a mandatory co-located TLS terminator.
- Generated scan outputs, SBOMs, and tool caches stay out of git.
- Runnable Dockerfiles use a non-root final user. NGINX images use port 8080
  plus writable `/tmp` PID/temp paths; fuzz images preserve writable source,
  build, and output mounts for the ClusterFuzzLite action.

## Focused Verification

```bash
make security-static
make release-supply-chain-check
make supply-chain
make harness-check
make docs-check
```

If Trivy or Syft are unavailable locally, run the feasible `security-*`
subtargets and report the missing tool exactly.
