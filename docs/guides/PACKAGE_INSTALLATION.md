# Package Installation Guide

This guide covers installing DEB and RPM artifacts produced by the release
workflows. These are community-maintained dynamic-module packages, not
official NGINX repository packages.

This is the canonical operator procedure for package installation. For the
release-engineering contract behind the artifacts, see
[PACKAGE_DISTRIBUTION.md](PACKAGE_DISTRIBUTION.md). For source builds and
other installation methods, see [INSTALLATION.md](INSTALLATION.md).

## Repository Publishing Status

GitHub Releases are the current distribution channel for DEB and RPM package
artifacts, but asset availability is tag-specific.
The project plans public APT/YUM repositories but has not launched them yet.
They are not part of the current GA channel.

Bare package-manager installation commands only work after an operator
publishes and configures a real APT or YUM repository. Until then, download the
matching package artifact, `SHA256SUMS`, and `SHA256SUMS.asc` from the same
GitHub Release. `SHA256SUMS` detects transfer corruption. It is not an
authenticated trust anchor.

> **Availability check:** A release candidate and its compatibility-matrix
> entry do not make a DEB or RPM package downloadable. Before using the
> commands below, confirm that the selected GitHub Release contains the exact
> package, `SHA256SUMS`, and `SHA256SUMS.asc`. The release signing-key
> fingerprint appears in [GPG_KEY_MANAGEMENT.md](./GPG_KEY_MANAGEMENT.md).
> Import the key through an independently authenticated channel and verify
> against that authoritative fingerprint. Do not treat a same-release key
> file, key ID, or checksum as project authenticity on its own. If you cannot
> authenticate the key through an independent channel, use the
> [Manual Source Build](./INSTALLATION.md#6-secondary-manual-source-build).

## Select the Matching Artifact

Package filenames include the module version, target NGINX version, and CPU
architecture. The target NGINX version must match the installed NGINX ABI.

### Platform Availability

| Platform | Format | Source |
|----------|--------|--------|
| glibc-based Linux (Ubuntu, Debian, RHEL, AlmaLinux, Amazon Linux) | DEB/RPM | `release-packages.yml` (GitHub Releases) |
| musl-based Linux (Alpine, etc.) | Dynamic-module tarball | `release-packages.yml` `musl-build` job (GitHub Releases) — pair it with an ABI-compatible NGINX binary per the compatibility matrix; `release-binaries.yml` remains as the manual rebuild tool only |

DEB and RPM packages are built on glibc-based build images and target
glibc-based distributions only. For musl-based environments (Alpine Linux,
and so on), use the pre-built dynamic-module tarball from the musl-build job
of the release-packages workflow (it is a loadable module artifact, not a
static server binary — pair it with an ABI-compatible NGINX executable, see
the compatibility matrix in PACKAGE_COMPATIBILITY.md) or build from source.

DEB format:

```text
nginx-module-markdown-for-agents_<VERSION>_nginx-<NGINX_VERSION>_<ARCH>.deb
```

RPM format:

```text
nginx-module-markdown-for-agents-<VERSION>-nginx<NGINX_VERSION>-1.<ARCH>.rpm
```

Architecture mapping:

| Platform | DEB Arch | RPM Arch |
|----------|----------|----------|
| x86_64 | amd64 | x86_64 |
| arm64 | arm64 | aarch64 |

## DEB Artifacts (Ubuntu, Debian)

Replace `VERSION` below with a published release version. `NGINX_VERSION` must
match the NGINX ABI you run.

```bash
set -euo pipefail
VERSION="<published-version>"
NGINX_VERSION=1.26.3
ARCH=amd64
BASE_URL="https://github.com/cnkang/nginx-markdown-for-agents/releases/download/v${VERSION}"
PKG="nginx-module-markdown-for-agents_${VERSION}_nginx-${NGINX_VERSION}_${ARCH}.deb"

curl -fsSL -o SHA256SUMS "${BASE_URL}/SHA256SUMS"
curl -fsSL -o SHA256SUMS.asc "${BASE_URL}/SHA256SUMS.asc"
curl -fsSL -o "${PKG}" "${BASE_URL}/${PKG}"
# Supply both values through an independently authenticated channel. Do not
# import a key from the same release asset set. The checked-in project key
# (packaging/nginx-markdown-for-agents-release.asc in the git repository) is
# the transport; the fingerprint published in docs/guides/GPG_KEY_MANAGEMENT.md
# is the trust anchor — confirm the imported key matches it exactly.
: "${RELEASE_KEY_PATH:?set RELEASE_KEY_PATH to the project public-key file (packaging/nginx-markdown-for-agents-release.asc, from the git repository, not the release assets)}"
: "${TRUSTED_FINGERPRINT:?set TRUSTED_FINGERPRINT to the fingerprint published in docs/guides/GPG_KEY_MANAGEMENT.md}"
[[ "${TRUSTED_FINGERPRINT}" =~ ^[A-Fa-f0-9]{40}$ ]] || exit 1
GNUPGDIR="$(mktemp -d)"
trap 'rm -rf "${GNUPGDIR}"' EXIT
gpg --batch --homedir "${GNUPGDIR}" --import "${RELEASE_KEY_PATH}"
VALIDSIG="$(gpg --batch --homedir "${GNUPGDIR}" --status-fd=1 \
    --verify SHA256SUMS.asc SHA256SUMS 2>/dev/null \
    | awk '$2 == "VALIDSIG" { print toupper($3); exit }')"
EXPECTED_FINGERPRINT="$(printf '%s' "${TRUSTED_FINGERPRINT}" | tr '[:lower:]' '[:upper:]')"
[[ "${VALIDSIG}" == "${EXPECTED_FINGERPRINT}" ]] || exit 1
CHECKSUM_LINE="$(awk -v file="${PKG}" '$2 == file { print; count++ } END { exit count == 1 ? 0 : 1 }' SHA256SUMS)"; printf '%s\n' "${CHECKSUM_LINE}" | sha256sum -c -
sudo apt install "./${PKG}"
```

## RPM Artifacts (AlmaLinux, Amazon Linux, RHEL)

Replace `VERSION` below with a published release version. `NGINX_VERSION` must
match the NGINX ABI you run.

```bash
set -euo pipefail
VERSION="<published-version>"
NGINX_VERSION=1.26.3
ARCH=x86_64
BASE_URL="https://github.com/cnkang/nginx-markdown-for-agents/releases/download/v${VERSION}"
PKG="nginx-module-markdown-for-agents-${VERSION}-nginx${NGINX_VERSION}-1.${ARCH}.rpm"

curl -fsSL -o SHA256SUMS "${BASE_URL}/SHA256SUMS"
curl -fsSL -o SHA256SUMS.asc "${BASE_URL}/SHA256SUMS.asc"
curl -fsSL -o "${PKG}" "${BASE_URL}/${PKG}"
# Supply both values through an independently authenticated channel. Do not
# import a key from the same release asset set. The checked-in project key
# (packaging/nginx-markdown-for-agents-release.asc in the git repository) is
# the transport; the fingerprint published in docs/guides/GPG_KEY_MANAGEMENT.md
# is the trust anchor — confirm the imported key matches it exactly.
: "${RELEASE_KEY_PATH:?set RELEASE_KEY_PATH to the project public-key file (packaging/nginx-markdown-for-agents-release.asc, from the git repository, not the release assets)}"
: "${TRUSTED_FINGERPRINT:?set TRUSTED_FINGERPRINT to the fingerprint published in docs/guides/GPG_KEY_MANAGEMENT.md}"
[[ "${TRUSTED_FINGERPRINT}" =~ ^[A-Fa-f0-9]{40}$ ]] || exit 1
GNUPGDIR="$(mktemp -d)"
trap 'rm -rf "${GNUPGDIR}"' EXIT
gpg --batch --homedir "${GNUPGDIR}" --import "${RELEASE_KEY_PATH}"
VALIDSIG="$(gpg --batch --homedir "${GNUPGDIR}" --status-fd=1 \
    --verify SHA256SUMS.asc SHA256SUMS 2>/dev/null \
    | awk '$2 == "VALIDSIG" { print toupper($3); exit }')"
EXPECTED_FINGERPRINT="$(printf '%s' "${TRUSTED_FINGERPRINT}" | tr '[:lower:]' '[:upper:]')"
[[ "${VALIDSIG}" == "${EXPECTED_FINGERPRINT}" ]] || exit 1
CHECKSUM_LINE="$(awk -v file="${PKG}" '$2 == file { print; count++ } END { exit count == 1 ? 0 : 1 }' SHA256SUMS)"; printf '%s\n' "${CHECKSUM_LINE}" | sha256sum -c -
sudo rpm -Uvh "./${PKG}"
```

## Verify Installation

```bash
sudo nginx -t
# Confirm the active configuration contains an active load_module directive
# for the canonical module filename (a filename mention elsewhere, for
# example inside a comment, does not load anything).
sudo nginx -T 2>&1 | grep -E '^[[:space:]]*load_module[[:space:]]+[^;]*ngx_http_markdown_filter_module\.so[[:space:]]*;'
```

Install the module binary using the canonical NGINX dynamic-module name:

```text
ngx_http_markdown_filter_module.so
```

Package names and artifact filenames use `nginx-module-markdown-for-agents`.
The module filename remains `ngx_http_markdown_filter_module.so`.

## Enable Module

`load_module` is a **main-context** directive: it belongs at the top level of
`nginx.conf`, before the `events` and `http` blocks. It is invalid inside
`http`, `server`, or `location`, and `conf.d/` is the wrong place for it.

A relative module path in `load_module` resolves against the NGINX **prefix**
(not against `--modules-path`), so whether the relative form works depends on
which nginx package you installed. Inspect both values on your host:

```bash
nginx -V 2>&1 | tr ' ' '\n' | grep -E 'modules-path|prefix='
```

Each package family installs the `.so` where that family expects it, and the
directive form below is the one that resolves there:

| Family | Module directory | Loader snippet | How it becomes active |
| --- | --- | --- | --- |
| DEB — distribution package (Debian/Ubuntu) | `/usr/lib/nginx/modules` | `/usr/share/nginx/modules-available/mod-markdown.conf` | Symlink it into `/etc/nginx/modules-enabled/`, which `nginx.conf` includes at the top level |
| DEB — nginx.org package | `/usr/lib/nginx/modules` | `/usr/share/nginx/modules-available/mod-markdown.conf` | No `modules-enabled` directory and no module include: add the directive (absolute path) to the top level of `/etc/nginx/nginx.conf` |
| RPM — nginx.org package (RHEL/Fedora/Alma/Rocky) | `/usr/lib64/nginx/modules` | `/usr/share/nginx/modules/mod-markdown.conf` | No automatic include: add the directive to the top level of `/etc/nginx/nginx.conf`, or `include /usr/share/nginx/modules/*.conf;` there |
| Alpine — distribution package | `/usr/lib/nginx/modules` | `/etc/nginx/modules/*.conf` | `nginx.conf` includes that directory at the top level. Drop the directive in as a file |
| Alpine — nginx.org package/image | `/usr/lib/nginx/modules` | — | No module include: add the directive to the top level of `/etc/nginx/nginx.conf` |
| Arch/Omarchy — distribution package | `/usr/lib/nginx/modules` | `/etc/nginx/modules.d/*.conf` | `nginx.conf` includes that directory at the top level. Drop the directive in as a file |
| Source build / custom prefix | `<prefix>/modules` (or the configured `--modules-path`) | — | Add the directive to the top level of your `nginx.conf` |

Because the project's packages install the module only in the compiled modules
directory, the shipped snippets use the form that resolves on their family: an
absolute path for DEB (`/usr/lib/nginx/modules/...`, because distribution
module packages also copy the `.so` into the prefix directory, which is why
their own snippets can use the relative form) and the relative form for RPM (nginx.org
ships `/etc/nginx/modules` as a symlink to `/usr/lib64/nginx/modules`).

Enable it for the package you installed:

```bash
# DEB, distribution package: activate the shipped snippet, then reload.
sudo ln -sf /usr/share/nginx/modules-available/mod-markdown.conf \
    /etc/nginx/modules-enabled/50-mod-markdown.conf

# DEB (nginx.org) / RPM / Alpine (nginx.org): add the directive at the top
# level of nginx.conf — before the events block, e.g. with
#   load_module /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so;   # DEB, Alpine
#   load_module modules/ngx_http_markdown_filter_module.so;                  # RPM (nginx.org)
# For RPM you can also keep the shipped snippet in play:
sudo sed -i 's|^#load_module |load_module |' \
    /usr/share/nginx/modules/mod-markdown.conf
#   and add to the top level of nginx.conf:  include /usr/share/nginx/modules/*.conf;
```

This project verified the directory layout and include behaviour above
against real packages instead of assuming them: Debian 12 (nginx 1.22.1 — `include
/etc/nginx/modules-enabled/*.conf;` at line 5, and `libnginx-mod-http-geoip2`
installs the `.so` into both `/usr/lib/nginx/modules` and
`/usr/share/nginx/modules`), Ubuntu 24.04 with the nginx.org repository (nginx
1.30.4 — no `modules-enabled`, no module include, `/etc/nginx/modules` is a
symlink to `/usr/lib/nginx/modules`), Rocky Linux 9 with the nginx.org
repository (nginx 1.30.4 — no module include, and `/etc/nginx/modules` symlinks
to `/usr/lib64/nginx/modules`), Alpine 3.21 (nginx 1.26.3 — `include
/etc/nginx/modules/*.conf;` at line 15), the nginx.org Alpine image (nginx
1.30.4 — no module include), and Arch (`include modules.d/*.conf;` at line 13).
Re-check your own host with the command above: distributions change this
between releases.

Then configure the filter in `http`, `server`, or `location` context:

```nginx
location / {
    markdown_filter on;
}
```

Reload after validation, and confirm the module loads:

```bash
sudo nginx -t && sudo nginx -s reload
sudo nginx -T 2>&1 | grep -E '^[[:space:]]*load_module[[:space:]]+[^;]*ngx_http_markdown_filter_module\.so'
```

Loading the module does not change any response on its own: conversion stays
opt-in per location through `markdown_filter on;`.

## Upgrade

Download the new artifact and matching `SHA256SUMS` file from the target
release. Before installing, repeat the detached-signature verification in the
DEB or RPM section above with an independently authenticated
`TRUSTED_FINGERPRINT`, then verify the selected package against that release's
`SHA256SUMS`. Reinstall the package only after both checks pass.

## Rollback

Download the previous release artifact that matches the installed NGINX
version and architecture. Repeat the detached-signature verification in the
DEB or RPM section above with the independently authenticated fingerprint,
then verify the artifact against that release's `SHA256SUMS` before installing
it locally.

## Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| `module is not binary compatible` | Package built for a different NGINX version | Install the artifact whose filename matches your NGINX version |
| `cannot open shared object file` | `load_module` path does not match package family | Use `/usr/lib/nginx/modules/...` for DEB and `/usr/lib64/nginx/modules/...` for RPM |
| Checksum verification fails | Package and `SHA256SUMS` came from different releases or the download is corrupt | Re-download both files from the same GitHub Release |
| Bare APT/YUM install fails | Public package repositories are not published yet | Use the GitHub Release artifact workflow above |


## Automated Diagnostics

Use [`nginx-markdown-doctor`](./doctor.md) for automated installation
verification after installing a package:

```bash
bash tools/doctor/nginx-markdown-doctor.sh
```
