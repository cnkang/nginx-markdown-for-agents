# Upgrade to 1.0.0-rc.1

The 1.0.0-rc.1 candidate is not yet published. v0.9.4 remains the latest public
stable release. Use this guide only after the candidate publishes, or use the
artifact-only rehearsal output for testing.

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
| RPM release | `nginx1.28.3.1` |

The tilde keeps the candidate below the final `1.0.0` package. A stable v0.9.4
package remains above the candidate in the upgrade path that ends at `1.0.0`.

## Install the candidate

1. Record the NGINX version, package origin, package revision, architecture,
   and Ubuntu ABI provide when applicable.
2. Download the artifact, `SHA256SUMS`, and `SHA256SUMS.asc`. Verify the
   signature and checksum as documented in
   [PACKAGE_INSTALLATION.md](PACKAGE_INSTALLATION.md).
3. Install the package that matches the NGINX origin and exact version. Keep
   the previous module package for rollback.
4. Test and reload NGINX:

   ```bash
   sudo nginx -t
   sudo systemctl reload-or-restart nginx
   ```

5. Send a request with `Accept: text/markdown` and confirm the response has
   `Content-Type: text/markdown`. Send a normal browser request and confirm it
   still receives HTML.
6. Run `nginx-markdown-doctor --json` and review any failed or skipped check.

Do not treat a skipped check or missing evidence as a pass.
