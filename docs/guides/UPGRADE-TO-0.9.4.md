# Upgrade to 0.9.4

v0.9.4 is the published compatibility maintenance release. Download its
signed assets and checksums from the [v0.9.4 GitHub release](https://github.com/cnkang/nginx-markdown-for-agents/releases/tag/v0.9.4).

The release is non-breaking. It keeps all 20 active directives and their
defaults, the HTTP representation, metrics and diagnostics, reason codes, and
FFI ABI version 3.

## Choose the matching artifact

| NGINX installation | Artifact family |
|---|---|
| nginx.org package | DEB/RPM: exact NGINX version, distribution, and architecture; module archive: exact version, runtime libc (`glibc` or `musl`), and architecture |
| Ubuntu 24.04 or 26.04 archive package | Ubuntu-native DEB for the exact suite, architecture, package revision, and ABI |
| Source-built NGINX | Build the module against the exact NGINX source and configure arguments used for the server. Matching version, libc, and architecture alone do not guarantee a matching NGINX module build signature. |

Do not substitute an nginx.org package for an Ubuntu-native package. A matching
version string or ABI provide alone does not prove that a module can load.
Use only rows marked `verified` in the published matrix.

## Install the release

1. Record the NGINX version, package origin, package revision, architecture,
   and Ubuntu ABI provide when applicable.
2. Download the artifact, `SHA256SUMS`, and `SHA256SUMS.asc` from the approved
   v0.9.4 release. Verify the signature and checksum as documented in
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

The v0.9.4 release notes and compatibility matrix list package revisions and
architectures with recorded evidence. Do not treat a skipped check or missing
evidence as a pass.
