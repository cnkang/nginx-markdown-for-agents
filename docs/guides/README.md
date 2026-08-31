# Guides Documentation

This directory contains the canonical operational guides for building, installing, configuring, deploying, and running the project.

Use these documents when you need decisions and procedures you can act on directly in an environment. They are not just implementation background.

## Recommended Reading Order

1. [INSTALLATION.md](INSTALLATION.md) if your goal is to get the module running in NGINX.
2. [PACKAGE_INSTALLATION.md](PACKAGE_INSTALLATION.md) if you already selected a DEB/RPM artifact.
3. [DEPLOYMENT_EXAMPLES.md](DEPLOYMENT_EXAMPLES.md) if you want working configuration patterns quickly.
4. [CONFIGURATION.md](CONFIGURATION.md) if you need the full directive reference and tuning details.
5. [OPERATIONS.md](OPERATIONS.md) if you are preparing for production monitoring and troubleshooting.
6. [BUILD_INSTRUCTIONS.md](BUILD_INSTRUCTIONS.md) if you are building from source or working locally.
7. [ROLLOUT_COOKBOOK.md](ROLLOUT_COOKBOOK.md) if you are planning a controlled enablement.
8. [HOMEBREW_TAP_RELEASE.md](HOMEBREW_TAP_RELEASE.md) if you publish via a dedicated Homebrew tap.

## Guide Index

| Guide | What it covers |
|-------|----------------|
| [INSTALLATION.md](INSTALLATION.md) | Prerequisites, supported installation paths, and NGINX integration |
| [PACKAGE_INSTALLATION.md](PACKAGE_INSTALLATION.md) | Operator workflow for published DEB/RPM artifacts |
| [PACKAGE_DISTRIBUTION.md](PACKAGE_DISTRIBUTION.md) | Release and packaging-maintainer distribution contract |
| [DEPLOYMENT_EXAMPLES.md](DEPLOYMENT_EXAMPLES.md) | Practical configuration patterns and rollout examples |
| [CONFIGURATION.md](CONFIGURATION.md) | Directive reference, defaults, and configuration behavior |
| [OPERATIONS.md](OPERATIONS.md) | Monitoring, troubleshooting, and operational runbooks |
| [BUILD_INSTRUCTIONS.md](BUILD_INSTRUCTIONS.md) | Source builds, development workflows, and local verification |
| [DYNAMIC_CONFIG.md](DYNAMIC_CONFIG.md) | Dynamic configuration (dynconf) overlay and restore |
| [KUBERNETES_DEPLOYMENT.md](KUBERNETES_DEPLOYMENT.md) | Kubernetes and Ingress Controller deployment |
| [ROLLOUT_COOKBOOK.md](ROLLOUT_COOKBOOK.md) | Controlled enablement and staged rollout |
| [streaming-rollout-cookbook.md](streaming-rollout-cookbook.md) | Streaming-specific rollout supplement |
| [performance-rollout-091.md](performance-rollout-091.md) | Historical: 0.9.1 performance rollout (superseded in 0.9.2) |
| [OPERATIONAL_ROLLBACK.md](OPERATIONAL_ROLLBACK.md) | Runtime mitigation: disable or narrow conversion without binary replacement |
| [VERSION_ROLLBACK-0.9.2.md](VERSION_ROLLBACK-0.9.2.md) | Version downgrade: 0.9.2 binary + matching configuration |
| [UPGRADE-TO-0.9.2.md](UPGRADE-TO-0.9.2.md) | Operational upgrade sequence to 0.9.2 |
| [MIGRATION-0.9.2.md](MIGRATION-0.9.2.md) | Configuration migration 0.9.1 → 0.9.2 |
| [0.9.2-breaking-changes.md](0.9.2-breaking-changes.md) | Compact breaking-change reference for 0.9.2 |
| [doctor.md](doctor.md) | nginx-markdown-doctor diagnostics tool |
| [prometheus-metrics.md](prometheus-metrics.md) | Prometheus metrics contract |
| [PERFORMANCE_TUNING.md](PERFORMANCE_TUNING.md) | Performance settings and presets |
| [streaming-troubleshooting.md](streaming-troubleshooting.md) | Streaming-specific troubleshooting |
| [GPG_KEY_MANAGEMENT.md](GPG_KEY_MANAGEMENT.md) | Release signing key management |
| [HOMEBREW_TAP_RELEASE.md](HOMEBREW_TAP_RELEASE.md) | Homebrew tap publication and post-release macOS verification |

## Scope

Use these guides for maintained how-to documentation. If you need deeper implementation detail, move to [../features/README.md](../features/README.md). If you need test references, use [../testing/README.md](../testing/README.md). If you maintain the repo-owned harness, use [../harness/README.md](../harness/README.md).

In short:

- `INSTALLATION.md` gets the module into NGINX
- `PACKAGE_INSTALLATION.md` installs a selected DEB/RPM artifact
- `PACKAGE_DISTRIBUTION.md` defines how the project publishes release artifacts
- `DEPLOYMENT_EXAMPLES.md` gets you to a working rollout pattern faster
- `CONFIGURATION.md` defines the knobs and policies
- `OPERATIONS.md` covers monitoring, troubleshooting, and runtime practice
- `OPERATIONAL_ROLLBACK.md` mitigates incidents without replacing the binary
- `VERSION_ROLLBACK-0.9.2.md` downgrades the binary and configuration together

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-29 | Hermes | Retained INSTALL.md as the legacy-link and package-layout compatibility stub (INSTALLATION.md remains canonical); moved HARNESS_MAINTENANCE to ../harness/; added rollback/upgrade/migration/rollout index rows |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
