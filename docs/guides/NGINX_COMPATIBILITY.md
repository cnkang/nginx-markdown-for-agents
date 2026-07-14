# NGINX Dynamic Module Compatibility Guide

This guide covers NGINX dynamic module compatibility requirements for
nginx-markdown-for-agents, including supported versions, the `--with-compat`
flag, and known limitations.

For full troubleshooting and package-level compatibility details, see the
[Package Compatibility Guide](../COMPATIBILITY.md).

---

## Supported NGINX Versions

The minimum supported NGINX version is **1.24.0**. Prebuilt packages are
produced for selected nginx.org official stable and mainline releases listed
in the compatibility matrix below.

> **Canonical source:** [`tools/release-matrix.json`](../../tools/release-matrix.json)
> is the machine-readable source of truth. The table below is a human-readable
> snapshot.

| NGINX Version | Channel | Support Tier |
|---------------|---------|--------------|
| 1.24.0 | oldstable | Full (prebuilt binary) |
| 1.26.3 | stable | Full (prebuilt binary) |
| 1.28.3 | stable | Full (prebuilt binary) |
| 1.30.3 | stable | Full (prebuilt binary) |
| 1.31.2 | mainline | Full (prebuilt binary) |
Architectures: `x86_64` (amd64) and `aarch64` (arm64), both glibc and musl.

If your NGINX version is >= 1.24.0 but not listed above, build the module
from source using the instructions in the
[Installation Guide](./INSTALLATION.md#6-secondary-manual-source-build).

---

## The `--with-compat` Flag

### What It Does

The `--with-compat` configure option reduces the set of NGINX build options
that contribute to the binary compatibility signature. This makes it easier
for third-party dynamic modules to load across different configure-option
combinations of the **same** NGINX version.

### When to Use It

Use `--with-compat` when building the module from source against your local
NGINX installation:

```bash
./configure --with-compat --add-dynamic-module=/path/to/nginx-markdown-for-agents/components/nginx-module
make modules
```

This maximizes the chance that the compiled module will load on your NGINX
binary, even if your NGINX was built with a slightly different set of
configure options than the module expects.

### When It Is Not Required

Prebuilt packages from this project are compiled against the exact nginx.org
official source for each target version. If you install a prebuilt package on
a matching nginx.org official build, `--with-compat` is not required because
the signatures already match.

### What `--with-compat` Does NOT Do

- **Does not guarantee cross-version compatibility.** A module built for
  NGINX 1.26.3 will not load on NGINX 1.24.0, regardless of `--with-compat`.
- **Does not eliminate all signature differences.** Some configure options
  still affect the signature even with `--with-compat` enabled.
- **Does not make modules universally portable.** The module must still be
  compiled against the exact NGINX version it will run on.

---

## Known Limitations

### Exact Version Match Requirement

NGINX dynamic modules require an **exact version match**. The binary
compatibility signature includes the NGINX version number, so a module built
for NGINX 1.26.2 will not load on NGINX 1.26.3. There is no approximate
version matching.

### Unsupported NGINX Variants

The following NGINX variants are **not supported** by prebuilt packages:

| Variant | Reason |
|---------|--------|
| Distribution-provided NGINX (apt/yum default) | Compiled with different configure options; signature mismatch likely |
| Vendor-patched NGINX | Modified source may alter ABI or internal structs |
| OpenResty | Includes LuaJIT patches and additional modules that change the binary signature |
| Tengine | Fork with significant internal changes incompatible with upstream ABI |
| Custom-compiled NGINX | Unknown configure options produce unpredictable signatures |

For these environments, build the module from source against your specific
NGINX source tree and configuration.

### Architecture Constraints

- Prebuilt packages are available for `x86_64` and `aarch64` only.
- Cross-architecture loading is not possible (an x86_64 module cannot load
  on an aarch64 NGINX binary and vice versa).

### libc Constraints

- Prebuilt packages are available for both glibc and musl variants.
- A glibc-built module will not load on a musl-based system (e.g., Alpine
  Linux) and vice versa.
- Use `ldd --version` to determine your libc type.

### macOS Limitations

- No prebuilt macOS binaries are provided.
- macOS users must build from source or use the Homebrew tap (convenience
  tier).
- Apple Silicon (M1/M2/M3) requires the `aarch64-apple-darwin` Rust target.

### Fail-Open Behavior

The module defaults to `markdown_error_policy pass` (fail-open). If the
conversion fails at runtime (timeout, memory budget exceeded, converter
error), the original HTML response is returned unchanged. This is a safety
design choice, not a compatibility limitation, but operators should be aware
that conversion failures are silent from the client perspective.

---

## Diagnosing Compatibility Issues

### Quick Checks

```bash
# Check NGINX version
nginx -v

# Check architecture
uname -m

# Check libc type
ldd --version 2>&1 | head -1

# Check if --with-compat was used in your NGINX build
nginx -V 2>&1 | grep -- '--with-compat'
```

### Common Error Messages

```text
nginx: [emerg] module "..." version 1026003 instead of 1024000
```

This means the module was built for a different NGINX version than the one
running. Install the package matching your exact NGINX version.

```text
nginx: [emerg] module "..." is not binary compatible
```

This means the configure-option signature does not match. Either use a
prebuilt package for an nginx.org official build, or build from source with
`--with-compat`.

### Resolution Matrix

| Situation | Action |
|-----------|--------|
| Wrong NGINX version | Install the package matching your exact version |
| Wrong architecture | Download the correct architecture variant |
| Distribution-provided NGINX | Switch to nginx.org official packages, or build from source |
| OpenResty / Tengine / custom | Build from source against your NGINX source tree |
| musl/glibc mismatch | Download the correct libc variant |

---

## References

- [Package Compatibility Guide](../COMPATIBILITY.md) (full troubleshooting)
- [Installation Guide](./INSTALLATION.md) (all installation methods)
- [Installation Diagnostics (doctor)](./doctor.md) (automated checks)
- [Package Installation Guide](./PACKAGE_INSTALLATION.md) (DEB/RPM details)
- [NGINX Dynamic Modules documentation](https://nginx.org/en/docs/ngx_core_module.html#load_module)

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.7.0 | 2026-05-20 | Kiro | Initial NGINX compatibility guide for docs/guides/ |
| 0.9.1 | 2026-07-13 | Kang | Align legacy directive references with 0.9.0 Config V2 implementation (markdown_limits, markdown_error_policy, markdown_accept, markdown_cache_validation; retire markdown_large_body_threshold) |
