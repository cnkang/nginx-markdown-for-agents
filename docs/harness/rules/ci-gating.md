---
domain: ci-gating
rules: [13, 54]
paths:
  - ".github/workflows/**"
  - "Makefile"
  - "packaging/**"
  - "examples/**/Dockerfile*"
  - "tools/build_release/**"
  - "tools/harness/detect_release_supply_chain.py"
---

## CI Gating

### 13. CI gating blind spots and supply chain integrity
Histor issues: `7bf22a0`, `090c5a5`, `034e42f`, `7018a3c`, `08f18fa`,
`c79b17c9`, `0d26e510`, `62ff8b8a`, `dc15987f`.

Required:
- Update workflow path filters whenever checks depend on new file paths.
- Baseline/bootstrap modes must not upload/compare artifacts incorrectly.
- Remove redundant CI steps that can desynchronize behavior or waste runtime.
- **Supply chain hardening (GitHub Actions)**:
  - All third-party GitHub Actions must pin to immutable commit SHA
    references, not mutable version tags (`v4`, `v1`, and so on).  Include a
    version comment for human readability:
    ```yaml
    uses: actions/checkout@a5ac7e51b41094c92402da3b24376905380afc29  # v4.1.6
    ```
  - When updating an Action version, update the SHA and the version comment
    together.  Never leave a stale version comment.
  - First-party actions (`actions/checkout`, `actions/upload-artifact`) are
    not exempt — pin them to SHA as well.
  - Workflow changes must pass `actionlint` and the focused supplemental
    static security gate in `security-static.yml`. CodeQL remains the primary
    C/C++ and Rust SAST workflow. Supplemental Semgrep rules should cover
    workflow/script/release/config risks, not duplicate CodeQL language scans.
- **Supply chain hardening (binary downloads)**:
  - CI workflows and Dockerfiles must verify downloaded binaries and source
    tarballs against a known-good checksum (SHA256 minimum). The harness must
    check that each download path contains this verification.
  - Maintain checksums in a version-controlled file (for example
    `packaging/checksums.sha256`) and reference it in download scripts.
  - Forbid the `curl URL | sudo tar` pattern.  Use download→verify→extract
    as separate steps.
  - When a new version of an external dependency arrives, update the
    checksum file in the same change set.
  - Artifact-producing builder images must use repository-reviewed
    multi-architecture manifest digests. Keep a readable tag before
    `@sha256:` where useful, but never let the tag select release builder
    bytes.
  - Documented secure source builds must accept a full reviewed commit ID,
    resolve the fetched object to a commit, compare exact equality, and only
    then execute repository build logic. A mutable ref may be a reachability
    hint, never the authorized identity.
  - **Verified toolchain installers**: When a release workflow installs the
    Rust toolchain via `rustup`, the workflow must use a verified installer
    script (for example `packaging/scripts/install-verified-rustup.sh`) that
    validates the downloaded `rustup-init` checksum before execution. Release
    workflows must invoke the script with an explicit `bash` invocation (not
    `sh`) to ensure bash-only syntax works. Release-gate validators must verify
    that the installer script exists and appears in the workflow.
- **Validator/gate regex synchronization**:
  - When refactoring C struct layout (flat fields → nested sub-structs,
    field renames), update all validator scripts and release-gate regex
    patterns that reference the old field paths in the same change set.
  - `make release-gates-check` must catch regex/pattern drift. If it does not,
    the gate validator itself has a bug.
