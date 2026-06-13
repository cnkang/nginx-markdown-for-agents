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

## Required Sync Points

- CodeQL remains the primary C/C++ and Rust SAST workflow.
- Semgrep stays focused on workflow/script/release/config risks.
- actionlint, shellcheck, gitleaks, and cargo-deny are PR-blocking security
  static checks.
- Trivy, SBOM generation, and Scorecard are scheduled/manual visibility checks
  until a blocking threshold is explicitly adopted.
- Third-party Actions remain pinned to immutable SHAs with version comments.
- Generated scan outputs, SBOMs, and tool caches stay out of git.

## Focused Verification

```bash
make security-static
make supply-chain
make docs-check
```

If Trivy or Syft are unavailable locally, run the feasible `security-*`
subtargets and report the missing tool exactly.
