# Package Distribution Strategy

## Overview

This document defines the distribution strategy for the NGINX Markdown Filter
Module binary packages (DEB and RPM), including artifact naming conventions,
integrity verification via SHA256SUMS, and GPG signature verification.

## Distribution Channels

| Channel | Format | Status | Signing |
|---------|--------|--------|---------|
| GitHub Releases | .deb + .rpm | Active for published release artifacts; tag-specific; verify assets before downloading | SHA256SUMS; `SHA256SUMS.asc` for published releases |
| Self-hosted APT | .deb | Planned; no public repository URL yet | GPG |
| Self-hosted YUM | .rpm | Planned; no public repository URL yet | GPG |

APT/YUM repository publishing is intentionally tracked as a future distribution
step. Until the project publishes a real repository and signing key, installation docs
must point users to GitHub Release package artifacts rather than bare
`apt-get install` or `yum install` commands.

The release matrix describes the package build and validation targets. It does
not prove that a particular tag has public package assets. Before using a
package command, verify that the target GitHub Release lists both the exact
package and its `SHA256SUMS` file. Release candidates do not make package
assets available. If the release publishes no matching asset, use the
[Manual Source Build](./INSTALLATION.md#6-secondary-manual-source-build).

For repository and release-build checksum verification, use the authoritative
[`packaging/checksums.sha256`](../../packaging/checksums.sha256) file with
[`packaging/scripts/verify-checksum.sh`](../../packaging/scripts/verify-checksum.sh).
`packaging/nginx-checksums.yaml` is legacy compatibility data only. Active
workflows do not consume it. Do not add new release versions there.

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
| VERSION | Module semantic version | `<VERSION>` |
| NGINX_VERSION | Target NGINX version (major.minor.patch) | 1.26.3 |
| ARCH | CPU architecture (amd64, arm64) | amd64 |

Example:

```text
nginx-module-markdown-for-agents_<VERSION>_nginx-1.26.3_amd64.deb
```

### RPM Package Naming

Format:

```text
nginx-module-markdown-for-agents-<VERSION>-nginx<NGINX_VERSION>-1.<ARCH>.rpm
```

Components:

| Field | Description | Example |
|-------|-------------|---------|
| VERSION | Module semantic version | `<VERSION>` |
| NGINX_VERSION | Target NGINX version (major.minor.patch) | 1.26.3 |
| ARCH | CPU architecture (x86_64, aarch64) | x86_64 |

Example:

```text
nginx-module-markdown-for-agents-<VERSION>-nginx1.26.3-1.x86_64.rpm
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

Any published package release must include a `SHA256SUMS` file containing
SHA-256 checksums for all distributed artifacts. Use this file to verify
download integrity before installation.

### Downloading the Checksum File

Download `SHA256SUMS` from the same GitHub Release page as the package:

```bash
RELEASE_TAG="<published-release-tag>"
curl -fsSLO "https://github.com/<org>/nginx-markdown-for-agents/releases/download/${RELEASE_TAG}/SHA256SUMS"
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
sha256sum "nginx-module-markdown-for-agents_<VERSION>_nginx-1.26.3_amd64.deb"

# Compare the output against the corresponding line in SHA256SUMS
grep "nginx-module-markdown-for-agents_<VERSION>_nginx-1.26.3_amd64.deb" SHA256SUMS
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
a1b2c3d4...  nginx-module-markdown-for-agents_<VERSION>_nginx-1.26.3_amd64.deb
e5f6a7b8...  nginx-module-markdown-for-agents-<VERSION>-nginx1.26.3-1.x86_64.rpm
f9a0b1c2...  release-manifest.json
```

## Release Manifest

Every release includes a `release-manifest.json` providing structured metadata
about the release: git tag, commit SHA, package filenames with SHA-256 hashes,
source archive hash (for tag releases), and GitHub Actions workflow metadata.

The `integrity-checksums` CI job generates the manifest and includes it
in `SHA256SUMS`. The release then signs the `SHA256SUMS` file as
`SHA256SUMS.asc` for tag releases, providing a chain of custody:

```
release-manifest.json → included in SHA256SUMS → signed as SHA256SUMS.asc
```

The manifest provides release asset traceability and checksum cross-reference.
It does not by itself prove byte-for-byte reproducible builds.

Download the manifest from the same GitHub Release page:

```bash
RELEASE_TAG="<published-release-tag>"
curl -fsSL -H "Accept: application/json" -o release-manifest.json \
  "https://github.com/<org>/nginx-markdown-for-agents/releases/download/${RELEASE_TAG}/release-manifest.json"
```

## GPG Signature Verification

For a published GitHub Release, the `release-binaries` workflow publishes a
detached ASCII-armored signature file (`SHA256SUMS.asc`) alongside
`SHA256SUMS`. The signing job checks out the exact commit resolved by the
workflow's prepare job, so the signing script and release metadata come from
the same immutable source revision. Manual runs only publish the signature
when the requested ref is a version tag (`v...`).
The `release-signing` environment secrets are therefore mandatory for a
published release. The workflow fails closed rather than publishing an
unsigned release asset.

### Importing the Project Public Key

Before verifying signatures, obtain the project signing public key and its
full fingerprint. The project checks the public key in at
`packaging/nginx-markdown-for-agents-release.asc` and publishes its
signing-subkey fingerprint as `15C792438EAA762B421E60D21E8D41E7D19A8A75`
(primary key `7A3743687FEEE0313128355038724643EA12C02A`). A key ID or
keyserver result only transports the key. It does not establish identity —
verify the full fingerprint with `gpg --show-keys` after import. See
[GPG Key Management](GPG_KEY_MANAGEMENT.md) for the verification contract.

```bash
# Import the checked-in project public key, then confirm the fingerprint.
gpg --show-keys packaging/nginx-markdown-for-agents-release.asc

# Or import from a keyserver after checking the published full fingerprint
# (replace KEY_ID with the published project key ID)
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
RELEASE_TAG="<published-release-tag>"
PACKAGE_FILE="<package-file>"
BASE_URL="https://github.com/<org>/nginx-markdown-for-agents/releases/download/${RELEASE_TAG}"
curl -fsSLO "${BASE_URL}/SHA256SUMS"
curl -fsSLO "${BASE_URL}/SHA256SUMS.asc"
curl -fsSLO "${BASE_URL}/${PACKAGE_FILE}"
curl -fsSLO "${BASE_URL}/release-manifest.json"

# 2. Verify GPG signature on the checksum file
gpg --verify SHA256SUMS.asc SHA256SUMS

# 3. Verify the package and manifest checksums
sha256sum --check --ignore-missing SHA256SUMS
```

If all steps succeed and the imported key matches the independently
authenticated full fingerprint, the package and manifest are authentic and
intact. A successful `gpg --verify` result without that identity check proves
integrity under an untrusted key, not project authenticity.
The manifest (`release-manifest.json`) appears in `SHA256SUMS`. The GPG
signature on `SHA256SUMS` therefore covers it.

## Security Policy

- Release checksums are GPG-signed with the project release key (`SHA256SUMS.asc` signs `SHA256SUMS`). Users verify package authenticity by first verifying the signed checksum file, then checking the downloaded package against `SHA256SUMS`.
- The default `postinst` script does NOT add `load_module` to `nginx.conf`.
- Operators must explicitly enable the module, ensuring intentional activation.
- The module loads as a dynamic module (`--add-dynamic-module`), not compiled in.

## Non-Goals

- Publishing to official NGINX, Debian, or Fedora repositories.
- Providing static module builds (dynamic module only).
- Supporting NGINX forks or custom builds with different ABI.
