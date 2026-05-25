---
domain: ci-gating
rules: [13]
paths:
  - ".github/workflows/**"
  - "Makefile"
---

## CI Gating

### 13. CI gating blind spots and supply chain integrity
Historical issues: `7bf22a0`, `090c5a5`, `034e42f`, `7018a3c`, `08f18fa`,
`c79b17c9`, `0d26e510`.

Required:
- Update workflow path filters whenever checks depend on new file paths.
- Baseline/bootstrap modes must not upload/compare artifacts incorrectly.
- Remove redundant CI steps that can desynchronize behavior or waste runtime.
- **Supply chain hardening (GitHub Actions)**:
  - All third-party GitHub Actions must be pinned to immutable commit SHA
    references, not mutable version tags (`v4`, `v1`, etc.).  Include a
    version comment for human readability:
    ```yaml
    uses: actions/checkout@a5ac7e51b41094c92402da3b24376905380afc29  # v4.1.6
    ```
  - When updating an Action version, update the SHA and the version comment
    together.  Never leave a stale version comment.
  - First-party actions (`actions/checkout`, `actions/upload-artifact`) are
    not exempt — pin them to SHA as well.
- **Supply chain hardening (binary downloads)**:
  - Downloaded binaries and source tarballs in CI workflows and Dockerfiles
    must be verified against a known-good checksum (SHA256 minimum).
  - Maintain checksums in a version-controlled file (for example
    `packaging/nginx-checksums.yaml`) and reference it in download scripts.
  - Forbid the `curl URL | sudo tar` pattern.  Use download→verify→extract
    as separate steps.
  - When a new version of an external dependency is adopted, update the
    checksum file in the same change set.
- **Validator/gate regex synchronization**:
  - When refactoring C struct layout (flat fields → nested sub-structs,
    field renames), update all validator scripts and release-gate regex
    patterns that reference the old field paths in the same change set.
  - `make release-gates-check` must catch regex/pattern drift; if it does
    not, the gate validator itself has a bug.

Verification:
- `bash tools/harness/detect_ci_supply_chain.sh`
- `grep -rn 'uses:' .github/workflows/ | grep -v '@[0-9a-f]\{40\}'` — should
  return no results (all actions pinned to SHA).
- `make release-gates-check`
