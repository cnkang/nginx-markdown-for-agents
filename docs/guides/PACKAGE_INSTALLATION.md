# Package Installation Guide

This guide covers installing DEB and RPM artifacts produced by the v0.7.0 and
later release workflows. These are community-maintained dynamic-module packages, not
official NGINX repository packages.

## Repository Publishing Status

GitHub Releases are the current distribution channel for DEB and RPM package
artifacts.
Public APT/YUM repositories are planned but not available yet.
They are not part of the current GA channel.

Bare package-manager installation commands only work after an operator
publishes and configures a real APT or YUM repository. Until then, download the
matching package artifact and its `SHA256SUMS` file from the same GitHub
Release.

## Select the Matching Artifact

Package filenames include the module version, target NGINX version, and CPU
architecture. The target NGINX version must match the installed NGINX ABI.

### Platform Availability

| Platform | Format | Source |
|----------|--------|--------|
| glibc-based Linux (Ubuntu, Debian, RHEL, AlmaLinux, Amazon Linux) | DEB/RPM | `release-packages.yml` (GitHub Releases) |
| musl-based Linux (Alpine, etc.) | Static binary tarball | `release-binaries.yml` (GitHub Releases) |

DEB and RPM packages are built on glibc-based build images and target
glibc-based distributions only. For musl-based environments (Alpine Linux,
etc.), use the pre-built binary tarball from the Release Binaries workflow or
build from source.

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

Replace `VERSION` below with the release tag you are installing (latest patch
on the 0.8.x line is `0.8.2`). `NGINX_VERSION` must match the NGINX ABI you
run.

```bash
VERSION=0.8.2  # replace with the release tag you are installing
NGINX_VERSION=1.26.3
ARCH=amd64
BASE_URL="https://github.com/cnkang/nginx-markdown-for-agents/releases/download/v${VERSION}"
PKG="nginx-module-markdown-for-agents_${VERSION}_nginx-${NGINX_VERSION}_${ARCH}.deb"

curl -fSLO "${BASE_URL}/SHA256SUMS"
curl -fSLO "${BASE_URL}/${PKG}"
grep " ${PKG}$" SHA256SUMS | sha256sum -c -
sudo apt install "./${PKG}"
```

## RPM Artifacts (AlmaLinux, Amazon Linux, RHEL)

Replace `VERSION` below with the release tag you are installing (latest patch
on the 0.8.x line is `0.8.2`). `NGINX_VERSION` must match the NGINX ABI you
run.

```bash
VERSION=0.8.2  # replace with the release tag you are installing
NGINX_VERSION=1.26.3
ARCH=x86_64
BASE_URL="https://github.com/cnkang/nginx-markdown-for-agents/releases/download/v${VERSION}"
PKG="nginx-module-markdown-for-agents-${VERSION}-nginx${NGINX_VERSION}-1.${ARCH}.rpm"

curl -fSLO "${BASE_URL}/SHA256SUMS"
curl -fSLO "${BASE_URL}/${PKG}"
grep " ${PKG}$" SHA256SUMS | sha256sum -c -
sudo rpm -Uvh "./${PKG}"
```

## Verify Installation

```bash
nginx -V 2>&1 | grep markdown
nginx -t
```

The module binary is installed using the canonical NGINX dynamic-module name:

```text
ngx_http_markdown_filter_module.so
```

Package names and artifact filenames use `nginx-module-markdown-for-agents`;
the module filename remains `ngx_http_markdown_filter_module.so`.

## Enable Module

Add the matching `load_module` directive at the top level of the NGINX
configuration, before `http`:

```nginx
# DEB packages:
load_module /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so;

# RPM packages:
load_module /usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so;
```

Then configure the filter in `http`, `server`, or `location` context:

```nginx
location / {
    markdown_filter on;
}
```

Reload after validation:

```bash
sudo nginx -t && sudo nginx -s reload
```

## Upgrade

Download the new artifact and matching `SHA256SUMS` file from the target
release, verify the checksum, then reinstall the package with the same local
artifact flow.

## Rollback

Download the previous release artifact that matches the installed NGINX
version and architecture, verify it against that release's `SHA256SUMS`, then
install it locally.

## Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| `module is not binary compatible` | Package built for a different NGINX version | Install the artifact whose filename matches your NGINX version |
| `cannot open shared object file` | `load_module` path does not match package family | Use `/usr/lib/nginx/modules/...` for DEB and `/usr/lib64/nginx/modules/...` for RPM |
| Checksum verification fails | Package and `SHA256SUMS` came from different releases or the download is corrupt | Re-download both files from the same GitHub Release |
| Bare APT/YUM install fails | Public package repositories are not published yet | Use the GitHub Release artifact workflow above |
