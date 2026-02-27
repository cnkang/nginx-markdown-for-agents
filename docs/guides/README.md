# Guides Documentation

This directory contains maintained operator and maintainer guides for building, installing, configuring, and running the project.

## Contents

- [BUILD_INSTRUCTIONS.md](BUILD_INSTRUCTIONS.md) - local build, smoke-test, and development workflows
- [INSTALLATION.md](INSTALLATION.md) - platform prerequisites and NGINX module installation
- [CONFIGURATION.md](CONFIGURATION.md) - directive reference and configuration examples
- [OPERATIONS.md](OPERATIONS.md) - monitoring, troubleshooting, runbooks, and operations guidance

## Usage Order (Recommended)

For deployment-focused users, start with the repository `README.md` first, then use the guides below for canonical detailed steps.

1. [BUILD_INSTRUCTIONS.md](BUILD_INSTRUCTIONS.md) for local development builds and quick validation
2. [INSTALLATION.md](INSTALLATION.md) for NGINX source build and deployment setup
3. [CONFIGURATION.md](CONFIGURATION.md) for directive selection and tuning
4. [OPERATIONS.md](OPERATIONS.md) for production operations and incident response

## Notes

- These guides are canonical project guides (not archived process notes).
- Feature-level implementation details live under `../features/`.
- Testing strategy and test-reference documents live under `../testing/`.

## Terminology (Used in Guides)

- **Module** means the NGINX Markdown filter module (C / NGINX integration layer).
- **Rust converter** means the Rust HTML-to-Markdown conversion library and FFI implementation.
- **Metrics endpoint** means the `markdown_metrics` HTTP endpoint (plain text or JSON; not Prometheus exposition format).
