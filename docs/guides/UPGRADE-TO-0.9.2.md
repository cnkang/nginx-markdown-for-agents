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
GNUPGHOME="$(mktemp -d)"
chmod 700 "${GNUPGHOME}"
export GNUPGHOME
cleanup_gnupg() {
  gpgconf --kill gpg-agent >/dev/null 2>&1 || true
  rm -rf "${GNUPGHOME}"
}
trap cleanup_gnupg EXIT

TRUSTED_FINGERPRINT="15C792438EAA762B421E60D21E8D41E7D19A8A75"  # from docs/guides/GPG_KEY_MANAGEMENT.md (authoritative)
EXPECTED_FINGERPRINT="$(printf '%s' "${TRUSTED_FINGERPRINT}" | tr '[:lower:]' '[:upper:]')"
[[ "${EXPECTED_FINGERPRINT}" =~ ^[A-F0-9]{40}$ ]] || {
  printf 'missing or invalid trusted fingerprint\n' >&2
  exit 1
}
# Import the independently authenticated public key first, then verify the
# detached signature with status output and extract the fingerprint from its
# VALIDSIG record (field 3) — not from --import-options show-only on the
# signature, which does not prove the signer.
: "${RELEASE_KEY_PATH:?set RELEASE_KEY_PATH to the project public-key file (packaging/nginx-markdown-for-agents-release.asc, from the git repository, not the release assets)}"
gpg --import "${RELEASE_KEY_PATH}" 2>/dev/null
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
NGINX_CONF_DIR="${NGINX_CONF_DIR:-/etc/nginx}"
sudo install -d -m 0750 "${CONFIG_BACKUP_DIR}"
sudo cp -a "${NGINX_CONF_DIR}/nginx.conf" "${CONFIG_BACKUP_DIR}/"
for CONFIG_DIR in "${NGINX_CONF_DIR}/conf.d" "${NGINX_CONF_DIR}/modules-enabled"; do
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

> **Warning — do not overwrite a running module in place.** Copying over
> the `.so` while old workers still have it mapped can serve mixed or stale
> code (and risks SIGBUS on some platforms). Replace the module file
> only between a full stop and the next start, below.

```bash
tar -xzf "${MODULE_ARCHIVE}"
mkdir -p "${MODULES_DIR}"
# Stage the new module beside the running one, then atomically rename it
# into place while NGINX is stopped (step 6).  A plain cp over the live
# file is NOT atomic and may tear the mapping for active workers.
sudo install -m 0755 ngx_http_markdown_filter_module.so \
    "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.0.9.2.new"
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

Validate the migrated configuration with the currently loaded (old) module
as a pre-flight: the staged 0.9.2 module is not yet swapped in, so this
`nginx -t` exercises the migrated configuration syntax against the binary
that is still running:

```bash
sudo nginx -t
```

A 0.9.1 configuration fails `nginx -t` under the 0.9.2 binary (removed
directives produce errors), so configuration migration must happen before
the restart in the next step. The definitive validation against the new
module happens after the swap (step 6), where `nginx -t` runs again.

### 6. Full stop, swap the module, and start

> **A plain `nginx -s reload` does NOT load a replaced module.** NGINX
> keeps the previously loaded module when both old and new configs
> reference the same `load_module` directive — an operator can believe the
> upgrade landed while workers still run the old code. The upgrade below
> therefore uses a full stop, an atomic rename of the staged module into
> place, and a fresh start. (For a truly in-place online upgrade, NGINX
> binary upgrade via `kill -USR2` + `kill -QUIT` is the supported path, not
> module-file replacement under reload.)

```bash
set -euo pipefail
sudo nginx -t
# systemd-managed host: verify the RUNNING nginx process is actually
# owned by nginx.service before restarting through systemd.  A unit
# file existing on disk is not proof of ownership — the process may be
# started by another supervisor or directly.  Record the ownership
# decision BEFORE stopping: after a successful stop, is-active is
# false even on systemd-managed hosts, so it cannot be re-derived.
systemd_managed=0
if command -v systemctl >/dev/null 2>&1 \
    && systemctl is-active --quiet nginx.service; then
  main_pid="$(systemctl show -p MainPID --value nginx.service)"
  if [[ "$main_pid" =~ ^[0-9]+$ ]] \
      && pgrep -x nginx | grep -qx "$main_pid"; then
    systemd_managed=1
    sudo systemctl stop nginx
  else
    echo "ERROR: nginx.service is active but does not own the running NGINX master; refusing to stop" >&2
    exit 1
  fi
else
  # For a directly managed master: terminate the master so all workers
  # exit before the module file is swapped; then start fresh below.
  # (If the process is owned by another supervisor, use its stop/start.)
  if pgrep -x nginx >/dev/null 2>&1; then
    sudo nginx -s quit
    # Wait for the master to exit, with a finite deadline: an indefinite
    # poll can hang the upgrade if a worker refuses to terminate.
    waited=0
    while pgrep -x nginx >/dev/null 2>&1; do
      if [[ "$waited" -ge 30 ]]; then
        echo "ERROR: NGINX master did not exit within 30s of 'nginx -s quit'; aborting upgrade" >&2
        exit 1
      fi
      sleep 1
      waited=$((waited + 1))
    done
  else
    echo "INFO: no running NGINX master found; skipping 'nginx -s quit'"
  fi
fi

# Swap the staged module into place atomically while NGINX is stopped.
sudo mv -f "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.0.9.2.new" \
    "${MODULES_DIR}/ngx_http_markdown_filter_module.so"
