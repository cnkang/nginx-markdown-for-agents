# Upgrade to 1.0.0-rc.1

The 1.0.0-rc.1 candidate is not yet published. v0.9.4 remains the latest public
stable release. Use this guide only after the candidate publishes. A rehearsal
dispatch produces unsigned packages, so use that output for staging tests and
verify the SHA-256 checksum only.

The candidate is non-breaking against v0.9.4. It keeps all 20 active directives
and their defaults, the HTTP representation, metrics and diagnostics, reason
codes, and FFI ABI version 3.

## Choose the matching artifact

| NGINX installation | Artifact family |
|---|---|
| nginx.org package | DEB/RPM for the exact NGINX version, distribution, and architecture. Use the module archive for the exact version, runtime libc, and architecture. |
| Ubuntu 24.04 or 26.04 archive package | Ubuntu-native DEB for the exact suite, architecture, package revision, and ABI |
| Source-built NGINX | Build the module against the exact NGINX source and configure arguments used for the server |

Do not substitute an nginx.org package for an Ubuntu-native package. Use only
rows marked `verified` in the published matrix.

## Package version ordering

The candidate maps the release identity to a Debian and RPM package version.

| Item | Value |
|------|-------|
| Release identity | `1.0.0-rc.1` |
| Debian upstream version | `1.0.0~bdudfaababfa` |
| RPM version | `1.0.0~bdudfaababfa` |
| RPM release | Target-dependent: `nginx<NGINX_VERSION>.1`, for example `nginx1.28.3.1` for NGINX 1.28.3 |

The tilde keeps the candidate below the final `1.0.0` package and above the
published v0.9.4 package. An upgrade from v0.9.4 to the candidate moves forward
in package order.

## Install the candidate

1. Record the NGINX version, package origin, package revision, architecture,
   and Ubuntu ABI provide when applicable.
2. For a published release, download the artifact, `SHA256SUMS`, and
   `SHA256SUMS.asc`, then verify the signature and checksum as documented in
   [PACKAGE_INSTALLATION.md](PACKAGE_INSTALLATION.md). For rehearsal output,
   verify the SHA-256 checksum only. A rehearsal dispatch publishes no
   signature.
3. Install the package that matches the NGINX origin and exact version. Keep
   the previous module package for rollback.
4. Test the configuration and restart NGINX to load the installed module:

   ```bash
   sudo nginx -t
   sudo systemctl restart nginx
   ```

5. Send a request with `Accept: text/markdown` and confirm the response has
   `Content-Type: text/markdown`. Send a normal browser request and confirm it
   still receives HTML.
6. Run `nginx-markdown-doctor --json` and review any failed or skipped check.

## Source-built NGINX

After you clone the repository, check out the `v1.0.0-rc.1` tag before you
build. The default branch can move to a later candidate.

Build the module with the manual source build procedure in
[INSTALLATION.md](INSTALLATION.md#6-secondary-manual-source-build). Use the
same NGINX source and configure arguments as the running server.

Before the Install step, set `MODULES_DIR` and `BACKUP_DIR` to the active
module directory and an existing backup directory. On the first upgrade, set
`PRE_CANDIDATE_BACKUP` to an absolute path that does not exist yet, for
example
`PRE_CANDIDATE_BACKUP="$BACKUP_DIR/ngx_http_markdown_filter_module.so.pre-1.0.0-rc.1"`.
Set `CURRENT_BACKUP` to `"$PRE_CANDIDATE_BACKUP"` on the first upgrade, or to a
new unused path on a repeat upgrade. Do not change `PRE_CANDIDATE_BACKUP`
after the first upgrade. Set `NGINX_BIN` to the absolute path of the binary
that the service runs.

Serialize upgrades and rollbacks for the same module. All operators must use
the same lock directory next to the active module file. Acquire the lock
before you back up or replace the module:

```bash
MODULE_LOCK="$MODULES_DIR/.ngx_http_markdown_filter_module.so.upgrade-lock"
sudo mkdir "$MODULE_LOCK"
```

If `mkdir` fails, stop the procedure. If another operation holds the lock,
wait for it to finish and acquire the lock before you retry. Keep the lock
through backup, installation, configuration testing, and service start.
Keep it until you decide to keep the candidate or roll back. If you roll back,
reuse this lock and do not run `mkdir` again.
If you find a lock after an interruption, confirm that the prior operation
has ended and resolve any incomplete upgrade before clearing the lock.

Before the first backup, confirm that the active module is the pre-candidate
build. The backup command creates the destination exclusively. Back up the
current module and stop NGINX before you install the candidate.

```bash
sudo sh -c 'set -C; cat "$1" > "$2"' sh \
  "$MODULES_DIR/ngx_http_markdown_filter_module.so" \
  "$CURRENT_BACKUP" &&
  sudo systemctl stop nginx
```

If the copy fails partway, remove the partial `CURRENT_BACKUP` file before
you retry. Do not remove a valid backup when the exclusive create fails.

When the backup and stop commands succeed, complete the Install step. Then
test the configuration with the installed NGINX binary and start NGINX.

```bash
sudo "$NGINX_BIN" -t && sudo systemctl start nginx
```

To roll back, use `PRE_CANDIDATE_BACKUP` from the first upgrade while holding
the lock acquired above. Set `MODULE_LOCK` to the same lock path if you are in
a new shell. If upgrades overlapped, verify that the backup contains the
pre-candidate module before you restore it. If you cannot verify it, restore
a verified copy from a trusted pre-candidate archive to that backup path.
Stop the rollback if you have no verified copy. A repeat upgrade's backup can
contain the candidate.

Stop NGINX before you replace the mapped module. Then test the configuration
with `NGINX_BIN` from the upgrade and start NGINX to load the previous module:

```bash
sudo systemctl stop nginx &&
  sudo cp "$PRE_CANDIDATE_BACKUP" \
    "$MODULES_DIR/ngx_http_markdown_filter_module.so" &&
  sudo "$NGINX_BIN" -t && sudo systemctl start nginx
```

Release the lock only after the chosen module has passed the configuration
test and NGINX has started successfully:

```bash
sudo rmdir "$MODULE_LOCK"
```

Do not treat a skipped check or missing evidence as a pass.