- **Release/package chain invariants**:
  - Use exactly one canonical dynamic module filename across NGINX build
    outputs, nFPM config, Debian/RPM specs, load-module snippets, install
    docs, smoke tests, and install-layout gates.  If the addon output changes,
    update all of those surfaces and the validator in the same change set.
    Package-format-specific module directories must match the target
    nginx.org package `--modules-path`, for example RPM packages must not
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
  - Release workflows that inspect compiled binaries must explicitly install
    the package that provides the inspection tool and run a preflight check
    before using it.  For example, a workflow that calls `nm` must install
    `binutils` and fail with a clear message if `command -v nm` fails.
  - Release Rust builds must use a repository-pinned toolchain synchronized
    with `components/rust-converter/Cargo.toml` `rust-version`.  Do not leave
    release workflows on floating `stable` when the crate requires a specific
    compiler version.
  - Every workflow capable of producing release package artifacts must apply
    the same Rust release build invariants: `--locked`, intended feature set,
    explicit target triple, and the matching target output directory. If a
    workflow stays only as a compatibility path, mark it as non-canonical
    and validate that status in the release gate.
  - Standalone package workflows must use the same package name and install
    layout as the canonical nFPM path.  If a workflow packages a prebuilt
    `.so`, its SPEC/control metadata must not try to rebuild from missing
    source files, it must validate user-supplied package versions before using
    them in paths, package metadata, RPM macros, or artifact names, and it must
    run the install-layout gate against its output.
  - Package dependency constraints must be satisfiable by the target distro
    package manager.  If the distro package appends a release suffix or epoch,
    Do not exact-match only the upstream NGINX source version. Use a
    distro-resolvable package EVR or a non-exact floor plus ABI smoke coverage.
    Prebuilt dynamic-module packages must constrain the supported NGINX minor
    ABI range with both a floor and an exclusive next-minor ceiling unless a
    separate install-time ABI check is the only supported guard.  For nginx.org
    RPM packages, use an epoch-aware dependency floor that is satisfiable across
    supported nginx.org epochs, and rely on the package preinstall ABI branch
    guard plus smoke tests for the upper-bound check when epochs vary across
    minor branches.
  - Container jobs that use Bash-only syntax (`[[ ... ]]`, brace expansion,
    arrays, `source`, or `set -o pipefail`) must set `defaults.run.shell:
    bash` at the job level or `shell: bash` on every affected run step.
  - Release Dockerfiles that copy and execute repository scripts must install
    every interpreter named by those scripts' shebangs before the first
    execution, or invoke only scripts valid for the base image's guaranteed
    shell.  Minimal base images such as Alpine must not assume
    `/usr/bin/env bash` exists unless the image build installs `bash` in the
    same image stage before the script runs.
  - Package smoke tests for architecture-specific artifacts must run on a
    matching runner architecture or an explicit emulation path.
  - Container-job package smoke images must contain prerequisites needed before
    the first workflow step, including `tar` or `git` for `actions/checkout`.
    When a minimal image lacks checkout prerequisites, the release job must use
    a host-checkout plus `docker run` smoke pattern instead of using the image
    as the job container.
  - Tag release gates in GitHub Actions must run only repository-owned
    validators and artifacts available in a clean CI checkout.  Legacy or
    local-spec validators that require user-local Kiro/spec directories must not
    run in tag release CI unless the repository checks in those inputs or
    explicitly downloaded first.
  - Custom gates that reimplement platform gating semantics must match the
    platform's documented success criteria exactly.  A tag gate evaluating
    GitHub required status checks must accept every conclusion GitHub counts
    as satisfying (`success`, `skipped`, `neutral`) and keep rejecting all
    others. Required contexts may come from both the Checks API and the Commit
    Status API, so the gate must evaluate both sources before declaring a
    context missing.
  - When consuming structured API requirements, preserve every constraint
    field, not just the primary identifier.  A ruleset required check carries
    an optional `integration_id` pinning the GitHub App that must produce the
    result. If the gate drops it, the custom gate becomes more permissive than
    the ruleset it enforces.
  - The change must wire every newly added gate or validator test file into a
    blocking CI job and the workflow's change-detection path filter in the same
    changeset. A test that exists in the repository but never runs in CI is
    not a gate, the critical tag logic must not execute for the first time
    at tag creation.
  - Release evidence that aggregates measurements from multiple runs must
    validate every component's provenance environment against the declared
    comparison environment.  Top-level environment claims (platform, load
    generator, runtime version) do not imply component-level consistency. The
    release process must split a component measured in a diverging environment
    into environment-truthful evidence, or the gate must fail closed instead
    of comparing across environments.
  - When a newer release gate reuses prior-version validators, any assertion
    about the active project version, package version, or release line must
    take the version from the caller.  A prior-version validator may keep its
    standalone default, but it must not hard-fail a newer release gate solely
    because `Cargo.toml`, package metadata, or chart metadata has advanced to
    the newer release version.
  - Workflows, release gates, and documentation renderers that consume
    `tools/release-matrix.json` must use the repository's current checked-in
    schema.  If the matrix schema changes, update all active consumers in the
    same change set. A release workflow must not keep reading stale aliases
    such as `matrix`, `nginx`, `os_type`, or `support_tier: full` after the
    source of truth has moved to `entries`, `nginx_version`, `libc`, and
    `support_tier: supported`.
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
    install fail only because the script received an unfamiliar lifecycle
    argument.
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
    operators enable directive families without an explicit in-image module path
    (including metrics directives), and must not create implicit `hostPath`
    mounts from module path values.  Custom volumes must be explicit opt-in
    values such as `extraVolumes` and `extraVolumeMounts`.
  - Local K8s smoke tests must use an explicit kind kube-context for every
    Helm/kubectl operation.  If a test deploys a stock NGINX image without the
    module, it must disable module-specific directives. If it reuses a
    pre-existing cluster, it must not delete that cluster during cleanup.
    Runtime assertions must count structured pod fields with one item per line,
    not by grepping collapsed one-line jsonpath output.
  - **Homebrew formula release integrity**:
    - The formula SHA-256 checksum must hash the exact downloaded bytes served
      by the Formula's declared URL. Do not substitute a local `git archive`,
      a working-tree archive, or any other byte stream even when it resolves
      to the same commit.
    - Resolve the release tag to one commit and source the executable Formula
      program from that exact commit. Manual publication must not combine
      Formula bytes from its dispatch branch with archive identity from a
      different tag.
    - Compare the downloaded tag archive's normalized file content, executable
      bits, and symlink targets with a local `git archive` of that resolved
      commit before hashing or publishing it. A URL plus a computed checksum
      does not by itself prove which commit supplied the downloaded bytes.
    - In the Homebrew formula, the `version` stanza must appear before the
      `sha256` stanza. `brew audit --strict` requires this ordering. A formula
      with `sha256` before `version` fails the audit even if both
      values are correct.
    - The formula's NGINX dependency version must derive from package
      metadata (for example `nginx --version` output or the dependency
      formula's `version`), not hardcoded to a specific upstream version.
      Hardcoded versions drift when the NGINX formula updates.
    - The tap publish workflow must validate that the git tag exists and
      matches the expected release version before pushing the formula update
      to the tap repository.  Publishing a formula for a non-existent tag
      causes `brew install` to fail for users.
    - The formula gate and the release verify workflow must use the same
      `brew audit` standard.  If the gate uses `brew audit` (without
      `--strict`) and the release verify uses `brew audit --strict`, formula
      issues pass the gate but fail post-release verification.
    - The formula must build the module against the NGINX version installed
      by the formula's dependency, not against a separately-specified NGINX
      source tree.  Building against a different version produces a module
      binary with ABI mismatch.
    - When a source-build formula requires `cbindgen`, the formula
      must install `cbindgen` as a build dependency before the build step.
      A missing `cbindgen` causes the Rust converter to fail generating C
      headers, breaking the source build.

Verification:
- `bash tools/harness/detect_ci_supply_chain.sh`
- `make release-supply-chain-check`
- `make security-static`
- `grep -rn 'uses:' .github/workflows/ | grep -v '@[0-9a-f]\{40\}'` — should
  return no results (all actions pinned to SHA).
- `python3 tools/release/gates/validate_package_metadata.py`
- `python3 tools/release/gates/validate_k8s_manifests.py`
- `make release-gates-check`

---

### 54. Release artifact path traversal protection
Historical issues: `f303ec3f`, `ee7c9d22`.

Required:
- When validating filenames from a release manifest against an artifact directory, resolve paths and verify containment before accessing. Use `Path.relative_to()` (not `startswith`) on resolved paths to reject filenames that escape the artifact directory.

Verification:
- `grep -rn 'relative_to\|startswith.*resolve' packaging/scripts/validate-release-manifest.py` — verify path traversal guards exist.
