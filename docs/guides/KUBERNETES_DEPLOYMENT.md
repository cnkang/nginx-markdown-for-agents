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

## F5 NGINX Ingress Controller

**Feasible with limitations.** The F5 NGINX Ingress Controller supports
dynamic module loading via volume mounts, but the approach requires careful
version alignment.

### Injection Method

Mount the compiled `.so` file into the Ingress Controller pod via
a ConfigMap or PersistentVolume, and add `load_module` via the
`main-snippet` ConfigMap key.

**Image prerequisite.** This flow requires a custom Ingress Controller
image that already contains the module. A volume mount alone does not
make the flow supported: the Controller must load a module binary whose
ABI matches the NGINX inside the image, and managed deployments do not
allow replacing the Controller image (see Known Limitations). Treat the
example below as a feasibility sketch, not a supported deployment path.

```yaml
volumeMounts:
  - name: markdown-module
    mountPath: /etc/nginx/modules
    readOnly: true
```

### Known Limitations

1. **ABI binding**: The `.so` must compile against the exact NGINX
   version inside the F5 Controller image, using a compatible build
   configuration (matching `configure` arguments). Use `--with-compat`
   only when the target binary also enables it; otherwise the module
   binary must be built with the same configure arguments as the
   Controller's NGINX.
2. **No custom image**: F5 does not support replacing the Controller
   image with a custom build in managed deployments.
3. **Module updates**: Require rebuilding the `.so` when the Controller
   image upgrades.

### Alternative: Sidecar Proxy

For environments where Ingress Controller modification is not feasible,
deploy an NGINX sidecar with the markdown module in front of the
application pods.

## Enable/Verify/Disable/Rollback

### Enable

1. Build custom Ingress Controller image with module included
2. Update Deployment to use custom image
3. Add `load_module` and `markdown_filter` via ConfigMap snippet

### Verify

Verify the active configuration and a module-specific request. `nginx -V`
describes how the base binary was built. It does not prove that NGINX loaded a
separately compiled dynamic module.

```bash
kubectl exec -n ingress-nginx <pod> -- nginx -T 2>&1 \
  | grep -E '^[[:space:]]*load_module[[:space:]]+[^;]*ngx_http_markdown_filter_module\.so[[:space:]]*;'
kubectl exec -n ingress-nginx <pod> -- nginx -T 2>&1 \
  | grep -E '^[[:space:]]*markdown_filter[[:space:]]+on[[:space:]]*;'
kubectl exec -n ingress-nginx <pod> -- nginx -t
curl -fsS -D /tmp/markdown-smoke-headers.txt -H 'Accept: text/markdown' \
  https://<ingress-host>/docs/example
grep -i '^content-type: text/markdown' /tmp/markdown-smoke-headers.txt
```

The first two commands must show an active, semicolon-terminated
`load_module` directive and an active `markdown_filter on` directive. A
comment mentioning the module filename must not satisfy them. `nginx -t`
validates syntax only, so the directive greps are the load evidence. The
request must return Markdown rather than the upstream HTML, and the final
`grep` asserts that the response `Content-Type` header is `text/markdown`,
failing when the header is missing or names another type. For a negative
control, temporarily remove the active `load_module` line and repeat
`nginx -t`. The module-specific configuration must then fail. The smoke test
must report that failure rather than treating it as successful.

### Disable

Remove `markdown_filter on` from ConfigMap. This disables only Markdown conversion — the module remains loaded in NGINX and metrics or diagnostics handlers may still run. For full module deactivation, explicitly remove the `load_module` directive from the ConfigMap snippet and rebuild the custom image or revert to the upstream Ingress Controller image.

### Rollback

Revert Deployment image tag to upstream Ingress Controller image.

---

## Custom Ingress Controller Image Build

This section documents how to build a custom NGINX Ingress Controller image
that includes the `ngx_http_markdown_filter_module`. The Dockerfile lives
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

