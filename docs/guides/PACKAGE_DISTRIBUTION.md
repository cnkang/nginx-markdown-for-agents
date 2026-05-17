# Package Distribution Strategy

## Overview

This document defines the distribution strategy for the NGINX Markdown Filter
Module binary packages (DEB and RPM).

## Distribution Channels

| Channel | Format | Repository | Signing |
|---------|--------|-----------|---------|
| Self-hosted APT | .deb | `PLACEHOLDER: APT_REPO_URL` | GPG |
| Self-hosted YUM | .rpm | `PLACEHOLDER: YUM_REPO_URL` | GPG |
| GitHub Releases | .deb + .rpm | Attachments | GPG |

> **PLACEHOLDER**: The self-hosted APT and YUM repository URLs are placeholders.
> This project does not currently own the hosting domain. Replace with your
> self-hosted repository URL when available. **(Placeholder — must be completed before distribution.)**

## Important Disclaimers

1. **These are NOT official NGINX repository packages.** They are community-maintained.
2. **NGINX dynamic module ABI is version-sensitive.** Each package is built
   against a specific NGINX version and channel (stable/mainline). Mismatched
   ABI versions will cause module load failures at runtime.
3. **Default installation does NOT globally enable the module.** Operators must
   explicitly add `load_module` directives. This prevents accidental conversion
   of all HTML responses.

## NGINX ABI Sensitivity

The dynamic module ABI changes between NGINX major/minor versions. Package
names encode the target NGINX version:

```
nginx-markdown-module_<version>~nginx<nginx_ver>+<distro><distro_ver>_<arch>.deb
```

Example: `nginx-markdown-module_0.7.0~nginx1.26.2+ubuntu22.04_amd64.deb`

## Security Policy

- Packages are GPG-signed with a project key.
- The default `postinst` script does NOT add `load_module` to `nginx.conf`.
- Operators must explicitly enable the module, ensuring intentional activation.
- Module is loaded as a dynamic module (`--add-dynamic-module`), not compiled in.

## Non-Goals

- Publishing to official NGINX, Debian, or Fedora repositories.
- Providing static module builds (dynamic module only).
- Supporting NGINX forks or custom builds with different ABI.
