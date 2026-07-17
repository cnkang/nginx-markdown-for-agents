# Kubernetes Deployment Guide

## Overview

This guide provides reference examples for deploying the NGINX Markdown
Filter Module in Kubernetes environments with NGINX Ingress Controllers.

**Important:** These are reference examples, not officially supported
configurations. Each Ingress Controller implementation may require
different customization approaches.

## Verified Ingress Controllers

| Controller | Version | Module Injection | Status |
|-----------|---------|-----------------|--------|
| ingress-nginx (community) | 1.10+ | Custom image build | Verified |
| F5 NGINX Ingress Controller | Latest | Dynamic module volume | Experimental |

## Enable/Verify/Disable/Rollback

### Enable

1. Build custom Ingress Controller image with module included
2. Update Deployment to use custom image
3. Add `load_module` and `markdown_filter` via ConfigMap snippet

### Verify

```bash
kubectl exec -n ingress-nginx <pod> -- nginx -V 2>&1 | grep markdown
```

### Disable

Remove `markdown_filter on` from ConfigMap; module remains loaded but inactive.

### Rollback

Revert Deployment image tag to upstream Ingress Controller image.

---

## Custom Ingress Controller Image Build

This section documents how to build a custom NGINX Ingress Controller image
that includes the `ngx_http_markdown_filter_module`. The Dockerfile is located
at `examples/kubernetes/Dockerfile.ingress`.

### Prerequisites

Before building the custom image, ensure the following tools are available:

| Tool | Purpose | Minimum Version |
|------|---------|-----------------|
| Docker or Podman | Container image build | Docker 20.10+ / Podman 4.0+ |
| Docker Buildx (optional) | Multi-platform builds | Bundled with Docker 20.10+ |
| Rust toolchain | Compiles the Rust converter component | Rust 1.97.0 (MSRV 1.97) |
| NGINX source | Module compilation target | Must match Ingress Controller NGINX version |
| Git | Clone module source | Any recent version |

The Dockerfile handles Rust toolchain and NGINX source installation
automatically during the build. You only need Docker/Podman installed
locally to run the build.

**NGINX version matching:** The NGINX source version used for module
compilation must exactly match the NGINX binary version inside the
Ingress Controller base image. An ABI mismatch causes the module to
fail to load at runtime.

### Build Steps

The Dockerfile uses a multi-stage build:

1. **Stage 1 (module-build):** Installs build dependencies, Rust toolchain,
   downloads NGINX source, and compiles the dynamic module.
2. **Stage 2 (runtime):** Copies the compiled `.so` into the Ingress
   Controller image and injects the `load_module` configuration snippet.

#### Default Build

Build with default settings (NGINX 1.26.3, F5 NGINX Ingress Controller 3.7.2):

```bash
docker build -f examples/kubernetes/Dockerfile.ingress \
  -t my-ingress:latest .
```

#### Custom NGINX Version

Override the NGINX version to match your Ingress Controller:

```bash
docker build -f examples/kubernetes/Dockerfile.ingress \
  --build-arg NGINX_VERSION=1.24.0 \
  -t my-ingress:nginx-1.24 .
```

#### Custom Ingress Controller Image

Use a different base Ingress Controller image:

```bash
docker build -f examples/kubernetes/Dockerfile.ingress \
  --build-arg NGINX_INGRESS_IMAGE=nginx/nginx-ingress:3.7.2 \
  -t my-ingress:custom .
```

For plain NGINX (non-Ingress deployment):

```bash
docker build -f examples/kubernetes/Dockerfile.ingress \
  --build-arg NGINX_VERSION=1.26.3 \
  --build-arg NGINX_INGRESS_IMAGE=nginx:1.26.3 \
  -t my-nginx-markdown:latest .
```

#### Custom Module Source

Build from a specific branch, tag, or fork:

```bash
docker build -f examples/kubernetes/Dockerfile.ingress \
  --build-arg MODULE_REPO=https://github.com/your-org/nginx-markdown-for-agents.git \
  --build-arg MODULE_REF=v0.7.0 \
  -t my-ingress:v0.7.0 .
```

#### Multi-platform Build

Build for both amd64 and arm64 architectures:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -f examples/kubernetes/Dockerfile.ingress \
  -t my-ingress:multi .
