# nginx-markdown-for-agents APT Repository

This is the APT package repository for `nginx-module-markdown-for-agents`, an NGINX dynamic
filter module that serves Markdown to AI agents while keeping HTML for normal
clients.

> **Note**: This is a self-hosted, unofficial repository template. It is not
> part of the Debian, Ubuntu, or any other official distribution archive.
> For the latest GitHub Release artifacts, use the GitHub Release DEB artifacts
> with `SHA256SUMS`. `pkg.example.com` and the commands below are operator
> examples for repositories you publish yourself.

---

## Self-Hosted Quick Start

### 1. Import the GPG signing key

```bash
key_file="$(mktemp)"
curl -fsSL -o "$key_file" \
    https://pkg.example.com/nginx-markdown/gpg.key
gpg --show-keys --fingerprint "$key_file"
sudo gpg --dearmor --yes --output \
    /usr/share/keyrings/nginx-markdown-archive-keyring.gpg "$key_file"
rm -f "$key_file"
```

### 2. Add the repository

```bash
echo "deb [signed-by=/usr/share/keyrings/nginx-markdown-archive-keyring.gpg arch=amd64] \
    https://pkg.example.com/nginx-markdown/apt stable main" | \
    sudo tee /etc/apt/sources.list.d/nginx-markdown.list
```

For arm64 systems, replace `arch=amd64` with `arch=arm64`.

### 3. Install the module from your self-hosted repository

```bash
sudo apt-get update
sudo apt-get install nginx-module-markdown-for-agents
```

### 4. Enable the module

After installation, enable the module in your NGINX configuration.
This package targets nginx.org builds, whose default `nginx.conf` does
**not** include `/etc/nginx/modules-enabled/*.conf` — use a top-level
`load_module` directive instead:

```bash
# Add at the TOP LEVEL of /etc/nginx/nginx.conf (before the http block):
#   load_module /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so;

# Test and reload
sudo nginx -t && sudo systemctl reload nginx
```

### 5. Verify

```bash
# Confirm the module loads and the directive parses (dynamic modules do
# not appear in `nginx -V` — that output reflects the build, not runtime
# load_module).  Use the doctor or an explicit load test instead:
nginx-markdown-doctor

# Or manually: create a temp config with load_module + markdown_filter on,
# then `nginx -t -c <tmp.conf>`.

# Test conversion
curl -H "Accept: text/markdown" http://localhost/
```

---

## Available Distributions

| Codename | Description | Status |
|----------|-------------|--------|
| `stable` | Current stable release | Active |

---

## Available Architectures

| Architecture | Description |
|-------------|-------------|
| `amd64` | x86_64 / AMD64 |
| `arm64` | AArch64 / ARM64 |

---

## Components

| Component | Description |
|-----------|-------------|
| `main` | Primary packages (nginx-module-markdown-for-agents) |

---

## Repository Structure

```
repo/apt/
├── dists/
│   └── stable/
│       ├── main/
│       │   ├── binary-amd64/
│       │   │   ├── Packages
│       │   │   └── Packages.gz
│       │   └── binary-arm64/
│       │       ├── Packages
│       │       └── Packages.gz
│       ├── Release
│       ├── Release.gpg
│       └── InRelease
├── pool/
│   └── main/
│       └── *.deb
└── README.md
```

---

## GPG Key Information

### Key Details

- **Key ID**: `7A3743687FEEE0313128355038724643EA12C02A`
- **Key Type**: RSA 4096 (primary certification key, expires 2031-05-19)
- **Key URL**: checked in at `packaging/nginx-markdown-for-agents-release.asc`
- **Fingerprint (signing subkey)**: `15C792438EAA762B421E60D21E8D41E7D19A8A75` — verify this value with `gpg --fingerprint` after importing

### Importing the Key

Modern APT (Debian 12+, Ubuntu 22.04+):

```bash
key_file="$(mktemp)"
curl -fsSL -o "$key_file" \
    https://pkg.example.com/nginx-markdown/gpg.key
gpg --show-keys --fingerprint "$key_file"
sudo gpg --dearmor --yes --output \
    /usr/share/keyrings/nginx-markdown-archive-keyring.gpg "$key_file"
rm -f "$key_file"
```

Legacy APT (older systems):

```bash
key_file="$(mktemp)"
curl -fsSL -o "$key_file" \
    https://pkg.example.com/nginx-markdown/gpg.key
gpg --show-keys --fingerprint "$key_file"
sudo apt-key add "$key_file"
rm -f "$key_file"
```

### Verifying Package Signatures

```bash
# Verify the Release file signature
gpg --verify /var/lib/apt/lists/*nginx-markdown*Release.gpg
```

**Canonical release verification**: for a published release, download the
exact versioned GitHub Release artifact first, verify the release signature
over `SHA256SUMS.asc`, then check the downloaded artifacts against the
checksums file — all in the same directory. The example below uses the v0.9.1
release assets (the 0.9.2 release is not yet published; adapt the version
once available). Asset names follow the canonical
`nginx-module-markdown-for-agents_<ver>_nginx-<nginx-ver>_<arch>.deb` form —
list the exact names with `gh release view v0.9.1 --json assets`:

