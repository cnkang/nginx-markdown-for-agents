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
module directory and an existing backup directory. Back up the current module
and stop NGINX before you install the candidate.

```bash
sudo cp "$MODULES_DIR/ngx_http_markdown_filter_module.so" \
  "$BACKUP_DIR/ngx_http_markdown_filter_module.so.pre-1.0.0-rc.1" &&
  sudo systemctl stop nginx
```

When the backup and stop commands succeed, complete the Install step. Then
test the configuration with the installed NGINX binary and start NGINX.

```bash
sudo nginx -t && sudo systemctl start nginx
```

To roll back, stop NGINX before replacing the mapped module. Then test the
configuration and start NGINX to load the previous module:

```bash
sudo systemctl stop nginx &&
  sudo cp "$BACKUP_DIR/ngx_http_markdown_filter_module.so.pre-1.0.0-rc.1" \
    "$MODULES_DIR/ngx_http_markdown_filter_module.so" &&
  sudo nginx -t && sudo systemctl start nginx
```

Do not treat a skipped check or missing evidence as a pass.