```

Multi-platform builds require Docker Buildx with a builder that supports
the target platforms (e.g., `docker buildx create --use`).

### Customization Points

The following build arguments control the image build:

| Build Arg | Default | Description |
|-----------|---------|-------------|
| `NGINX_VERSION` | `1.26.3` | NGINX version for module compilation. Must match the NGINX binary in the base image exactly. Minimum supported: 1.24.0. |
| `NGINX_INGRESS_IMAGE` | `nginx/nginx-ingress:3.7.2` | Base Ingress Controller image. Can be any image containing an NGINX binary (including plain `nginx:*` images). |
| `MODULE_REPO` | `https://github.com/cnkang/nginx-markdown-for-agents.git` | Git repository URL for the module source code. |
| `MODULE_REF` | `main` | Git ref to checkout (branch name, tag, or commit SHA). |

### Verification

After building the image, verify the module is correctly compiled and loaded:

#### Check NGINX Version and Module

```bash
docker run --rm my-ingress:latest nginx -V 2>&1 | grep -E '(version|markdown)'
```

Expected output includes the NGINX version and the module in the configure
arguments.

#### Validate NGINX Configuration

```bash
docker run --rm my-ingress:latest nginx -t
```

Expected output:

```
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

#### Verify Module Loading

```bash
docker run --rm my-ingress:latest cat /etc/nginx/modules/10-mod-markdown.conf
```

Expected output:

```
# nginx-markdown-for-agents module
# Loaded automatically by NGINX Ingress Controller
load_module /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so;
```

#### Verify in Kubernetes

After deploying the custom image to your cluster:

```bash
kubectl exec -n ingress-nginx <pod-name> -- nginx -V 2>&1 | grep markdown
kubectl exec -n ingress-nginx <pod-name> -- nginx -t
```

### Troubleshooting

#### ABI Mismatch (Module Load Failure)

**Symptom:** Pod enters CrashLoopBackOff. Logs show:

```
nginx: [emerg] module is not binary compatible
```

**Cause:** The `NGINX_VERSION` build arg does not match the NGINX binary
version inside the base Ingress Controller image.

**Solution:**
1. Check the NGINX version in the base image:
   ```bash
   docker run --rm nginx/nginx-ingress:3.7.2 nginx -v
   ```
2. Rebuild with the matching version:
   ```bash
   docker build --build-arg NGINX_VERSION=<correct-version> ...
   ```

#### Missing Build Dependencies

**Symptom:** Build fails during module compilation with missing header errors.

**Cause:** The Dockerfile installs standard build dependencies for
Debian-based builds. If you modify the build stage, ensure `libpcre2-dev`,
`libssl-dev`, and `zlib1g-dev` are present.

**Solution:** Do not remove packages from the `apt-get install` line in
the module-build stage.

#### Rust Compilation Failure

**Symptom:** Build fails with Rust compiler errors.

**Cause:** Source builds require Rust 1.97.0 or newer (MSRV 1.97).
Network issues during `rustup` installation can also cause failures.

**Solution:**
- Ensure the build environment has internet access for downloading the
  Rust toolchain and crate dependencies.
- If behind a proxy, configure `HTTP_PROXY`/`HTTPS_PROXY` build args.

#### Multi-platform Build Failures

**Symptom:** `docker buildx build` fails for arm64 on an amd64 host.

**Cause:** QEMU emulation not configured or buildx builder not set up.

**Solution:**
```bash
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
docker buildx create --name multibuilder --use
docker buildx inspect --bootstrap
```

#### Module File Not Found in Runtime Image

**Symptom:** NGINX reports the module `.so` file does not exist.

**Cause:** The COPY instruction path does not match the module output
location, or the base image uses a non-standard modules directory.

**Solution:** Verify the module path inside the image:
```bash
docker run --rm my-ingress:latest ls -la /usr/lib/nginx/modules/
```

If the base image uses a different path, adjust the Dockerfile COPY
destination accordingly.

---

## Helm Chart

The chart is installable by default with a stock `nginx` image. The default
`markdown.enabled=false` keeps `load_module` and `markdown_*` directives out of
the rendered `nginx.conf`, which is the path used by the local stock-nginx
smoke test.

To enable the markdown module, use an image that already contains
`ngx_http_markdown_filter_module.so`, then set both values:

```bash
helm install nginx-markdown charts/nginx-markdown \
  --set image.repository=<your-nginx-markdown-image> \
  --set image.tag=<tag> \
  --set markdown.enabled=true \
  --set-string markdown.loadModule=/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so
