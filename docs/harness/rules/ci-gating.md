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
    `packaging/checksums.sha256`) and reference it in download scripts.
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
- **Release/package chain invariants**:
  - Use exactly one canonical dynamic module filename across NGINX build
    outputs, nFPM config, Debian/RPM specs, load-module snippets, install
    docs, smoke tests, and install-layout gates.  If the addon output changes,
    update all of those surfaces and the validator in the same change set.
    Package-format-specific module directories must match the target
    nginx.org package `--modules-path`; for example RPM packages must not
    install under a DEB-only module directory when nginx.org RPMs load modules
    from `/usr/lib64/nginx/modules`.
  - Every NGINX source version requested by release workflows or release
    Dockerfiles must have a matching entry in `packaging/checksums.sha256`.
    Do not introduce active release paths that rely on unchecked source
    versions.
  - Artifact producer names and consumer patterns must match exactly across
    release package upload, smoke-test download, and signing workflows.
    Fail-closed signing is correct, but mismatched artifact names are still a
    release integration bug.
  - Standalone package workflows must use the same package name and install
    layout as the canonical nFPM path.  If a workflow packages a prebuilt
    `.so`, its SPEC/control metadata must not try to rebuild from missing
    source files, it must validate user-supplied package versions before using
    them in paths, package metadata, RPM macros, or artifact names, and it must
    run the install-layout gate against its output.
  - Package dependency constraints must be satisfiable by the target distro
    package manager.  If the distro package appends a release suffix or epoch,
    do not exact-match only the upstream NGINX source version; use a
    distro-resolvable package EVR or a non-exact floor plus ABI smoke coverage.
  - Container jobs that use Bash-only syntax (`[[ ... ]]`, brace expansion,
    arrays, `source`, or `set -o pipefail`) must set `defaults.run.shell:
    bash` at the job level or `shell: bash` on every affected run step.
  - Package smoke tests for architecture-specific artifacts must run on a
    matching runner architecture or an explicit emulation path.
  - Package smoke tests must select external package repositories from
    `/etc/os-release` or equivalent target-distro evidence.  Do not route
    Amazon Linux through CentOS repository paths.
  - Release package build environments must not introduce glibc requirements
    newer than any supported smoke-test/runtime distro for the same artifact
    family.  Build Linux module artifacts on the oldest supported glibc
    baseline or split artifacts by distro family.
  - Package maintainer scripts must accept the lifecycle arguments passed by
    each target package manager.  Advisory post-install scripts must recognize
    RPM numeric `%post` arguments and must not make an otherwise successful
    install fail only because an unfamiliar lifecycle argument was observed.
  - Public install docs must match the currently published package channel.
    Bare APT/YUM repository install commands are forbidden until the repository
    URL, signing key, and release workflow are real and validated.  If only
    GitHub Release DEB/RPM artifacts exist, docs must use artifact download
    plus checksum verification.
  - Helm charts with `runAsNonRoot`, dropped capabilities, and
    `readOnlyRootFilesystem` defaults must use an unprivileged listen port and
    writable runtime/temp mounts in the rendered pod spec.
  - Helm charts that support optional dynamic modules must keep default renders
    compatible with stock images, must fail clearly when module-specific
    directive families are enabled without an explicit in-image module path
    (including metrics directives), and must not create implicit `hostPath`
    mounts from module path values.  Custom volumes must be explicit opt-in
    values such as `extraVolumes` and `extraVolumeMounts`.
  - Local K8s smoke tests must use an explicit kind kube-context for every
    Helm/kubectl operation.  If a test deploys a stock NGINX image without the
    module, it must disable module-specific directives; if it reuses a
    pre-existing cluster, it must not delete that cluster during cleanup.
    Runtime assertions must count structured pod fields with one item per line,
    not by grepping collapsed one-line jsonpath output.

Verification:
- `bash tools/harness/detect_ci_supply_chain.sh`
- `grep -rn 'uses:' .github/workflows/ | grep -v '@[0-9a-f]\{40\}'` — should
  return no results (all actions pinned to SHA).
- `python3 tools/release/gates/validate_package_metadata_070.py`
- `python3 tools/release/gates/validate_k8s_manifests_070.py`
- `make release-gates-check`
