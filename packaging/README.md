# Packaging Infrastructure

Build, test, and release DEB/RPM packages for `nginx-module-markdown-for-agents`
using nFPM declarative configuration and GitHub Actions.

## Directory Structure

```
packaging/
├── nfpm/
│   ├── nfpm.yaml              # nFPM declarative package configuration
│   ├── modules-available/     # NGINX module config snippet (mod-markdown.conf)
│   └── scripts/               # Package lifecycle scripts
│       ├── postinstall.sh     #   Runs after package installation
│       └── preremove.sh       #   Runs before package removal
├── scripts/
│   ├── build-deb.sh           # DEB build helper
│   ├── generate-checksums.sh  # SHA256SUMS generation
│   ├── gpg-sign-checksums.sh  # GPG detached signature for SHA256SUMS
│   ├── smoke-test-basic.sh    # Basic installation + module load test
│   ├── smoke-test-diagnostics.sh  # Failure diagnostic output
│   └── validate-version.sh    # Version format validation
├── matrix.yaml                # Build matrix configuration (NGINX versions, arches, platforms)
└── README.md                  # This file
```

## nFPM Configuration

The package definition lives in `nfpm/nfpm.yaml`. A single YAML file produces
both `.deb` and `.rpm` packages via nFPM's `--packager` flag.

### Environment Variables

The configuration uses template variables injected by the CI matrix:

| Variable | Description | Example |
|----------|-------------|---------|
| `PKG_VERSION` | Project version (from tag or workflow input) | `0.8.3` |
| `NGINX_VERSION` | Target NGINX version from build matrix | `1.26.3` |
| `NFPM_ARCH` | Target architecture | `amd64`, `arm64` |

### Building Packages Locally

```bash
# Set required environment variables
export PKG_VERSION="0.9.1"
export NGINX_VERSION="1.26.3"
export NFPM_ARCH="amd64"

# Generate DEB
nfpm package --packager deb --target "dist/nginx-module-markdown-for-agents_${PKG_VERSION}_nginx-${NGINX_VERSION}_${NFPM_ARCH}.deb"

# Generate RPM
nfpm package --packager rpm --target "dist/nginx-module-markdown-for-agents-${PKG_VERSION}-nginx${NGINX_VERSION}-1.x86_64.rpm"
```

### Extending the Configuration

The nFPM config consumes the installation layout from 0.7.0 release package naming and layout. To add new
files to the package, append entries to the `contents` list in `nfpm.yaml`.
No structural changes are needed when the build matrix is extended — the
environment variables handle parameterization.

## Smoke Test Scripts

### smoke-test-basic.sh

Validates that a package installs correctly and the module loads in NGINX.

```bash
# Usage
packaging/scripts/smoke-test-basic.sh PACKAGE_FILE NGINX_VERSION

# Examples
packaging/scripts/smoke-test-basic.sh dist/nginx-module-markdown-for-agents_0.8.3_nginx-1.26.3_amd64.deb 1.26.3
packaging/scripts/smoke-test-basic.sh dist/nginx-module-markdown-for-agents-0.8.3-nginx1.26.3-1.x86_64.rpm 1.26.3
```

The script:
1. Detects package format from file extension (.deb or .rpm)
2. Installs the matching nginx.org NGINX package
3. Installs the module package
4. Verifies the `.so` file exists at the expected path
5. Adds a `load_module` directive and runs `nginx -t`
6. On failure, automatically invokes `smoke-test-diagnostics.sh`

### smoke-test-diagnostics.sh

Collects diagnostic information when a smoke test fails. Called automatically
by `smoke-test-basic.sh` on error, or can be run standalone.

```bash
# Set environment, then run
export INSTALL_LOG="/tmp/install.log"
export NGINX_VERSION="1.26.3"
export PACKAGE_FILE="dist/my-package.deb"

packaging/scripts/smoke-test-diagnostics.sh
```

Outputs (to stderr):
- `nginx -V` — compiled NGINX version and configure arguments
- `nginx -t` — configuration test errors
- Module `.so` path existence check
- `ldd` dependency listing for the module
- Package manager install log
- Build NGINX version and package filename
- System architecture (`uname -m`, `dpkg --print-architecture` or `rpm --eval`)

## Integrity Verification

Release artifacts include a `SHA256SUMS` file and (when GPG signing is enabled)
a `SHA256SUMS.asc` detached signature.

### Verifying Checksums

After downloading packages and `SHA256SUMS` into the same directory:

```bash
sha256sum -c SHA256SUMS
```

Every line should report `OK`. Any mismatch indicates a corrupted or tampered file.

### Verifying GPG Signature

When GPG signing is enabled, verify the signature on `SHA256SUMS`:

```bash
# Import the project's public signing key (one-time)
gpg --import nginx-markdown-for-agents-release.asc

# Verify the signature
gpg --verify SHA256SUMS.asc SHA256SUMS
```

Expected output includes `Good signature from ...` and the key fingerprint.

**Public key fingerprint:**

```
[FINGERPRINT TO BE ADDED WHEN GPG SIGNING IS ENABLED]
```

The signing key will be published to the project's GitHub Releases page and
the repository wiki once GPG signing is activated. Until then, only SHA256SUMS
checksum verification is available.

### Generating Checksums (CI / Maintainers)

```bash
# Generate SHA256SUMS for all packages in dist/
packaging/scripts/generate-checksums.sh -d dist/

# Sign the checksums (requires GPG key configured)
packaging/scripts/gpg-sign-checksums.sh -f dist/SHA256SUMS
```

## Extension Points

### Adding a New NGINX Version

1. Edit `packaging/matrix.yaml` — append the version to `nginx_versions`
2. No workflow or nFPM config changes needed; the matrix drives everything

### Adding a New Platform

1. Add an entry under `smoke_test_containers` in `matrix.yaml`
2. Update the workflow matrix include/exclude rules if the new platform
   requires special handling (e.g., different repo URL)

### Enabling GPG Signing

1. Generate a dedicated release signing GPG key pair
2. Configure GitHub repository secrets:
   - `GPG_PRIVATE_KEY` — ASCII-armored private key
   - `GPG_PASSPHRASE` — key passphrase
   - `GPG_KEY_ID` — key ID for signing
3. Create a Protected Environment named `release-signing` with required reviewers
4. The workflow automatically detects configured secrets and enables signing

### Adding a New Architecture

1. Append to `architectures` in `matrix.yaml`
2. Add a corresponding runner entry under `build_runners`
3. nFPM handles the new arch via the `NFPM_ARCH` environment variable
