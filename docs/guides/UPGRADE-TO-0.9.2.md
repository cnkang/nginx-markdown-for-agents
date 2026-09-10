# Upgrade Guide: 0.9.2

## Overview

> **Platform requirement:** the upgrade and rollback procedures in this
> guide use GNU coreutils (`mv -T`, `stat -c`, `readlink -f`,
> `sha256sum`) and are therefore Linux-only. macOS/BSD hosts must run
> the equivalent commands with GNU coreutils installed (e.g.
> `brew install coreutils` and a `gmv`/`gstat`/`greadlink`/`gsha256sum`
> prefix) or adapt the commands accordingly.

This guide covers upgrading to nginx-markdown-for-agents 0.9.2 from 0.9.1.
0.9.2 is a **breaking release**. The release freezes 20 active directives and
retains five removed names as reject-only migration entries. Those entries fail
`nginx -t` with an explicit migration message until migrated. Older names that
are no longer registered fail with `unknown directive`. Review
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
# Absolute-path guard for the backup root (defensive: the value is a
# fixed constant today, but if it ever becomes environment-overridable
# a relative value such as "backup" or "." must be rejected BEFORE any
# privileged creation or cleanup operation).
case "${CONFIG_BACKUP_DIR}" in
  /*) ;;
  *)
    echo "ERROR: CONFIG_BACKUP_DIR must be an absolute path" >&2
    exit 1
    ;;
esac
NGINX_CONF_DIR="${NGINX_CONF_DIR:-/etc/nginx}"
# Path-safety guard: the rollback deletes and recreates the configuration
# root with sudo, so reject unsafe overrides.  The value must be an
# absolute path to an EXISTING directory that contains nginx.conf (a
# config root), must not be the filesystem root or the backup directory,
# and its resolved symlink target must be safe too.
case "${NGINX_CONF_DIR}" in
  /*)
    if [[ "${NGINX_CONF_DIR}" == "/" \
          || "${NGINX_CONF_DIR}" == "${CONFIG_BACKUP_DIR}" \
          || "${NGINX_CONF_DIR}" == "${CONFIG_BACKUP_DIR}/"* \
          || "${NGINX_CONF_DIR}" == "/etc" \
          || "${NGINX_CONF_DIR}" == "/var" \
          || "${NGINX_CONF_DIR}" == "/usr" \
          || "${NGINX_CONF_DIR}" == "/opt" \
          || "${NGINX_CONF_DIR}" == "/srv" \
          || "${NGINX_CONF_DIR}" == "/home" \
          || "${NGINX_CONF_DIR}" == "/root" \
          || ! -d "${NGINX_CONF_DIR}" \
          || ! -f "${NGINX_CONF_DIR}/nginx.conf" \
          || -L "${NGINX_CONF_DIR}/nginx.conf" ]]; then
      echo "ERROR: unsafe NGINX_CONF_DIR '${NGINX_CONF_DIR}' (must be an absolute existing dedicated config root containing a REAL nginx.conf, not the filesystem root, a system root, or the backup directory)" >&2
      exit 1
    fi
    ;;
  *)
    echo "ERROR: NGINX_CONF_DIR must be an absolute path" >&2
    exit 1
    ;;
esac
# Reject a symlinked backup root or ANY symlinked parent component
# BEFORE privileged creation: install -d would follow a symlink in an
# existing parent and create the directory outside the intended
# location.  -L is false for a NON-EXISTENT path, so a fresh host with
# missing parents still passes this precheck (the parents are created
# by install -d itself, which never follows a symlink it just made).
BACKUP_PARENT="$(dirname "${CONFIG_BACKUP_DIR}")"
while [[ "${BACKUP_PARENT}" != "/" && "${BACKUP_PARENT}" != "." ]]; do
  if [[ -L "${BACKUP_PARENT}" ]]; then
    echo "ERROR: CONFIG_BACKUP_DIR parent component '${BACKUP_PARENT}' is a symlink; install -d would follow it outside the intended location" >&2
    exit 1
  fi
  BACKUP_PARENT="$(dirname "${BACKUP_PARENT}")"
done
if [[ -L "${CONFIG_BACKUP_DIR}" ]]; then
  echo "ERROR: CONFIG_BACKUP_DIR must not be a symlink (resolved target would be modified by install -d)" >&2
  exit 1
fi
# Create the backup root (a fresh host may not have it) BEFORE
# canonicalizing: GNU readlink -f fails when parent components are
# missing, which would abort a fresh-host upgrade before the directory
# is created.  The resolved-target checks below still reject a backup
# root that resolves to a system root.
sudo install -d -m 0750 "${CONFIG_BACKUP_DIR}"
RESOLVED_CONF_DIR="$(readlink -f "${NGINX_CONF_DIR}")"
RESOLVED_BACKUP_DIR="$(readlink -f "${CONFIG_BACKUP_DIR}")"
if [[ "${RESOLVED_CONF_DIR}" == "/" \
      || "${RESOLVED_CONF_DIR}" == "${CONFIG_BACKUP_DIR}" \
      || "${RESOLVED_CONF_DIR}" == "${CONFIG_BACKUP_DIR}/"* \
      || "${RESOLVED_CONF_DIR}" == "/etc" \
      || "${RESOLVED_CONF_DIR}" == "/var" \
      || "${RESOLVED_CONF_DIR}" == "/usr" \
      || "${RESOLVED_CONF_DIR}" == "/opt" \
      || "${RESOLVED_CONF_DIR}" == "/srv" \
      || "${RESOLVED_CONF_DIR}" == "/home" \
      || "${RESOLVED_CONF_DIR}" == "/root" ]]; then
  echo "ERROR: NGINX_CONF_DIR resolves to an unsafe target '${RESOLVED_CONF_DIR}'" >&2
  exit 1
fi
# The configuration root and the backup directory must be disjoint:
# neither may be the other or an ancestor/descendant of the other, or a
# rollback rm -rf could delete the backup itself.
if [[ "${RESOLVED_CONF_DIR}" == "${RESOLVED_BACKUP_DIR}" \
      || "${RESOLVED_CONF_DIR}" == "${RESOLVED_BACKUP_DIR}/"* \
      || "${RESOLVED_BACKUP_DIR}" == "${RESOLVED_CONF_DIR}" \
      || "${RESOLVED_BACKUP_DIR}" == "${RESOLVED_CONF_DIR}/"* ]]; then
  echo "ERROR: NGINX_CONF_DIR and CONFIG_BACKUP_DIR must be disjoint paths (resolved: '${RESOLVED_CONF_DIR}' vs '${RESOLVED_BACKUP_DIR}')" >&2
  exit 1
fi
# The backup root itself must not be a symlink and must resolve to a
# dedicated directory: a symlinked CONFIG_BACKUP_DIR could point the
# destructive rm -rf below at a system root or the config tree.
if [[ -L "${CONFIG_BACKUP_DIR}" \
      || "${RESOLVED_BACKUP_DIR}" == "/" \
      || "${RESOLVED_BACKUP_DIR}" == "/etc" \
      || "${RESOLVED_BACKUP_DIR}" == "/var" \
      || "${RESOLVED_BACKUP_DIR}" == "/usr" \
      || "${RESOLVED_BACKUP_DIR}" == "/opt" \
      || "${RESOLVED_BACKUP_DIR}" == "/srv" \
      || "${RESOLVED_BACKUP_DIR}" == "/home" \
      || "${RESOLVED_BACKUP_DIR}" == "/root" ]]; then
  echo "ERROR: unsafe CONFIG_BACKUP_DIR '${CONFIG_BACKUP_DIR}' (must be a real dedicated directory, not a symlink or a system root; resolved: '${RESOLVED_BACKUP_DIR}')" >&2
  exit 1
fi
# Back up the ENTIRE configuration tree (not just nginx.conf + conf.d +
# modules-enabled): MIGRATION-0.9.2.md may touch any path under
# ${NGINX_CONF_DIR}, and the rollback path must be able to restore every
# migrated file and remove anything the migration added.
# Reserve the snapshot root: a pre-existing tree/ from an older backup
# would merge stale files into the snapshot, so it is removed first.
# Stage the new snapshot in a temporary sibling directory and rename it
# into place ONLY after the copy completes, so an interrupted copy can
# never leave a half-written tree/ (the previous tree/ stays valid until
# the atomic rename).
sudo rm -rf "${CONFIG_BACKUP_DIR}/tree.new"
sudo cp -a "${NGINX_CONF_DIR}/." "${CONFIG_BACKUP_DIR}/tree.new/" || {
  sudo rm -rf "${CONFIG_BACKUP_DIR}/tree.new"
  echo "ERROR: configuration snapshot copy failed; the previous snapshot (if any) is preserved at ${CONFIG_BACKUP_DIR}/tree" >&2
  exit 1
}
# Failure-safe replacement: move the previous snapshot aside, install
# the new one, and restore the old on failure — never delete the
# previous snapshot before the new one is in place.
sudo rm -rf "${CONFIG_BACKUP_DIR}/tree.old"
if [[ -e "${CONFIG_BACKUP_DIR}/tree" ]]; then
  sudo mv "${CONFIG_BACKUP_DIR}/tree" "${CONFIG_BACKUP_DIR}/tree.old"
fi
sudo mv "${CONFIG_BACKUP_DIR}/tree.new" "${CONFIG_BACKUP_DIR}/tree" || {
  if [[ -e "${CONFIG_BACKUP_DIR}/tree.old" ]]; then
    if ! sudo mv "${CONFIG_BACKUP_DIR}/tree.old" "${CONFIG_BACKUP_DIR}/tree"; then
      echo "ERROR: could not install the new configuration snapshot AND could not restore the previous one; both artifacts are preserved at ${CONFIG_BACKUP_DIR}/tree.new and ${CONFIG_BACKUP_DIR}/tree.old — restore manually" >&2
      exit 1
    fi
  fi
  echo "ERROR: could not install the new configuration snapshot; the previous snapshot was restored" >&2
  exit 1
}
sudo rm -rf "${CONFIG_BACKUP_DIR}/tree.old"
# Record whether the configuration root itself is a symlink: the rollback
# restores the tree wholesale (rm -rf + cp -a), which would replace a
# symlink root with a real directory and orphan the original target.
if [[ -L "${NGINX_CONF_DIR}" ]]; then
  readlink "${NGINX_CONF_DIR}" | sudo tee "${CONFIG_BACKUP_DIR}/tree-root-link" >/dev/null
else
  sudo rm -f "${CONFIG_BACKUP_DIR}/tree-root-link"
fi
MODULE_PATH="$MODULES_DIR/ngx_http_markdown_filter_module.so"
MODULE_BACKUP="${MODULE_PATH}.0.9.1.bak"
if [[ ! -f "${MODULE_PATH}" ]]; then
  echo "ERROR: current module is missing: ${MODULE_PATH}" >&2
  exit 1
fi
if [[ -e "${MODULE_BACKUP}" ]]; then
  if ! sudo -n cmp -s -- "${MODULE_BACKUP}" "${MODULE_PATH}"; then
    echo "ERROR: existing backup differs from the installed module or cannot be read; inspect it before upgrading" >&2
    exit 1
  fi
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

0.9.2 is a breaking configuration release (20 active directives plus five
reject-only migration entries). Before validating or restarting
NGINX, apply the 0.9.2 migration:

```bash
# Apply the 0.9.2 directive changes documented in MIGRATION-0.9.2.md to
# the STAGED COPY below (${STAGED_ROOT}) BEFORE validation: removed
# profile/OTel directives, consolidated markdown_limits keys, and the
# default-policy actions.  The runtime dynconf file/watcher was removed;
# move its values to static directives and validate with nginx -t before
# a controlled reload.
# (The markdown_streaming_engine -> markdown_streaming rename happened in
# 0.9.1, not 0.9.2; 0.9.2 removed markdown_stream_threshold and
# markdown_streaming_zero_copy.)
# See docs/guides/MIGRATION-0.9.2.md for the complete mapping.
# After staged validation succeeds, repeat the SAME edits on the active
# tree (${NGINX_CONF_DIR}) before the module swap.
```

Validate the migrated configuration with the staged 0.9.2 module BEFORE
stopping NGINX: back up the running module first (see the source-build
sequence below), then run `nginx -t` against a temporary configuration
whose `load_module` entry points to the staged `.so` — a plain
`sudo nginx -t` would still load the ACTIVE (0.9.1) module, so it cannot
prove the migrated syntax is valid under the 0.9.2 binary:

```bash
STAGED_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/nginx-0.9.2-staged-XXXXXX")"
# The staged tree contains root-owned files (sudo cp -a / sudo sed), so
# cleanup needs sudo; preserve the original exit status even if the
# cleanup itself fails.
trap 'rc=$?; sudo rm -rf -- "$STAGED_ROOT" || :; exit "$rc"' EXIT
sudo cp -a "${NGINX_CONF_DIR}/." "${STAGED_ROOT}/"
# Apply the deterministic part of MIGRATION-0.9.2.md to the STAGED COPY:
# remove the five retired directives (three dynconf + two prune
# selectors).  The remaining migration items are consumer-side (reason
# integers, diagnostics schema) and need no config edit.  The staged
# tree is a disposable copy: edit in place WITHOUT .bak backups, so no
# stale backup file can be scanned below, counted as a second
# load_module entry, or loaded by a wildcard include during nginx -t.
if sudo grep -rlE "markdown_dynamic_config|markdown_dynamic_config_path|markdown_dynconf_dry_run|markdown_prune_selectors|markdown_prune_protection_selectors" "${STAGED_ROOT}" 2>/dev/null \
    | while read -r staged_conf; do
        sudo sed -i -E \
            -e "s|^[[:space:]]*markdown_dynamic_config[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_dynamic_config_path[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_dynconf_dry_run[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_prune_selectors[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_prune_protection_selectors[[:space:]]+[^;]*;||" \
            "${staged_conf}" || exit 1
      done; then
    pipeline_status=(0 0)
else
    pipeline_status=("${PIPESTATUS[@]}")
fi
grep_rc="${pipeline_status[0]}"
sed_rc="${pipeline_status[1]}"
if { [[ "$grep_rc" -ne 0 ]] && [[ "$grep_rc" -ne 1 ]]; } || [[ "$sed_rc" -ne 0 ]]; then
  echo "ERROR: migration edit failed (grep=$grep_rc sed=$sed_rc)" >&2
  exit 1
fi
# A configuration with NO retired directives is already 0.9.2 compliant:
# grep exit status 1 (no match) is accepted by the check above; any
# other grep or sed failure aborts the upgrade.
# Rewrite the Markdown module's load_module entry across the WHOLE
# staged tree (the entry may live in nginx.conf or an included file
# such as modules-enabled/*.conf), then verify exactly one staged entry.
sudo grep -rl "ngx_http_markdown_filter_module\.so" "${STAGED_ROOT}"  2>/dev/null \
    | while read -r staged_conf; do
        # The staged tree is a disposable copy: edit in place WITHOUT
        # .bak backups, so no stale backup file can be scanned below,
        # counted as a second load_module entry, or loaded by a
        # wildcard include during nginx -t.
        sudo sed -i "s|^[[:space:]]*load_module[[:space:]]\\+.*ngx_http_markdown_filter_module\\.so.*|load_module ${MODULES_DIR}/.ngx_http_markdown_filter_module.so.0.9.2.new;|" \
            "${staged_conf}"
      done || true
staged_loads="$(sudo grep -rc --exclude='*.bak' --exclude='*.disabled' 'ngx_http_markdown_filter_module.so.0.9.2.new' "${STAGED_ROOT}" | awk -F: '{s+=$2} END {print s+0}')"
if [[ "${staged_loads}" -ne 1 ]]; then
    echo "ERROR: expected exactly one Markdown load_module entry in the staged config tree, found ${staged_loads}" >&2
    exit 1
fi
sudo nginx -t -c "${STAGED_ROOT}/nginx.conf"
# Staged validation succeeded.  Apply the SAME migration to the ACTIVE
# tree now (before the module swap): remove the five retired
# directives.  The active load_module entry stays as-is — it already
# references the canonical module path, which the swap below replaces
# with the 0.9.2 binary.
# Snapshot the active tree FIRST: the sed edits below are in-place, so a
# mid-migration failure must be able to restore the untouched tree
# instead of leaving a partially migrated configuration.  The snapshot
# lives in a UNIQUE sibling directory (mktemp): a fixed
# .migrate-backup name would nest inside the active root when
# NGINX_CONF_DIR carries a trailing slash, and a stale snapshot from an
# interrupted run would be merged by cp -a instead of replaced.
# For a symlinked root, resolve the target FIRST and create the
# snapshot beside the RESOLVED parent: a snapshot created beside the
# symlink path itself can land INSIDE the resolved target (e.g.
# /data/link -> /data), which the restore path would then delete.
SNAPSHOT_SRC="${NGINX_CONF_DIR}"
SNAPSHOT_PARENT="$(dirname "${NGINX_CONF_DIR}")"
if [[ -L "${NGINX_CONF_DIR}" ]]; then
  SNAPSHOT_SRC="$(readlink -f "${NGINX_CONF_DIR}")"
  SNAPSHOT_PARENT="$(dirname "${SNAPSHOT_SRC}")"
fi
MIGRATE_BACKUP="$(sudo mktemp -d "${SNAPSHOT_PARENT}/.nginx-migrate-XXXXXX")" || {
  echo "ERROR: could not allocate a migration snapshot directory; aborting" >&2
  exit 1
}
sudo cp -a "${SNAPSHOT_SRC}/." "${MIGRATE_BACKUP}/" || {
  sudo rm -rf "${MIGRATE_BACKUP}"
  echo "ERROR: could not snapshot the active configuration tree before migration; aborting" >&2
  exit 1
}
# Interruption-safe recovery: from this point until the migration
# completes, any exit (including a signal during the in-place sed
# edits) restores the untouched snapshot — the root may still EXIST
# but be partially edited, so the trap replaces it wholesale whenever
# MIGRATE_ACTIVE is set.  The trap also keeps cleaning STAGED_ROOT
# (it replaces the earlier STAGED_ROOT-only trap in this bash block).
# Disarmed after the migration succeeds.
# The restore is symlink-aware: a symlinked root is recreated as a link
# and the snapshot restored into the RESOLVED TARGET, preserving the
# root's symlink identity on every failure path.
migrate_restore() {
  if [[ "$MIGRATE_ACTIVE" -ne 1 ]] || [[ ! -d "$MIGRATE_BACKUP" ]]; then
    return 0
  fi
  if [[ -L "${NGINX_CONF_DIR}" ]]; then
    ROOT_LINK_TARGET="$(readlink -f "${NGINX_CONF_DIR}")"
    if [[ "${MIGRATE_BACKUP}" == "${ROOT_LINK_TARGET}" \
          || "${MIGRATE_BACKUP}" == "${ROOT_LINK_TARGET}/"* ]]; then
      return 0
    fi
    # Atomic link swap with NO dangling window: the replacement link
    # points DIRECTLY at the snapshot directory (the snapshot is itself
    # a unique staging path), and the old target is deleted only AFTER
    # the swap succeeds.  Every failure leaves the active link
    # untouched and the snapshot preserved for manual recovery.
    if ! sudo rm -f "${NGINX_CONF_DIR}.link-new" 2>/dev/null; then
      return 0
    fi
    if ! sudo ln -s "${MIGRATE_BACKUP}" "${NGINX_CONF_DIR}.link-new" 2>/dev/null; then
      return 0
    fi
    if ! sudo mv -Tf "${NGINX_CONF_DIR}.link-new" "${NGINX_CONF_DIR}" 2>/dev/null; then
      sudo rm -f "${NGINX_CONF_DIR}.link-new" 2>/dev/null || true
      return 0
    fi
    # The active link now points at the snapshot; the old target is
    # orphaned and safe to remove (a removal failure only leaves a
    # stale directory, never a dangling link).
    sudo rm -rf "${ROOT_LINK_TARGET}" 2>/dev/null || true
  else
    # Non-symlink root: move the active tree to a UNIQUE sibling,
    # install the snapshot, and roll back on failure.
    ROLLBACK_OLD="$(sudo mktemp -d "$(dirname "${NGINX_CONF_DIR}")/.nginx-old-XXXXXX")" 2>/dev/null || return 0
    sudo rmdir "${ROLLBACK_OLD}" 2>/dev/null || true
    if ! sudo mv "${NGINX_CONF_DIR}" "${ROLLBACK_OLD}" 2>/dev/null; then
      return 0
    fi
    if ! sudo mv "${MIGRATE_BACKUP}" "${NGINX_CONF_DIR}" 2>/dev/null; then
      sudo mv "${ROLLBACK_OLD}" "${NGINX_CONF_DIR}" 2>/dev/null || true
      return 0
    fi
    sudo rm -rf "${ROLLBACK_OLD}" 2>/dev/null || true
  fi
}
MIGRATE_ACTIVE=1
trap 'rc=$?; sudo rm -rf -- "$STAGED_ROOT" || :; migrate_restore; exit "$rc"' EXIT
if sudo grep -rlE "markdown_dynamic_config|markdown_dynamic_config_path|markdown_dynconf_dry_run|markdown_prune_selectors|markdown_prune_protection_selectors" "${NGINX_CONF_DIR}" 2>/dev/null \
    | while read -r active_conf; do
        sudo sed -i -E \
            -e "s|^[[:space:]]*markdown_dynamic_config[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_dynamic_config_path[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_dynconf_dry_run[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_prune_selectors[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_prune_protection_selectors[[:space:]]+[^;]*;||" \
            "${active_conf}" || exit 1
      done; then
    pipeline_status=(0 0)
else
    pipeline_status=("${PIPESTATUS[@]}")
fi
grep_rc="${pipeline_status[0]}"
sed_rc="${pipeline_status[1]}"
if { [[ "$grep_rc" -ne 0 ]] && [[ "$grep_rc" -ne 1 ]]; } || [[ "$sed_rc" -ne 0 ]]; then
  echo "ERROR: migration edit failed (grep=$grep_rc sed=$sed_rc); restoring the untouched active tree" >&2
  if [[ -L "${NGINX_CONF_DIR}" ]]; then
    # Symlinked root: recreate the link and restore the snapshot into
    # the RESOLVED TARGET, preserving the root's symlink identity.
    ROOT_LINK_TARGET="$(readlink -f "${NGINX_CONF_DIR}")"
    # The migration snapshot must NOT live inside the resolved target:
    # deleting the target below would delete the snapshot itself.
    if [[ "${MIGRATE_BACKUP}" == "${ROOT_LINK_TARGET}"           || "${MIGRATE_BACKUP}" == "${ROOT_LINK_TARGET}/"* ]]; then
      echo "ERROR: migration snapshot ${MIGRATE_BACKUP} is inside the resolved target ${ROOT_LINK_TARGET}; restore manually" >&2
      exit 1
    fi
    # Atomic link swap with NO dangling window: the replacement link
    # points DIRECTLY at the snapshot directory (the snapshot is itself
    # a unique staging path), and the old target is deleted only AFTER
    # the swap succeeds.  Every failure leaves the active link
    # untouched and the snapshot preserved for manual recovery.
    if ! sudo rm -f "${NGINX_CONF_DIR}.link-new" 2>/dev/null; then
      echo "ERROR: could not clear the staging symlink ${NGINX_CONF_DIR}.link-new; restore manually from ${MIGRATE_BACKUP}" >&2
      exit 1
    fi
    sudo ln -s "${MIGRATE_BACKUP}" "${NGINX_CONF_DIR}.link-new" || {
      echo "ERROR: could not create the replacement configuration-root symlink; the active link is untouched and the resolved target was NOT deleted. Restore manually from ${MIGRATE_BACKUP}" >&2
      exit 1
    }
    sudo mv -Tf "${NGINX_CONF_DIR}.link-new" "${NGINX_CONF_DIR}" || {
      sudo rm -f "${NGINX_CONF_DIR}.link-new" 2>/dev/null || true
      echo "ERROR: could not replace the configuration-root symlink; the active link is untouched. Restore manually from ${MIGRATE_BACKUP}" >&2
      exit 1
    }
    # The active link now points at the snapshot; the old target is
    # orphaned and safe to remove (a removal failure only leaves a
    # stale directory, never a dangling link).
    sudo rm -rf "${ROOT_LINK_TARGET}" 2>/dev/null || true
  else
    sudo rm -rf "${NGINX_CONF_DIR}" 2>/dev/null || true
    sudo mv "${MIGRATE_BACKUP}" "${NGINX_CONF_DIR}" 2>/dev/null || {
      echo "ERROR: could not restore the active tree from ${MIGRATE_BACKUP}; restore manually" >&2
      exit 1
    }
  fi
  # The explicit restore above already replaced the active root; disarm
  # the restoration trap BEFORE exiting so it cannot rm -rf the freshly
  # restored tree and fail the second mv.
  MIGRATE_ACTIVE=0
  exit 1
fi
# Migration succeeded: mark the migration complete (the trap no longer
# restores), clean the staged tree and the snapshot explicitly (the
# trap that used to clean them was replaced), and disarm the trap ONLY
# after both cleanups succeed — a failed cleanup is reported so the
# root-owned temporary directory is not silently leaked.
MIGRATE_ACTIVE=0
if ! sudo rm -rf -- "$STAGED_ROOT" 2>/dev/null; then
  echo "ERROR: could not clean the staged tree $STAGED_ROOT; remove it manually" >&2
  exit 1
fi
if ! sudo rm -rf "${MIGRATE_BACKUP}" 2>/dev/null; then
  echo "ERROR: could not clean the migration snapshot ${MIGRATE_BACKUP}; remove it manually" >&2
  exit 1
fi
trap - EXIT
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
# Restore the pre-migration configuration tree (staged, symlink-aware,
# every operation checked) so the old module is never left paired with
# the migrated configuration.  Fails with manual-recovery guidance.
restore_pre_migration_tree() {
  if [[ -L "${NGINX_CONF_DIR}" ]]; then
    ROOT_LINK_TARGET="$(readlink -f "${NGINX_CONF_DIR}")"
    RESTORE_STAGED="$(sudo mktemp -d "$(dirname "${ROOT_LINK_TARGET}")/.nginx-restore-XXXXXX")" || {
      echo "ERROR: could not allocate a restore staging directory; restore manually from ${CONFIG_BACKUP_DIR}/tree" >&2
      exit 1
    }
    sudo cp -a "${CONFIG_BACKUP_DIR}/tree/." "${RESTORE_STAGED}/" || {
      echo "ERROR: could not stage the pre-migration tree; restore manually from ${CONFIG_BACKUP_DIR}/tree" >&2
      exit 1
    }
    sudo rm -rf "${ROOT_LINK_TARGET}" 2>/dev/null || true
    sudo mv "${RESTORE_STAGED}" "${ROOT_LINK_TARGET}" || {
      echo "ERROR: could not install the pre-migration tree; restore manually from ${CONFIG_BACKUP_DIR}/tree" >&2
      exit 1
    }
    sudo rm -f "${NGINX_CONF_DIR}.link-new" 2>/dev/null || true
    sudo ln -s "${ROOT_LINK_TARGET}" "${NGINX_CONF_DIR}.link-new" || {
      echo "ERROR: could not recreate the configuration-root symlink; restore manually from ${CONFIG_BACKUP_DIR}/tree" >&2
      exit 1
    }
    sudo mv -Tf "${NGINX_CONF_DIR}.link-new" "${NGINX_CONF_DIR}" || {
      echo "ERROR: could not replace the configuration-root symlink; restore manually from ${CONFIG_BACKUP_DIR}/tree" >&2
      exit 1
    }
  else
    RESTORE_STAGED="$(sudo mktemp -d "$(dirname "${NGINX_CONF_DIR%/}")/.nginx-restore-XXXXXX")" || {
      echo "ERROR: could not allocate a restore staging directory; restore manually from ${CONFIG_BACKUP_DIR}/tree" >&2
      exit 1
    }
    sudo cp -a "${CONFIG_BACKUP_DIR}/tree/." "${RESTORE_STAGED}/" || {
      echo "ERROR: could not stage the pre-migration tree; restore manually from ${CONFIG_BACKUP_DIR}/tree" >&2
      exit 1
    }
    sudo rm -rf "${NGINX_CONF_DIR}" 2>/dev/null || true
    sudo mv "${RESTORE_STAGED}" "${NGINX_CONF_DIR}" || {
      echo "ERROR: could not install the pre-migration tree; restore manually from ${CONFIG_BACKUP_DIR}/tree" >&2
      exit 1
    }
  fi
}
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
    # The active configuration was already migrated; restore the
    # pre-migration tree so the old module is not left paired with the
    # migrated configuration.
    restore_pre_migration_tree
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
        # Same pairing guard: restore the pre-migration tree.
        restore_pre_migration_tree
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
    "${MODULES_DIR}/ngx_http_markdown_filter_module.so" || {
  echo "ERROR: module swap failed; the old module is still installed and the active configuration is migrated. Restoring the pre-migration tree so the pair stays consistent" >&2
  restore_pre_migration_tree
  exit 1
}
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
  # Restore the ENTIRE backed-up configuration tree wholesale: the
  # backup (step 3) captured ${NGINX_CONF_DIR}/. as tree/, so restoring
  # it replaces every migrated file and removes anything the migration
  # added, matching the source-build rollback below.
  # Build the complete restored tree in a sibling staging path FIRST and
  # validate it, so a failure leaves the active tree untouched; only a
  # fully staged and validated tree replaces the active root.  The
  # staging dir sits BESIDE the configuration root so the final mv stays
  # on the same filesystem (a TMPDIR staging dir would cross devices and
  # fail the atomic rename).  For a symlinked root, resolve the target
  # FIRST and stage beside the TARGET's parent so the mv cannot cross
  # devices.
  if [[ -f "${CONFIG_BACKUP_DIR}/tree-root-link" ]]; then
    ROOT_LINK_TARGET="$(readlink -f "${NGINX_CONF_DIR}")"
    RESTORE_STAGED="$(sudo mktemp -d "$(dirname "${ROOT_LINK_TARGET}")/.nginx-restore-XXXXXX")"
  else
    RESTORE_STAGED="$(sudo mktemp -d "$(dirname "${NGINX_CONF_DIR%/}")/.nginx-restore-XXXXXX")"
  fi
  # Dedicated cleanup trap: any unguarded failure under set -euo pipefail
  # must still remove the staging tree; disarmed after a successful move.
  RESTORE_CLEANUP_SET=1
  trap 'rc=$?; sudo rm -rf -- "$RESTORE_STAGED" || :; exit "$rc"' EXIT
  sudo cp -a "${CONFIG_BACKUP_DIR}/tree/." "${RESTORE_STAGED}/" 2>/dev/null || {
    sudo rm -rf "${RESTORE_STAGED}"
    echo "ERROR: configuration restore staging failed; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
    exit 1
  }
  if ! sudo nginx -t -c "${RESTORE_STAGED}/nginx.conf"; then
    sudo rm -rf "${RESTORE_STAGED}"
    echo "ERROR: restored configuration fails validation; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
    exit 1
  fi
  # Staging validated: atomically replace the active root.  A symlink
  # root is recreated as a link and the staged tree atomically moved
  # into its resolved target (old target preserved until the swap
  # succeeds); a real directory is replaced wholesale with mv.
  # The mktemp staging dir is user-owned mode-0700; restore the active
  # root's owner/group/mode onto the staged ROOT DIRECTORY ONLY before
  # the rename (cp -a already preserved file-specific ownership and
  # modes beneath it; a recursive chown/chmod would overwrite them).
  ROOT_OWNER="$(stat -c '%U:%G' "${NGINX_CONF_DIR}")"
  ROOT_MODE="$(stat -c '%a' "${NGINX_CONF_DIR}")"
  sudo chown "${ROOT_OWNER}" "${RESTORE_STAGED}"
  sudo chmod "${ROOT_MODE}" "${RESTORE_STAGED}"
  if [[ -f "${CONFIG_BACKUP_DIR}/tree-root-link" ]]; then
    # Create a temporary symlink and atomically rename it over the
    # active link (GNU mv -T uses rename(2)), so a failure never
    # leaves the configuration root without a link.
    ROOT_LINK_TARGET="$(sudo cat "${CONFIG_BACKUP_DIR}/tree-root-link")" || {
      echo "ERROR: could not read the configuration-root symlink marker ${CONFIG_BACKUP_DIR}/tree-root-link; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    }
    if [[ -z "${ROOT_LINK_TARGET}" ]]; then
      echo "ERROR: the configuration-root symlink marker is empty; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    fi
    sudo ln -s "${ROOT_LINK_TARGET}" "${NGINX_CONF_DIR}.link-new" || {
      # Remove any stale temporary link (a previous failed attempt may
      # have left one) and the staged tree, then report manual recovery.
      sudo rm -f "${NGINX_CONF_DIR}.link-new"
      sudo rm -rf "${RESTORE_STAGED}"
      echo "ERROR: could not create the replacement configuration-root link; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    }
    sudo mv -Tf "${NGINX_CONF_DIR}.link-new" "${NGINX_CONF_DIR}" || {
      sudo rm -f "${NGINX_CONF_DIR}.link-new"
      sudo rm -rf "${RESTORE_STAGED}"
      echo "ERROR: could not replace the configuration-root link; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    }
    ROOT_LINK_TARGET="$(readlink -f "${NGINX_CONF_DIR}")"
    # The staging dir was created beside the SYMLINK; the final mv moves
    # it to the resolved TARGET, which may be on a different filesystem.
    # Verify device identity and fail closed before attempting the swap
    # (a cross-device mv would degrade to a non-atomic copy).
    if [[ "$(stat -c '%d' "${RESTORE_STAGED}")" != "$(stat -c '%d' "${ROOT_LINK_TARGET}")" ]]; then
      sudo rm -rf "${RESTORE_STAGED}"
      echo "ERROR: configuration root target is on a different filesystem than the staging dir; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    fi
    if [[ -e "${ROOT_LINK_TARGET}" || -L "${ROOT_LINK_TARGET}" ]]; then
      # Unique sibling rollback path: a fixed .rollback-old name would
      # be deleted before the replacement succeeds, losing the
      # last-known-good tree on an interrupted retry.
      ROLLBACK_OLD="$(sudo mktemp -d "$(dirname "${ROOT_LINK_TARGET}")/.nginx-old-XXXXXX")" || {
        sudo rm -rf "${RESTORE_STAGED}"
        echo "ERROR: could not allocate a rollback directory; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
        exit 1
      }
      sudo rmdir "${ROLLBACK_OLD}" 2>/dev/null || true
      sudo mv -f "${ROOT_LINK_TARGET}" "${ROLLBACK_OLD}" 2>/dev/null || {
        sudo rm -rf "${RESTORE_STAGED}"
        echo "ERROR: configuration swap failed; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
        exit 1
      }
    fi
    sudo mv -f "${RESTORE_STAGED}" "${ROOT_LINK_TARGET}" 2>/dev/null || {
      if [[ -n "${ROLLBACK_OLD:-}" && -e "${ROLLBACK_OLD}" ]]; then
        sudo mv -f "${ROLLBACK_OLD}" "${ROOT_LINK_TARGET}"
      fi
      sudo rm -rf "${RESTORE_STAGED}"
      echo "ERROR: configuration swap failed; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    }
    sudo rm -rf "${ROLLBACK_OLD:-}" 2>/dev/null || true
  else
    ROLLBACK_OLD="$(sudo mktemp -d "$(dirname "${NGINX_CONF_DIR%/}")/.nginx-old-XXXXXX")" || {
      sudo rm -rf "${RESTORE_STAGED}"
      echo "ERROR: could not allocate a rollback directory; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    }
    sudo rmdir "${ROLLBACK_OLD}" 2>/dev/null || true
    sudo mv -f "${NGINX_CONF_DIR}" "${ROLLBACK_OLD}" 2>/dev/null || {
      sudo rm -rf "${RESTORE_STAGED}"
      echo "ERROR: configuration swap failed; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    }
    sudo mv -f "${RESTORE_STAGED}" "${NGINX_CONF_DIR}" 2>/dev/null || {
      sudo mv -f "${ROLLBACK_OLD}" "${NGINX_CONF_DIR}"
      sudo rm -rf "${RESTORE_STAGED}"
      echo "ERROR: configuration swap failed; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    }
    sudo rm -rf "${ROLLBACK_OLD}" 2>/dev/null || true
  fi
  if sudo nginx -t; then
    echo "INFO: previous module and configuration restored and verified." >&2
  else
    echo "ERROR: restored module and configuration still fail validation; do not start NGINX. Restore manually from ${MODULE_BACKUP} and ${CONFIG_BACKUP_DIR}." >&2
    exit 1
  fi
  # The rollback left NGINX stopped; restart it on the restored, validated
  # pair using the ownership decision recorded before the stop.
  if [[ "$systemd_managed" -eq 1 ]]; then
    sudo systemctl start nginx
  else
    sudo nginx
  fi
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
rustup toolchain install 1.98.1
rustup default 1.98.1
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
# Self-contained variable definitions: this block may be run standalone,
# so CONFIG_BACKUP_DIR / NGINX_CONF_DIR must be defined here (and pass
# the same path-safety guard as the package flow) before any backup or
# rollback command expands them.
CONFIG_BACKUP_DIR="${CONFIG_BACKUP_DIR:-/var/backups/nginx-markdown-0.9.1}"
NGINX_CONF_DIR="${NGINX_CONF_DIR:-/etc/nginx}"
case "${NGINX_CONF_DIR}" in
  /*)
    if [[ "${NGINX_CONF_DIR}" == "/" \
          || "${NGINX_CONF_DIR}" == "/etc" \
          || "${NGINX_CONF_DIR}" == "/var" \
          || "${NGINX_CONF_DIR}" == "/usr" \
          || "${NGINX_CONF_DIR}" == "/opt" \
          || "${NGINX_CONF_DIR}" == "/srv" \
          || "${NGINX_CONF_DIR}" == "/home" \
          || "${NGINX_CONF_DIR}" == "/root" \
          || "${NGINX_CONF_DIR}" == "${CONFIG_BACKUP_DIR}" \
          || "${NGINX_CONF_DIR}" == "${CONFIG_BACKUP_DIR}/"* \
          || ! -d "${NGINX_CONF_DIR}" \
          || ! -f "${NGINX_CONF_DIR}/nginx.conf" \
          || -L "${NGINX_CONF_DIR}/nginx.conf" ]]; then
      echo "ERROR: unsafe NGINX_CONF_DIR '${NGINX_CONF_DIR}' (must be an absolute existing dedicated config root containing a REGULAR nginx.conf, not a system root, the backup directory, or a symlinked nginx.conf)" >&2
      exit 1
    fi
    ;;
  *)
    echo "ERROR: NGINX_CONF_DIR must be an absolute path" >&2
    exit 1
    ;;
esac
# Same resolved, bidirectional disjoint-path validation as the package
# flow: the backup directory must not live inside (or equal) the active
# configuration root, or a rollback rm -rf could delete the backup.
# RESOLVED_BACKUP_DIR is computed AFTER the backup root is created
# (below): GNU readlink -f fails when parent components are missing,
# which would abort a fresh-host upgrade before install -d runs.
RESOLVED_CONF_DIR="$(readlink -f "${NGINX_CONF_DIR}")"
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
# Back up the active NGINX configuration tree and the running module
# BEFORE stopping NGINX so a failed validation or start can always
# restore the pre-upgrade state.  Use the SAME tree snapshot contract as
# the package flow (${CONFIG_BACKUP_DIR}/tree + tree-root-link), so the
# rollback below restores the whole tree wholesale.
# Validate the environment-overridden backup root BEFORE the rm -rf:
# it must be an absolute, dedicated path, distinct from the config root.
case "${CONFIG_BACKUP_DIR}" in
  /*)
    if [[ "${CONFIG_BACKUP_DIR}" == "/" \
          || "${CONFIG_BACKUP_DIR}" == "/etc" \
          || "${CONFIG_BACKUP_DIR}" == "/var" \
          || "${CONFIG_BACKUP_DIR}" == "/usr" \
          || "${CONFIG_BACKUP_DIR}" == "/opt" \
          || "${CONFIG_BACKUP_DIR}" == "/srv" \
          || "${CONFIG_BACKUP_DIR}" == "/home" \
          || "${CONFIG_BACKUP_DIR}" == "/root" \
          || "${CONFIG_BACKUP_DIR}" == "${NGINX_CONF_DIR}" \
          || "${CONFIG_BACKUP_DIR}" == "${NGINX_CONF_DIR}/"* \
          || "${NGINX_CONF_DIR}" == "${CONFIG_BACKUP_DIR}/"* ]]; then
      echo "ERROR: unsafe CONFIG_BACKUP_DIR '${CONFIG_BACKUP_DIR}' (must be an absolute dedicated backup root, not a system root, and distinct from NGINX_CONF_DIR)" >&2
      exit 1
    fi
    ;;
  *)
    echo "ERROR: CONFIG_BACKUP_DIR must be an absolute path" >&2
    exit 1
    ;;
esac
# Reject a symlinked backup root or ANY symlinked parent component
# BEFORE privileged creation: install -d would follow a symlink in an
# existing parent and create the directory outside the intended
# location.  -L is false for a NON-EXISTENT path, so a fresh host with
# missing parents still passes this precheck (the parents are created
# by install -d itself, which never follows a symlink it just made).
BACKUP_PARENT="$(dirname "${CONFIG_BACKUP_DIR}")"
while [[ "${BACKUP_PARENT}" != "/" && "${BACKUP_PARENT}" != "." ]]; do
  if [[ -L "${BACKUP_PARENT}" ]]; then
    echo "ERROR: CONFIG_BACKUP_DIR parent component '${BACKUP_PARENT}' is a symlink; install -d would follow it outside the intended location" >&2
    exit 1
  fi
  BACKUP_PARENT="$(dirname "${BACKUP_PARENT}")"
done
if [[ -L "${CONFIG_BACKUP_DIR}" ]]; then
  echo "ERROR: CONFIG_BACKUP_DIR must not be a symlink (resolved target would be modified by install -d)" >&2
  exit 1
fi
# Create the backup root (a fresh host may not have it) BEFORE
# canonicalizing: GNU readlink -f fails when parent components are
# missing, which would abort a fresh-host upgrade before the directory
# is created.  The resolved-target checks below still reject a backup
# root that resolves to a system root.
sudo install -d -m 0750 "${CONFIG_BACKUP_DIR}"
# The backup root must not be a symlink and must resolve to a dedicated
# directory: a symlinked CONFIG_BACKUP_DIR could point the destructive
# rm -rf below at a system root or the config tree.
RESOLVED_BACKUP_DIR="$(readlink -f "${CONFIG_BACKUP_DIR}")"
if [[ -L "${CONFIG_BACKUP_DIR}" \
      || "${RESOLVED_BACKUP_DIR}" == "/" \
      || "${RESOLVED_BACKUP_DIR}" == "/etc" \
      || "${RESOLVED_BACKUP_DIR}" == "/var" \
      || "${RESOLVED_BACKUP_DIR}" == "/usr" \
      || "${RESOLVED_BACKUP_DIR}" == "/opt" \
      || "${RESOLVED_BACKUP_DIR}" == "/srv" \
      || "${RESOLVED_BACKUP_DIR}" == "/home" \
      || "${RESOLVED_BACKUP_DIR}" == "/root" ]]; then
  echo "ERROR: unsafe CONFIG_BACKUP_DIR '${CONFIG_BACKUP_DIR}' (must be a real dedicated directory, not a symlink or a system root; resolved: '${RESOLVED_BACKUP_DIR}')" >&2
  exit 1
fi
# Disjoint-path validation (moved here so RESOLVED_BACKUP_DIR is
# computed after the backup root exists):
if [[ "${RESOLVED_CONF_DIR}" == "${RESOLVED_BACKUP_DIR}" \
      || "${RESOLVED_CONF_DIR}" == "${RESOLVED_BACKUP_DIR}/"* \
      || "${RESOLVED_BACKUP_DIR}" == "${RESOLVED_CONF_DIR}" \
      || "${RESOLVED_BACKUP_DIR}" == "${RESOLVED_CONF_DIR}/"* ]]; then
  echo "ERROR: NGINX_CONF_DIR and CONFIG_BACKUP_DIR must be disjoint paths (resolved: '${RESOLVED_CONF_DIR}' vs '${RESOLVED_BACKUP_DIR}')" >&2
  exit 1
fi
sudo rm -rf "${CONFIG_BACKUP_DIR}/tree.new"
sudo cp -a "${NGINX_CONF_DIR}/." "${CONFIG_BACKUP_DIR}/tree.new/" || {
  sudo rm -rf "${CONFIG_BACKUP_DIR}/tree.new"
  echo "ERROR: configuration snapshot copy failed; the previous snapshot (if any) is preserved at ${CONFIG_BACKUP_DIR}/tree" >&2
  exit 1
}
# Failure-safe replacement: move the previous snapshot aside, install
# the new one, and restore the old on failure — never delete the
# previous snapshot before the new one is in place.
sudo rm -rf "${CONFIG_BACKUP_DIR}/tree.old"
if [[ -e "${CONFIG_BACKUP_DIR}/tree" ]]; then
  sudo mv "${CONFIG_BACKUP_DIR}/tree" "${CONFIG_BACKUP_DIR}/tree.old"
fi
sudo mv "${CONFIG_BACKUP_DIR}/tree.new" "${CONFIG_BACKUP_DIR}/tree" || {
  if [[ -e "${CONFIG_BACKUP_DIR}/tree.old" ]]; then
    if ! sudo mv "${CONFIG_BACKUP_DIR}/tree.old" "${CONFIG_BACKUP_DIR}/tree"; then
      echo "ERROR: could not install the new configuration snapshot AND could not restore the previous one; both artifacts are preserved at ${CONFIG_BACKUP_DIR}/tree.new and ${CONFIG_BACKUP_DIR}/tree.old — restore manually" >&2
      exit 1
    fi
  fi
  echo "ERROR: could not install the new configuration snapshot; the previous snapshot was restored" >&2
  exit 1
}
sudo rm -rf "${CONFIG_BACKUP_DIR}/tree.old"
if [[ -L "${NGINX_CONF_DIR}" ]]; then
  readlink "${NGINX_CONF_DIR}" | sudo tee "${CONFIG_BACKUP_DIR}/tree-root-link" >/dev/null
else
  sudo rm -f "${CONFIG_BACKUP_DIR}/tree-root-link"
fi
# Apply MIGRATION-0.9.2.md to a STAGED COPY of the configuration FIRST
# and validate it against the staged module, so a validation failure
# cannot leave the ACTIVE tree migrated while the old module is still
# installed.  Only after staged validation succeeds is the same
# migration applied to the active tree.
sudo cp objs/ngx_http_markdown_filter_module.so \
    "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.0.9.2.new"
STAGED_ROOT="$("$(command -v mktemp)" -d "${TMPDIR:-/tmp}/nginx-0.9.2-staged-XXXXXX")"
# The staged tree contains root-owned files (sudo cp -a / sudo sed), so
# cleanup needs sudo; preserve the original exit status even if the
# cleanup itself fails.
trap 'rc=$?; sudo rm -rf -- "$STAGED_ROOT" || :; exit "$rc"' EXIT
# Copy the live config dir into the staged tree, apply MIGRATION-0.9.2.md
# to the COPY, rewrite its load_module entry to reference the staged .so,
# then run nginx -t against that copy.
sudo cp -a "${NGINX_CONF_DIR}/." "${STAGED_ROOT}/"
# Apply MIGRATION-0.9.2.md to the STAGED COPY: perform the same manual
# edits (removed directives, replacements, default-policy actions) on
# ${STAGED_ROOT} that the guide describes for the active tree.  The
# staged tree is validated below BEFORE any active-tree change, so a
# migration mistake surfaces here with the active configuration still
# intact.  After staged validation succeeds, repeat the SAME edits on
# the active tree (${NGINX_CONF_DIR}) before the module swap.
# The deterministic part of the migration is removing the three retired
# dynconf directives (MIGRATION-0.9.2.md "Static configuration
# migration"); the remaining items are consumer-side (reason integers,
# diagnostics schema) and need no config edit:
if sudo grep -rlE "markdown_dynamic_config|markdown_dynamic_config_path|markdown_dynconf_dry_run|markdown_prune_selectors|markdown_prune_protection_selectors" "${STAGED_ROOT}" 2>/dev/null \
    | while read -r staged_conf; do
        # The staged tree is a disposable copy: edit in place WITHOUT
        # .bak backups, so no stale backup file can be scanned below,
        # counted as a second load_module entry, or loaded by a
        # wildcard include during nginx -t.
        sudo sed -i -E \
            -e "s|^[[:space:]]*markdown_dynamic_config[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_dynamic_config_path[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_dynconf_dry_run[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_prune_selectors[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_prune_protection_selectors[[:space:]]+[^;]*;||" \
            "${staged_conf}" || exit 1
      done; then
    pipeline_status=(0 0)
else
    pipeline_status=("${PIPESTATUS[@]}")
fi
grep_rc="${pipeline_status[0]}"
sed_rc="${pipeline_status[1]}"
if { [[ "$grep_rc" -ne 0 ]] && [[ "$grep_rc" -ne 1 ]]; } || [[ "$sed_rc" -ne 0 ]]; then
  echo "ERROR: migration edit failed (grep=$grep_rc sed=$sed_rc)" >&2
  exit 1
fi
# A configuration with NO retired directives is a valid 0.9.2
# configuration: grep exit status 1 (no match) is accepted by the check
# above; any other grep or sed failure aborts the upgrade.
# Rewrite ONLY the Markdown module's load_module entry (other modules'
# load_module lines must be preserved untouched), then verify exactly one
# staged entry exists — a missing or duplicated Markdown entry means the
# rewrite did not target the right line and validation would be meaningless.
# The entry may live in nginx.conf OR in an included file (e.g.
# modules-enabled/*.conf), so rewrite across the whole staged tree.
sudo grep -rl "ngx_http_markdown_filter_module\.so" "${STAGED_ROOT}"  2>/dev/null \
    | while read -r staged_conf; do
        sudo sed -i "s|^[[:space:]]*load_module[[:space:]]\\+.*ngx_http_markdown_filter_module\\.so.*|load_module ${MODULES_DIR}/.ngx_http_markdown_filter_module.so.0.9.2.new;|" \
            "${staged_conf}"
      done || true
staged_loads="$(sudo grep -rc --exclude='*.bak' --exclude='*.disabled' 'ngx_http_markdown_filter_module.so.0.9.2.new' "${STAGED_ROOT}" | awk -F: '{s+=$2} END {print s+0}')"
if [[ "${staged_loads}" -ne 1 ]]; then
    echo "ERROR: expected exactly one Markdown load_module entry in the staged config tree, found ${staged_loads}" >&2
    exit 1
fi
sudo nginx -t -c "${STAGED_ROOT}/nginx.conf"
# Staged validation succeeded.  Apply the SAME migration to the ACTIVE
# tree now (before the module swap): remove the five retired directives
# directives.  The active load_module entry stays as-is — it already
# references the canonical module path, which the swap below replaces
# with the 0.9.2 binary.
# Back up the running module FIRST: a backup failure or mismatch must
# abort while the untouched pre-upgrade tree is still restorable.
MODULE_BACKUP="${MODULES_DIR}/.ngx_http_markdown_filter_module.so.pre-0.9.2.bak"
MODULE_BACKUP_OWNED=0
if [[ -e "${MODULE_BACKUP}" ]]; then
    if ! sudo -n cmp -s -- "${MODULE_BACKUP}" \
        "${MODULES_DIR}/ngx_http_markdown_filter_module.so"; then
        echo "ERROR: existing backup differs from the installed module or cannot be read; inspect it before upgrading" >&2
        exit 1
    fi
    echo "Preserving existing pre-upgrade module backup: ${MODULE_BACKUP}"
else
    sudo cp -a "${MODULES_DIR}/ngx_http_markdown_filter_module.so" \
        "${MODULE_BACKUP}.staged"
    sudo mv -f "${MODULE_BACKUP}.staged" "${MODULE_BACKUP}"
    MODULE_BACKUP_OWNED=1
fi
# Snapshot the active tree FIRST: the sed edits below are in-place, so a
# mid-migration failure must be able to restore the untouched tree
# instead of leaving a partially migrated configuration.  The snapshot
# lives in a UNIQUE sibling directory (mktemp): a fixed
# .migrate-backup name would nest inside the active root when
# NGINX_CONF_DIR carries a trailing slash, and a stale snapshot from an
# interrupted run would be merged by cp -a instead of replaced.
# For a symlinked root, resolve the target FIRST and create the
# snapshot beside the RESOLVED parent: a snapshot created beside the
# symlink path itself can land INSIDE the resolved target (e.g.
# /data/link -> /data), which the restore path would then delete.
SNAPSHOT_SRC="${NGINX_CONF_DIR}"
SNAPSHOT_PARENT="$(dirname "${NGINX_CONF_DIR}")"
if [[ -L "${NGINX_CONF_DIR}" ]]; then
  SNAPSHOT_SRC="$(readlink -f "${NGINX_CONF_DIR}")"
  SNAPSHOT_PARENT="$(dirname "${SNAPSHOT_SRC}")"
fi
MIGRATE_BACKUP="$(sudo mktemp -d "${SNAPSHOT_PARENT}/.nginx-migrate-XXXXXX")" || {
  echo "ERROR: could not allocate a migration snapshot directory; aborting" >&2
  exit 1
}
sudo cp -a "${SNAPSHOT_SRC}/." "${MIGRATE_BACKUP}/" || {
  sudo rm -rf "${MIGRATE_BACKUP}"
  echo "ERROR: could not snapshot the active configuration tree before migration; aborting" >&2
  exit 1
}
# Interruption-safe recovery: from this point until the migration
# completes, any exit (including a signal during the in-place sed
# edits) restores the untouched snapshot — the root may still EXIST
# but be partially edited, so the trap replaces it wholesale whenever
# MIGRATE_ACTIVE is set.  The trap also keeps cleaning STAGED_ROOT
# (it replaces the earlier STAGED_ROOT-only trap in this bash block).
# Disarmed after the migration succeeds.
# The restore is symlink-aware: a symlinked root is recreated as a link
# and the snapshot restored into the RESOLVED TARGET, preserving the
# root's symlink identity on every failure path.
migrate_restore() {
  if [[ "$MIGRATE_ACTIVE" -ne 1 ]] || [[ ! -d "$MIGRATE_BACKUP" ]]; then
    return 0
  fi
  if [[ -L "${NGINX_CONF_DIR}" ]]; then
    ROOT_LINK_TARGET="$(readlink -f "${NGINX_CONF_DIR}")"
    if [[ "${MIGRATE_BACKUP}" == "${ROOT_LINK_TARGET}" \
          || "${MIGRATE_BACKUP}" == "${ROOT_LINK_TARGET}/"* ]]; then
      return 0
    fi
    # Atomic link swap with NO dangling window: the replacement link
    # points DIRECTLY at the snapshot directory (the snapshot is itself
    # a unique staging path), and the old target is deleted only AFTER
    # the swap succeeds.  Every failure leaves the active link
    # untouched and the snapshot preserved for manual recovery.
    if ! sudo rm -f "${NGINX_CONF_DIR}.link-new" 2>/dev/null; then
      return 0
    fi
    if ! sudo ln -s "${MIGRATE_BACKUP}" "${NGINX_CONF_DIR}.link-new" 2>/dev/null; then
      return 0
    fi
    if ! sudo mv -Tf "${NGINX_CONF_DIR}.link-new" "${NGINX_CONF_DIR}" 2>/dev/null; then
      sudo rm -f "${NGINX_CONF_DIR}.link-new" 2>/dev/null || true
      return 0
    fi
    # The active link now points at the snapshot; the old target is
    # orphaned and safe to remove (a removal failure only leaves a
    # stale directory, never a dangling link).
    sudo rm -rf "${ROOT_LINK_TARGET}" 2>/dev/null || true
  else
    # Non-symlink root: move the active tree to a UNIQUE sibling,
    # install the snapshot, and roll back on failure.
    ROLLBACK_OLD="$(sudo mktemp -d "$(dirname "${NGINX_CONF_DIR}")/.nginx-old-XXXXXX")" 2>/dev/null || return 0
    sudo rmdir "${ROLLBACK_OLD}" 2>/dev/null || true
    if ! sudo mv "${NGINX_CONF_DIR}" "${ROLLBACK_OLD}" 2>/dev/null; then
      return 0
    fi
    if ! sudo mv "${MIGRATE_BACKUP}" "${NGINX_CONF_DIR}" 2>/dev/null; then
      sudo mv "${ROLLBACK_OLD}" "${NGINX_CONF_DIR}" 2>/dev/null || true
      return 0
    fi
    sudo rm -rf "${ROLLBACK_OLD}" 2>/dev/null || true
  fi
}
MIGRATE_ACTIVE=1
trap 'rc=$?; sudo rm -rf -- "$STAGED_ROOT" || :; migrate_restore; exit "$rc"' EXIT
if sudo grep -rlE "markdown_dynamic_config|markdown_dynamic_config_path|markdown_dynconf_dry_run|markdown_prune_selectors|markdown_prune_protection_selectors" "${NGINX_CONF_DIR}" 2>/dev/null \
    | while read -r active_conf; do
        sudo sed -i -E \
            -e "s|^[[:space:]]*markdown_dynamic_config[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_dynamic_config_path[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_dynconf_dry_run[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_prune_selectors[[:space:]]+[^;]*;||" \
            -e "s|^[[:space:]]*markdown_prune_protection_selectors[[:space:]]+[^;]*;||" \
            "${active_conf}" || exit 1
      done; then
    pipeline_status=(0 0)
else
    pipeline_status=("${PIPESTATUS[@]}")
fi
grep_rc="${pipeline_status[0]}"
sed_rc="${pipeline_status[1]}"
if { [[ "$grep_rc" -ne 0 ]] && [[ "$grep_rc" -ne 1 ]]; } || [[ "$sed_rc" -ne 0 ]]; then
  echo "ERROR: migration edit failed (grep=$grep_rc sed=$sed_rc); restoring the untouched active tree" >&2
  if [[ -L "${NGINX_CONF_DIR}" ]]; then
    # Symlinked root: recreate the link and restore the snapshot into
    # the RESOLVED TARGET, preserving the root's symlink identity.
    ROOT_LINK_TARGET="$(readlink -f "${NGINX_CONF_DIR}")"
    # The migration snapshot must NOT live inside the resolved target:
    # deleting the target below would delete the snapshot itself.
    if [[ "${MIGRATE_BACKUP}" == "${ROOT_LINK_TARGET}"           || "${MIGRATE_BACKUP}" == "${ROOT_LINK_TARGET}/"* ]]; then
      echo "ERROR: migration snapshot ${MIGRATE_BACKUP} is inside the resolved target ${ROOT_LINK_TARGET}; restore manually" >&2
      exit 1
    fi
    # Atomic link swap with NO dangling window: the replacement link
    # points DIRECTLY at the snapshot directory (the snapshot is itself
    # a unique staging path), and the old target is deleted only AFTER
    # the swap succeeds.  Every failure leaves the active link
    # untouched and the snapshot preserved for manual recovery.
    if ! sudo rm -f "${NGINX_CONF_DIR}.link-new" 2>/dev/null; then
      echo "ERROR: could not clear the staging symlink ${NGINX_CONF_DIR}.link-new; restore manually from ${MIGRATE_BACKUP}" >&2
      exit 1
    fi
    sudo ln -s "${MIGRATE_BACKUP}" "${NGINX_CONF_DIR}.link-new" || {
      echo "ERROR: could not create the replacement configuration-root symlink; the active link is untouched and the resolved target was NOT deleted. Restore manually from ${MIGRATE_BACKUP}" >&2
      exit 1
    }
    sudo mv -Tf "${NGINX_CONF_DIR}.link-new" "${NGINX_CONF_DIR}" || {
      sudo rm -f "${NGINX_CONF_DIR}.link-new" 2>/dev/null || true
      echo "ERROR: could not replace the configuration-root symlink; the active link is untouched. Restore manually from ${MIGRATE_BACKUP}" >&2
      exit 1
    }
    # The active link now points at the snapshot; the old target is
    # orphaned and safe to remove (a removal failure only leaves a
    # stale directory, never a dangling link).
    sudo rm -rf "${ROOT_LINK_TARGET}" 2>/dev/null || true
  else
    sudo rm -rf "${NGINX_CONF_DIR}" 2>/dev/null || true
    sudo mv "${MIGRATE_BACKUP}" "${NGINX_CONF_DIR}" 2>/dev/null || {
      echo "ERROR: could not restore the active tree from ${MIGRATE_BACKUP}; restore manually" >&2
      exit 1
    }
  fi
  # The explicit restore above already replaced the active root; disarm
  # the restoration trap BEFORE exiting so it cannot rm -rf the freshly
  # restored tree and fail the second mv.
  MIGRATE_ACTIVE=0
  exit 1
fi
# Migration succeeded.  The migration EXIT trap stays ARMED through the
# module swap below: a stop/quit or swap failure must restore the
# pre-migration tree (MIGRATE_BACKUP) so the old module is never left
# paired with the migrated configuration.  The trap is disarmed and the
# snapshot cleaned only after the post-start verification succeeds.
# A configuration with NO retired directives is already 0.9.2
# compliant: grep exit status 1 (no match) is accepted by the check
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
        # The active configuration was already migrated; the migration
        # EXIT trap restores the pre-migration tree (MIGRATE_BACKUP) on
        # exit, so the old module is not left paired with the migrated
        # configuration.
        exit 1
    fi
else
    if pgrep -x nginx >/dev/null 2>&1; then
        sudo nginx -s quit
        waited=0
        while pgrep -x nginx >/dev/null 2>&1; do
            if [[ "$waited" -ge 30 ]]; then
                echo "ERROR: NGINX master did not exit within 30s of 'nginx -s quit'; aborting upgrade" >&2
                # Same pairing guard: the migration EXIT trap restores
                # the pre-migration tree on exit.
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
if ! sudo nginx -t; then
  echo "ERROR: nginx -t failed after module swap; restoring previous module and configuration..." >&2
  # Stage the backup beside the live module, then swap atomically with
  # mv -f so a torn in-place copy can never leave a half-written .so.
  # Guard BOTH commands: a failure must be reported with the specific
  # operation, and the configuration recovery below still runs so the
  # operator is not left with an uncertain module/configuration pair.
  MODULE_RESTORE_FAILED=0
  sudo cp -a "${MODULE_BACKUP}" \
      "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.restore-staged" || {
    echo "ERROR: could not stage the previous module from ${MODULE_BACKUP}; the 0.9.2 module remains installed" >&2
    MODULE_RESTORE_FAILED=1
  }
  if [[ "$MODULE_RESTORE_FAILED" -eq 0 ]]; then
    sudo mv -f "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.restore-staged" \
        "${MODULES_DIR}/ngx_http_markdown_filter_module.so" || {
      echo "ERROR: could not replace the active module with the previous module; the 0.9.2 module remains installed" >&2
      MODULE_RESTORE_FAILED=1
    }
  fi
  # The active configuration tree was migrated by MIGRATION-0.9.2.md
  # before the swap; the old module may not accept the migrated
  # directives, so restore the backed-up 0.9.1 tree alongside the old
  # module before re-validating.  The whole tree is restored (not just
  # nginx.conf + conf.d + modules-enabled) and any path the migration
  # added is removed, so no migrated configuration can remain.
  # If the MODULE restore failed, the 0.9.2 module is still installed:
  # restoring the 0.9.1 configuration would pair it with the 0.9.2
  # binary.  Fail closed with a manual-recovery instruction instead of
  # continuing into the configuration restore and restart.
  if [[ "$MODULE_RESTORE_FAILED" -ne 0 ]]; then
    echo "ERROR: the previous module could not be restored; NGINX remains stopped with the 0.9.2 module installed and the migrated configuration active. Restore manually: install the previous module from ${MODULE_BACKUP} and the 0.9.1 tree from ${CONFIG_BACKUP_DIR}/tree, then run nginx -t and start NGINX" >&2
    exit 1
  fi
  # Build the complete restored tree in a sibling staging path FIRST and
  # validate it, so a failure leaves the active tree untouched; only a
  # fully staged and validated tree replaces the active root.  The
  # staging dir sits BESIDE the configuration root so the final mv stays
  # on the same filesystem.  For a symlinked root, resolve the target
  # FIRST and stage beside the TARGET's parent so the mv cannot cross
  # devices.
  if [[ -f "${CONFIG_BACKUP_DIR}/tree-root-link" ]]; then
    ROOT_LINK_TARGET="$(readlink -f "${NGINX_CONF_DIR}")"
    RESTORE_STAGED="$(sudo mktemp -d "$(dirname "${ROOT_LINK_TARGET}")/.nginx-restore-XXXXXX")"
  else
    RESTORE_STAGED="$(sudo mktemp -d "$(dirname "${NGINX_CONF_DIR%/}")/.nginx-restore-XXXXXX")"
  fi
  # Dedicated cleanup trap: any unguarded failure under set -euo pipefail
  # must still remove the staging tree; disarmed after a successful move.
  # This trap REPLACES the earlier STAGED_ROOT trap (same bash block), so
  # it must clean BOTH staging trees or the earlier one leaks on the
  # module-validation failure path.
  RESTORE_CLEANUP_SET=1
  trap 'rc=$?; sudo rm -rf -- "$STAGED_ROOT" "$RESTORE_STAGED" || :; exit "$rc"' EXIT
  if ! sudo nginx -t -c "${RESTORE_STAGED}/nginx.conf"; then
    sudo rm -rf "${RESTORE_STAGED}"
    echo "ERROR: restored configuration fails validation; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
    exit 1
  fi
  # Staging validated: atomically replace the active root.  A symlink
  # root is recreated as a link and the staged tree atomically moved
  # into its resolved target (old target preserved until the swap
  # succeeds); a real directory is replaced wholesale with mv.
  # The mktemp staging dir is user-owned mode-0700; restore the active
  # root's owner/group/mode onto the staged ROOT DIRECTORY ONLY before
  # the rename (cp -a already preserved file-specific ownership and
  # modes beneath it; a recursive chown/chmod would overwrite them).
  ROOT_OWNER="$(stat -c '%U:%G' "${NGINX_CONF_DIR}")"
  ROOT_MODE="$(stat -c '%a' "${NGINX_CONF_DIR}")"
  sudo chown "${ROOT_OWNER}" "${RESTORE_STAGED}"
  sudo chmod "${ROOT_MODE}" "${RESTORE_STAGED}"
  if [[ -f "${CONFIG_BACKUP_DIR}/tree-root-link" ]]; then
    # Create a temporary symlink and atomically rename it over the
    # active link (GNU mv -T uses rename(2)), so a failure never
    # leaves the configuration root without a link.
    ROOT_LINK_TARGET="$(sudo cat "${CONFIG_BACKUP_DIR}/tree-root-link")" || {
      echo "ERROR: could not read the configuration-root symlink marker ${CONFIG_BACKUP_DIR}/tree-root-link; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    }
    if [[ -z "${ROOT_LINK_TARGET}" ]]; then
      echo "ERROR: the configuration-root symlink marker is empty; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    fi
    sudo ln -s "${ROOT_LINK_TARGET}" "${NGINX_CONF_DIR}.link-new" || {
      # Remove any stale temporary link (a previous failed attempt may
      # have left one) and the staged tree, then report manual recovery.
      sudo rm -f "${NGINX_CONF_DIR}.link-new"
      sudo rm -rf "${RESTORE_STAGED}"
      echo "ERROR: could not create the replacement configuration-root link; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    }
    sudo mv -Tf "${NGINX_CONF_DIR}.link-new" "${NGINX_CONF_DIR}" || {
      sudo rm -f "${NGINX_CONF_DIR}.link-new"
      sudo rm -rf "${RESTORE_STAGED}"
      echo "ERROR: could not replace the configuration-root link; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    }
    ROOT_LINK_TARGET="$(readlink -f "${NGINX_CONF_DIR}")"
    # The staging dir was created beside the SYMLINK; the final mv moves
    # it to the resolved TARGET, which may be on a different filesystem.
    # Verify device identity and fail closed before attempting the swap
    # (a cross-device mv would degrade to a non-atomic copy).
    if [[ "$(stat -c '%d' "${RESTORE_STAGED}")" != "$(stat -c '%d' "${ROOT_LINK_TARGET}")" ]]; then
      sudo rm -rf "${RESTORE_STAGED}"
      echo "ERROR: configuration root target is on a different filesystem than the staging dir; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    fi
    if [[ -e "${ROOT_LINK_TARGET}" || -L "${ROOT_LINK_TARGET}" ]]; then
      # Unique sibling rollback path: a fixed .rollback-old name would
      # be deleted before the replacement succeeds, losing the
      # last-known-good tree on an interrupted retry.
      ROLLBACK_OLD="$(sudo mktemp -d "$(dirname "${ROOT_LINK_TARGET}")/.nginx-old-XXXXXX")" || {
        sudo rm -rf "${RESTORE_STAGED}"
        echo "ERROR: could not allocate a rollback directory; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
        exit 1
      }
      sudo rmdir "${ROLLBACK_OLD}" 2>/dev/null || true
      sudo mv -f "${ROOT_LINK_TARGET}" "${ROLLBACK_OLD}"
    fi
    sudo mv -f "${RESTORE_STAGED}" "${ROOT_LINK_TARGET}" || {
      if [[ -n "${ROLLBACK_OLD:-}" && -e "${ROLLBACK_OLD}" ]]; then
        sudo mv -f "${ROLLBACK_OLD}" "${ROOT_LINK_TARGET}"
      fi
      sudo rm -rf "${RESTORE_STAGED}"
      echo "ERROR: configuration swap failed; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    }
    sudo rm -rf "${ROLLBACK_OLD:-}" 2>/dev/null || true
  else
    ROLLBACK_OLD="$(sudo mktemp -d "$(dirname "${NGINX_CONF_DIR%/}")/.nginx-old-XXXXXX")" || {
      sudo rm -rf "${RESTORE_STAGED}"
      echo "ERROR: could not allocate a rollback directory; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    }
    sudo rmdir "${ROLLBACK_OLD}" 2>/dev/null || true
    sudo mv -f "${NGINX_CONF_DIR}" "${ROLLBACK_OLD}"
    sudo mv -f "${RESTORE_STAGED}" "${NGINX_CONF_DIR}" || {
      sudo mv -f "${ROLLBACK_OLD}" "${NGINX_CONF_DIR}"
      sudo rm -rf "${RESTORE_STAGED}"
      echo "ERROR: configuration swap failed; NGINX remains stopped. Restore manually from ${CONFIG_BACKUP_DIR}." >&2
      exit 1
    }
    sudo rm -rf "${ROLLBACK_OLD}" 2>/dev/null || true
  fi
  if ! sudo nginx -t; then
    echo "ERROR: restored module and configuration also fail validation; do not start NGINX. ${MODULE_BACKUP} and ${CONFIG_BACKUP_DIR} are preserved — restore manually from them." >&2
    exit 1
  fi
  echo "INFO: previous module and configuration restored and verified." >&2
  # The rollback left NGINX stopped; restart it on the restored, validated
  # pair using the ownership decision recorded before the stop.
  if [[ "$systemd_managed" -eq 1 ]]; then
    sudo systemctl start nginx
  else
    sudo nginx
  fi
  exit 1
fi
# Start a fresh master with the new module and validate it before
# discarding the pre-upgrade backup: a failed start or an unhealthy
# post-start check must leave ${MODULE_BACKUP} available for rollback.
if [[ "$systemd_managed" -eq 1 ]]; then
    sudo systemctl start nginx
else
    sudo nginx
fi
# Post-start verification: the new master must be serving and converting
# before the backup is removed.  Probe a fixed, known-convertible fixture
# (a path already verified to return text/html upstream and convert to
# Markdown) and require the converted representation, not just any
# response: a 404 or an unconverted pass-through would prove nothing.
sleep 1
if [[ "$systemd_managed" -eq 1 ]]; then
    if ! systemctl is-active --quiet nginx; then
      echo "ERROR: nginx service inactive after start; restoring the previous module so the migration trap's configuration restore pairs with it" >&2
      sudo cp -a "${MODULE_BACKUP}" "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.restore-staged" 2>/dev/null || true
      sudo mv -f "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.restore-staged" "${MODULES_DIR}/ngx_http_markdown_filter_module.so" 2>/dev/null || true
      exit 1
    fi
elif ! pgrep -x nginx >/dev/null 2>&1; then
  echo "ERROR: NGINX master not running after start; restoring the previous module so the migration trap's configuration restore pairs with it" >&2
  sudo cp -a "${MODULE_BACKUP}" "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.restore-staged" 2>/dev/null || true
  sudo mv -f "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.restore-staged" "${MODULES_DIR}/ngx_http_markdown_filter_module.so" 2>/dev/null || true
  exit 1
fi
PROBE_PATH="/known-convertible-page"   # adjust to your verified fixture
# A fixed string that appears in the converted Markdown of that fixture.
# The post-start check greps for it with -Fq so a response that merely
# starts with a Markdown-ish character cannot pass.
PROBE_MARKER="Kubernetes module test"   # adjust to your fixture's heading
PROBE_BODY="$(mktemp)"
PROBE_HEADERS="$(mktemp)"
if ! curl -fsS --max-time 10 -H 'Accept: text/markdown' \
        -D "${PROBE_HEADERS}" \
        -o "${PROBE_BODY}" "http://localhost${PROBE_PATH}"; then
  echo "ERROR: post-start check failed (probe request); restoring the previous module so the migration trap's configuration restore pairs with it" >&2
  sudo cp -a "${MODULE_BACKUP}" "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.restore-staged" 2>/dev/null || true
  sudo mv -f "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.restore-staged" "${MODULES_DIR}/ngx_http_markdown_filter_module.so" 2>/dev/null || true
  rm -f "${PROBE_BODY}" "${PROBE_HEADERS}"
  exit 1
fi
if ! grep -qi '^Content-Type: text/markdown' "${PROBE_HEADERS}"; then
  echo "ERROR: post-start check failed (probe response is not text/markdown); keeping ${MODULE_BACKUP} for rollback" >&2
  echo "  Inspect the probe response and verify ${PROBE_PATH} converts before removing the backup." >&2
  rm -f "${PROBE_BODY}" "${PROBE_HEADERS}"
  exit 1
fi
if ! grep -Fq "${PROBE_MARKER}" "${PROBE_BODY}"; then
  echo "ERROR: post-start check failed (converted body lacks the fixture marker); restoring the previous module so the migration trap's configuration restore pairs with it" >&2
  echo "  Inspect the probe response and verify ${PROBE_PATH} converts before removing the backup." >&2
  sudo cp -a "${MODULE_BACKUP}" "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.restore-staged" 2>/dev/null || true
  sudo mv -f "${MODULES_DIR}/.ngx_http_markdown_filter_module.so.restore-staged" "${MODULES_DIR}/ngx_http_markdown_filter_module.so" 2>/dev/null || true
  rm -f "${PROBE_BODY}" "${PROBE_HEADERS}"
  exit 1
fi
rm -f "${PROBE_BODY}" "${PROBE_HEADERS}"
# The swap and post-start verification succeeded: disarm the migration
# trap and clean the staged tree and the migration snapshot.
MIGRATE_ACTIVE=0
if ! sudo rm -rf -- "$STAGED_ROOT" 2>/dev/null; then
  echo "ERROR: could not clean the staged tree $STAGED_ROOT; remove it manually" >&2
  exit 1
fi
if ! sudo rm -rf "${MIGRATE_BACKUP}" 2>/dev/null; then
  echo "ERROR: could not clean the migration snapshot ${MIGRATE_BACKUP}; remove it manually" >&2
  exit 1
fi
trap - EXIT
# Discard the backup only when THIS run created it; a pre-existing backup
# left by an earlier upgrade stays until that upgrade's cleanup removes it.
if [[ "${MODULE_BACKUP_OWNED}" -eq 1 ]]; then
  if ! sudo -n rm -f -- "${MODULE_BACKUP}"; then
    echo "ERROR: backup cleanup failed; remove the verified backup with administrator access" >&2
    exit 1
  fi
else
  echo "INFO: keeping pre-existing ${MODULE_BACKUP} (not created by this run)"
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
| 0.9.2 | 2026-09-07 | Kang | Source-build restore copies the backup (never consumes it), the post-start check prefers systemctl is-active on systemd hosts, and backup removal waits for a known-convertible fixture to return Markdown |
| 0.9.2 | 2026-08-15 | Kang | Added Step 5 migrate-the-configuration before restart |
| 0.9.2 | 2026-07-30 | Kang | Initial upgrade guide for 0.9.2 |
