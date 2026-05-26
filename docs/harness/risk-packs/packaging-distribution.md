# Packaging Distribution Pack

## Triggers

Changes to package workflows, Helm chart files, Debian/RPM package metadata,
Homebrew formula/tap workflows, install scripts, release-gate packaging checks,
or packaging documentation.

## Risks

- Package NGINX ABI mismatch causing module load failure at runtime
- Missing GPG signature verification in install scripts
- Helm chart values not mapping to all markdown configuration directives
- Ingress annotation parsing errors causing silent misconfiguration
- Package dependency conflicts across NGINX versions (1.24.x/1.26.x/1.29.x/1.30.x)
- Dynamic module filename drift between the actual NGINX addon output,
  package payloads, load snippets, install docs, and release gates
- Release workflow/Dockerfile NGINX versions missing from the checksum table
- Artifact upload/download/signing name drift between release jobs
- Standalone DEB/RPM workflows drifting from the canonical package name,
  install layout, or prebuilt-module packaging contract
- Containerized package jobs using Bash-only syntax under GitHub Actions'
  default container `sh` shell
- User-supplied package versions flowing into standalone package paths,
  metadata, RPM macros, or artifact names without validation
- Exact package dependencies using only upstream NGINX source versions when
  target distro package versions include release suffixes or epochs
- RPM smoke tests selecting the wrong nginx.org repository family for the
  target distribution
- Architecture-specific package smoke tests running on the wrong runner
  architecture
- Secure-by-default Helm chart settings that prevent NGINX from binding or
  writing runtime/temp files
- Local K8s smoke tests deploying stock NGINX images while rendering
  module-specific directives, using the wrong kube-context, or deleting
  pre-existing kind clusters
- Homebrew formula checksums generated from a different source archive than
  the release/tag that the formula installs
- Tap publish/verification workflows drifting from the repo-owned release-gate
  and installation docs

## Common Supporting Packs

- `docs-tooling-drift` when packaging docs change
- `release-governance` when release gates or matrix change

## Sync Points

- `tools/install.sh` must stay consistent with new package formats
- `tools/release-matrix.json` must include all supported platforms
- `tools/release/matrix/` tooling must stay consistent with matrix schema
- `.github/workflows/release-packages.yml`,
  `.github/workflows/release-deb.yml`, `.github/workflows/release-rpm.yml`,
  and `.github/workflows/sign-and-publish.yml` must agree on package artifact
  names, supported NGINX versions, and architecture-specific runner labels.
- `packaging/checksums.sha256` must cover every NGINX source version requested
  by active release workflows and release Dockerfiles.
- `packaging/nfpm/nfpm.yaml`, Debian/RPM specs, load snippets, smoke tests,
  install-layout gates, and public install docs must use the same module `.so`
  filename as the NGINX dynamic module build output.  Their package-specific
  module directories must match the target nginx.org package `--modules-path`.
- Standalone package workflows must install the same canonical doc/license
  paths as nFPM packages and must run `check_install_layout.sh` against their
  generated packages before upload.  They must validate package version inputs
  before using them in generated paths or metadata.
- Package dependencies must be satisfiable by the target package manager; exact
  constraints must use distro-resolvable EVRs, not naked upstream source
  versions when release suffixes or epochs are expected.
- RPM smoke tests must derive nginx.org repository family from the target
  distribution instead of assuming CentOS-compatible paths for every RPM image.
- Release package build environments must use a glibc baseline compatible with
  every smoke-test/runtime distro for the generated artifact family, or the
  artifact set must be split by distro family.
- Package maintainer scripts must accept target package manager lifecycle
  arguments, including RPM numeric `%post` arguments, without failing an
  otherwise successful install for advisory output.
- Containerized package workflows that use Bash syntax must set
  `defaults.run.shell: bash` or equivalent step-level shells.
- Helm chart defaults must render a pod that can start under the chart's
  default security context.  Local K8s smoke tests that use stock NGINX images
  must disable module directives, run Helm/kubectl against the intended kind
  context, count structured pod fields without collapsed jsonpath output, and
  preserve pre-existing clusters.
- Homebrew formula repository and repo-owned formula template must stay in sync
  with release artifacts, tag timing, checksums, and post-release verification.
- `docs/guides/INSTALLATION.md` must document all installation methods
- `docs/guides/HOMEBREW_TAP_RELEASE.md`, `README.md`, and `CHANGELOG.md`
  must stay consistent with Homebrew tap behavior when those surfaces exist.
- `docs/guides/kubernetes-deployment.md` must document Helm chart usage
- `CHANGELOG.md` must record packaging changes

## Minimum Verification

```bash
helm lint charts/nginx-markdown
helm template test charts/nginx-markdown
python3 tools/release/gates/validate_package_metadata_070.py
python3 tools/release/gates/validate_k8s_manifests_070.py
make docs-check
make release-gates-check
```

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.0 | 2026-04-28 | v0.6.0-planning | Initial pack definition |
| 0.6.0 | 2026-05-03 | Codex | Covered Homebrew workflows/formula, package metadata, and release-gate docs |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.7.2 | 2026-05-25 | Codex | Added release package chain invariants for module names, checksum coverage, artifact naming, architecture-matched smoke tests, and Helm secure defaults |
| 0.7.3 | 2026-05-26 | Codex | Added standalone DEB/RPM package-name, layout, and prebuilt-module contract coverage |
| 0.7.4 | 2026-05-26 | Codex | Added GitHub Actions container shell coverage for standalone package workflows |
| 0.7.5 | 2026-05-26 | Codex | Added package dependency satisfiability, version-input validation, distro-specific RPM repo selection, package script lifecycle args, module path/glibc runtime compatibility, and local K8s smoke context/module safety coverage |