Build with default settings (NGINX 1.27.2, F5 NGINX Ingress Controller 3.7.2):

```bash
MODULE_SHA="$(git rev-parse HEAD)"
docker build -f examples/kubernetes/Dockerfile.ingress \
  --build-arg MODULE_SHA="${MODULE_SHA}" \
  -t my-ingress:latest .
```

#### Custom NGINX Version

Override the NGINX version to match your Ingress Controller:

```bash
docker build -f examples/kubernetes/Dockerfile.ingress \
  --build-arg NGINX_VERSION=1.24.0 \
  --build-arg MODULE_SHA="$(git rev-parse HEAD)" \
  -t my-ingress:nginx-1.24 .
```

#### Custom Ingress Controller Image

Use a different base Ingress Controller image:

```bash
docker build -f examples/kubernetes/Dockerfile.ingress \
  --build-arg NGINX_INGRESS_IMAGE=nginx/nginx-ingress@sha256:60f690573c0599aadd45468899f9baaa97a4775e852c4402b0003a1e58a8dc17 \
  --build-arg MODULE_SHA="$(git rev-parse HEAD)" \
  -t my-ingress:custom .
```

For plain NGINX (non-Ingress deployment):

```bash
docker build -f examples/kubernetes/Dockerfile.ingress \
  --build-arg NGINX_VERSION=1.26.3 \
  --build-arg NGINX_INGRESS_IMAGE=nginx:1.26.3@sha256:41b194461e4bae16f9b25d68b0976ed4735b89ca625c89aad88e1c1c3b7e8860 \
  --build-arg MODULE_SHA="$(git rev-parse HEAD)" \
  -t my-nginx-markdown:latest .
```

#### Custom Module Source

Use a branch or tag only as a reachability hint and separately provide the
full reviewed commit identity. The build fails closed if the fetched object
does not resolve to that exact commit:

```bash
docker build -f examples/kubernetes/Dockerfile.ingress \
  --build-arg MODULE_REPO=https://github.com/your-org/nginx-markdown-for-agents.git \
  --build-arg MODULE_REF=main \
  --build-arg MODULE_SHA="FULL_40_HEX_COMMIT_SHA" \
  -t my-ingress:custom .
```

#### Multi-platform Build

Build for both amd64 and arm64 architectures:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -f examples/kubernetes/Dockerfile.ingress \
  --build-arg MODULE_SHA="$(git rev-parse HEAD)" \
  -t my-ingress:multi .
```

Multi-platform builds require Docker Buildx with a builder that supports
the target platforms (for example `docker buildx create --use`).

### Customization Points

The following build arguments control the image build:

| Build Arg | Default | Description |
|-----------|---------|-------------|
| `NGINX_VERSION` | `1.27.2` | NGINX version for module compilation. Must match the NGINX binary in the base image exactly. Minimum supported: 1.24.0. |
| `NGINX_INGRESS_IMAGE` | `nginx/nginx-ingress@sha256:60f690...` | Immutable base Ingress Controller image. Custom values must include a full digest and contain an NGINX binary. |
| `MODULE_REPO` | `https://github.com/cnkang/nginx-markdown-for-agents.git` | Git repository URL for the module source code. |
| `MODULE_REF` | `main` | Branch or tag used only as a reachability hint when direct object fetch is unavailable. |
| `MODULE_SHA` | required | Full 40-character reviewed commit identity. The build verifies exact equality before running repository code. |

### Verification

After building the image, verify the module is correctly compiled and loaded:

#### Check NGINX Version and Module

```bash
docker run --rm my-ingress:latest nginx -T 2>&1 | grep -F 'load_module'
docker run --rm my-ingress:latest nginx -t
```

