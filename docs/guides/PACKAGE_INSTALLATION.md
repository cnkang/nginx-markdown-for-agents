# Package Installation Guide

## DEB-based Systems (Ubuntu, Debian)

### Install from Repository

> **PLACEHOLDER**: The APT repository URL and GPG key URL below are placeholders.
> This project does not currently own the domain. Replace with your self-hosted
> repository URL when available. **(Placeholder — must be completed before distribution.)**

```bash
# Add GPG key
# PLACEHOLDER: Replace APT_REPO_URL with your actual repository base URL
curl -fsSL https://APT_REPO_URL/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/nginx-markdown.gpg

# Add repository
echo "deb [signed-by=/usr/share/keyrings/nginx-markdown.gpg] https://APT_REPO_URL $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/nginx-markdown.list

sudo apt-get update
sudo apt-get install nginx-markdown-module
```

### Verify Installation

```bash
nginx -V 2>&1 | grep markdown
# Should show: --add-dynamic-module=.../nginx-markdown-module

# Test module load
nginx -t
```

### Enable Module

Add to your NGINX configuration (top-level context, before `http`):

```nginx
# DEB packages:
load_module /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so;

# RPM packages:
load_module /usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so;
```

Then configure in `http`, `server`, or `location` context:

```nginx
location / {
    markdown_filter on;
}
```

## RPM-based Systems (RHEL, Amazon Linux)

### Install from Repository

> **PLACEHOLDER**: The YUM repository URL and GPG key URL below are placeholders.
> This project does not currently own the domain. Replace with your self-hosted
> repository URL when available. **(Placeholder — must be completed before distribution.)**

```bash
# Add GPG key
# PLACEHOLDER: Replace YUM_REPO_URL with your actual repository base URL
sudo rpm --import https://YUM_REPO_URL/gpg.key

# Add repository
sudo tee /etc/yum.repos.d/nginx-markdown.repo <<'EOF'
[nginx-markdown]
name=NGINX Markdown Module
baseurl=https://YUM_REPO_URL/$releasever/$basearch
enabled=1
gpgcheck=1
EOF

sudo yum install nginx-markdown-module
```

## Upgrade

```bash
# DEB
sudo apt-get update && sudo apt-get install nginx-markdown-module
sudo nginx -s reload

# RPM
sudo yum update nginx-markdown-module
sudo nginx -s reload
```

## Rollback

```bash
# DEB - install specific version
sudo apt-get install nginx-markdown-module=<old_version>

# RPM - downgrade
sudo yum downgrade nginx-markdown-module
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `nginx: [emerg] module ... has wrong version` | ABI mismatch | Install package matching your NGINX version |
| `nginx: [emerg] dlopen() failed` | Missing shared library | Check `ldd` on .so file |
| GPG signature verification failed | Expired/rotated key | Re-import GPG key |
| Module not loaded | Missing `load_module` | Add `load_module` directive |
