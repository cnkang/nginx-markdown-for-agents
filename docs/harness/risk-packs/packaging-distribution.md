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
- Architecture-specific package smoke tests running on the wrong runner
  architecture
- Secure-by-default Helm chart settings that prevent NGINX from binding or
  writing runtime/temp files
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
  filename as the NGINX dynamic module build output.
- Standalone package workflows must install the same canonical doc/license
  paths as nFPM packages and must run `check_install_layout.sh` against their
  generated packages before upload.
- Containerized package workflows that use Bash syntax must set
  `defaults.run.shell: bash` or equivalent step-level shells.
- Helm chart defaults must render a pod that can start under the chart's
  default security context.
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
