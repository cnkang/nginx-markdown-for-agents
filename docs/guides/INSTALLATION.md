# NGINX Markdown Filter Module — Installation Guide

## Table of Contents

1. [Overview](#1-overview)
2. [Shortest Success Path](#2-shortest-success-path)
3. [Install Path Tiers](#3-install-path-tiers)
4. [Primary: Install Script](#4-primary-install-script)
5. [Secondary: Docker Source Build](#5-secondary-docker-source-build)
6. [Secondary: Manual Source Build](#6-secondary-manual-source-build)
7. [Compatibility Matrix](#7-compatibility-matrix)
8. [Release Artifact Naming](#8-release-artifact-naming)
9. [Operator Verification](#9-operator-verification)
10. [Troubleshooting](#10-troubleshooting)
11. [Environment-Specific Notes](#11-environment-specific-notes)

---

## 1. Overview

The NGINX Markdown filter module enables AI agents to retrieve web content in Markdown format through HTTP content negotiation. When a client sends `Accept: text/markdown`, the module converts the upstream HTML response to Markdown on the fly. Browsers and normal clients continue to receive HTML unchanged.

The project consists of two main components:

- **Rust converter**: HTML-to-Markdown conversion library (`libnginx_markdown_converter.a`)
- **NGINX filter module (C)**: NGINX integration layer that invokes the Rust converter via FFI

This guide covers every supported installation method — from a single-command install script to a full source build — along with platform compatibility, verification procedures, troubleshooting, and environment-specific notes.

---

## 2. Shortest Success Path

For a system with NGINX already installed (official build), four commands get you to a verified conversion:

```bash
# Step 1: Install the module (auto-detects version, downloads binary, wires config)
curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash

# Step 2: Reload NGINX
sudo nginx -t && sudo nginx -s reload

# Step 3: Verify — request the default welcome page as Markdown
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/

# Step 4: Confirm HTML passthrough is preserved
curl -sD - -o /dev/null -H "Accept: text/html" http://localhost/
```

Expected output for Step 3:

```http
HTTP/1.1 200 OK
Content-Type: text/markdown; charset=utf-8
Vary: Accept
...
```

Expected output for Step 4:

```http
HTTP/1.1 200 OK
Content-Type: text/html
...
```

The install script auto-enables `markdown_filter on` and wires the `load_module` directive, so no manual configuration editing is required. The NGINX default welcome page (`/usr/share/nginx/html/index.html`) serves as the demo content source — no upstream or proxy configuration needed.

> **Note:** If you need a standalone demo configuration file, see [`examples/nginx-configs/00-minimal-demo.conf`](../../examples/nginx-configs/00-minimal-demo.conf).

---

## 3. Install Path Tiers

Each installation method is classified into a tier that sets expectations for friction and support level.

| Tier | Meaning | CI-Verified | Example |
|------|---------|-------------|---------|
| **Primary** | Recommended, lowest friction | Yes | `tools/install.sh` |
| **Secondary** | Supported, more steps required | Yes | Docker source build, manual source build |
| **Convenience** | Available, not officially recommended | No | Community-maintained methods |

- **Primary** — Recommended for most users. Pre-built binary, automated configuration, CI-verified across the full platform matrix.
- **Secondary** — Fully supported but requires more manual steps. Use when the primary path does not cover your platform or you need a custom NGINX build.
- **Convenience** — Community-contributed methods that are not part of the project's CI pipeline. Use at your own discretion.

---

## 4. Primary: Install Script

**Tier: Primary**

The install script (`tools/install.sh`) is the recommended installation method for users running official NGINX builds.

### Usage

```bash
curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash
```

The script requires `sudo` (root privileges) to write the module binary and modify NGINX configuration files.

### Auto-Wiring Behavior

The install script performs the following automatically:

1. Detects your NGINX version, OS type (glibc/musl), and architecture from `nginx -V` metadata.
2. Downloads the correct pre-built binary from GitHub Releases.
3. Verifies the SHA-256 checksum of the downloaded binary.
4. Copies the `.so` file to the NGINX modules directory.
5. Wires the `load_module` directive into the NGINX configuration:
   - Absolute path: `load_module /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so;`
   - Relative path: `load_module modules/ngx_http_markdown_filter_module.so;`
6. Enables `markdown_filter on;` automatically (prefers `conf.d` snippet, falls back to `http {}` insertion).
7. Runs `nginx -t` and prints a targeted manual-action list only if auto-wiring is incomplete.

After the script completes, reload NGINX:

```bash
sudo nginx -t && sudo nginx -s reload
```

### When to Use a Different Method

- Your NGINX version is not in the [Compatibility Matrix](#7-compatibility-matrix) → use [Manual Source Build](#6-secondary-manual-source-build)
- You need a fully self-contained Docker image → use [Docker Source Build](#5-secondary-docker-source-build)
- You are on macOS → use [Manual Source Build](#6-secondary-manual-source-build) (no pre-built macOS binaries)

---

## 5. Secondary: Docker Source Build

**Tier: Secondary**

For a fully self-contained Docker image that compiles the module from source against the exact official `nginx` image you run, use the provided multi-stage Dockerfile:

- [`examples/docker/Dockerfile.official-nginx-source-build`](../../examples/docker/Dockerfile.official-nginx-source-build)

This follows the official-image multi-stage pattern:

1. Start from an official `nginx` image.
2. Install build dependencies in the build stage.
3. Clone this repository inside the image.
4. Compile the module in the build stage.
5. Copy the resulting `.so` into a clean official `nginx` runtime image.

### Platform-Specific Build Notes

- **Alpine-based** official images use `nginx-mod-dev`, which provides a matching NGINX source tree in the container.
- **Debian-based** official images do not currently provide a matching `nginx-dev` package, so the Dockerfile downloads the exact matching NGINX source tarball for the build stage only.
- In all cases, the runtime container remains the unmodified official `nginx` image.

### Build Examples

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

### Run and Verify

```bash
docker run --rm -p 8080:80 nginx-markdown:mainline

# Markdown conversion
curl -sD - -o /dev/null -H "Accept: text/markdown" http://127.0.0.1:8080/

# HTML passthrough (unchanged)
curl -sD - -o /dev/null -H "Accept: text/html" http://127.0.0.1:8080/
```

---

## 6. Secondary: Manual Source Build

**Tier: Secondary**

If you use a custom NGINX build, or a platform not supported by the pre-built binaries, compile the module from source. This section covers the Rust library build, NGINX module compilation, and platform prerequisites.

### Scope and Verification Notes

- This section includes both Rust converter build steps and manual NGINX module compilation steps.
- The top-level repository `Makefile` builds the Rust library and generated header, but does **not** compile NGINX itself.
- NGINX compilation requires a local NGINX source tree and platform-specific build dependencies.
- For local development builds and standalone test binaries, see `docs/guides/BUILD_INSTRUCTIONS.md`.

### Build Requirements

| Component | Minimum Version | Purpose |
|-----------|----------------|---------|
| **Rust Toolchain** | 1.91.0+ | Building the Rust converter |
| **Cargo** | 1.91.0+ | Rust package manager (included with Rust) |
| **cbindgen** | 0.24.0+ | Generating C header files from Rust |
| **NGINX** | 1.24.0+ | Web server (source code required for module compilation) |
| **GCC/Clang** | GCC 4.8+ or Clang 3.4+ | C compiler for NGINX module |
| **Make** | 3.81+ | Build automation |
| **PCRE** | 8.0+ | Regular expression library (NGINX dependency) |
| **zlib** | 1.2.0+ | Compression library (NGINX dependency) |
| **OpenSSL** | 1.0.2+ | SSL/TLS support (optional, for HTTPS) |

**Development Headers Required:**
- PCRE development headers (`pcre-devel` or `libpcre3-dev`)
- zlib development headers (`zlib-devel` or `zlib1g-dev`)
- OpenSSL development headers (`openssl-devel` or `libssl-dev`) — optional

### Platform-Specific Prerequisites

#### Ubuntu / Debian

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
wget https://nginx.org/download/nginx-1.24.0.tar.gz
tar -xzf nginx-1.24.0.tar.gz
```

#### CentOS / RHEL / Rocky Linux

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
wget https://nginx.org/download/nginx-1.24.0.tar.gz
tar -xzf nginx-1.24.0.tar.gz
```

#### macOS

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
curl -O https://nginx.org/download/nginx-1.24.0.tar.gz
tar -xzf nginx-1.24.0.tar.gz
```

#### Verify Prerequisites

```bash
# Check Rust version
rustc --version  # Should be 1.91.0 or higher

# Check Cargo version
cargo --version

# Check cbindgen installation
cbindgen --version

# Check GCC/Clang version
gcc --version    # or clang --version

# Check Make version
make --version
```

### Step 1: Clone the Repository

```bash
git clone https://github.com/cnkang/nginx-markdown-for-agents.git
cd nginx-markdown-for-agents
```

### Step 2: Build the Rust Library

The Rust converter must be built before compiling the NGINX module.

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

#### Platform-Specific Rust Build Notes

**macOS (Apple Silicon):**
```bash
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
rustup target add x86_64-unknown-linux-gnu
cargo build --target x86_64-unknown-linux-gnu --release
```

#### Verify Rust Build

```bash
# Check that the library was created (path depends on target architecture)
find components/rust-converter/target -path '*/release/libnginx_markdown_converter.a' -maxdepth 4

# Check that the header was generated
ls -lh components/nginx-module/src/markdown_converter.h
```

### Step 3: Build the NGINX Module

#### Prepare NGINX Source

```bash
# Navigate to NGINX source directory
cd /tmp/nginx-1.24.0

# Set module path (adjust to your actual path)
export MODULE_PATH=/path/to/nginx-markdown-for-agents/components/nginx-module
```

#### Configure as a Dynamic Module (Recommended)

Dynamic modules can be loaded/unloaded without recompiling NGINX.

```bash
./configure \
    --prefix=/usr/local/nginx \
    --with-compat \
    --add-dynamic-module=$MODULE_PATH \
    --with-http_ssl_module \
    --with-pcre
```

#### Configure as a Static Module

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

#### Compile

```bash
# Compile (use a parallelism value appropriate for your platform)
# Linux example:
make -j$(nproc)

# macOS example:
# make -j$(sysctl -n hw.ncpu)
```

#### Verify Module Compilation

```bash
# For dynamic modules, check that the .so file was created
ls -lh objs/ngx_http_markdown_filter_module.so

# For static modules, check that NGINX binary was created
ls -lh objs/nginx
```

### Step 4: Install

```bash
# From the NGINX source directory
sudo make install

# For dynamic modules, copy the .so to the modules directory
sudo mkdir -p /usr/local/nginx/modules
sudo cp objs/ngx_http_markdown_filter_module.so /usr/local/nginx/modules/
```

### Step 5: Configure NGINX

Create or edit `/usr/local/nginx/conf/nginx.conf`:

```nginx
# Load the module at the top of nginx.conf (dynamic module only)
load_module modules/ngx_http_markdown_filter_module.so;

http {
    # Enable markdown filter
    markdown_filter on;

    # Safe defaults
    markdown_max_size 10m;
    markdown_timeout 5s;
    markdown_on_error pass;  # Fail-open: return original HTML on conversion error

    server {
        listen 80;

        location / {
            root /usr/share/nginx/html;
            index index.html;
        }
    }
}
```

### Step 6: Test and Start

```bash
# Test configuration syntax
sudo /usr/local/nginx/sbin/nginx -t

# Start NGINX (or reload if already running)
sudo /usr/local/nginx/sbin/nginx
# sudo /usr/local/nginx/sbin/nginx -s reload
```

### Step 7: Verify

```bash
# Verify markdown conversion
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/

# Expected: Content-Type: text/markdown; charset=utf-8
```

---

## 7. Compatibility Matrix

> **Canonical source:** [`tools/release-matrix.json`](../../tools/release-matrix.json) is the canonical, machine-readable source of truth for platform support. The table below is a human-readable snapshot; always consult the JSON file for automation and CI.

### Support Tiers

- **Full** — Pre-built binary available, install script supported, CI-verified.
- **Source Only** — No pre-built binary; build from source using the [Manual Source Build](#6-secondary-manual-source-build) instructions.

### Minimum Supported Version

The minimum supported NGINX version is **1.24.0**. Older versions are out of scope due to differences in the dynamic module ABI.

If your NGINX version is >= 1.24.0 but not listed in the matrix below, use the [Manual Source Build](#6-secondary-manual-source-build) instructions to compile the module for your version.

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
| 1.28.3 | glibc | aarch64 | Full |
| 1.28.3 | glibc | x86_64 | Full |
| 1.28.3 | musl | aarch64 | Full |
| 1.28.3 | musl | x86_64 | Full |
| 1.29.8 | glibc | aarch64 | Full |
| 1.29.8 | glibc | x86_64 | Full |
| 1.29.8 | musl | aarch64 | Full |
| 1.29.8 | musl | x86_64 | Full |
<!-- END AUTO-GENERATED MATRIX -->

---

## 8. Release Artifact Naming

Pre-built binaries follow this naming convention:

```text
ngx_http_markdown_filter_module-<nginx_version>-<os_type>-<arch>.tar.gz
```

### Component Explanations

| Component | Values | Example |
|-----------|--------|---------|
| `nginx_version` | Exact NGINX version (semver) | `1.26.3` |
| `os_type` | `glibc` or `musl` | `glibc` |
| `arch` | `x86_64` or `aarch64` | `x86_64` |

**Example:** `ngx_http_markdown_filter_module-1.26.3-glibc-x86_64.tar.gz`

### Exact Version Match Requirement

NGINX dynamic modules require an **exact version match**. A module built for NGINX 1.26.2 will **not** load on NGINX 1.26.3. The NGINX module ABI is tied to the exact patch version, so approximate version matching is not supported.

### Determining the Correct Artifact

Use these commands to identify the correct artifact for your system:

```bash
# NGINX version
nginx -v 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+'

# Architecture
uname -m

# libc type
ldd --version 2>&1 | head -1
# musl: shows "musl libc"
# glibc: shows "ldd (GNU libc)" or similar
```

For example, if `nginx -v` reports `1.26.3`, `uname -m` reports `x86_64`, and `ldd --version` shows GNU libc, the correct artifact is:

```text
ngx_http_markdown_filter_module-1.26.3-glibc-x86_64.tar.gz
```

---

## 9. Operator Verification

This section describes how to confirm the module is working at each stage, using only standard Linux/Alpine tools.

### Module States and Observable Indicators

| Module State | Observable Indicator | Verification Method |
|-------------|---------------------|---------------------|
| **Installation successful** | `.so` file exists in modules directory, `nginx -t` passes | File check + config test |
| **Module loaded** | NGINX starts without errors, log confirms initialization | Log inspection |
| **Conversion pipeline hit** | `Accept: text/markdown` request returns `Content-Type: text/markdown` | Conversion curl |
| **Policy passed but fail-open** | Conversion attempted but failed; original HTML returned with `Content-Type: text/html` | Error log + HTML passthrough curl |

### Verification Commands

#### 1. Config Test (`nginx -t`)

Confirms the module binary is loadable and the configuration is syntactically valid:

```bash
sudo nginx -t
```

Expected output:

```text
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

#### 2. Conversion Curl

Confirms the full conversion pipeline is working — the module intercepts the response and converts HTML to Markdown:

```bash
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/
```

Expected response headers:

```text
HTTP/1.1 200 OK
Content-Type: text/markdown; charset=utf-8
Vary: Accept
```

#### 3. HTML Passthrough Curl

Confirms that requests without `Accept: text/markdown` are served unchanged:

```bash
curl -sD - -o /dev/null -H "Accept: text/html" http://localhost/
```

Expected response headers:

```text
HTTP/1.1 200 OK
Content-Type: text/html
```

#### 4. Log Inspection

Confirms the module initialized in worker processes:

```bash
tail /var/log/nginx/error.log | grep "markdown filter"
```

Look for initialization messages such as:

```text
[info] markdown filter: converter initialized in worker process (pid: ...)
```

#### 5. Metrics Endpoint (When Enabled)

If a metrics endpoint location is configured, confirm conversion counters are incrementing:

```bash
# Plain text
curl http://localhost/markdown-metrics

# JSON
curl -H "Accept: application/json" http://localhost/markdown-metrics
```

Look for counters such as `conversions_attempted`, `conversions_succeeded`, `conversions_failed`, and `conversions_bypassed`.

### Understanding Fail-Open Behavior

The default configuration uses `markdown_on_error pass` (fail-open). This means:

- If the module attempts a conversion and the conversion **fails** (e.g., timeout, converter error), the original HTML response is returned with `Content-Type: text/html`.
- This is **distinct** from requests that were never eligible for conversion (e.g., wrong `Content-Type`, non-200 status, missing `Accept: text/markdown` header). Those are "skipped" requests, not "fail-open."
- To detect fail-open events, inspect the NGINX error log for conversion failure messages.

---

## 10. Troubleshooting

This section contains structured Standard Operating Procedures (SOPs) for common installation and runtime failures. SOPs 1–6 use the same error categories as the install script's structured output. SOPs 7–9 cover runtime and configuration issues.

### Standard Operating Procedures

#### SOP 1: Module Not Loaded

**Category:** `config`

**Symptom:**
NGINX fails to start or the module has no effect. The error log shows:

```text
unknown directive "markdown_filter"
```

or:

```text
dlopen() "/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so" failed
```

**Root Cause:**
The `load_module` directive is missing from `nginx.conf`, or the path to the `.so` file is incorrect. This can happen when the install script's auto-wiring was incomplete or the module file was moved after installation.

**Resolution Steps:**

> If your NGINX uses a custom `--conf-path` or `--modules-path`, replace the default paths below with the values from `nginx -V`.

1. Determine your modules directory from `nginx -V` and verify the module file exists:
   ```bash
   # Find the modules path (varies by platform)
   MODULES_PATH=$(nginx -V 2>&1 | sed -n 's/.*--modules-path=\([^ ]*\).*/\1/p')

   # If extraction fails, probe common locations
   if [ -z "$MODULES_PATH" ]; then
       for p in /usr/lib/nginx/modules /etc/nginx/modules /usr/local/nginx/modules; do
           [ -d "$p" ] && MODULES_PATH="$p" && break
       done
   fi
   if [ -z "$MODULES_PATH" ]; then
       echo "ERROR: Could not determine modules directory."
       echo "Run 'nginx -V' and look for --modules-path= in the output,"
       echo "then set it manually, e.g.: MODULES_PATH=/usr/lib/nginx/modules"
   else
       echo "Modules directory: $MODULES_PATH"
       ls -lh "${MODULES_PATH}/ngx_http_markdown_filter_module.so"
   fi
   ```
   If `MODULES_PATH` is empty, resolve the path first (see the error message above) before continuing with the steps below.
2. If the file is missing, re-run the install script:
   ```bash
   curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash
   ```
3. If the file exists, confirm the `load_module` directive is present at the top of `nginx.conf` (before the `http {}` block):
   ```bash
   # If your NGINX uses a custom --conf-path, replace /etc/nginx/nginx.conf accordingly
   grep -n 'load_module' /etc/nginx/nginx.conf
   ```
4. If missing, add it manually:
   ```nginx
   load_module modules/ngx_http_markdown_filter_module.so;
   ```
5. Test and reload:
   ```bash
   sudo nginx -t && sudo nginx -s reload
   ```

---

#### SOP 2: NGINX Version / ABI Mismatch

**Category:** `version_mismatch`

**Symptom:**
The install script exits with an error such as:

```text
ERROR: NGINX version 1.25.4 is not in the supported matrix
```

or NGINX refuses to load the module with:

```text
module is not binary compatible
```

**Root Cause:**
NGINX dynamic modules require an exact version match. A module built for NGINX 1.26.2 will not load on NGINX 1.26.3. The pre-built binary does not exist for your exact NGINX version.

**Resolution Steps:**

1. Check your exact NGINX version:
   ```bash
   nginx -v 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+'
   ```
2. Compare against the [Compatibility Matrix](#7-compatibility-matrix) or the canonical source:
   ```bash
   curl -sL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/release-matrix.json | python3 -m json.tool
   ```
3. If your version is not in the matrix but is >= 1.24.0, build from source using the [Manual Source Build](#6-secondary-manual-source-build) instructions.
4. If your version is below 1.24.0, upgrade NGINX to a supported version.

---

#### SOP 3: Architecture Not Supported

**Category:** `arch_unsupported`

**Symptom:**
The install script exits with an error such as:

```text
ERROR: Architecture "s390x" is not supported. Supported: x86_64, aarch64
```

**Root Cause:**
Pre-built binaries are only available for `x86_64` and `aarch64` architectures. Other architectures (e.g., `s390x`, `ppc64le`, `armv7l`) are not in the release matrix.

**Resolution Steps:**

1. Confirm your architecture:
   ```bash
   uname -m
   ```
2. If the output is not `x86_64` or `aarch64`, pre-built binaries are not available.
3. Build from source using the [Manual Source Build](#6-secondary-manual-source-build) instructions. The Rust compiler and NGINX source build support a wide range of architectures.

---

#### SOP 4: libc Incompatibility

**Category:** `config`

**Symptom:**
NGINX fails to load the module with errors such as:

```text
dlopen() ... failed (Error relocating ... symbol not found)
```

or the install script reports a libc detection mismatch.

**Root Cause:**
A glibc-linked binary was installed on a musl-based system (e.g., Alpine Linux) or vice versa. The two C standard library implementations are not ABI-compatible.

**Resolution Steps:**

> If your NGINX uses a custom `--conf-path` or `--modules-path`, replace the default paths below with the values from `nginx -V`.

1. Determine your system's libc type:
   ```bash
   ldd --version 2>&1 | head -1
   ```
   - **glibc**: output contains "GNU libc" or "GLIBC"
   - **musl**: output contains "musl libc"
2. On Alpine or other musl-based systems, an alternative check:
   ```bash
   apk info musl 2>/dev/null && echo "musl" || echo "not musl"
   ```
3. Verify the installed module matches your libc by inspecting its dynamic dependencies:
   ```bash
   # Use the modules path from nginx -V (see SOP 1 step 1 for multi-path probe)
   MODULES_PATH=$(nginx -V 2>&1 | sed -n 's/.*--modules-path=\([^ ]*\).*/\1/p')
   if [ -z "$MODULES_PATH" ]; then
       for p in /usr/lib/nginx/modules /etc/nginx/modules /usr/local/nginx/modules; do
           [ -d "$p" ] && MODULES_PATH="$p" && break
       done
   fi
   if [ -z "$MODULES_PATH" ]; then
       echo "ERROR: Could not determine modules directory."
       echo "Run 'nginx -V' and look for --modules-path= in the output,"
       echo "then set it manually, e.g.: MODULES_PATH=/usr/lib/nginx/modules"
   else
       ldd "${MODULES_PATH}/ngx_http_markdown_filter_module.so"
   fi
   ```
   If `MODULES_PATH` is empty, resolve the path first (see the error message above) before continuing with the steps below.
   - **glibc**: output references `libc.so.6` and `/lib/x86_64-linux-gnu/` (or similar)
   - **musl**: output references `ld-musl-*.so.1` or shows "statically linked"
4. Re-run the install script — it auto-detects the libc type from `nginx -V` metadata:
   ```bash
   curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash
   ```
5. If the auto-detection fails, build from source using the [Manual Source Build](#6-secondary-manual-source-build) instructions to ensure native libc linkage.

---

#### SOP 5: Network Download Failure

**Category:** `network`

**Symptom:**
The install script fails with errors such as:

```text
ERROR: Failed to download module binary from GitHub
```

or:

```text
curl: (6) Could not resolve host: github.com
```

**Root Cause:**
The system cannot reach GitHub to download the pre-built binary or checksum file. This is typically caused by network restrictions, proxy configuration, DNS resolution failures, or air-gapped environments.

**Resolution Steps:**

1. Test connectivity to GitHub:
   ```bash
   curl -sI https://github.com
   ```
2. If behind a proxy, configure the proxy environment variables:
   ```bash
   export https_proxy=http://your-proxy:port
   export http_proxy=http://your-proxy:port
   ```
3. Test DNS resolution:
   ```bash
   nslookup github.com
   ```
4. If the system is air-gapped, manually download the binary and checksum on a connected machine.
   Manual download is intended only for air-gapped or troubleshooting scenarios — prefer the [install script](#4-primary-install-script) for normal installations.
   ```bash
   # On a connected machine — substitute <release_tag>, <nginx_version>, <os_type>, and <arch>
   # <release_tag> must match the current release (e.g. v0.4.0)
   wget https://github.com/cnkang/nginx-markdown-for-agents/releases/download/<release_tag>/ngx_http_markdown_filter_module-<nginx_version>-<os_type>-<arch>.tar.gz
   wget https://github.com/cnkang/nginx-markdown-for-agents/releases/download/<release_tag>/ngx_http_markdown_filter_module-<nginx_version>-<os_type>-<arch>.tar.gz.sha256
   ```
5. Transfer the files to the target system and install manually, or use the [Manual Source Build](#6-secondary-manual-source-build) instructions.

---

#### SOP 6: Checksum Verification Failure

**Category:** `checksum`

**Symptom:**
The install script fails with an error such as:

```text
ERROR: SHA-256 checksum verification failed
```

**Root Cause:**
The SHA-256 hash of the downloaded binary does not match the expected checksum from the release. This can be caused by a corrupted download, an incomplete transfer, a network intermediary modifying the file, or a tampered artifact.

**Resolution Steps:**

1. Remove the cached download and retry:
   ```bash
   curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash
   ```
2. If the failure persists, manually verify the checksum.
   Manual download is intended only for troubleshooting — prefer the [install script](#4-primary-install-script) for normal installations.
   ```bash
   # Download the binary and checksum file — substitute <release_tag>, <nginx_version>, <os_type>, <arch>
   # <release_tag> must match the current release (e.g. v0.4.0)
   wget https://github.com/cnkang/nginx-markdown-for-agents/releases/download/<release_tag>/ngx_http_markdown_filter_module-<nginx_version>-<os_type>-<arch>.tar.gz
   wget https://github.com/cnkang/nginx-markdown-for-agents/releases/download/<release_tag>/ngx_http_markdown_filter_module-<nginx_version>-<os_type>-<arch>.tar.gz.sha256

   # Verify
   sha256sum -c ngx_http_markdown_filter_module-<nginx_version>-<os_type>-<arch>.tar.gz.sha256
   ```
3. If the checksum still fails, try downloading from a different network or machine to rule out a network intermediary.
4. If the issue persists, report it on the project's GitHub issue tracker — the release artifact may need to be re-published.

---

#### SOP 7: Content Negotiation Not Triggering

**Symptom:**
Requests that should return Markdown are returning HTML instead. The response has `Content-Type: text/html` even though the client sends `Accept: text/markdown`.

**Root Cause:**
The module only converts a response when all eligibility requirements are met. If any requirement is not satisfied, the original response is passed through unchanged.

The eligibility requirements are:

1. **HTTP status 200** — the upstream response must have status code `200 OK`. Redirects (3xx), client errors (4xx), and server errors (5xx) are not eligible.
2. **Upstream `Content-Type: text/html`** — the upstream response must have `Content-Type: text/html` (with any charset parameter). Other content types (e.g., `application/json`, `text/plain`) are not eligible.
3. **Request `Accept` includes `text/markdown`** — the client request must include `text/markdown` in the `Accept` header. Without this, the module does not activate.
4. **Response size within `markdown_max_size`** — the upstream response body must not exceed the configured `markdown_max_size` limit (default: `10m`). Responses larger than this limit are passed through unchanged.

**Resolution Steps:**

1. Confirm the module is enabled:
   ```bash
   grep -r 'markdown_filter' /etc/nginx/
   ```
   Ensure `markdown_filter on;` is set in the relevant context.
2. Verify the request includes the correct `Accept` header:
   ```bash
   curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/
   ```
3. Check the upstream response status and Content-Type:
   ```bash
   curl -sD - -o /dev/null http://localhost/
   ```
   Confirm the status is `200` and `Content-Type` is `text/html`.
4. Check the response size against `markdown_max_size`:
   ```bash
   curl -sI http://localhost/ | grep -i content-length
   ```
5. Inspect the NGINX error log for eligibility skip reasons:
   ```bash
   tail -50 /var/log/nginx/error.log | grep "markdown"
   ```

---

#### SOP 8: Upstream Response Not Eligible

**Symptom:**
The module is loaded and `markdown_filter on` is set, but specific pages are not being converted. Other pages may convert correctly.

**Root Cause:**
The upstream response for those pages does not meet the eligibility criteria. Common reasons:

- **Status code is not 200** — the upstream returns a redirect (301, 302), a client error (403, 404), or a server error (500, 502). Only `200 OK` responses are eligible.
- **Content-Type is not `text/html`** — the upstream returns `application/json`, `text/plain`, `application/xml`, or another non-HTML content type.

**Resolution Steps:**

1. Check the upstream response headers for the affected page:
   ```bash
   curl -sD - -o /dev/null http://localhost/path/to/page
   ```
2. Verify the status code is `200 OK`. If the upstream returns a redirect, the final response after following redirects may have a different status.
3. Verify the `Content-Type` header is `text/html`:
   ```bash
   curl -sI http://localhost/path/to/page | grep -i content-type
   ```
4. If the upstream returns a different content type, the module correctly skips conversion. Conversion only applies to HTML content.
5. If the upstream returns a non-200 status, resolve the upstream issue first (e.g., fix the redirect chain, resolve the 404).

---

#### SOP 9: Compression / Decompression Issues

**Symptom:**
The module fails to convert responses that are compressed by the upstream. The error log may show conversion failures, or the response is passed through as HTML despite meeting all other eligibility requirements.

**Root Cause:**
The upstream server sends a compressed response (gzip, brotli, or deflate), and the module cannot decompress it before conversion. This can happen when the module's built-in decompression is not handling the encoding, or when NGINX's own compression interacts with the module's pipeline.

**Resolution Steps:**

1. Check if the upstream is sending compressed responses:
   ```bash
   curl -sD - -o /dev/null -H "Accept-Encoding: gzip" http://localhost/path/to/page | grep -i content-encoding
   ```
2. As a workaround, disable upstream compression by stripping the `Accept-Encoding` header in the NGINX proxy configuration:
   ```nginx
   location / {
       proxy_set_header Accept-Encoding "";
       proxy_pass http://upstream;
   }
   ```
   This forces the upstream to send uncompressed responses, which the module can convert directly.
3. Reload NGINX after the configuration change:
   ```bash
   sudo nginx -t && sudo nginx -s reload
   ```
4. For details on the module's built-in automatic decompression support (gzip, brotli, deflate), see [`docs/features/AUTOMATIC_DECOMPRESSION.md`](../features/AUTOMATIC_DECOMPRESSION.md).

---

## 11. Environment-Specific Notes

### Bare-Metal Linux (glibc)

This is the standard path. The install script runs with `sudo` and auto-detects the glibc environment. No special considerations.

```bash
curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash
```

### Alpine Linux (musl)

The install script auto-detects musl-based systems. The module root path may differ between distributions:

- Debian/Ubuntu: `/usr/lib/nginx/modules`
- Alpine: `/usr/lib/nginx/modules` or `/etc/nginx/modules`

The install script handles this automatically via `nginx -V` metadata. Ensure you are using the `musl` variant of the pre-built binary (the install script selects this for you).

### Docker Containers

When running the install script inside a Docker container, the root check is unnecessary. Set the `SKIP_ROOT_CHECK=1` environment variable:

```bash
SKIP_ROOT_CHECK=1 bash tools/install.sh
```

For a fully self-contained Docker build, use the [Docker Source Build](#5-secondary-docker-source-build) method instead.

### macOS

macOS is **source build only** — no pre-built binaries are available. Follow the [Manual Source Build](#6-secondary-manual-source-build) instructions with the macOS-specific prerequisites.

For Apple Silicon (M1/M2/M3), ensure you build the Rust library for the correct target:

```bash
cd components/rust-converter
cargo build --target aarch64-apple-darwin --release
```

### CI Verification

The [`install-verify.yml`](../../.github/workflows/install-verify.yml) CI workflow runs weekly and validates the install script across the full platform matrix defined in `tools/release-matrix.json`. This provides confidence that the primary install path works on all supported combinations of NGINX version, OS type, and architecture.

---

## Build Troubleshooting

This section covers common issues when building from source. For install-script failures, see the [Troubleshooting](#10-troubleshooting) section (populated separately).

### Issue: "markdown_converter.h: No such file or directory"

**Cause:** C header file not generated or not copied to the nginx-module directory.

**Solution:**
```bash
cd components/rust-converter
cbindgen --config cbindgen.toml --crate nginx-markdown-converter --output include/markdown_converter.h
cp include/markdown_converter.h ../nginx-module/src/
```

### Issue: "undefined reference to markdown_converter_new"

**Cause:** Rust library not linked correctly or not built.

**Solution:**
```bash
# Rebuild Rust library
cd components/rust-converter
cargo clean
cargo build --release

# Verify library exists
find target -path '*/release/libnginx_markdown_converter.a' -maxdepth 4

# Reconfigure and rebuild NGINX
cd /tmp/nginx-1.24.0
make clean
./configure --add-dynamic-module=$MODULE_PATH
make
```

### Issue: Architecture Mismatch (macOS)

**Error:** `ld: warning: ignoring file ... building for macOS-arm64 but attempting to link with file built for macOS-x86_64`

**Cause:** Rust library built for wrong architecture.

**Solution:**
```bash
uname -m  # arm64 or x86_64

# For Apple Silicon (M1/M2/M3)
cd components/rust-converter
cargo build --target aarch64-apple-darwin --release

# For Intel Macs
cd components/rust-converter
cargo build --target x86_64-apple-darwin --release
```

### Issue: "PCRE library not found"

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

### Issue: Rust Compilation Fails

**Error:** `error: failed to compile nginx-markdown-converter`

**Solution:**
```bash
# Update Rust toolchain
rustup update

# Check Rust version (must be 1.91.0+)
rustc --version

# Clean and rebuild
cd components/rust-converter
cargo clean
cargo build --release --verbose
```

### Issue: Module Not Loading

**Symptom:** NGINX starts but module does not work.

**Solution:**
```bash
# Check NGINX error log
sudo tail -100 /var/log/nginx/error.log

# Ensure load_module directive is present and path is correct
# In nginx.conf:
load_module modules/ngx_http_markdown_filter_module.so;

# Verify module file exists
ls -lh /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so

# Reload NGINX
sudo nginx -s reload
```

### Issue: Conversion Not Happening

**Symptom:** Requests return HTML instead of Markdown.

**Possible Causes:**

1. Module disabled — ensure `markdown_filter on;` is set.
2. `Accept` header missing — ensure the request includes `Accept: text/markdown`.
3. Response not eligible — status code must be 200, `Content-Type` must be `text/html`, response size must be within `markdown_max_size`.
4. Check NGINX error log for details:
   ```bash
   sudo tail -f /var/log/nginx/error.log
   ```

### Issue: Conversion Timeout

**Error Log:** `markdown filter: conversion timeout exceeded`

**Solution:**
```nginx
# Increase timeout in nginx.conf
markdown_timeout 10s;  # Increase from default 5s

# Or increase max size if large pages are timing out
markdown_max_size 20m;  # Increase from default 10m
```

### Issue: High Memory Usage

**Symptom:** NGINX worker processes consuming excessive memory.

**Solution:**
```nginx
# Reduce max response size
markdown_max_size 5m;

# Disable conversion for large pages
location /large-content {
    markdown_filter off;
}
```

### Issue: "unknown directive markdown_filter"

**Cause:** Module not loaded or not compiled correctly.

**Solution:**
```bash
# For dynamic modules, ensure load_module is present
load_module modules/ngx_http_markdown_filter_module.so;

# For static modules, verify module was compiled
nginx -V 2>&1 | grep markdown

# Rebuild if necessary
```

### Issue: Configuration Test Fails

**Error:** `nginx: configuration file test failed`

**Solution:**
```bash
# Run configuration test with verbose output
sudo nginx -t

# Check error log for specific error
sudo tail -50 /var/log/nginx/error.log

# Common issues:
# - Syntax errors in nginx.conf
# - Invalid directive values
# - Missing semicolons
```

### Issue: Slow Response Times

**Solution:**

1. Optimize resource limits:
   ```nginx
   markdown_max_size 5m;
   markdown_timeout 3s;
   ```

2. Disable for specific paths:
   ```nginx
   location /heavy-pages {
       markdown_filter off;
   }
   ```

3. Use fail-open strategy:
   ```nginx
   markdown_on_error pass;  # Return original HTML on timeout
   ```

---

## Getting Help

If you encounter issues not covered in this guide:

1. Check the error log: `/var/log/nginx/error.log`
2. Enable debug logging: `error_log /path/to/log debug;`
3. Review configuration: `nginx -t -c /path/to/nginx.conf`
4. Check the project issue tracker on GitHub
5. Consult the maintained guides under `docs/`

---

## Additional Resources

- [Project README](../../README.md)
- [Documentation Index](../README.md)
- [Configuration Guide](CONFIGURATION.md)
- [Build Instructions (Development)](BUILD_INSTRUCTIONS.md)
- [System Architecture](../architecture/SYSTEM_ARCHITECTURE.md)
- [NGINX Documentation](https://nginx.org/en/docs/)
- [Rust Documentation](https://doc.rust-lang.org/)
