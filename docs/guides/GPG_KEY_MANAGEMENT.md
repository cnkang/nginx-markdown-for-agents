# GPG Key Management

## Overview

This document describes the GPG key lifecycle for signing NGINX Markdown Filter
Module packages (DEB/RPM) and repository metadata. It covers key generation,
distribution, import instructions for end users, key rotation, CI/CD
integration, and security considerations.

Current public package channel: GitHub Release DEB/RPM artifacts with
checksum verification. Public APT/YUM repositories are not available yet; GPG
key publication for repository metadata applies to future self-hosted repository publication.

---

## 1. Key Generation

Generate a dedicated GPG signing key for the project. Do not reuse personal
keys.

### Recommended Parameters

| Parameter | Value |
|-----------|-------|
| Algorithm | RSA 4096 or Ed25519 |
| Expiry | No expiry or 5+ years |
| Usage | Signing only (no encryption) |
| UID | `NGINX Markdown Module <maintainer@example.com>` |
| Subkey | Dedicated signing subkey (keep primary offline) |

### Generation Commands

```bash
# Option A: RSA 4096 (widest compatibility)
gpg --full-generate-key
# Select: (1) RSA and RSA
# Key size: 4096
# Expiry: 0 (no expiry) or 5y
# Real name: NGINX Markdown Module
# Email: maintainer@example.com

# Option B: Ed25519 (modern, smaller signatures)
gpg --quick-generate-key \
  "NGINX Markdown Module <maintainer@example.com>" \
  ed25519 sign 0

# Add a dedicated signing subkey (recommended)
gpg --edit-key <KEY_ID>
# > addkey → select signing-only → save
```

<!-- TODO: Replace with actual values when GPG key is generated -->

### Key Details (Placeholder)

| Field | Value |
|-------|-------|
| Key ID | <!-- TODO: Replace with actual key ID --> `XXXXXXXXXXXXXXXX` |
| Fingerprint | <!-- TODO: Replace with actual fingerprint --> `XXXX XXXX XXXX XXXX XXXX  XXXX XXXX XXXX XXXX XXXX` |
| UID | <!-- TODO: Replace with actual UID --> `NGINX Markdown Module <maintainer@example.com>` |
| Created | <!-- TODO: Replace with actual date --> `YYYY-MM-DD` |
| Expires | <!-- TODO: Replace with actual expiry --> `Never` or `YYYY-MM-DD` |

---

## 2. Key Distribution

The public key is distributed through multiple channels to ensure availability
and verifiability.

### Distribution Channels

| Channel | URL / Location | Priority |
|---------|---------------|----------|
| HTTPS (primary) | <!-- TODO: Replace with actual URL --> `https://pkg.example.com/nginx-markdown/gpg.key` | Primary |
| GitHub Releases | Attached to each release as `gpg.key` | Secondary |
| Key server (optional) | `keys.openpgp.org` or `keyserver.ubuntu.com` | Optional |

### Export Public Key

```bash
# Export armored public key
gpg --armor --export <KEY_ID> > gpg.key

# Verify export
gpg --show-keys gpg.key
```

<!-- TODO: Replace <KEY_ID> with actual key ID when GPG key is generated -->

### Publish to Key Server (Optional)

```bash
gpg --keyserver keys.openpgp.org --send-keys <KEY_ID>
```

---

## 3. Key Import Instructions

Public APT/YUM repositories are not part of the 0.7.0 GA channel. The examples
below are for self-hosted repository publication after the repository URL and
signing key are real. For current v0.7.0+ package artifacts, download the package and
`SHA256SUMS` file from the same GitHub Release and follow
`PACKAGE_INSTALLATION.md`.

### Future APT Repository Users (Debian/Ubuntu)

```bash
# Download and install the GPG key into the system keyring
curl -fsSL https://pkg.example.com/nginx-markdown/gpg.key \
  | sudo gpg --dearmor -o /usr/share/keyrings/nginx-markdown.gpg

# Add the repository with signed-by reference
echo "deb [signed-by=/usr/share/keyrings/nginx-markdown.gpg] \
  https://pkg.example.com/nginx-markdown/apt $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/nginx-markdown.list
```

