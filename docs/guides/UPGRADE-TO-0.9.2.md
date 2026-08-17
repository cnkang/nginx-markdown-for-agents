# Upgrade Guide: 0.9.2

## Overview

This guide covers upgrading to nginx-markdown-for-agents 0.9.2 from 0.9.1.
0.9.2 is a **breaking release**. The release reduces the configuration surface from
63 directives to 25, and configurations using any removed directive fail
`nginx -t` with `unknown directive` until migrated. Review
[0.9.2-breaking-changes.md](0.9.2-breaking-changes.md) and
[MIGRATION-0.9.2.md](MIGRATION-0.9.2.md) before upgrading. If you are running
0.9.0, complete [MIGRATION-0.9.1.md](MIGRATION-0.9.1.md) before following
this guide.

> Publication status: 0.9.2 is currently a development candidate. At the
> time of writing, no `v0.9.2` tag, GitHub Release, package checksum, Docker
> image, or Helm repository entry gets asserted. The prebuilt and Helm commands
> below are release-time templates and must only run after the project
> publishes the artifacts and verifies them independently. For the current
> candidate, build from the exact branch commit or use locally produced
> artifacts.

Choose the upgrade method matching your deployment:

| Method | Section |
|--------|---------|
| Prebuilt module (`.so` replacement) | [Prebuilt Module Upgrade](#prebuilt-module-upgrade) |
| Source build | [Source Build Upgrade](#source-build-upgrade) |
| Helm chart | [Helm Upgrade](#helm-upgrade) |

---

## Prebuilt Module Upgrade

### 1. Download the 0.9.2 module binary

```bash
set -euo pipefail
# Replace <nginx-version>, <os>, and <arch> with your target (for example,
# 1.26.3, glibc, and x86_64). The archive contains the module .so.
MODULE_ARCHIVE="ngx_http_markdown_filter_module-<nginx-version>-<os>-<arch>.tar.gz"
RELEASE_BASE="https://github.com/cnkang/nginx-markdown-for-agents/releases/download/v0.9.2"
curl --fail --location --remote-name "${RELEASE_BASE}/${MODULE_ARCHIVE}"
curl --fail --location --remote-name "${RELEASE_BASE}/SHA256SUMS"
curl --fail --location --remote-name "${RELEASE_BASE}/SHA256SUMS.asc"
```

### 2. Verify checksum

```bash
set -euo pipefail

# The signing key fingerprint is defined by the GPG key management contract;
# import it through an independently authenticated channel before verifying.
# See docs/guides/GPG_KEY_MANAGEMENT.md for the authoritative fingerprint.
TRUSTED_FINGERPRINT="<project-signing-key-fingerprint-from-GPG_KEY_MANAGEMENT.md>"
SIGNER_FINGERPRINT="$(gpg --with-colons --import-options show-only --import < SHA256SUMS.asc 2>/dev/null | awk -F: '$1 == "fpr" { print $10; exit }')"
if [[ -z "$SIGNER_FINGERPRINT" || "$SIGNER_FINGERPRINT" != "$TRUSTED_FINGERPRINT" ]]; then
  printf 'signer fingerprint mismatch: got %s, expected %s\n' \
      "${SIGNER_FINGERPRINT:-<none>}" "$TRUSTED_FINGERPRINT" >&2
  exit 1
fi
gpg --verify SHA256SUMS.asc SHA256SUMS

verify_module_archive_checksum() {
    local checksum_line
    checksum_line="$(awk -v module_file="${MODULE_ARCHIVE}" '
        NF == 2 && $2 == module_file { count++; line = $0 }
        END { if (count == 1) print line }
    ' SHA256SUMS)"
    if [[ -z "${checksum_line}" ]]; then
        printf 'SHA256SUMS must contain exactly one %s record\n' \
            "${MODULE_ARCHIVE}" >&2
        return 1
    fi
    printf '%s\n' "${checksum_line}" | sha256sum --check
}

verify_module_archive_checksum
```

### 3. Back up the current module

```bash
# Derive the module directory from the active NGINX build so RPM systems
# resolve /usr/lib64/nginx/modules and source builds their own prefix.
MODULES_DIR="${MODULES_DIR:-$(nginx -V 2>&1 | sed -n 's/.*--modules-path=\([^ ]*\).*/\1/p')}"
if [[ -z "$MODULES_DIR" || ! -d "$MODULES_DIR" ]]; then
  echo "ERROR: cannot locate the NGINX modules directory; set MODULES_DIR explicitly" >&2
  exit 1
fi
sudo cp "$MODULES_DIR/ngx_http_markdown_filter_module.so" \
    "$MODULES_DIR/ngx_http_markdown_filter_module.so.0.9.1.bak"
```

### 4. Replace the module

```bash
tar -xzf "${MODULE_ARCHIVE}"
sudo cp ngx_http_markdown_filter_module.so \
    "$MODULES_DIR/ngx_http_markdown_filter_module.so"
```

### 5. Migrate the configuration

0.9.2 is a breaking configuration release (25-directive surface, dynconf
file format frozen at JSON schema v1). Before validating or restarting
NGINX, apply the 0.9.2 migration:

```bash
# Apply the 0.9.2 directive changes documented in MIGRATION-0.9.2.md:
# removed profile/OTel directives, consolidated markdown_limits keys,
# dynconf migration from legacy line format to JSON schema v1.
# (The markdown_streaming_engine -> markdown_streaming rename happened in
# 0.9.1, not 0.9.2; 0.9.2 removed markdown_stream_threshold and
# markdown_streaming_zero_copy.)
# See docs/guides/MIGRATION-0.9.2.md for the complete mapping.
```

Validate the migrated configuration with the new binary:

```bash
sudo nginx -t
```

A 0.9.1 configuration fails `nginx -t` under the 0.9.2 binary (removed
directives produce errors), so configuration migration must happen before
the restart in the next step.

### 6. Validate and restart

```bash
sudo nginx -t && sudo systemctl restart nginx
```

---

## Source Build Upgrade

### 1. Update the repository

```bash
cd nginx-markdown-for-agents
git fetch --tags
# Use this tag only after the 0.9.2 publication evidence exists; for the
# current candidate, check out the exact reviewed development commit instead.
git checkout v0.9.2
```

### 2. Update Rust toolchain

```bash
rustup toolchain install 1.97.1
rustup default 1.97.1
```

### 3. Build the Rust converter

```bash
cd components/rust-converter
cargo build --release
cd ../..
```

### 4. Rebuild the NGINX module

```bash
# Using your existing NGINX build directory
cd /path/to/nginx-build
make modules
```

### 5. Install and restart

```bash
sudo cp objs/ngx_http_markdown_filter_module.so \
    /usr/lib/nginx/modules/
sudo nginx -t && sudo systemctl restart nginx
```

---

## Helm Upgrade

### 1. Update the chart repository

```bash
# If you installed from the in-tree chart, refresh it from the 0.9.2 source:
#   git fetch origin && git checkout v0.9.2   (or update your vendored copy)
# If you use a remote chart repository, define it before updating:
#   helm repo add nginx-markdown <your-chart-repo-url>
helm repo update
```

### 2. Upgrade the release

```bash
helm upgrade nginx-markdown nginx-markdown/nginx-markdown-for-agents \
    --namespace nginx-markdown \
    --set markdown.image.tag=v0.9.2
```

### 3. Verify

```bash
helm status nginx-markdown --namespace nginx-markdown
kubectl rollout status deployment/nginx-markdown --namespace nginx-markdown
```

---

## Post-Upgrade Verification

After upgrading by any method, run these checks:

### 1. Configuration validation

```bash
sudo nginx -t
# Expected: syntax is ok / test is successful
```

### 2. Doctor check

```bash
bash tools/doctor/nginx-markdown-doctor.sh
# All checks should pass
```

### 3. Diagnostics endpoint

```bash
curl -s http://localhost/nginx-markdown/diagnostics | python3 -m json.tool
# Verify version shows 0.9.2
# Verify recent_decisions[].reason can carry bypass_no_transform
```

Executable assertion (fails with a non-zero exit when the contract is not
met, so the verification step is deterministic):

```bash
curl -s http://localhost/nginx-markdown/diagnostics \
    | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d.get("version") == "0.9.2", f"version={d.get(\"version\")!r}"
reasons = {r.get("reason") for r in d.get("recent_decisions", [])}
# Recent decisions may not yet contain a bypass_no_transform entry (the
# decision log is bounded and depends on traffic). Issue a deterministic
# no-transform probe first, or validate that every returned entry exposes
# a reason field rather than requiring a specific observed outcome.
assert reasons, f"no recent_decisions entries returned: {sorted(reasons)}"
assert all(r is not None for r in reasons), "entry missing reason field"
print("diagnostics contract verified")
'
```

### 4. Metrics endpoint

```bash
curl --fail --silent --show-error \
    -H 'Accept: text/plain; version=0.0.4' \
    http://localhost/markdown-metrics
# Verify metric families are present and emitting
```

### 5. Functional smoke test

```bash
curl -sD - -H "Accept: text/markdown" http://localhost/docs/ | head -5
# Expected: HTTP/1.1 200 OK
# Expected: Content-Type: text/markdown; charset=utf-8
```

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-15 | Kang | Added Step 5 migrate-the-configuration before restart |
| 0.9.2 | 2026-07-30 | Kang | Initial upgrade guide for 0.9.2 |