The first command must show the active module load, and the second must
successfully parse the active configuration. `nginx -V` only describes how
the binary was built. It does not prove that NGINX loaded a dynamic module.

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
kubectl exec -n ingress-nginx <pod-name> -- nginx -T 2>&1 \
  | grep -F 'load_module'
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
   docker run --rm nginx/nginx-ingress@sha256:60f690573c0599aadd45468899f9baaa97a4775e852c4402b0003a1e58a8dc17 nginx -v
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

The chart does NOT default the runtime image: Helm refuses to render the
Deployment until both `image.repository` and `image.tag` are set explicitly.
The chart therefore cannot install with zero overrides. The local stock-nginx
smoke test supplies a stock `nginx` image when `markdown.enabled=false`:

```bash
helm install nginx-markdown charts/nginx-markdown \
  --set image.repository=nginx --set image.tag=1.26.3
```

To enable the markdown module, use an image that already contains
`ngx_http_markdown_filter_module.so`, then set both values:

```bash
helm install nginx-markdown charts/nginx-markdown \
  --set image.repository=<your-nginx-markdown-image> \
  --set image.tag=<tag> \
  --set markdown.enabled=true \
  --set-string markdown.loadModule=/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so
```

When `markdown.enabled=true`, the chart requires `markdown.loadModule`. The chart does
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
4. **Metrics endpoint** — Verifies the module metrics path (default
   `/_markdown_metrics`, matching `values.metrics.uri`).  Override with
   `-m/--metrics`.  The endpoint must return HTTP 200 with Prometheus-format
   data.

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
| `-n, --namespace NS` | `default` (`$K8S_NAMESPACE`) | Kubernetes namespace |
| `-l, --label LABEL` | `app.kubernetes.io/name=nginx-markdown` | Pod label selector (Helm selectorLabels) |
| `-p, --port PORT` | `8080` | Local port for port-forward |
| `-m, --metrics PATH` | `/_markdown_metrics` | Module metrics path (`values.metrics.uri`) |
| `-t, --timeout SECS` | `10` | Curl timeout in seconds |

The port-forward targets the Pod's container port `8080` (the chart's
NGINX `listen`).  The Service maps `80 → 8080`, so direct Pod forwarding
uses `8080`, not `80`.

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

Exit code `0` means all checks passed. `1` means one or more failed.

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

#### Covered Scenarios

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
./e2e-scenarios.sh --namespace my-ns --image myrepo/nginx-markdown:v0.9.2
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

Exit code `0` means all scenarios passed. `1` means one or more failed.
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

This test needs no Kubernetes cluster — it operates entirely with
local Docker commands.

#### What It Verifies

1. **Docker build** — Image builds without errors
2. **Active module configuration** — `nginx -T` lists the active
   `load_module` line, and positive/negative `nginx -t` checks prove that
   the module-specific directive requires the module
3. **HTTP conversion** — `Accept: text/markdown` returns converted content
4. **Module file** — `.so` file exists at
   `/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so`
5. **load_module snippet** — Configuration snippet at
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

[INFO]  Test: active load_module, directive parsing, and negative control
[PASS]  active include and positive/negative module configuration checks passed
[INFO]  Test: HTTP Accept: text/markdown conversion
[PASS]  HTTP Accept: text/markdown returned converted content
[INFO]  Test: Module .so file exists at expected path
[PASS]  Module file exists: /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so
[INFO]  Test: load_module configuration snippet exists
[PASS]  load_module snippet correctly references markdown module

------------------------------------------------------------
[INFO]  Results: 5 passed, 0 failed
------------------------------------------------------------
RESULT: PASS (5/5 checks passed)
```

Exit code `0` means all checks passed. `1` means one or more failed.
`2` means a usage error or missing prerequisites.

---

### See Also

- [Package Distribution Guide](PACKAGE_DISTRIBUTION.md)
- [examples/kubernetes/manifest/](../../examples/kubernetes/manifest/) — K8s deployment manifests
- [charts/nginx-markdown/](../../charts/nginx-markdown/) — Helm chart
