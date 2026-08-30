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
EXPECTED_FINGERPRINT="$(printf '%s' "${TRUSTED_FINGERPRINT}" | tr '[:lower:]' '[:upper:]')"
[[ "${EXPECTED_FINGERPRINT}" =~ ^[A-F0-9]{40}$ ]] || {
  printf 'missing or invalid trusted fingerprint\n' >&2
  exit 1
}
# Import the independently authenticated public key first, then verify the
# detached signature with status output and extract the fingerprint from its
# VALIDSIG record (field 3) — not from --import-options show-only on the
# signature, which does not prove the signer.
gpg --import <(curl -fsSL "${SIGNING_KEY_URL:?set to the independently authenticated key URL}") 2>/dev/null
SIGNER_FINGERPRINT="$(gpg --status-fd=1 --verify SHA256SUMS.asc SHA256SUMS 2>/dev/null \
    | awk '$2 == "VALIDSIG" { print toupper($3); exit }')"
if [[ -z "$SIGNER_FINGERPRINT" || "$SIGNER_FINGERPRINT" != "$EXPECTED_FINGERPRINT" ]]; then
  printf 'signer fingerprint mismatch: got %s, expected %s\n' \
      "${SIGNER_FINGERPRINT:-<none>}" "$EXPECTED_FINGERPRINT" >&2
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
CONFIG_BACKUP_DIR="/var/backups/nginx-markdown-0.9.1"
sudo install -d -m 0750 "${CONFIG_BACKUP_DIR}"
sudo cp -a /etc/nginx/nginx.conf "${CONFIG_BACKUP_DIR}/"
for CONFIG_DIR in /etc/nginx/conf.d /etc/nginx/modules-enabled; do
  if [[ -d "${CONFIG_DIR}" ]]; then
    sudo cp -a "${CONFIG_DIR}" "${CONFIG_BACKUP_DIR}/"
  fi
done
MODULE_PATH="$MODULES_DIR/ngx_http_markdown_filter_module.so"
MODULE_BACKUP="${MODULE_PATH}.0.9.1.bak"
if [[ ! -f "${MODULE_PATH}" ]]; then
  echo "ERROR: current module is missing: ${MODULE_PATH}" >&2
  exit 1
fi
if [[ -e "${MODULE_BACKUP}" ]]; then
  echo "Preserving existing module backup: ${MODULE_BACKUP}"
else
  sudo cp -a "${MODULE_PATH}" "${MODULE_BACKUP}"
fi
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

### 6. Validate and restart with the active service manager

```bash
sudo nginx -t
# systemd-managed host: verify the RUNNING nginx process is actually
# owned by nginx.service before restarting through systemd.  A unit
# file existing on disk is not proof of ownership — the process may be
# started by another supervisor or directly.
if systemctl is-active --quiet nginx.service \
    && systemctl show -p MainPID --value nginx.service | grep -q '[0-9]' \
    && [ "$(systemctl show -p MainPID --value nginx.service)" = "$(pgrep -x nginx | head -1)" ]; then
  sudo systemctl restart nginx
else
  # If another supervisor owns NGINX, use its restart/reload operation instead.
  # For a directly managed master process, the equivalent is:
  sudo nginx -s reload
fi
```

---

## Source Build Upgrade

### 1. Update the repository

```bash
cd nginx-markdown-for-agents
RELEASE_TAG=v0.9.2
git fetch --tags origin "${RELEASE_TAG}"
# Copy EXPECTED_COMMIT from the independently authenticated release evidence.
# A signed tag proves tag ownership; this equality binds the source checkout to
# the exact reviewed commit recorded by the release process.
EXPECTED_COMMIT="<release-evidence-commit>"
git verify-tag "${RELEASE_TAG}"
[[ "$(git rev-parse "${RELEASE_TAG}^{commit}")" == "${EXPECTED_COMMIT}" ]] || {
    echo "release tag does not resolve to the authenticated expected commit" >&2
    exit 1
}
git checkout --detach "${RELEASE_TAG}"
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
# Copy the module into the ACTIVE NGINX module directory.  Determine it
# from the running binary: `nginx -V 2>&1 | grep modules-path` (for example
# /usr/lib/nginx/modules on Debian/Ubuntu, /usr/lib64/nginx/modules on
# RHEL-family).  Do not hard-code a path that differs from your install.
MODULES_DIR="$(nginx -V 2>&1 | sed -n 's/.*--modules-path=\([^ ]*\).*/\1/p')"
if [[ -z "${MODULES_DIR}" || ! -d "${MODULES_DIR}" ]]; then
    echo "ERROR: could not determine a valid --modules-path from 'nginx -V'" >&2
    exit 1
fi
sudo cp objs/ngx_http_markdown_filter_module.so "${MODULES_DIR}/"
sudo nginx -t
# Restart through the host's service manager only when systemd actually
# owns the running NGINX process.  A unit file existing on disk is not
# proof of ownership — the process may be started by another supervisor
# or directly.
if command -v systemctl >/dev/null 2>&1 \
    && systemctl is-active --quiet nginx.service \
    && systemctl show -p MainPID --value nginx.service | grep -q '[0-9]' \
    && [ "$(systemctl show -p MainPID --value nginx.service)" = "$(pgrep -x nginx | head -1)" ]; then
    sudo systemctl restart nginx
else
    sudo nginx -s reload
fi
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
# Use the chart source selected in Step 1.
# In-tree chart (checked out at the 0.9.2 tag):
helm upgrade nginx-markdown ./charts/nginx-markdown \
    --namespace nginx-markdown \
    --set markdown.image.tag=v0.9.2

# Remote chart repository (added in Step 1):
#   helm upgrade nginx-markdown nginx-markdown/nginx-markdown-for-agents \
#       --namespace nginx-markdown \
#       --set markdown.image.tag=v0.9.2
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
recent = d.get("recent_decisions")
assert isinstance(recent, list), f"recent_decisions is not a list: {type(recent).__name__}"
reasons = [r.get("reason") for r in recent]
# Recent decisions may not yet contain a bypass_no_transform entry (the
# decision log is bounded and depends on traffic). Every returned entry
# must expose a reason field; an empty log is acceptable because no
# specific observed outcome is required.
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
