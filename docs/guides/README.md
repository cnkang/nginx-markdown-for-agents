# Guides Documentation

This directory contains the canonical operational guides for building, installing, configuring, deploying, and running the project.

Use these documents when you need decisions and procedures you can act on directly in an environment, not just implementation background.

## Recommended Reading Order

1. [INSTALLATION.md](INSTALLATION.md) if your goal is to get the module running in NGINX.
2. [DEPLOYMENT_EXAMPLES.md](DEPLOYMENT_EXAMPLES.md) if you want working configuration patterns quickly.
3. [CONFIGURATION.md](CONFIGURATION.md) if you need the full directive reference and tuning details.
4. [OPERATIONS.md](OPERATIONS.md) if you are preparing for production monitoring and troubleshooting.
5. [BUILD_INSTRUCTIONS.md](BUILD_INSTRUCTIONS.md) if you are building from source or working locally.

## Guide Index

| Guide | What it covers |
|-------|----------------|
| [INSTALLATION.md](INSTALLATION.md) | Prerequisites, supported installation paths, and NGINX integration |
| [DEPLOYMENT_EXAMPLES.md](DEPLOYMENT_EXAMPLES.md) | Practical configuration patterns and rollout examples |
| [CONFIGURATION.md](CONFIGURATION.md) | Directive reference, defaults, and configuration behavior |
| [OPERATIONS.md](OPERATIONS.md) | Monitoring, troubleshooting, and operational runbooks |
| [BUILD_INSTRUCTIONS.md](BUILD_INSTRUCTIONS.md) | Source builds, development workflows, and local verification |

## Scope

Use these guides for maintained how-to documentation. If you need deeper implementation detail, move to [../features/README.md](../features/README.md). If you need test references, use [../testing/README.md](../testing/README.md).

In short:

- `INSTALLATION.md` gets the module into NGINX
- `DEPLOYMENT_EXAMPLES.md` gets you to a working rollout pattern faster
- `CONFIGURATION.md` defines the knobs and policies
- `OPERATIONS.md` covers monitoring, troubleshooting, and runtime practice