<!-- TODO: Replace https://pkg.example.com/nginx-markdown/ with actual URL when available -->

### Future YUM/DNF Repository Users (RHEL, AlmaLinux, Amazon Linux)

```bash
# Import the GPG key
sudo rpm --import https://pkg.example.com/nginx-markdown/gpg.key

# Add the repository
sudo tee /etc/yum.repos.d/nginx-markdown.repo <<'EOF'
[nginx-markdown]
name=NGINX Markdown Module
baseurl=https://pkg.example.com/nginx-markdown/yum/$releasever/$basearch
enabled=1
gpgcheck=1
gpgkey=https://pkg.example.com/nginx-markdown/gpg.key
EOF
```

<!-- TODO: Replace https://pkg.example.com/nginx-markdown/ with actual URL when available -->

### Verify Key Fingerprint

After importing, verify the key fingerprint matches the published value:

```bash
# APT
gpg --no-default-keyring \
  --keyring /usr/share/keyrings/nginx-markdown.gpg \
  --fingerprint

# RPM
rpm -qa gpg-pubkey* --qf '%{NAME}-%{VERSION}-%{RELEASE}\t%{SUMMARY}\n' \
  | grep -i markdown
```

Expected fingerprint:
<!-- TODO: Replace with actual fingerprint when GPG key is generated -->
```
XXXX XXXX XXXX XXXX XXXX  XXXX XXXX XXXX XXXX XXXX
```

---

## 4. Key Rotation Process

Key rotation is required when a key is compromised, approaching expiry, or as
part of periodic security hygiene.

### Step-by-Step Procedure

#### Step 1: Generate New Key

```bash
gpg --full-generate-key
# Use same parameters as original key (see Section 1)
```

#### Step 2: Publish New Key Alongside Old Key (Transition Period)

During the transition period (recommended: 90 days minimum), both keys are
available:

```bash
# Export both keys into a combined keyring file
gpg --armor --export <OLD_KEY_ID> <NEW_KEY_ID> > gpg.key

# Or publish separately
gpg --armor --export <NEW_KEY_ID> > gpg-new.key
```

<!-- TODO: Replace <OLD_KEY_ID> and <NEW_KEY_ID> with actual values during rotation -->

Update the HTTPS distribution endpoint to serve the combined key file.

#### Step 3: Sign New Packages with New Key

Update CI/CD secrets (see Section 5) to use the new key for all new package
builds:

- Update `GPG_PRIVATE_KEY` secret with new private key
- Update `GPG_PASSPHRASE` secret with new passphrase
- Update `GPG_KEY_ID` secret with new key ID

#### Step 4: Update Repository Metadata Signatures

Re-sign repository metadata with the new key:

```bash
# APT: Re-sign Release file
gpg --default-key <NEW_KEY_ID> -abs -o dists/stable/Release.gpg dists/stable/Release
gpg --default-key <NEW_KEY_ID> --clearsign -o dists/stable/InRelease dists/stable/Release

# YUM: Re-sign repomd.xml
gpg --default-key <NEW_KEY_ID> --detach-sign --armor repodata/repomd.xml
```

#### Step 5: Announce Rotation

Publish a rotation announcement including:

- New key fingerprint
- Transition timeline (start date, end date for old key)
- Instructions for users to import the new key
- Link to the updated key file

Announcement channels:
- GitHub Release notes
- Repository README
- Project documentation

#### Step 6: Remove Old Key (After Transition Period)

After the transition period expires:

1. Remove old key from the combined key file
2. Revoke old key on key servers (if published)
3. Update documentation to reference only the new key
4. Remove old key from CI/CD secrets

```bash
# Revoke old key (generates revocation certificate)
gpg --gen-revoke <OLD_KEY_ID> > revoke-old.asc
gpg --import revoke-old.asc
gpg --keyserver keys.openpgp.org --send-keys <OLD_KEY_ID>
```

