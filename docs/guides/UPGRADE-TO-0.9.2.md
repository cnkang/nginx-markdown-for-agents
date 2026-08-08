# Upgrade Guide: 0.9.2

## Overview

This guide covers upgrading to nginx-markdown-for-agents 0.9.2 from 0.9.1.
0.9.2 is a **breaking release**. The release reduces the configuration surface from
63 directives to 25, and configurations using any removed directive fail
`nginx -t` with `unknown directive` until migrated. Review
[0.9.2-breaking-changes.md](0.9.2-breaking-changes.md) and
[MIGRATION-0.9.2.md](MIGRATION-0.9.2.md) before upgrading. If you are running
0.9.0, complete [MIGRATION-0.9.1.md](MIGRATION-0.9.1.md) before following
this guide.

> Publication status: 0.9.2 is currently a development candidate. At the
> time of writing, no `v0.9.2` tag, GitHub Release, package checksum, Docker
> image, or Helm repository entry gets asserted. The prebuilt, Helm, and Docker
> commands below are release-time templates and must only run after those
> the project publishes artifacts and verifies them independently. For the current
> candidate, build from the exact branch commit or use locally produced
> artifacts.

Choose the upgrade method matching your deployment:

| Method | Section |
|--------|---------|
| Prebuilt module (`.so` replacement) | [Prebuilt Module Upgrade](#prebuilt-module-upgrade) |
| Source build | [Source Build Upgrade](#source-build-upgrade) |
| Helm chart | [Helm Upgrade](#helm-upgrade) |
| Docker | [Docker Upgrade](#docker-upgrade) |

---

## Prebuilt Module Upgrade

### 1. Download the 0.9.2 module binary

```bash
# Replace <nginx-version> and <os> with your target (e.g., 1.26.3, ubuntu22.04)
MODULE_FILE="ngx_http_markdown_filter_module-0.9.2-nginx<nginx-version>-<os>-x86_64.so"
curl --fail --location --remote-name "https://github.com/cnkang/nginx-markdown-for-agents/releases/download/v0.9.2/${MODULE_FILE}"
curl --fail --location --remote-name https://github.com/cnkang/nginx-markdown-for-agents/releases/download/v0.9.2/SHA256SUMS
```

### 2. Verify checksum

```bash
CHECKSUM_LINE="$(awk -v module_file="${MODULE_FILE}" '
    NF == 2 && $2 == module_file { count++; line = $0 }
    END { if (count != 1) exit 1; print line }
' SHA256SUMS)" || {
    echo "SHA256SUMS must contain exactly one ${MODULE_FILE} record" >&2
    exit 1
}
printf '%s\n' "${CHECKSUM_LINE}" | sha256sum --check
```

### 3. Back up the current module

```bash
sudo cp /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so \
    /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so.0.9.1.bak
```

### 4. Replace the module

```bash
sudo cp ngx_http_markdown_filter_module-0.9.2-nginx*.so \
    /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so
```

### 5. Validate and restart

```bash
sudo nginx -t && sudo systemctl restart nginx
```

---

## Source Build Upgrade

### 1. Update the repository

```bash
cd nginx-markdown-for-agents
git fetch --tags
# Use this tag only after the 0.9.2 publication evidence exists; for the
# current candidate, check out the exact reviewed development commit instead.
git checkout v0.9.2
```

### 2. Update Rust toolchain

```bash
rustup toolchain install 1.97.0
rustup default 1.97.0
```

### 3. Build the Rust converter

```bash
cd components/rust-converter
cargo build --release
cd ../..
```

### 4. Rebuild the NGINX module

```bash
# Using your existing NGINX build directory
cd /path/to/nginx-build
make modules
```

### 5. Install and restart

```bash
sudo cp objs/ngx_http_markdown_filter_module.so \
    /usr/lib/nginx/modules/
sudo nginx -t && sudo systemctl restart nginx
```

---

## Helm Upgrade

### 1. Update the chart repository

```bash
helm repo update
```

### 2. Upgrade the release

```bash
helm upgrade nginx-markdown nginx-markdown/nginx-markdown-for-agents \
    --namespace nginx-markdown \
    --set markdown.image.tag=v0.9.2
```

### 3. Verify

```bash
helm status nginx-markdown --namespace nginx-markdown
kubectl rollout status deployment/nginx-markdown --namespace nginx-markdown
```

---

## Docker Upgrade

### 1. Pull the 0.9.2 image

```bash
docker pull cnkang/nginx-markdown-for-agents:v0.9.2
```

### 2. Update your compose or deployment

```yaml
image: cnkang/nginx-markdown-for-agents:v0.9.2
```

### 3. Restart

```bash
docker compose up -d
```

---

## Post-Upgrade Verification

After upgrading by any method, run these checks:

### 1. Configuration validation

```bash
sudo nginx -t
# Expected: syntax is ok / test is successful
```

### 2. Doctor check

```bash
bash tools/doctor/nginx-markdown-doctor.sh
# All checks should pass
```

### 3. Diagnostics endpoint

```bash
curl -s http://localhost/nginx-markdown/diagnostics | python3 -m json.tool
# Verify version shows 0.9.2
# Verify reason_to_code includes bypass_no_transform
```

### 4. Metrics endpoint

```bash
curl --fail --silent --show-error http://localhost/markdown-metrics
# Verify metric families are present and emitting
```

### 5. Functional smoke test

```bash
curl -sD - -H "Accept: text/markdown" http://localhost/docs/ | head -5
# Expected: HTTP/1.1 200 OK
# Expected: Content-Type: text/markdown; charset=utf-8
```

---

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-07-30 | Kang | Initial upgrade guide for 0.9.2 |
