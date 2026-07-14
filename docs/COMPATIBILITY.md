# Package Compatibility Guide

This document is the canonical compatibility reference for
nginx-markdown-for-agents prebuilt packages (.deb and .rpm). It explains the
NGINX dynamic module compatibility model, supported environments, and
troubleshooting guidance for version mismatch errors.

---

## Supported Scope

Prebuilt packages are produced **only** for selected official nginx.org stable
releases. The module is compiled against the exact NGINX source version
specified in the build matrix and is not a universal shared library.

Key points:

- Both nginx.org stable and selected mainline releases are supported as shown
  in the build matrix below.
- Each prebuilt package targets a specific NGINX version and CPU architecture.
- Users with unsupported NGINX versions must build from source (see below).

### Package Dependency Scope

The package manager dependency `nginx (>= ${NGINX_VERSION})` allows
installation alongside any NGINX version at or above the build target.
However, **runtime compatibility is only verified for the exact NGINX
versions listed in the build matrix below**. Loading the module on any other
NGINX version will likely fail with a binary compatibility signature mismatch
(see "Version Mismatch Error Troubleshooting" below).
Install the package only on the NGINX version matching the artifact
filename.

---

## NGINX Dynamic Module Compatibility Model

### Binary Compat Signature

NGINX validates every dynamic module at load time using a **Binary
Compatibility Signature**. This signature is derived from:

1. The NGINX version number (e.g., `1.26.3`).
2. The set of `./configure` options used to build NGINX.
3. Internal ABI markers that reflect struct layouts and API contracts.

When you add `load_module modules/ngx_http_markdown_filter_module.so;` to your
`nginx.conf` and run `nginx -t`, NGINX compares the module's compiled
signature against its own. If they do not match, NGINX refuses to load the
module and logs an error.

### The `--with-compat` Flag and Its Limitations

The `--with-compat` configure option reduces the set of options that
contribute to the binary compatibility signature. This makes it easier for
third-party modules to be compatible across different configure-option
combinations of the **same** NGINX version.

However, `--with-compat` does **not**:

- Guarantee cross-version compatibility (e.g., a module built for 1.26.3
  will not load on 1.24.0).
- Eliminate all signature differences (some options still affect the
  signature even with `--with-compat`).
- Make modules universally portable across different NGINX builds.

In summary: `--with-compat` reduces configure-option differences but does
not guarantee universality. The module must still be compiled against the
exact NGINX version it will run on.

---

## Build Matrix

The initial release targets the following architecture and NGINX version
combinations:

| Architecture | NGINX Branch | Version | Package Formats |
|-------------|-------------|---------|-----------------|
| amd64 (x86_64) | stable | 1.26.x | .deb, .rpm |
| arm64 (aarch64) | stable | 1.26.x | .deb, .rpm |

### Artifact Naming

Each package filename encodes the full version and platform information to
prevent accidental use of an incompatible package:

- **DEB**: `nginx-module-markdown-for-agents_{version}_nginx-{nginx_version}_{arch}.deb`
- **RPM**: `nginx-module-markdown-for-agents-{version}-nginx{nginx_version}-1.{arch}.rpm`

Examples:

```
nginx-module-markdown-for-agents_0.9.1_nginx-1.26.3_amd64.deb
nginx-module-markdown-for-agents_0.9.1_nginx-1.26.3_arm64.deb
nginx-module-markdown-for-agents-0.9.1-nginx1.26.3-1.x86_64.rpm
nginx-module-markdown-for-agents-0.9.1-nginx1.26.3-1.aarch64.rpm
```

Always select the package that matches both your NGINX version and CPU
architecture.

The NGINX version embedded in the artifact name is the version tested by the
release pipeline. After upgrading NGINX, install the package built for the new
NGINX version before reloading the module, then run `nginx -t` to validate that
the module still loads with the active NGINX binary.

---

## Unsupported Environments

The following NGINX variants are **not supported** by prebuilt packages:

| Environment | Reason |
|-------------|--------|
| Distribution-provided NGINX (apt/yum default) | Compiled with different configure options; signature mismatch likely |
| Vendor-patched NGINX | Modified source may alter ABI or internal structs |
| OpenResty | Includes LuaJIT patches and additional modules that change the binary signature |
| Tengine | Fork with significant internal changes incompatible with upstream ABI |
| Custom-compiled NGINX | Unknown configure options produce unpredictable signatures |

If you are running any of the above, you must build the module from source
against your specific NGINX source tree and configuration (see the
"Building from Source" section below).

### How to Identify Your NGINX Source

Run `nginx -V` and inspect the output:

```bash
nginx -V 2>&1 | head -5
```

- **nginx.org official**: Version line shows `nginx/1.26.x` with no
  distribution suffix. The `--prefix` is typically `/etc/nginx`.
- **Distribution-provided**: Version line may include a suffix like
  `(Ubuntu)` or the configure options include distribution-specific paths
  (e.g., `--conf-path=/etc/nginx/nginx.conf` with non-standard module
  paths).
