# Packaging Distribution Pack

## Triggers

Changes to `.github/workflows/release-apt.yml`, `release-yum.yml`, Helm chart files, Homebrew formula, install scripts, or packaging documentation.

## Risks

- Package NGINX ABI mismatch causing module load failure at runtime
- Missing GPG signature verification in install scripts
- Helm chart values not mapping to all markdown configuration directives
- Ingress annotation parsing errors causing silent misconfiguration
- Package dependency conflicts across NGINX versions (1.24.x/1.26.x/1.29.x/1.30.x)

## Common Supporting Packs

- `docs-tooling-drift` when packaging docs change
- `release-governance` when release gates or matrix change

## Sync Points

- `tools/install.sh` must stay consistent with new package formats
- `tools/release-matrix.json` must include all supported platforms
- `tools/release/matrix/` tooling must stay consistent with matrix schema
- Homebrew formula repository (external) must stay in sync with release artifacts; Homebrew tap is a supporting surface pending implementation
- `docs/guides/INSTALLATION.md` must document all installation methods
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
