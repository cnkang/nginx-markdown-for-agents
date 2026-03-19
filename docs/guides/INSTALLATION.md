# NGINX Markdown Filter Module - Installation Guide

## Table of Contents

1. [Overview](#overview)
2. [Build Requirements](#build-requirements)
3. [Platform-Specific Prerequisites](#platform-specific-prerequisites)
4. [Building the Rust Library](#building-the-rust-library)
5. [Building the NGINX Module](#building-the-nginx-module)
6. [Installation](#installation)
7. [Verification](#verification)
8. [Compatibility Matrix and Install Troubleshooting](#compatibility-matrix-and-install-troubleshooting)
9. [Troubleshooting](#troubleshooting)

---

## Overview

The NGINX Markdown filter module enables AI agents to retrieve web content in Markdown format through HTTP content negotiation. The project consists of two main components:

- **Rust converter**: HTML-to-Markdown conversion library (`libnginx_markdown_converter.a`)
- **NGINX filter module (C)**: NGINX integration layer that invokes the Rust converter via FFI

This guide provides step-by-step instructions for installing the module. The easiest and recommended way is to use the pre-compiled binaries via the installation script. Alternatively, you can build the module from source.

### Setup using Pre-Compiled Binaries (Recommended)

If you are using an official NGINX build (like those from the `nginx` PPA, Alpine `nginx` package, or the official Docker images), you can install the module without compiling anything. 

Run the installation script to automatically detect your NGINX version, OS, and Architecture, and download the correct pre-compiled module:

```bash
curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash
```

Important notes:
- Minimum supported NGINX version is `1.24.0`.
- Pre-compiled dynamic modules are NGINX-version-specific. Your local `nginx -v` version must match exactly.
- If the exact version is not found, the installer will list available pre-built versions grouped by major.minor (for readability), but you still need an exact patch match.

After the script completes, it auto-generates and wires runtime configuration for you:

- Detects `nginx.conf` and modules path from `nginx -V` metadata.
- Creates a main-context include for module snippets when needed (for example `modules-enabled/*.conf`).
- Writes a module loader snippet, using the path style expected by your build:
  - absolute: `load_module /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so;`
  - relative: `load_module modules/ngx_http_markdown_filter_module.so;`
- Enables `markdown_filter on;` automatically (prefer `conf.d` snippet, fallback to `http {}` insertion).
- Runs `nginx -t` and prints a targeted manual-action list only if auto wiring is incomplete.

Then reload NGINX:
```bash
sudo nginx -t && sudo nginx -s reload
```

### Docker (Official NGINX Images + Source Build)

If you want a fully self-contained Docker image that compiles the module from source against the exact official `nginx` image you run, use:

- `examples/docker/Dockerfile.official-nginx-source-build`

This follows the official-image multi-stage pattern:

- start from an official `nginx` image
- install build dependencies in the build stage
- `git clone` this repository inside the image
- compile the module in the build stage
- copy the resulting `.so` into a clean official `nginx` runtime image for functional verification

Platform-specific build notes:

- Alpine-based official images use `nginx-mod-dev`, which provides a matching NGINX source tree in the container.
- Debian-based official images do not currently provide a matching `nginx-dev` package for the official `nginx` image version, so the Dockerfile downloads the exact matching NGINX source tarball only for the build stage.
- In all cases, the runtime and verification container remains the official `nginx` image.

Build examples for the most common official variants:

```bash
# mainline
docker build \
  -f examples/docker/Dockerfile.official-nginx-source-build \
  --build-arg NGINX_IMAGE=nginx:mainline \
  --build-arg MODULE_REPO=https://github.com/cnkang/nginx-markdown-for-agents.git \
  --build-arg MODULE_REF=main \
  -t nginx-markdown:mainline \
  .

# mainline-alpine
docker build \
  -f examples/docker/Dockerfile.official-nginx-source-build \
  --build-arg NGINX_IMAGE=nginx:mainline-alpine \
  --build-arg MODULE_REPO=https://github.com/cnkang/nginx-markdown-for-agents.git \
  --build-arg MODULE_REF=main \
  -t nginx-markdown:mainline-alpine \
  .

# stable
docker build \
  -f examples/docker/Dockerfile.official-nginx-source-build \
  --build-arg NGINX_IMAGE=nginx:stable \
  --build-arg MODULE_REPO=https://github.com/cnkang/nginx-markdown-for-agents.git \
  --build-arg MODULE_REF=main \
  -t nginx-markdown:stable \
  .

# stable-alpine
docker build \
  -f examples/docker/Dockerfile.official-nginx-source-build \
  --build-arg NGINX_IMAGE=nginx:stable-alpine \
  --build-arg MODULE_REPO=https://github.com/cnkang/nginx-markdown-for-agents.git \
  --build-arg MODULE_REF=main \
  -t nginx-markdown:stable-alpine \
  .
```

Run and verify behavior:

```bash
docker run --rm -p 8080:80 nginx-markdown:mainline

# markdown variant
curl -sD - -o /dev/null -H "Accept: text/markdown" http://127.0.0.1:8080/

# html variant remains unchanged
curl -sD - -o /dev/null -H "Accept: text/html" http://127.0.0.1:8080/
```

### Installation from Source

If you use a custom NGINX build, or a platform not supported by the pre-compiled binaries, follow the instructions below to compile from source.

### Scope and Verification Notes

- This guide includes both Rust converter build steps and manual NGINX module compilation steps for custom builds.
- The top-level repository `Makefile` builds the Rust library and generated header, but does **not** currently compile NGINX itself.
- NGINX compilation and installation steps in this document require a local NGINX source tree and platform-specific build dependencies.
- For local development builds and standalone test binaries, see `docs/guides/BUILD_INSTRUCTIONS.md`.

---

## Build Requirements

### Required Software

| Component | Minimum Version | Purpose |
|-----------|----------------|---------|
| **Rust Toolchain** | 1.85.0+ | Building the Rust converter |
| **Cargo** | 1.85.0+ | Rust package manager (included with Rust) |
| **cbindgen** | 0.24.0+ | Generating C header files from Rust |
| **NGINX** | 1.24.0+ | Web server (source code required for module compilation) |
| **GCC/Clang** | GCC 4.8+ or Clang 3.4+ | C compiler for NGINX module |
| **Make** | 3.81+ | Build automation |
| **PCRE** | 8.0+ | Regular expression library (NGINX dependency) |
| **zlib** | 1.2.0+ | Compression library (NGINX dependency) |
| **OpenSSL** | 1.0.2+ | SSL/TLS support (optional, for HTTPS) |

### System Dependencies

**Development Headers Required:**
- PCRE development headers (`pcre-devel` or `libpcre3-dev`)
- zlib development headers (`zlib-devel` or `zlib1g-dev`)
- OpenSSL development headers (`openssl-devel` or `libssl-dev`) - optional

---

## Platform-Specific Prerequisites

### Ubuntu / Debian

```bash
# Update package list
sudo apt-get update

# Install build essentials
sudo apt-get install -y build-essential

# Install NGINX dependencies
sudo apt-get install -y libpcre3 libpcre3-dev zlib1g zlib1g-dev libssl-dev

# Install Rust (if not already installed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# Install cbindgen
cargo install cbindgen

# Download NGINX source (example for 1.24.0)
cd /tmp
wget http://nginx.org/download/nginx-1.24.0.tar.gz
tar -xzf nginx-1.24.0.tar.gz
```

### CentOS / RHEL / Rocky Linux

```bash
# Install development tools
sudo yum groupinstall -y "Development Tools"

# Install NGINX dependencies
sudo yum install -y pcre pcre-devel zlib zlib-devel openssl openssl-devel

# Install Rust (if not already installed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# Install cbindgen
cargo install cbindgen

# Download NGINX source (example for 1.24.0)
cd /tmp
wget http://nginx.org/download/nginx-1.24.0.tar.gz
tar -xzf nginx-1.24.0.tar.gz
```

### macOS

```bash
# Install Xcode Command Line Tools
xcode-select --install

# Install Homebrew (if not already installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install dependencies
brew install pcre openssl

# Install Rust (if not already installed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env

# For Apple Silicon (M1/M2/M3), ensure ARM64 toolchain
rustup target add aarch64-apple-darwin

# Install cbindgen
cargo install cbindgen

# Download NGINX source (example for 1.24.0)
cd /tmp
curl -O http://nginx.org/download/nginx-1.24.0.tar.gz
tar -xzf nginx-1.24.0.tar.gz
```

### Verify Prerequisites

```bash
# Check Rust version
rustc --version  # Should be 1.85.0 or higher

# Check Cargo version
cargo --version

# Check cbindgen installation
cbindgen --version

# Check GCC/Clang version
gcc --version    # or clang --version

# Check Make version
make --version
```

---

## Building the Rust Library

The Rust converter must be built before compiling the NGINX module.

### Step 1: Clone the Repository

```bash
git clone https://github.com/cnkang/nginx-markdown-for-agents.git
cd nginx-markdown-for-agents
```

### Step 2: Build the Rust Library

#### Option A: Using the Makefile (Recommended)

```bash
# Build release version and copy headers (recommended)
make

# Or run the steps separately:
# make rust-lib
# make copy-headers
```

#### Option B: Manual Build

```bash
cd components/rust-converter

# Build release version
cargo build --release

# Generate C header file
cbindgen --config cbindgen.toml --crate nginx-markdown-converter --output include/markdown_converter.h

# Copy header to nginx-module directory
cp include/markdown_converter.h ../nginx-module/src/

cd ..
```

#### Platform-Specific Build Notes

**macOS (Apple Silicon):**
```bash
# Ensure you're building for the correct architecture
cd components/rust-converter
cargo build --target aarch64-apple-darwin --release
cd ..
```

**macOS (Intel):**
```bash
cd components/rust-converter
cargo build --target x86_64-apple-darwin --release
cd ..
```

**Linux (cross-compilation):**
```bash
# For cross-compiling to different architectures
rustup target add x86_64-unknown-linux-gnu
cargo build --target x86_64-unknown-linux-gnu --release
```

### Step 3: Verify Rust Build

```bash
# Check that the library was created (path depends on target architecture)
find components/rust-converter/target -path '*/release/libnginx_markdown_converter.a' -maxdepth 4

# Check that the header was generated
ls -lh components/nginx-module/src/markdown_converter.h

# Expected output:
# -rw-r--r--  1 user  group   XXX KB  libnginx_markdown_converter.a
# -rw-r--r--  1 user  group   XXX KB  markdown_converter.h
```

---

## Building the NGINX Module

### Step 1: Prepare NGINX Source

```bash
# Navigate to NGINX source directory
cd /tmp/nginx-1.24.0

# Set module path (adjust to your actual path)
export MODULE_PATH=/path/to/nginx-markdown-for-agents/components/nginx-module
```

### Step 2: Configure NGINX with the Module

#### As a Dynamic Module (Recommended)

Dynamic modules can be loaded/unloaded without recompiling NGINX.

```bash
./configure \
    --prefix=/usr/local/nginx \
    --with-compat \
    --add-dynamic-module=$MODULE_PATH \
    --with-http_ssl_module \
    --with-pcre
```

#### As a Static Module

Static modules are compiled directly into the NGINX binary.

```bash
./configure \
    --prefix=/usr/local/nginx \
    --add-module=$MODULE_PATH \
    --with-http_ssl_module \
    --with-pcre
```

#### Common Configuration Options

| Option | Description |
|--------|-------------|
| `--prefix=/path` | Installation directory (default: `/usr/local/nginx`) |
| `--with-http_ssl_module` | Enable HTTPS support |
| `--with-pcre` | Enable PCRE for regex support |
| `--with-debug` | Enable debug logging (useful for development) |
| `--with-cc-opt="-O2"` | Additional compiler flags |

**Example with custom paths:**
```bash
./configure \
    --prefix=/opt/nginx \
    --add-dynamic-module=$MODULE_PATH \
    --with-http_ssl_module \
    --with-pcre=/usr/local/pcre \
    --with-zlib=/usr/local/zlib \
    --with-openssl=/usr/local/openssl
```

### Step 3: Compile NGINX

```bash
# Compile (use a parallelism value appropriate for your platform)
# Linux example:
make -j$(nproc)

# macOS example:
# make -j$(sysctl -n hw.ncpu)

# Expected output should include:
# - Compilation of NGINX core
# - Compilation of markdown filter module sources
# - Linking with libnginx_markdown_converter.a
```

### Step 4: Verify Module Compilation

```bash
# For dynamic modules, check that the .so file was created
ls -lh objs/ngx_http_markdown_filter_module.so

# For static modules, check that NGINX binary was created
ls -lh objs/nginx
```

---

## Installation

### Step 1: Install NGINX

```bash
# From the NGINX source directory
sudo make install
```

This installs NGINX to the configured prefix (default: `/usr/local/nginx`).

### Step 2: Install Dynamic Module (if applicable)

```bash
# Copy the module to NGINX modules directory
sudo mkdir -p /usr/local/nginx/modules
sudo cp objs/ngx_http_markdown_filter_module.so /usr/local/nginx/modules/
```

### Step 3: Configure NGINX

Create or edit `/usr/local/nginx/conf/nginx.conf`:

#### For Dynamic Module

```nginx
# Load the module at the top of nginx.conf
load_module modules/ngx_http_markdown_filter_module.so;

http {
    # Enable markdown filter globally
    markdown_filter on;
    
    # Configure resource limits
    markdown_max_size 10m;
    markdown_timeout 5s;
    
    # Configure failure strategy
    markdown_on_error pass;  # fail-open (return original HTML on error)
    
    server {
        listen 80;
        server_name example.com;
        
        location / {
            # Proxy to backend
            proxy_pass http://backend;
            
            # Markdown filter is enabled (inherited from http block)
        }
        
        location /api {
            proxy_pass http://backend;
            
            # Disable markdown filter for API endpoints
            markdown_filter off;
        }
    }
}
```

#### For Static Module

```nginx
http {
    # Enable markdown filter globally
    markdown_filter on;
    
    # Configure resource limits
    markdown_max_size 10m;
    markdown_timeout 5s;
    
    server {
        listen 80;
        server_name example.com;
        
        location / {
            proxy_pass http://backend;
        }
    }
}
```

### Step 4: Test Configuration

```bash
# Test NGINX configuration syntax
sudo /usr/local/nginx/sbin/nginx -t

# Expected output:
# nginx: the configuration file /usr/local/nginx/conf/nginx.conf syntax is ok
# nginx: configuration file /usr/local/nginx/conf/nginx.conf test is successful
```

### Step 5: Start NGINX

```bash
# Start NGINX
sudo /usr/local/nginx/sbin/nginx

# Or reload if already running
sudo /usr/local/nginx/sbin/nginx -s reload
```

---

## Verification

### Step 1: Check Module Loading

```bash
# Check NGINX error log for module initialization
sudo tail -f /usr/local/nginx/logs/error.log

# Expected log entries (exact wording may vary by log level/build):
# [info] markdown filter: converter initialized in worker process (pid: ...)
```

### Step 2: Test Markdown Conversion

```bash
# Test with curl (requesting Markdown)
curl -H "Accept: text/markdown" http://localhost/

# Header-focused verification (GET-based; preferred over curl -I in proxied setups)
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/

# Expected response headers:
# Content-Type includes text/markdown (often with charset=utf-8)
# Vary includes Accept (may also include Accept-Encoding when compression is enabled)

# Test with curl (requesting HTML)
curl -H "Accept: text/html" http://localhost/

# Expected response headers:
# Content-Type: text/html
```

Note:
- Prefer GET-based header checks (`curl -sD - -o /dev/null ...`) for verification.
- `curl -I` / `HEAD` can be misleading in some proxy deployments because the upstream `HEAD` response has no body to convert.

### Step 3: Verify Conversion

```bash
# Request a page and check the output format
curl -H "Accept: text/markdown" http://localhost/ | head -20

# Expected output should be Markdown format:
# # Page Title
#
# This is a paragraph with [a link](http://example.com).
#
# ## Section Heading
# ...
```

### Step 4: Check Metrics (if enabled)

```bash
# If a metrics endpoint location is configured (example path)
curl http://localhost/markdown-metrics

# JSON output is also supported
curl -H "Accept: application/json" http://localhost/markdown-metrics

# Look for markdown filter metrics:
# conversions_attempted: 10
# conversions_succeeded: 9
# conversions_failed: 1
# conversions_bypassed: 2
```

---

## Compatibility Matrix and Install Troubleshooting

This section covers pre-built binary compatibility and common install-script failure scenarios. For source-build issues, see [Troubleshooting](#troubleshooting) below.

> **Authoritative source:** The canonical platform support matrix is maintained in [`tools/release-matrix.json`](../../tools/release-matrix.json). The table below is a human-readable snapshot; always consult the JSON file for automation and CI.

> **Supported baseline:** Pre-built binary support starts at NGINX `1.24.0`. Older versions are intentionally out of scope due to implementation differences in the dynamic module ABI. Versions `>= 1.24.0` that are not listed in the matrix can be built from source.

### Platform Compatibility Matrix

<!-- BEGIN AUTO-GENERATED MATRIX -->
| NGINX Version | OS Type | Architecture | Support Tier |
|---------------|---------|--------------|--------------|
| 1.24.0 | glibc | aarch64 | Full |
| 1.24.0 | glibc | x86_64 | Full |
| 1.24.0 | musl | aarch64 | Full |
| 1.24.0 | musl | x86_64 | Full |
| 1.26.3 | glibc | aarch64 | Full |
| 1.26.3 | glibc | x86_64 | Full |
| 1.26.3 | musl | aarch64 | Full |
| 1.26.3 | musl | x86_64 | Full |
| 1.28.2 | glibc | aarch64 | Full |
| 1.28.2 | glibc | x86_64 | Full |
| 1.28.2 | musl | aarch64 | Full |
| 1.28.2 | musl | x86_64 | Full |
| 1.29.6 | glibc | aarch64 | Full |
| 1.29.6 | glibc | x86_64 | Full |
| 1.29.6 | musl | aarch64 | Full |
| 1.29.6 | musl | x86_64 | Full |
<!-- END AUTO-GENERATED MATRIX -->

**Support Tiers:**

- **Full** — Pre-built binary available, install script supported, CI-verified.
- **Source Only** — No pre-built binary; build from source using the instructions in this guide.

### Standard Operating Procedures (SOPs) for Install Failures

The following SOPs cover the most common failure scenarios when using the install script (`tools/install.sh`) to install pre-built binaries.

---

#### SOP 1: Version Mismatch

**Symptom:**

```
[ERROR] version_mismatch: No pre-built module found for NGINX X.Y.Z
```

**Root Cause:**

NGINX dynamic modules are compiled against a specific NGINX version and are not binary-compatible across versions. The install script requires an exact patch-version match (e.g., `1.26.3`, not just `1.26.x`).

**Resolution Steps:**

1. Check your installed NGINX version:
   ```bash
   nginx -v
   ```
2. Compare against the [Platform Compatibility Matrix](#platform-compatibility-matrix) above (or `tools/release-matrix.json`).
3. If a pre-built binary exists for a different patch version, upgrade or downgrade NGINX to a supported version:
   ```bash
   # Example: install a specific NGINX version on Ubuntu
   sudo apt-get install nginx=1.26.3-1~jammy
   ```
4. If no pre-built binary is available for your version, build from source:
   ```bash
   # See "Installation from Source" section in this guide
   ```
5. Re-run the install script after switching versions:
   ```bash
   curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash
   ```

---

#### SOP 2: Architecture Not Supported

**Symptom:**

```
[ERROR] arch_unsupported: Unsupported architecture: <arch>
```

**Root Cause:**

Pre-built binaries are only available for `x86_64` (amd64) and `aarch64` (arm64). Other architectures (e.g., `armv7l`, `s390x`, `ppc64le`) are not supported with pre-built binaries.

**Resolution Steps:**

1. Verify your system architecture:
   ```bash
   uname -m
   ```
2. If the output is not `x86_64` or `aarch64`, you must build from source. Follow the [Installation from Source](#installation-from-source) section.
3. If you are running inside a container or VM, ensure the architecture matches the host or that QEMU user-mode emulation is configured.

---

#### SOP 3: libc Incompatibility

**Symptom:**

The module appears to install, but NGINX fails to start or `nginx -t` reports symbol errors such as:

```
nginx: [emerg] dlopen() ... failed (... undefined symbol: ...)
```

Or the module loads but crashes at runtime with segmentation faults.

**Root Cause:**

A binary built against glibc was installed on a musl-based system (e.g., Alpine Linux), or vice versa. glibc and musl binaries are not interchangeable.

**Resolution Steps:**

1. Determine your system's libc type:
   ```bash
   # Check if the system uses musl
   ldd --version 2>&1 | head -1
   # musl systems typically show "musl libc" in the output

   # Alternative: inspect the dynamic linker
   file /bin/sh
   # Look for "dynamically linked" and the interpreter path
   # musl: /lib/ld-musl-*.so.1
   # glibc: /lib64/ld-linux-*.so.2 or /lib/x86_64-linux-gnu/ld-linux-*.so.2
   ```
2. Verify the installed module matches your libc:
   ```bash
   # Check what the module is linked against
   ldd /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so
   ```
3. If there is a mismatch, re-run the install script — it auto-detects the OS type. If auto-detection failed, report the issue and build from source as a workaround.
4. On Alpine Linux (musl), ensure you are using the `musl` variant of the pre-built binary. On Debian/Ubuntu/RHEL (glibc), ensure you are using the `glibc` variant.

---

#### SOP 4: Network Download Failure

**Symptom:**

```
[ERROR] network: Failed to download <url>
```

**Root Cause:**

The install script downloads pre-built binaries from GitHub Releases. Failures can be caused by:
- No internet connectivity or DNS resolution failure
- Corporate firewall or proxy blocking GitHub
- GitHub API rate limiting (60 requests/hour for unauthenticated requests)
- Temporary GitHub outage

**Resolution Steps:**

1. Verify network connectivity to GitHub:
   ```bash
   curl -I https://api.github.com/
   ```
2. If behind a proxy, configure the environment:
   ```bash
   export https_proxy=http://your-proxy:port
   ```
3. If rate-limited, wait or authenticate:
   ```bash
   # Check remaining rate limit
   curl -s https://api.github.com/rate_limit | grep -A 2 '"core"'
   ```
4. As a workaround, manually download the binary from the [GitHub Releases page](https://github.com/cnkang/nginx-markdown-for-agents/releases) and place it in the NGINX modules directory:
   ```bash
   # Example: manual download and install
   wget https://github.com/cnkang/nginx-markdown-for-agents/releases/download/<tag>/<asset-name>.so \
     -O /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so
   ```
5. Re-run the install script after connectivity is restored:
   ```bash
   curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash
   ```

---

#### SOP 5: Checksum Verification Failure

**Symptom:**

```
[ERROR] checksum: Checksum verification failed
```

**Root Cause:**

The SHA-256 checksum of the downloaded binary does not match the expected value published in the release. Possible causes:
- Corrupted download (partial transfer, network interruption)
- Man-in-the-middle (MITM) attack or content tampering
- Wrong file downloaded (e.g., mismatched version or platform)

**Resolution Steps:**

1. Re-download the binary — transient network issues are the most common cause:
   ```bash
   curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash
   ```
2. If the failure persists, manually verify the checksum:
   ```bash
   # Download the binary and checksum file
   wget https://github.com/cnkang/nginx-markdown-for-agents/releases/download/<tag>/<asset-name>.so
   wget https://github.com/cnkang/nginx-markdown-for-agents/releases/download/<tag>/checksums.txt

   # Verify
   sha256sum -c checksums.txt
   ```
3. Compare the expected checksum from the release page with the actual checksum of your downloaded file:
   ```bash
   sha256sum <downloaded-file>.so
   ```
4. If checksums consistently do not match, this may indicate a security issue. Do not install the binary. Report the issue to the project maintainers.

---

## Troubleshooting

### Build Issues

#### Issue: "markdown_converter.h: No such file or directory"

**Cause:** C header file not generated or not copied to nginx-module directory.

**Solution:**
```bash
cd components/rust-converter
cbindgen --config cbindgen.toml --crate nginx-markdown-converter --output include/markdown_converter.h
cp include/markdown_converter.h ../nginx-module/src/
```

#### Issue: "undefined reference to markdown_converter_new"

**Cause:** Rust library not linked correctly or not built.

**Solution:**
```bash
# Rebuild Rust library
cd components/rust-converter
cargo clean
cargo build --release

# Verify library exists (manual builds may use target/release; Makefile builds use target/<triple>/release)
find target -path '*/release/libnginx_markdown_converter.a' -maxdepth 4

# Reconfigure and rebuild NGINX
cd /tmp/nginx-1.24.0
make clean
./configure --add-dynamic-module=$MODULE_PATH
make
```

#### Issue: Architecture Mismatch (macOS)

**Error:** `ld: warning: ignoring file ... building for macOS-arm64 but attempting to link with file built for macOS-x86_64`

**Cause:** Rust library built for wrong architecture.

**Solution:**
```bash
# Check your architecture
uname -m  # arm64 or x86_64

# For Apple Silicon (M1/M2/M3)
cd components/rust-converter
cargo build --target aarch64-apple-darwin --release

# For Intel Macs
cd components/rust-converter
cargo build --target x86_64-apple-darwin --release
```

#### Issue: "PCRE library not found"

**Cause:** PCRE development headers not installed.

**Solution:**
```bash
# Ubuntu/Debian
sudo apt-get install libpcre3-dev

# CentOS/RHEL
sudo yum install pcre-devel

# macOS
brew install pcre
```

#### Issue: Rust Compilation Fails

**Error:** `error: failed to compile nginx-markdown-converter`

**Solution:**
```bash
# Update Rust toolchain
rustup update

# Check Rust version (must be 1.70.0+)
rustc --version

# Clean and rebuild
cd components/rust-converter
cargo clean
cargo build --release --verbose
```

### Runtime Issues

#### Issue: Module Not Loading

**Symptom:** NGINX starts but module doesn't work.

**Diagnosis:**
```bash
# Check NGINX error log
sudo tail -100 /usr/local/nginx/logs/error.log

# Check if module is loaded (for dynamic modules)
sudo /usr/local/nginx/sbin/nginx -V 2>&1 | grep markdown
```

**Solution:**
```bash
# For dynamic modules, ensure load_module directive is present
# and path is correct in nginx.conf
load_module modules/ngx_http_markdown_filter_module.so;

# Verify module file exists
ls -lh /usr/local/nginx/modules/ngx_http_markdown_filter_module.so

# Reload NGINX
sudo /usr/local/nginx/sbin/nginx -s reload
```

#### Issue: Conversion Not Happening

**Symptom:** Requests return HTML instead of Markdown.

**Diagnosis:**
```bash
# Test with explicit Accept header
curl -v -H "Accept: text/markdown" http://localhost/

# Check response headers
# Look for: Content-Type: text/markdown
```

**Possible Causes:**

1. **Module disabled in configuration**
   ```nginx
   # Check nginx.conf
   markdown_filter on;  # Must be 'on'
   ```

2. **Accept header not sent**
   ```bash
   # Ensure Accept header includes text/markdown
   curl -H "Accept: text/markdown" http://localhost/
   ```

3. **Response not eligible for conversion**
   - Status code must be 200
   - Content-Type must be text/html
   - Response size must be within limits

4. **Check NGINX error log for details**
   ```bash
   sudo tail -f /usr/local/nginx/logs/error.log
   ```

#### Issue: Conversion Timeout

**Symptom:** Requests fail or return original HTML.

**Error Log:** `markdown filter: conversion timeout exceeded`

**Solution:**
```nginx
# Increase timeout in nginx.conf
markdown_timeout 10s;  # Increase from default 5s

# Or increase max size if large pages are timing out
markdown_max_size 20m;  # Increase from default 10m
```

#### Issue: High Memory Usage

**Symptom:** NGINX worker processes consuming excessive memory.

**Solution:**
```nginx
# Reduce max response size
markdown_max_size 5m;  # Reduce from default 10m

# Disable conversion for large pages
location /large-content {
    markdown_filter off;
}
```

### Configuration Issues

#### Issue: "unknown directive markdown_filter"

**Cause:** Module not loaded or not compiled correctly.

**Solution:**
```bash
# For dynamic modules, ensure load_module is present
load_module modules/ngx_http_markdown_filter_module.so;

# For static modules, verify module was compiled
/usr/local/nginx/sbin/nginx -V 2>&1 | grep markdown

# Rebuild if necessary
```

#### Issue: Configuration Test Fails

**Error:** `nginx: configuration file test failed`

**Solution:**
```bash
# Run configuration test with verbose output
sudo /usr/local/nginx/sbin/nginx -t

# Check error log for specific error
sudo tail -50 /usr/local/nginx/logs/error.log

# Common issues:
# - Syntax errors in nginx.conf
# - Invalid directive values
# - Missing semicolons
```

### Performance Issues

#### Issue: Slow Response Times

**Diagnosis:**
```bash
# Enable debug logging
# In nginx.conf:
error_log /usr/local/nginx/logs/error.log debug;

# Check conversion times in logs
sudo grep "markdown filter: conversion" /usr/local/nginx/logs/error.log
```

**Solutions:**

1. **Optimize resource limits**
   ```nginx
   markdown_max_size 5m;      # Reduce if processing large pages
   markdown_timeout 3s;        # Reduce timeout
   ```

2. **Disable for specific paths**
   ```nginx
   location /heavy-pages {
       markdown_filter off;
   }
   ```

3. **Use fail-open strategy**
   ```nginx
   markdown_on_error pass;  # Return original HTML on timeout
   ```

### Getting Help

If you encounter issues not covered in this guide:

1. **Check the error log:** `/usr/local/nginx/logs/error.log`
2. **Enable debug logging:** `error_log /path/to/log debug;`
3. **Review configuration:** `nginx -t -c /path/to/nginx.conf`
4. **Check your repository issue tracker:** use the issue tracker configured for your deployment/fork
5. **Consult the maintained guides:** see `../../README.md`, `../README.md`, and the documentation under `docs/`

---

## Next Steps

After successful installation:

1. **Configure for your use case:** See [Configuration Guide](CONFIGURATION.md)
2. **Monitor performance:** Set up metrics collection
3. **Tune resource limits:** Adjust based on your traffic patterns
4. **Enable optional features:** Token estimation, YAML front matter, etc.

For production deployments, consider:
- Setting up monitoring and alerting
- Configuring log rotation
- Implementing rate limiting
- Testing failover scenarios
- Documenting your specific configuration

---

## Additional Resources

- **Project README:** [../../README.md](../../README.md)
- **Documentation Index:** [../README.md](../README.md)
- **Requirements Traceability:** [../project/PROJECT_STATUS.md](../project/PROJECT_STATUS.md)
- **Architecture Index:** [../architecture/README.md](../architecture/README.md)
- **System Architecture:** [../architecture/SYSTEM_ARCHITECTURE.md](../architecture/SYSTEM_ARCHITECTURE.md)
- **Build Instructions (macOS):** [BUILD_INSTRUCTIONS.md](BUILD_INSTRUCTIONS.md)
- **NGINX Documentation:** https://nginx.org/en/docs/
- **Rust Documentation:** https://doc.rust-lang.org/