- **OpenResty**: Version line shows `openresty/...` or includes
  `--with-luajit`.
- **Tengine**: Version line shows `Tengine/...`.

---

## Version Mismatch Error Troubleshooting

### Common Error Messages

When the module's binary compatibility signature does not match the running
NGINX binary, you will see errors like:

```text
nginx: [emerg] module "/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so"
  version 1026003 instead of 1024000 in /etc/nginx/nginx.conf:1
```

```text
nginx: [emerg] module "/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so"
  is not binary compatible in /etc/nginx/nginx.conf:1
```

### Diagnosis Steps

1. **Check your NGINX version**:

   ```bash
   nginx -v
   ```

   Compare the reported version against the package filename. They must
   match exactly (e.g., package built for `nginx-1.26.3` requires NGINX
   `1.26.3`).

2. **Check your architecture**:

   ```bash
   uname -m
   ```

   Ensure you installed the correct architecture variant (`amd64`/`x86_64`
   or `arm64`/`aarch64`).

3. **Check for `--with-compat`**:

   ```bash
   nginx -V 2>&1 | grep -- '--with-compat'
   ```

   While `--with-compat` is not strictly required for nginx.org official
   builds (since the package is compiled against the exact version), its
   absence on a non-official build is a strong indicator of
   incompatibility.

