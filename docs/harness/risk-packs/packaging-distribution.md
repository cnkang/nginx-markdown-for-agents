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
make docs-check
make release-gates-check
```

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.0 | 2026-04-28 | v0.6.0-planning | Initial pack definition |
| 0.6.0 | 2026-05-03 | Codex | Covered Homebrew workflows/formula, package metadata, and release-gate docs |