```

When `markdown.enabled=true`, `markdown.loadModule` is required. The chart does
not create a `hostPath` mount from that value. If a deployment needs additional
volumes or mounts, use the explicit opt-in `extraVolumes` and
`extraVolumeMounts` values.

---

## Testing

This section documents how to run the Kubernetes smoke test, E2E scenario
tests, and Docker build validation scripts located under
`examples/kubernetes/tests/`.

### Smoke Test

The smoke test (`examples/kubernetes/tests/smoke-test.sh`) verifies basic
module functionality after Kubernetes deployment.

#### Prerequisites

| Tool | Purpose |
|------|---------|
| `curl` | HTTP requests to the service |
| `kubectl` | Port-forward to cluster pods (unless `--url` is provided) |
| Running K8s cluster | Module must be deployed and pods Running |

#### What It Tests

1. **Pod health** — Verifies the pod is running and reachable
2. **Markdown conversion** — Sends `Accept: text/markdown` and confirms the
   response contains markdown content
3. **Accept negotiation** — Sends `Accept: text/html` and confirms the module
   does NOT convert (pass-through)
4. **Metrics endpoint** — Verifies `/metrics` returns HTTP 200 with
   Prometheus-format data

#### Usage

With automatic `kubectl port-forward` (default):

```bash
cd examples/kubernetes/tests
./smoke-test.sh
```

With an explicit service URL (no kubectl required):

```bash
./smoke-test.sh --url http://nginx-markdown.example.com
```

Custom namespace and pod label:

```bash
./smoke-test.sh --namespace my-ns --label app.kubernetes.io/name=nginx-markdown
```

All options:

| Option | Default | Description |
|--------|---------|-------------|
| `-u, --url URL` | (port-forward) | Service base URL |
| `-n, --namespace NS` | `ingress-nginx` | Kubernetes namespace |
| `-l, --label LABEL` | `app=nginx-markdown` | Pod label selector |
| `-p, --port PORT` | `8080` | Local port for port-forward |
| `-t, --timeout SECS` | `10` | Curl timeout in seconds |

#### Expected Output

```
============================================================
[INFO]  nginx-markdown-for-agents K8s Smoke Test
============================================================
[INFO]  Target URL: http://localhost:8080

[PASS]  Pod is running and port-forward is active
[PASS]  Markdown conversion: response Content-Type contains markdown
[PASS]  Accept negotiation: response is not markdown (Content-Type: text/html)
[PASS]  Metrics endpoint: accessible and contains Prometheus metrics (HTTP 200)

------------------------------------------------------------
[INFO]  Results: 4 passed, 0 failed
------------------------------------------------------------
```

Exit code `0` means all checks passed; `1` means one or more failed.

---

### E2E Scenarios

The E2E scenario script (`examples/kubernetes/tests/e2e-scenarios.sh`)
validates the full deployment lifecycle. It creates its own namespace,
runs five scenarios sequentially, and cleans up on exit.

#### Prerequisites

| Tool | Purpose |
|------|---------|
| `kubectl` | Cluster operations (deploy, scale, rollback) |
| `curl` | Used by the embedded smoke test between scenarios |
| Manifest directory | K8s manifests at `examples/kubernetes/manifest/` |

#### What Scenarios Are Covered

| # | Scenario | Description |
|---|----------|-------------|
| 1 | **Deploy** | Apply manifests, wait for pods Ready, run smoke test |
| 2 | **Config Update** | Update ConfigMap, trigger rollout, verify config pickup |
| 3 | **Rolling Upgrade** | Trigger rolling update, verify zero-downtime, no CrashLoopBackOff |
| 4 | **Rollback** | Execute `kubectl rollout undo`, verify previous revision restored |
| 5 | **Scale** | Scale up to 3 replicas, verify all Ready; scale down to 1, verify |

After each scenario the smoke test runs automatically to confirm the module
remains functional.

#### Usage

Run with defaults (creates namespace `nginx-markdown-e2e`):

```bash
cd examples/kubernetes/tests
./e2e-scenarios.sh
```

Custom namespace and image:

```bash
./e2e-scenarios.sh --namespace my-ns --image myrepo/nginx-markdown:v0.7.0
```

Custom manifest directory and timeout:

```bash
./e2e-scenarios.sh --manifest-dir /path/to/manifests --timeout 180
```

All options:

| Option | Default | Description |
|--------|---------|-------------|
| `-n, --namespace NS` | `nginx-markdown-e2e` | Kubernetes namespace (created/deleted by script) |
| `-m, --manifest-dir DIR` | `../manifest` | Path to K8s manifests |
| `-i, --image IMAGE` | `nginx-markdown:latest` | Container image for deployment |
| `-t, --timeout SECS` | `120` | Timeout for rollout wait |

#### Expected Output

```
============================================================
[INFO]  nginx-markdown-for-agents K8s E2E Scenarios
[INFO]  Namespace:    nginx-markdown-e2e
[INFO]  Manifest dir: /path/to/examples/kubernetes/manifest
[INFO]  Image:        nginx-markdown:latest
[INFO]  Timeout:      120s
============================================================

