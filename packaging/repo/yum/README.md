# nginx-markdown-for-agents YUM/DNF Repository

This is the YUM/DNF package repository for `nginx-module-markdown`, an NGINX
dynamic filter module that serves Markdown to AI agents while keeping HTML for
normal clients.

> **Note**: This is a self-hosted, unofficial repository. It is not part of
> Fedora, EPEL, AlmaLinux, Amazon Linux, or any other official distribution
> archive.

---

## Quick Start

### 1. Import the GPG signing key

```bash
sudo rpm --import https://pkg.example.com/nginx-markdown/gpg.key
```

### 2. Add the repository

Create a repo file at `/etc/yum.repos.d/nginx-markdown.repo`:

```ini
[nginx-markdown]
name=nginx-markdown-for-agents
baseurl=https://pkg.example.com/nginx-markdown/yum/
enabled=1
gpgcheck=1
gpgkey=https://pkg.example.com/nginx-markdown/gpg.key
repo_gpgcheck=1
sslverify=1
```

Or use `yum-config-manager` / `dnf config-manager`:

```bash
# RHEL/CentOS/AlmaLinux
sudo yum-config-manager --add-repo \
    https://pkg.example.com/nginx-markdown/yum/nginx-markdown.repo

# Fedora / DNF-based
sudo dnf config-manager --add-repo \
    https://pkg.example.com/nginx-markdown/yum/nginx-markdown.repo
```

### 3. Install the module

```bash
# YUM (RHEL/CentOS/AlmaLinux/Amazon Linux)
sudo yum install nginx-module-markdown

# DNF (Fedora)
sudo dnf install nginx-module-markdown
```

### 4. Enable the module

After installation, add the `load_module` directive to the **main context**
(top-level) of your NGINX configuration — it must appear before any `http`
block:

```bash
# Add load_module directive to the main context of nginx.conf:
sudo sed -i '1i load_module /usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so;' \
    /etc/nginx/nginx.conf

# Test and reload
sudo nginx -t && sudo systemctl reload nginx
```

> **Note:** The `load_module` directive is only valid in the main context.
> Placing it inside a `conf.d/` fragment that is included within the `http`
> block will cause an NGINX configuration error.

### 5. Verify

```bash
# Check module is loaded
nginx -V 2>&1 | grep -o 'markdown'

# Test conversion
curl -H "Accept: text/markdown" http://localhost/
```

---

## Available Packages

| Package | Description |
|---------|-------------|
| `nginx-module-markdown` | NGINX dynamic module for HTML-to-Markdown conversion |

Each package is built for a specific NGINX ABI version. The RPM dependency
system ensures you can only install packages compatible with your NGINX build.

---

## Supported Platforms

| Distribution | Versions | Architectures |
|-------------|----------|---------------|
| AlmaLinux | 9 | x86_64, aarch64 |
| Amazon Linux | 2023 | x86_64, aarch64 |
| RHEL | 9 | x86_64, aarch64 |
| Fedora | Latest | x86_64, aarch64 |

---

## Repository Structure

```
repo/yum/
├── packages/
│   └── *.rpm
├── repodata/
│   ├── repomd.xml
│   ├── repomd.xml.asc
│   ├── primary.xml.gz
│   ├── filelists.xml.gz
│   └── other.xml.gz
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

```bash
sudo rpm --import https://pkg.example.com/nginx-markdown/gpg.key
```

### Verifying Package Signatures

```bash
# Check a single package signature
rpm -K nginx-module-markdown-*.rpm

# Verify the repomd.xml signature
gpg --verify repodata/repomd.xml.asc repodata/repomd.xml
```

### Verifying Key Import

```bash
# List imported RPM GPG keys
rpm -qa gpg-pubkey*

# Show details of an imported key
rpm -qi gpg-pubkey-XXXXXXXX-YYYYYYYY
```

---

## Key Rotation

When the signing key is rotated:

1. The new key is published at the same URL (`gpg.key`)
2. A transition period allows both old and new keys to verify packages
3. Release announcements include the new key fingerprint
4. Users should re-import the key:

```bash
sudo rpm --import https://pkg.example.com/nginx-markdown/gpg.key
```

5. Old packages remain verifiable with the old key during the transition
6. After the transition period, only the new key is used for signing

Key rotation is documented in the project's
[PACKAGE_DISTRIBUTION.md](../../docs/guides/PACKAGE_DISTRIBUTION.md) guide.

---

## NGINX ABI Compatibility

The packages in this repository are built against specific NGINX versions.
Each RPM declares a `Requires: nginx-abi-X.Y` dependency to prevent
installation on incompatible NGINX builds.

If you encounter dependency errors:

1. Verify your NGINX version: `nginx -V`
2. Check the package requires a matching ABI version:
   ```bash
   rpm -qpR nginx-module-markdown-*.rpm | grep nginx-abi
   ```
3. See the [troubleshooting guide](../../docs/guides/PACKAGE_INSTALLATION.md)

---

## Troubleshooting

### "Public key not installed" error

```bash
# Import the signing key
sudo rpm --import https://pkg.example.com/nginx-markdown/gpg.key

# Retry installation
sudo yum install nginx-module-markdown
```

### "Signature verification failed" error

This may indicate the package was tampered with or the key is outdated:

```bash
# Re-import the latest key
sudo rpm --import https://pkg.example.com/nginx-markdown/gpg.key

# Clean YUM cache and retry
sudo yum clean all
sudo yum install nginx-module-markdown
```

### "Nothing provides nginx-abi-X.Y" dependency error

The module requires a specific NGINX ABI version. Install the matching
NGINX version from the official NGINX repository:

```bash
# Add official NGINX repository first
sudo yum install -y https://nginx.org/packages/mainline/centos/9/x86_64/RPMS/nginx-*.rpm

# Then retry module installation
sudo yum install nginx-module-markdown
```

### Repository metadata errors

```bash
# Clean cached metadata
sudo yum clean metadata
sudo yum makecache

# Or force a full cache rebuild
sudo yum clean all
sudo yum makecache
```

---

## Building from Source

If pre-built packages are not available for your platform, see the
[build instructions](https://github.com/cnkang/nginx-markdown-for-agents)
in the project repository.

---

## Security

- All RPM packages are signed with GPG (`rpm --addsign`)
- Repository metadata (`repomd.xml`) is signed (`repomd.xml.asc`)
- The signing key is distributed over HTTPS only
- `repo_gpgcheck=1` in the repo config verifies metadata signatures
- `gpgcheck=1` verifies individual package signatures
- Report security issues via the project's security policy

---

## Links

- **Project**: https://github.com/cnkang/nginx-markdown-for-agents
- **Issues**: https://github.com/cnkang/nginx-markdown-for-agents/issues
- **Installation Guide**: See `docs/guides/PACKAGE_INSTALLATION.md`
- **Distribution Guide**: See `docs/guides/PACKAGE_DISTRIBUTION.md`