4. **Verify NGINX source**:

   Confirm you are running an nginx.org official build, not a
   distribution-provided or vendor-patched variant (see "How to Identify
   Your NGINX Source" above).

### Resolution

| Situation | Action |
|-----------|--------|
| Wrong NGINX version | Install the nginx.org stable version matching the package, or download the package matching your version |
| Wrong architecture | Download the correct architecture variant |
| Distribution-provided NGINX | Switch to nginx.org official packages, or build the module from source |
| OpenResty / Tengine / custom | Build the module from source against your NGINX source tree |

---

## Building from Source

If your environment is not covered by the prebuilt packages, you can compile
the module from source against your local NGINX installation.

### Prerequisites

- NGINX source tree matching your installed NGINX version
- C compiler (gcc or clang)
- Rust toolchain (edition 2024, MSRV 1.97)
- Standard build tools (make, pkg-config)

### Steps

1. **Obtain the NGINX source** matching your installed version:

   ```bash
   nginx -v  # note the version number
   # Download matching source from https://nginx.org/en/download.html
   tar xzf nginx-<version>.tar.gz
   cd nginx-<version>
   ```

2. **Clone the module source**:

   ```bash
   git clone https://github.com/pederhan/nginx-markdown-for-agents.git
   ```

3. **Configure NGINX with the module**:

   ```bash
   ./configure --with-compat --add-dynamic-module=../nginx-markdown-for-agents
   ```

   Use `--with-compat` to maximize compatibility with your existing NGINX
   binary. If your NGINX was built with specific configure options, replicate
   them here (check `nginx -V` output).

4. **Build the module**:

   ```bash
   make modules
   ```

5. **Install the module**:

   ```bash
   sudo cp objs/ngx_http_markdown_filter_module.so /usr/lib/nginx/modules/
   ```

6. **Enable and verify**:

   Add to `nginx.conf` (top-level, before the `http` block):

   ```nginx
   load_module modules/ngx_http_markdown_filter_module.so;
   ```

   Then verify and reload:

   ```bash
   sudo nginx -t
   sudo systemctl reload nginx
   ```

7. **Confirm the module is active**:

   ```bash
   curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/
   ```

   A successful response includes `Content-Type: text/markdown` in the
   headers when the module is processing eligible responses.

---

## References

- [NGINX Dynamic Modules documentation](https://nginx.org/en/docs/ngx_core_module.html#load_module)
- [FFI ABI Compatibility Assumptions](architecture/FFI_ABI_COMPATIBILITY.md)
- [Installation Guide](guides/INSTALLATION.md)

---

<!-- BEGIN:release-matrix:compatibility-matrix -->

## Platform Compatibility Matrix

| NGINX Version | Channel | OS | libc | Arch | Artifact | Test Level | Tier | Blocking | Workflow |
|---------------|---------|-----|------|------|----------|------------|------|----------|----------|
| 1.31.2 | mainline | linux | glibc | arm64 | dynamic-module | smoke-test | supported | Yes | `.github/workflows/release-packages.yml` |
| 1.31.2 | mainline | linux | musl | arm64 | dynamic-module | docker-validation | supported | No | `.github/workflows/release-binaries.yml` |
| 1.31.2 | mainline | linux | glibc | amd64 | dynamic-module | smoke-test | supported | Yes | `.github/workflows/release-packages.yml` |
| 1.31.2 | mainline | linux | musl | amd64 | dynamic-module | docker-validation | supported | No | `.github/workflows/release-binaries.yml` |
| 1.31.2 | mainline | debian12 | glibc | arm64 | docker-image | functional-check | supported | Yes | `.github/workflows/official-nginx-docker.yml` |
| 1.31.2 | mainline | debian12 | glibc | amd64 | docker-image | functional-check | supported | Yes | `.github/workflows/official-nginx-docker.yml` |
| 1.31.2 | mainline | alpine3.20 | musl | arm64 | docker-image | functional-check | supported | Yes | `.github/workflows/official-nginx-docker.yml` |
| 1.31.2 | mainline | alpine3.20 | musl | amd64 | docker-image | functional-check | supported | Yes | `.github/workflows/official-nginx-docker.yml` |
| 1.30.3 | stable | linux | glibc | arm64 | dynamic-module | smoke-test | supported | Yes | `.github/workflows/release-packages.yml` |
| 1.30.3 | stable | linux | musl | arm64 | dynamic-module | docker-validation | supported | No | `.github/workflows/release-binaries.yml` |
| 1.30.3 | stable | linux | glibc | amd64 | dynamic-module | smoke-test | supported | Yes | `.github/workflows/release-packages.yml` |
| 1.30.3 | stable | linux | musl | amd64 | dynamic-module | docker-validation | supported | No | `.github/workflows/release-binaries.yml` |
| 1.28.3 | stable | linux | glibc | arm64 | dynamic-module | smoke-test | supported | Yes | `.github/workflows/release-packages.yml` |
| 1.28.3 | stable | linux | musl | arm64 | dynamic-module | docker-validation | supported | No | `.github/workflows/release-binaries.yml` |
| 1.28.3 | stable | linux | glibc | amd64 | dynamic-module | smoke-test | supported | Yes | `.github/workflows/release-packages.yml` |
| 1.28.3 | stable | linux | musl | amd64 | dynamic-module | docker-validation | supported | No | `.github/workflows/release-binaries.yml` |
| 1.26.3 | stable | macos | darwin | arm64 | homebrew-formula | formula-gate | experimental | No | `.github/workflows/homebrew-formula-gate.yml` |
| 1.26.3 | stable | linux | glibc | arm64 | dynamic-module | smoke-test | supported | Yes | `.github/workflows/release-packages.yml` |
| 1.26.3 | stable | linux | musl | arm64 | dynamic-module | docker-validation | supported | No | `.github/workflows/release-binaries.yml` |
| 1.26.3 | stable | linux | glibc | amd64 | dynamic-module | smoke-test | supported | Yes | `.github/workflows/release-packages.yml` |
| 1.26.3 | stable | linux | musl | amd64 | dynamic-module | docker-validation | supported | No | `.github/workflows/release-binaries.yml` |
| 1.26.3 | stable | debian12 | glibc | arm64 | docker-image | functional-check | supported | Yes | `.github/workflows/official-nginx-docker.yml` |
| 1.26.3 | stable | debian12 | glibc | arm64 | deb-package | smoke-test | supported | Yes | `.github/workflows/release-packages.yml` |
| 1.26.3 | stable | debian12 | glibc | amd64 | docker-image | functional-check | supported | Yes | `.github/workflows/official-nginx-docker.yml` |
| 1.26.3 | stable | debian12 | glibc | amd64 | deb-package | smoke-test | supported | Yes | `.github/workflows/release-packages.yml` |
| 1.26.3 | stable | any | n/a | any | source | ci-only | best-effort | No | `.github/workflows/ci.yml` |
| 1.26.3 | stable | alpine3.20 | musl | arm64 | docker-image | functional-check | supported | Yes | `.github/workflows/official-nginx-docker.yml` |
| 1.26.3 | stable | alpine3.20 | musl | amd64 | docker-image | functional-check | supported | Yes | `.github/workflows/official-nginx-docker.yml` |
| 1.26.3 | stable | almalinux9 | glibc | arm64 | rpm-package | smoke-test | supported | Yes | `.github/workflows/release-packages.yml` |
| 1.26.3 | stable | almalinux9 | glibc | amd64 | rpm-package | smoke-test | supported | Yes | `.github/workflows/release-packages.yml` |
| 1.24.0 | oldstable | linux | glibc | arm64 | dynamic-module | smoke-test | supported | Yes | `.github/workflows/release-packages.yml` |
| 1.24.0 | oldstable | linux | musl | arm64 | dynamic-module | docker-validation | supported | No | `.github/workflows/release-binaries.yml` |
| 1.24.0 | oldstable | linux | glibc | amd64 | dynamic-module | smoke-test | supported | Yes | `.github/workflows/release-packages.yml` |
| 1.24.0 | oldstable | linux | musl | amd64 | dynamic-module | docker-validation | supported | No | `.github/workflows/release-binaries.yml` |

### Tier Definitions

- **supported**: CI passes, artifact produced, install verified, release gate blocks.
- **experimental**: Available, not guaranteed, CI non-blocking, noted in release notes.
- **best-effort**: Source only, docs only, not a gate.
- **unsupported**: No artifacts, no commitment.
<!-- END:release-matrix:compatibility-matrix -->

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.7.0 | 2026-05-18 | Kiro | Initial compatibility guide for prebuilt package release |
| 0.8.3 | 2026-06-26 | Kang | Updated artifact naming examples to 0.8.3 |