### Rotation Timeline

| Phase | Duration | Actions |
|-------|----------|---------|
| Preparation | 1 week | Generate new key, test signing |
| Transition start | Day 0 | Publish both keys, sign with new key |
| Transition period | 90 days | Both keys valid, new packages use new key |
| Transition end | Day 90 | Remove old key, revoke on key servers |

---

## 5. CI/CD Integration

The GPG signing key is stored in GitHub Actions secrets for automated package
signing during the release workflow.

### Required Secrets

| Secret Name | Content | Format |
|-------------|---------|--------|
| `GPG_PRIVATE_KEY` | Armored private key (including subkeys) | ASCII-armored PEM |
| `GPG_PASSPHRASE` | Key passphrase | Plain text |
| `GPG_KEY_ID` | Key ID used for signing | 16-character hex ID |

<!-- TODO: Replace with actual key ID format when GPG key is generated -->

### Setting Up Secrets

```bash
# Export private key for CI
gpg --armor --export-secret-keys <KEY_ID> > private-key.asc

# Store in GitHub Secrets:
# Settings → Secrets and variables → Actions → New repository secret
#   Name: GPG_PRIVATE_KEY
#   Value: (contents of private-key.asc)
#
#   Name: GPG_PASSPHRASE
#   Value: (key passphrase)
#
#   Name: GPG_KEY_ID
#   Value: (16-char key ID, e.g. ABCDEF1234567890)

# Clean up - securely delete the exported key
shred -u private-key.asc
```

### Workflow Usage

The `sign-and-publish.yml` workflow uses these secrets to:

1. Import the private key into the CI runner's GPG keyring
2. Configure gpg-agent for non-interactive signing
3. Sign all `.deb` and `.rpm` packages with `dpkg-sig` and `rpm --addsign`
4. Sign APT repository metadata (`Release.gpg`, `InRelease`)
5. Sign YUM repository metadata (`repomd.xml.asc`)

### Verifying CI Signing

After a release build, verify signatures locally:

```bash
# Download signed package from GitHub Release
# Verify DEB signature
dpkg-sig --verify nginx-markdown-module_*.deb

# Verify RPM signature
rpm --import gpg.key
rpm -K nginx-markdown-module-*.rpm
```

---

## 6. Security Considerations

### Key Storage

- **Primary key offline**: Keep the primary private key on an air-gapped
  machine or hardware security module (HSM). Only the signing subkey should
  be used in CI.
- **CI secrets**: GitHub Actions secrets are encrypted at rest and masked in
  logs. Limit access to repository administrators.
- **Backup**: Store an encrypted backup of the primary key and revocation
  certificate in a secure, separate location.

### Access Control

- Limit GPG secret access to repository administrators only.
- Use GitHub environment protection rules for the signing workflow.
- Require approval for manual workflow dispatch of `sign-and-publish`.
- Audit secret access through GitHub audit logs.

### Compromise Response

If the signing key is suspected to be compromised:

1. **Immediately** revoke the compromised key:
   ```bash
   gpg --gen-revoke <COMPROMISED_KEY_ID> > revoke.asc
   gpg --import revoke.asc
   gpg --keyserver keys.openpgp.org --send-keys <COMPROMISED_KEY_ID>
   ```

2. **Rotate** all CI/CD secrets (see Section 4 for rotation procedure).

3. **Notify** users through:
   - GitHub Security Advisory
   - Repository README banner
   - Release notes

4. **Re-sign** all affected packages with the new key.

5. **Investigate** the compromise vector and remediate.

### Best Practices

- Use a dedicated signing subkey (not the primary key) in CI.
- Set calendar reminders for key expiry (if expiry is configured).
- Periodically verify that published packages match expected signatures.
- Monitor for unauthorized package publications.
- Keep GPG software updated on CI runners.

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.7.0 | 2026-05-17 | spec-agent | Initial GPG key management documentation |
