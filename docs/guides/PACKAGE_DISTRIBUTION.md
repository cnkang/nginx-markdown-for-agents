# Package Distribution Strategy

## Overview

This document defines the distribution strategy for the NGINX Markdown Filter
Module binary packages (DEB and RPM), including artifact naming conventions,
integrity verification via SHA256SUMS, and GPG signature verification.

## Distribution Channels

| Channel | Format | Status | Signing |
|---------|--------|--------|---------|
| GitHub Releases | .deb + .rpm | Active for v0.7.0+ release artifacts | SHA256SUMS / GPG |
| Self-hosted APT | .deb | Planned; no public repository URL yet | GPG |
| Self-hosted YUM | .rpm | Planned; no public repository URL yet | GPG |

APT/YUM repository publishing is intentionally tracked as a future distribution
step. Until a real repository and signing key are published, installation docs
must point users to GitHub Release package artifacts rather than bare
`apt-get install` or `yum install` commands.

<!-- BEGIN:release-matrix:distribution-matrix -->

### Release Matrix Distribution Overview

### Build Workflows

| Workflow | Entries | Tiers |
|----------|---------|-------|
| `.github/workflows/ci.yml` | 1 | best-effort |
| `.github/workflows/homebrew-formula-gate.yml` | 1 | experimental |
| `.github/workflows/official-nginx-docker.yml` | 8 | supported |
| `.github/workflows/release-binaries.yml` | 10 | supported |
| `.github/workflows/release-packages.yml` | 14 | supported |
<!-- END:release-matrix:distribution-matrix -->

## Important Disclaimers

1. **These are NOT official NGINX repository packages.** They are community-maintained.
2. **NGINX dynamic module ABI is version-sensitive.** Each package is built
   against a specific NGINX version and channel (stable/mainline). Mismatched
   ABI versions will cause module load failures at runtime.
3. **Default installation does NOT globally enable the module.** Operators must
   explicitly add `load_module` directives. This prevents accidental conversion
   of all HTML responses.

## Artifact Naming Conventions

Package filenames encode the module version, target NGINX version, and
architecture to prevent ABI mismatch at install time.

### DEB Package Naming

Format:

```text
nginx-module-markdown-for-agents_<VERSION>_nginx-<NGINX_VERSION>_<ARCH>.deb
```

Components:

| Field | Description | Example |
|-------|-------------|---------|
| VERSION | Module semantic version | 0.8.3 |
| NGINX_VERSION | Target NGINX version (major.minor.patch) | 1.26.3 |
| ARCH | CPU architecture (amd64, arm64) | amd64 |

Example:

```text
nginx-module-markdown-for-agents_0.8.3_nginx-1.26.3_amd64.deb
```

### RPM Package Naming

Format:

```text
nginx-module-markdown-for-agents-<VERSION>-nginx<NGINX_VERSION>-1.<ARCH>.rpm
```

Components:

| Field | Description | Example |
|-------|-------------|---------|
| VERSION | Module semantic version | 0.8.3 |
| NGINX_VERSION | Target NGINX version (major.minor.patch) | 1.26.3 |
| ARCH | CPU architecture (x86_64, aarch64) | x86_64 |

Example:

```text
nginx-module-markdown-for-agents-0.8.3-nginx1.26.3-1.x86_64.rpm
```

### Architecture Mapping

| Source Arch | DEB Arch | RPM Arch |
|-------------|----------|----------|
| amd64 | amd64 | x86_64 |
| arm64 | arm64 | aarch64 |

## NGINX ABI Sensitivity

The dynamic module ABI changes between NGINX major/minor versions. Always
install the package that matches your installed NGINX version exactly. A
version mismatch will cause a module load failure at runtime with an error
such as:

```text
nginx: [emerg] module is not binary compatible
```

## SHA256SUMS Verification

Every release includes a `SHA256SUMS` file containing SHA-256 checksums for
all distributed artifacts. Use this file to verify download integrity before
installation.

### Downloading the Checksum File

Download `SHA256SUMS` from the same GitHub Release page as the package:

```bash
curl -fsSLO https://github.com/<org>/nginx-markdown-for-agents/releases/download/v0.8.3/SHA256SUMS
```

### Verifying a Downloaded Package

After downloading both the package and `SHA256SUMS`:

```bash
# Verify a specific package against the checksum file
sha256sum --check --ignore-missing SHA256SUMS
```

Or verify manually:

