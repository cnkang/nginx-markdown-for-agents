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
| musl-based Linux (Alpine, etc.) | Dynamic-module tarball | `release-binaries.yml` (GitHub Releases) — pair it with an ABI-compatible NGINX binary per the compatibility matrix |

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
awk -v pkg="${PKG}" '$2 == pkg { print; count++ } END { exit count == 1 ? 0 : 1 }' SHA256SUMS | sha256sum -c -
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
awk -v pkg=" ${PKG}$" '$0 ~ pkg { count++; line=$0 } END { if (count == 1) print line; else exit 1 }' SHA256SUMS | sha256sum -c -
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