>>> SCENARIO: 1. Deploy
------------------------------------------------------------
[PASS]  Scenario 'Deploy' PASSED

>>> SCENARIO: 2. Config Update
------------------------------------------------------------
[PASS]  Scenario 'Config Update' PASSED

>>> SCENARIO: 3. Rolling Upgrade
------------------------------------------------------------
[PASS]  Scenario 'Rolling Upgrade' PASSED

>>> SCENARIO: 4. Rollback
------------------------------------------------------------
[PASS]  Scenario 'Rollback' PASSED

>>> SCENARIO: 5. Scale
------------------------------------------------------------
[PASS]  Scenario 'Scale' PASSED

============================================================
[INFO]  E2E Scenario Results Summary
------------------------------------------------------------
PASS: Deploy
PASS: Config Update
PASS: Rolling Upgrade
PASS: Rollback
PASS: Scale
------------------------------------------------------------
[INFO]  Total: 5 passed, 0 failed (of 5 scenarios)
============================================================
```

Exit code `0` means all scenarios passed; `1` means one or more failed.
The namespace is automatically deleted on exit (cleanup trap).

---

### Docker Build Test

The Docker build test (`examples/kubernetes/tests/test-docker-build.sh`)
validates that the custom Ingress Controller image builds correctly and
the module is properly loaded.

#### Prerequisites

| Tool | Purpose |
|------|---------|
| Docker (or Podman) | Build and inspect the container image |

No Kubernetes cluster is required — this test operates entirely with
local Docker commands.

#### What It Verifies

1. **Docker build** — Image builds without errors
2. **nginx -V** — NGINX reports the markdown module (or `nginx -t` passes
   for dynamic modules)
3. **Module file** — `.so` file exists at
   `/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so`
4. **load_module snippet** — Configuration snippet at
   `/etc/nginx/modules/10-mod-markdown.conf` references the module

#### Usage

Run with defaults:

```bash
cd examples/kubernetes/tests
./test-docker-build.sh
```

Custom Dockerfile and tag:

```bash
./test-docker-build.sh --dockerfile /path/to/Dockerfile.ingress --tag my-test:v1
```

Keep the built image for further inspection:

```bash
./test-docker-build.sh --no-cleanup
```

All options:

| Option | Default | Description |
|--------|---------|-------------|
| `-d, --dockerfile PATH` | `../Dockerfile.ingress` | Path to Dockerfile |
| `-t, --tag TAG` | `nginx-markdown-test:latest` | Image tag |
| `-c, --context PATH` | Repository root | Docker build context |
| `--no-cleanup` | (cleanup enabled) | Keep image after test |

#### Expected Output

```
============================================================
[INFO]  nginx-markdown-for-agents Docker Build Test
============================================================
[INFO]  Test: Docker image builds successfully
[PASS]  Docker image built successfully: nginx-markdown-test:latest

[INFO]  Test: nginx -V reports markdown module
[PASS]  nginx -t passes (module loads without error)
[INFO]  Test: Module .so file exists at expected path
[PASS]  Module file exists: /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so
[INFO]  Test: load_module configuration snippet exists
[PASS]  load_module snippet correctly references markdown module

------------------------------------------------------------
[INFO]  Results: 4 passed, 0 failed
------------------------------------------------------------
RESULT: PASS (4/4 checks passed)
```

Exit code `0` means all checks passed; `1` means one or more failed;
`2` means a usage error or missing prerequisites.

---

### See Also

- [F5 Ingress Controller Feasibility](F5_INGRESS_FEASIBILITY.md)
- [Package Distribution Guide](PACKAGE_DISTRIBUTION.md)
- [examples/kubernetes/manifest/](../../examples/kubernetes/manifest/) — K8s deployment manifests
- [charts/nginx-markdown/](../../charts/nginx-markdown/) — Helm chart