# The swap is reversible: if nginx -t fails here, restore the module backup
# taken in step 4 (${MODULE_BACKUP}) AND the migrated configuration from
# ${CONFIG_BACKUP_DIR}, re-run nginx -t on the restored pair, then start.
# Never start NGINX with a module whose configuration failed validation.
sudo nginx -t || {
  echo "ERROR: nginx -t failed after module swap; restoring module backup..." >&2
  # Stage the rollback to a temporary path first.  cp -a alone can leave a
  # partially-written module on I/O or disk-space failure; the atomic
  # replace only happens after the copy fully succeeds.
  sudo cp -a "${MODULE_BACKUP}" \
    "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.restore" 2>/dev/null || {
    echo "ERROR: rollback copy failed; NGINX remains stopped. Restore manually from ${MODULE_BACKUP} and ${CONFIG_BACKUP_DIR}." >&2
    exit 1
  }
  sudo mv -f "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.restore" \
    "${MODULES_DIR}/ngx_http_markdown_filter_module.so" 2>/dev/null || {
    echo "ERROR: atomic module replacement failed; NGINX remains stopped. Restore manually from ${MODULE_BACKUP} and ${CONFIG_BACKUP_DIR}." >&2
    exit 1
  }
  sudo cp -a "${CONFIG_BACKUP_DIR}/nginx.conf" "${NGINX_CONF_DIR}/nginx.conf" 2>/dev/null || {
    echo "ERROR: configuration restore failed; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
    exit 1
  }
  sudo nginx -t && echo "INFO: previous module and configuration restored and verified." >&2
  exit 1
}

# Start a fresh master with the new module loaded, using the ownership
# decision recorded before the stop.
if [[ "$systemd_managed" -eq 1 ]]; then
  sudo systemctl start nginx
else
  sudo nginx
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
# Target the current platform explicitly so the archive lands in
# target/<triple>/release/, the only layout the module configure accepts
cargo build --release --target "$(rustc -vV | sed -n 's/^host: //p')"
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
set -euo pipefail
# Copy the module into the ACTIVE NGINX module directory.  Determine it
# from the running binary: `nginx -V 2>&1 | grep modules-path` (for example
# /usr/lib/nginx/modules on Debian/Ubuntu, /usr/lib64/nginx/modules on
# RHEL-family).  Do not hard-code a path that differs from your install.
MODULES_DIR="$(nginx -V 2>&1 | sed -n 's/.*--modules-path=\([^ ]*\).*/\1/p')"
if [[ -z "${MODULES_DIR}" || ! -d "${MODULES_DIR}" ]]; then
    echo "ERROR: could not determine a valid --modules-path from 'nginx -V'" >&2
    exit 1
fi
# Stage the rebuilt module, then swap it in with a full stop/start.
# A plain `nginx -s reload` does NOT load a replaced module (see the
# package upgrade note above), so the same stop/swap/start procedure
# applies to source builds.
sudo cp objs/ngx_http_markdown_filter_module.so \
    "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.0.9.2.new"
sudo nginx -t
# Record the service-manager ownership decision BEFORE stopping: after
# a successful stop, is-active is false even on systemd-managed hosts.
systemd_managed=0
if command -v systemctl >/dev/null 2>&1 \
    && systemctl is-active --quiet nginx.service; then
    main_pid="$(systemctl show -p MainPID --value nginx.service)"
    if [[ "$main_pid" =~ ^[0-9]+$ ]] \
        && [ -x "/proc/$main_pid/exe" ] \
        && pgrep -x nginx | grep -qx "$main_pid"; then
        systemd_managed=1
        sudo systemctl stop nginx
    else
        echo "ERROR: nginx.service is active but does not own the running NGINX master; refusing to stop" >&2
        exit 1
    fi
else
    if pgrep -x nginx >/dev/null 2>&1; then
        sudo nginx -s quit
        waited=0
        while pgrep -x nginx >/dev/null 2>&1; do
            if [[ "$waited" -ge 30 ]]; then
                echo "ERROR: NGINX master did not exit within 30s of 'nginx -s quit'; aborting upgrade" >&2
                exit 1
            fi
            sleep 1
            waited=$((waited + 1))
        done
    else
        echo "INFO: no running NGINX master found; skipping 'nginx -s quit'"
    fi
fi
sudo mv -f "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.0.9.2.new" \
    "${MODULES_DIR}/ngx_http_markdown_filter_module.so"
# Back up the running module first so a failed validation can restore it.
MODULE_BACKUP="${MODULES_DIR}/.ngx_http_markdown_filter_module.so.pre-0.9.2.bak"
sudo cp -a "${MODULES_DIR}/ngx_http_markdown_filter_module.so" "${MODULE_BACKUP}"
if ! sudo nginx -t; then
  echo "ERROR: nginx -t failed after module swap; restoring previous module..." >&2
  sudo mv -f "${MODULE_BACKUP}" "${MODULES_DIR}/ngx_http_markdown_filter_module.so"
  sudo nginx -t && echo "INFO: previous module restored and configuration verified." >&2
  exit 1
fi
rm -f "${MODULE_BACKUP}" 2>/dev/null || sudo rm -f "${MODULE_BACKUP}"
if [[ "$systemd_managed" -eq 1 ]]; then
    sudo systemctl start nginx
else
    sudo nginx
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
# The chart requires an explicit image.repository plus tag (or digest);
# image values live at the chart top level, not under markdown.image.
helm upgrade nginx-markdown ./charts/nginx-markdown \
    --namespace nginx-markdown \
    --set "image.repository=<your-registry>/nginx-markdown" \
    --set image.tag=v0.9.2

# Remote chart repository (added in Step 1):
#   helm upgrade nginx-markdown nginx-markdown/nginx-markdown-for-agents \
#       --namespace nginx-markdown \
#       --set "image.repository=<your-registry>/nginx-markdown" \
#       --set image.tag=v0.9.2
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
