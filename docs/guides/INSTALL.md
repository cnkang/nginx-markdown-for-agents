# Prebuilt Package Installation Guide

## Overview

This guide covers installing the nginx-markdown-for-agents module from
prebuilt `.deb` or `.rpm` packages. Prebuilt packages are available for
selected nginx.org stable releases on Linux amd64 and arm64 architectures.

> **Important:** Prebuilt packages are compiled against a specific nginx.org
> stable version. They are NOT universal shared libraries. NGINX validates
> the module binary compatibility signature at load time, and loading will
> fail if the signatures do not match. See
> [COMPATIBILITY.md](../COMPATIBILITY.md) for details.

For other installation methods (install script, Docker source build, manual
source build, Homebrew), see [INSTALLATION.md](./INSTALLATION.md).

---

## Choosing the Correct Package

Each prebuilt package targets a specific combination of:

- **NGINX version** (exact nginx.org stable release, e.g., `1.26.3`)
- **Architecture** (`amd64`/`x86_64` or `arm64`/`aarch64`)

### Determine Your Environment

```bash
# NGINX version
nginx -v 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+'

# Architecture
uname -m
# x86_64 → use amd64 packages
# aarch64 → use arm64 packages
```

### Package Naming Convention

| Format | Pattern | Example |
|--------|---------|---------|
| DEB | `nginx-module-markdown-for-agents_{version}_nginx-{nginx_version}_{arch}.deb` | `nginx-module-markdown-for-agents_0.7.0_nginx-1.26.3_amd64.deb` |
| RPM | `nginx-module-markdown-for-agents-{version}-nginx{nginx_version}-1.{arch}.rpm` | `nginx-module-markdown-for-agents-0.7.0-nginx1.26.3-1.x86_64.rpm` |

Select the package that matches your exact NGINX version and architecture.
Using a package built for a different NGINX version will result in a binary
compatibility error at load time.

---

## Installation Steps

### DEB-based Systems (Ubuntu, Debian)

```bash
# Install the package
sudo dpkg -i nginx-module-markdown-for-agents_0.7.0_nginx-1.26.3_amd64.deb
```

### RPM-based Systems (RHEL, AlmaLinux, Amazon Linux)

```bash
# Install the package
sudo rpm -i nginx-module-markdown-for-agents-0.7.0-nginx1.26.3-1.x86_64.rpm
```

The package installs:

- DEB module binary: `/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so`
- RPM module binary: `/usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so`
- Documentation: `/usr/share/doc/nginx-markdown-for-agents/`
- License: `/usr/share/licenses/nginx-markdown-for-agents/LICENSE`

The package does **not** modify `nginx.conf`, reload NGINX, or restart NGINX.
You must enable the module manually (see next section).

---

## Enabling the Module

### Step 1: Add load_module Directive

Edit your `nginx.conf` and add the following line at the **top level** (before
the `http {}` block):

```nginx
# DEB packages:
load_module /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so;

# RPM packages:
load_module /usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so;
```

### Step 2: Verify Configuration

Test that the configuration is syntactically valid and the module loads:

```bash
sudo nginx -t
```

Expected output:

```text
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

If you see a binary compatibility error, your NGINX version does not match the
package target version. See [Troubleshooting](#troubleshooting) below.

### Step 3: Reload NGINX

Apply the configuration change without downtime:

```bash
sudo systemctl reload nginx
```

---

## Verifying Module Loading

After reloading, confirm the module is active by requesting content with the
`Accept: text/markdown` header:

```bash
curl -sD - -H "Accept: text/markdown" http://localhost/
```

Expected response headers:

```text
HTTP/1.1 200 OK
Content-Type: text/markdown; charset=utf-8
Vary: Accept
```

If the response returns `Content-Type: text/markdown`, the module is loaded and
converting HTML to Markdown for agent clients.

---

## Troubleshooting

### Binary Compatibility Error

**Symptom:**

```text
nginx: [emerg] module "/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so" is not binary compatible
```

**Cause:** The installed package was built for a different NGINX version than
the one running on your system.

**Resolution:**

1. Check your NGINX version: `nginx -v`
2. Download the package matching your exact NGINX version
3. Reinstall with the correct package

### Module Not Found

**Symptom:**

```text
nginx: [emerg] dlopen() "/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so" failed
```

**Cause:** The module file is missing or the path is incorrect.

**Resolution:**

1. Verify the file exists: `ls -l /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so`
2. If missing, reinstall the package
3. If your NGINX uses a non-standard modules path, use the full path in the
   `load_module` directive

### Unsupported NGINX Version

If your NGINX version is not available as a prebuilt package, you have two
options:

1. **Upgrade NGINX** to a supported nginx.org stable release
2. **Build from source** against your local NGINX source tree (see
   [Manual Source Build](./INSTALLATION.md#6-secondary-manual-source-build))

For the full list of supported versions and architectures, see the
[Compatibility Matrix](./INSTALLATION.md#7-compatibility-matrix).

For Kubernetes and Helm deployments, see
[`KUBERNETES_DEPLOYMENT.md`](./KUBERNETES_DEPLOYMENT.md); the Helm chart runs
with stock NGINX by default and requires an explicit module-enabled image plus
`markdown.loadModule` when markdown directives are enabled.