```bash
# Compute the checksum of the downloaded file
sha256sum nginx-module-markdown-for-agents_0.8.3_nginx-1.26.3_amd64.deb

# Compare the output against the corresponding line in SHA256SUMS
grep "nginx-module-markdown-for-agents_0.8.3_nginx-1.26.3_amd64.deb" SHA256SUMS
```

Both values must match exactly. If they differ, do not install the package
and re-download from the official release page.

### Checksum File Format

Each line in `SHA256SUMS` follows the standard format:

```text
<64-hex-char-hash>  <filename>
```

Example:

```text
a1b2c3d4...  nginx-module-markdown-for-agents_0.8.3_nginx-1.26.3_amd64.deb
e5f6a7b8...  nginx-module-markdown-for-agents-0.8.3-nginx1.26.3-1.x86_64.rpm
f9a0b1c2...  release-manifest.json
```

## Release Manifest

Every release includes a `release-manifest.json` providing structured metadata
about the release: git tag, commit SHA, package filenames with SHA-256 hashes,
source archive hash (for tag releases), and GitHub Actions workflow metadata.

The manifest is generated during the `integrity-checksums` CI job and is
included in `SHA256SUMS`. The `SHA256SUMS` file is then signed as
`SHA256SUMS.asc` for tag releases, providing a chain of custody:

```
release-manifest.json → included in SHA256SUMS → signed as SHA256SUMS.asc
```

The manifest provides release asset traceability and checksum cross-reference.
It does not by itself prove byte-for-byte reproducible builds.

Download the manifest from the same GitHub Release page:

```bash
curl -fsSLO https://github.com/<org>/nginx-markdown-for-agents/releases/download/v0.8.3/release-manifest.json
```

## GPG Signature Verification

When GPG signing is enabled for a release, a detached ASCII-armored signature
file (`SHA256SUMS.asc`) is published alongside `SHA256SUMS`. This allows
verification that the checksums were produced by the project maintainers.

### Importing the Project Public Key

Before verifying signatures, import the project signing public key:

```bash
# Import from a keyserver (replace KEY_ID with the actual project key ID)
gpg --keyserver hkps://keys.openpgp.org --recv-keys <KEY_ID>

# Or import from a local file if provided
gpg --import project-signing-key.asc
```

### Verifying the Signature

Download both `SHA256SUMS` and `SHA256SUMS.asc`, then verify:

```bash
gpg --verify SHA256SUMS.asc SHA256SUMS
```

A successful verification produces output similar to:

```text
gpg: Signature made Mon 01 Jan 2026 12:00:00 AM UTC
gpg:                using RSA key <KEY_ID>
gpg: Good signature from "nginx-markdown-for-agents release signing key"
```

If verification fails with `BAD signature`, do not trust the checksums or
the associated packages.

### Full Verification Workflow

The recommended verification sequence:

```bash
# 1. Download the package, checksums, signature, and manifest
curl -fsSLO https://github.com/<org>/nginx-markdown-for-agents/releases/download/v0.8.3/SHA256SUMS
curl -fsSLO https://github.com/<org>/nginx-markdown-for-agents/releases/download/v0.8.3/SHA256SUMS.asc
curl -fsSLO https://github.com/<org>/nginx-markdown-for-agents/releases/download/v0.8.3/<package-file>
curl -fsSLO https://github.com/<org>/nginx-markdown-for-agents/releases/download/v0.8.3/release-manifest.json

# 2. Verify GPG signature on the checksum file
gpg --verify SHA256SUMS.asc SHA256SUMS

# 3. Verify the package and manifest checksums
sha256sum --check --ignore-missing SHA256SUMS
```

If all steps succeed, the package and manifest are authentic and intact.
The manifest (`release-manifest.json`) is included in `SHA256SUMS` and therefore
covered by the GPG signature on `SHA256SUMS`.

## Security Policy

- Release checksums are GPG-signed with the project release key (`SHA256SUMS.asc` signs `SHA256SUMS`). Package authenticity is verified by first verifying the signed checksum file, then checking the downloaded package against `SHA256SUMS`.
- The default `postinst` script does NOT add `load_module` to `nginx.conf`.
- Operators must explicitly enable the module, ensuring intentional activation.
- Module is loaded as a dynamic module (`--add-dynamic-module`), not compiled in.

## Non-Goals

- Publishing to official NGINX, Debian, or Fedora repositories.
- Providing static module builds (dynamic module only).
- Supporting NGINX forks or custom builds with different ABI.
