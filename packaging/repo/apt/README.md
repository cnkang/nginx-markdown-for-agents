# nginx-markdown-for-agents APT Repository

This is the APT package repository for `nginx-module-markdown`, an NGINX dynamic
filter module that serves Markdown to AI agents while keeping HTML for normal
clients.

> **Note**: This is a self-hosted, unofficial repository. It is not part of the
> Debian, Ubuntu, or any other official distribution archive.

---

## Quick Start

### 1. Import the GPG signing key

```bash
curl -fsSL https://pkg.example.com/nginx-markdown/gpg.key | \
    sudo gpg --dearmor -o /usr/share/keyrings/nginx-markdown-archive-keyring.gpg
```

### 2. Add the repository

```bash
echo "deb [signed-by=/usr/share/keyrings/nginx-markdown-archive-keyring.gpg arch=amd64] \
    https://pkg.example.com/nginx-markdown/apt stable main" | \
    sudo tee /etc/apt/sources.list.d/nginx-markdown.list
```

For arm64 systems, replace `arch=amd64` with `arch=arm64`.

### 3. Install the module

```bash
sudo apt-get update
sudo apt-get install nginx-module-markdown
```

### 4. Enable the module

After installation, enable the module in your NGINX configuration:

```bash
# Uncomment the load_module line in the snippet:
sudo sed -i 's/^#load_module/load_module/' \
    /etc/nginx/modules-available/mod-markdown.conf

# Symlink to modules-enabled (if your NGINX uses this pattern):
sudo ln -sf /etc/nginx/modules-available/mod-markdown.conf \
    /etc/nginx/modules-enabled/50-mod-markdown.conf

# Test and reload
sudo nginx -t && sudo systemctl reload nginx
```

### 5. Verify

```bash
# Check module is loaded
nginx -V 2>&1 | grep -o 'markdown'

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
| `main` | Primary packages (nginx-module-markdown) |

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

- **Key ID**: Published at the key URL below
- **Key Type**: RSA 4096-bit (or Ed25519)
- **Key URL**: `https://pkg.example.com/nginx-markdown/gpg.key`
- **Fingerprint**: Verify against the project README or release notes

### Importing the Key

Modern APT (Debian 12+, Ubuntu 22.04+):

```bash
curl -fsSL https://pkg.example.com/nginx-markdown/gpg.key | \
    sudo gpg --dearmor -o /usr/share/keyrings/nginx-markdown-archive-keyring.gpg
```

Legacy APT (older systems):

```bash
curl -fsSL https://pkg.example.com/nginx-markdown/gpg.key | \
    sudo apt-key add -
```

### Verifying Package Signatures

```bash
# Verify the Release file signature
gpg --verify /var/lib/apt/lists/*nginx-markdown*Release.gpg

# Check package integrity after download
apt-get download nginx-module-markdown
dpkg-sig --verify nginx-module-markdown_*.deb
```

---

## Key Rotation

When the signing key is rotated:

1. The new key is published at the same URL (`gpg.key`)
2. A transition period allows both old and new keys
3. Release announcements include the new key fingerprint
4. Users should re-import the key:

```bash
curl -fsSL https://pkg.example.com/nginx-markdown/gpg.key | \
    sudo gpg --dearmor -o /usr/share/keyrings/nginx-markdown-archive-keyring.gpg
```

Key rotation is documented in the project's
[PACKAGE_DISTRIBUTION.md](../../docs/guides/PACKAGE_DISTRIBUTION.md) guide.

---

## NGINX ABI Compatibility

The packages in this repository are built against specific NGINX versions.
The package declares an ABI dependency (`nginx-abi-X.Y`) to prevent
installation on incompatible NGINX builds.

If you encounter dependency errors:

1. Verify your NGINX version: `nginx -V`
2. Check the package requires a matching ABI version
3. See the [troubleshooting guide](../../docs/guides/PACKAGE_INSTALLATION.md)

---

## Troubleshooting

### "NO_PUBKEY" error during apt-get update

```bash
# Re-import the signing key
curl -fsSL https://pkg.example.com/nginx-markdown/gpg.key | \
    sudo gpg --dearmor -o /usr/share/keyrings/nginx-markdown-archive-keyring.gpg
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
sudo apt-get install nginx-module-markdown
```

---

## Building from Source

If pre-built packages are not available for your platform, see the
[build instructions](https://github.com/cnkang/nginx-markdown-for-agents)
in the project repository.

---

## Security

- All packages are signed with GPG
- Repository metadata (Release) is signed (Release.gpg + InRelease)
- The signing key is distributed over HTTPS only
- Report security issues via the project's security policy

---

## Links

- **Project**: https://github.com/cnkang/nginx-markdown-for-agents
- **Issues**: https://github.com/cnkang/nginx-markdown-for-agents/issues
- **Installation Guide**: See `docs/guides/PACKAGE_INSTALLATION.md`
- **Distribution Guide**: See `docs/guides/PACKAGE_DISTRIBUTION.md`
