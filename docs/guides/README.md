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
7. [HARNESS_MAINTENANCE.md](HARNESS_MAINTENANCE.md) if you are maintaining repo-owned agent workflow rules and validation.
8. [HOMEBREW_TAP_RELEASE.md](HOMEBREW_TAP_RELEASE.md) if you publish via a dedicated Homebrew tap.

## Guide Index

| Guide | What it covers |
|-------|----------------|
| [INSTALLATION.md](INSTALLATION.md) | Prerequisites, supported installation paths, and NGINX integration |
| [PACKAGE_INSTALLATION.md](PACKAGE_INSTALLATION.md) | Operator workflow for published DEB/RPM artifacts |
| [PACKAGE_DISTRIBUTION.md](PACKAGE_DISTRIBUTION.md) | Release and packaging-maintainer distribution contract |
| [INSTALL.md](INSTALL.md) | Stable navigation entry point for installation documentation |
| [DEPLOYMENT_EXAMPLES.md](DEPLOYMENT_EXAMPLES.md) | Practical configuration patterns and rollout examples |
| [CONFIGURATION.md](CONFIGURATION.md) | Directive reference, defaults, and configuration behavior |
| [OPERATIONS.md](OPERATIONS.md) | Monitoring, troubleshooting, and operational runbooks |
| [BUILD_INSTRUCTIONS.md](BUILD_INSTRUCTIONS.md) | Source builds, development workflows, and local verification |
| [HARNESS_MAINTENANCE.md](HARNESS_MAINTENANCE.md) | Maintaining repo-owned harness rules, checks, and local adapters |
| [HOMEBREW_TAP_RELEASE.md](HOMEBREW_TAP_RELEASE.md) | Homebrew tap publication and post-release macOS verification |

## Scope

Use these guides for maintained how-to documentation. If you need deeper implementation detail, move to [../features/README.md](../features/README.md). If you need test references, use [../testing/README.md](../testing/README.md).

In short:

- `INSTALLATION.md` gets the module into NGINX
- `PACKAGE_INSTALLATION.md` installs a selected DEB/RPM artifact
- `PACKAGE_DISTRIBUTION.md` defines how the project publishes release artifacts
- `DEPLOYMENT_EXAMPLES.md` gets you to a working rollout pattern faster
- `CONFIGURATION.md` defines the knobs and policies
- `OPERATIONS.md` covers monitoring, troubleshooting, and runtime practice
- `HARNESS_MAINTENANCE.md` explains how to evolve the repo-owned harness without
  moving public rules into local-only files

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Added update tracking section |