```bash
VERSION=v0.9.1
BASE_URL="https://github.com/cnkang/nginx-markdown-for-agents/releases/download/${VERSION}"
curl -fsSLo SHA256SUMS "${BASE_URL}/SHA256SUMS"
curl -fsSLo SHA256SUMS.asc "${BASE_URL}/SHA256SUMS.asc"
curl -fsSLo nginx-module-markdown-for-agents_0.9.1_nginx-1.30.4_amd64.deb \
  "${BASE_URL}/nginx-module-markdown-for-agents_0.9.1_nginx-1.30.4_amd64.deb"
# Verify the signature AND the signer identity.  `gpg --verify` alone only
# proves the file was signed by *some* key.  Download the checked-in project
# public key into this working directory first (run from a repository clone),
# then import it into an isolated keyring and verify the signing-subkey
# fingerprint 15C792438EAA762B421E60D21E8D41E7D19A8A75 before trusting the
# signature.
cp "${REPO_ROOT:-.}/packaging/nginx-markdown-for-agents-release.asc" \
  nginx-markdown-for-agents-release.asc
KEYRING="$(mktemp -d)/keyring.gpg"
gpg --no-default-keyring --keyring "$KEYRING" \
    --import nginx-markdown-for-agents-release.asc
gpg --no-default-keyring --keyring "$KEYRING" --fingerprint
gpg --no-default-keyring --keyring "$KEYRING" --verify SHA256SUMS.asc SHA256SUMS
# Every file listed in SHA256SUMS must be present before `sha256sum -c`
# passes.  Select the checksum line for the exact artifact you fetched and
# fail unless exactly one entry matches (an empty selection would make
# `sha256sum -c` succeed vacuously):
PACKAGE="nginx-module-markdown-for-agents_0.9.1_nginx-1.30.4_amd64.deb"
awk -v pkg="$PACKAGE" '
  $2 == pkg || $2 == "*" pkg { print; n++ }
  END { exit n == 1 ? 0 : 1 }
' SHA256SUMS > SHA256SUMS.select
sha256sum -c SHA256SUMS.select
```

**Per-package signatures** (only for releases whose workflow produces them;
not part of the canonical release verification path):

```bash
dpkg-sig --verify nginx-module-markdown-for-agents_*.deb
```

---

## Key Rotation

When you rotate the signing key:

1. Publish the new key alongside the still-valid old key at the key URL.
2. Announce the new fingerprint and have users install and independently
   verify it before repository metadata relies on it.
3. Maintain an overlap period in which signatures made by both keys are
   accepted. Existing clients must refresh their local keyring during this
   overlap, before the old key is removed:

```bash
# Refresh from the checked-in project public key, then verify the
# signing-subkey fingerprint before trusting the repository.
gpg --no-default-keyring --keyring /tmp/markdown-keyring.gpg \
    --import packaging/nginx-markdown-for-agents-release.asc
gpg --no-default-keyring --keyring /tmp/markdown-keyring.gpg --fingerprint
# Install the keyring directly from the checked-in .asc file; the
# /tmp keyring above is used only for fingerprint verification.
sudo gpg --dearmor \
    -o /usr/share/keyrings/nginx-markdown-archive-keyring.gpg \
    < packaging/nginx-markdown-for-agents-release.asc
```

4. Remove the old key only after the overlap migration is complete.

The project documents key rotation in its
[PACKAGE_DISTRIBUTION.md](../../../docs/guides/PACKAGE_DISTRIBUTION.md) guide.

---

## NGINX ABI Compatibility

The packages in this repository are built against specific NGINX versions.
The package declares an ABI dependency (`nginx-abi-X.Y`) to prevent
installation on incompatible NGINX builds.

If you encounter dependency errors:

1. Verify your NGINX version: `nginx -V`
2. Check the package requires a matching ABI version
3. See the [troubleshooting guide](../../../docs/guides/PACKAGE_INSTALLATION.md)

---

## Troubleshooting

### "NO_PUBKEY" error during apt-get update

```bash
# Re-import the signing key
key_file="$(mktemp)"
curl -fsSL -o "$key_file" \
    https://pkg.example.com/nginx-markdown/gpg.key
gpg --show-keys --fingerprint "$key_file"
sudo gpg --dearmor --yes --output \
    /usr/share/keyrings/nginx-markdown-archive-keyring.gpg "$key_file"
rm -f "$key_file"
sudo apt-get update
```

### "Hash Sum mismatch" error

This may indicate a mirror sync issue or network problem:

```bash
sudo rm -rf /var/lib/apt/lists/*nginx-markdown*
sudo apt-get update
```

### Package dependency not satisfiable

The module requires a specific NGINX ABI version. Install the matching
NGINX version from the official NGINX repository:

```bash
# Add official NGINX repository first, then retry
sudo apt-get install <self-hosted-module-package-name>
```

---

## Building from Source

If pre-built packages are not available for your platform, see the
[build instructions](https://github.com/cnkang/nginx-markdown-for-agents)
in the project repository.

---

## Security

- GPG signs the `SHA256SUMS` checksum manifest for each release
- The APT repository signs metadata (Release) with Release.gpg + InRelease
- Individual `.deb` signatures are conditional: the signed checksum manifest
  is the guaranteed integrity surface for every package
- The system distributes the signing key over HTTPS only
- Report security issues via the project's security policy

---

## Links

- **Project**: https://github.com/cnkang/nginx-markdown-for-agents
- **Issues**: https://github.com/cnkang/nginx-markdown-for-agents/issues
- **Installation Guide**: See `docs/guides/PACKAGE_INSTALLATION.md`
- **Distribution Guide**: See `docs/guides/PACKAGE_DISTRIBUTION.md`
