#!/usr/bin/env bash
# verify_helm_cluster_smoke_e2e.sh — Deploy the chart to a real cluster and
# convert a document through it.
#
# Purpose:
#   The chart renders and lints in CI, but nothing installed it into a real
#   cluster and served a request through the module.  This check builds a
#   runtime image from the module in build/, loads it into a kind cluster,
#   installs charts/nginx-markdown with that image, waits for the rollout, and
#   requires a converted response through the Service.
#
# Usage:
#   verify_helm_cluster_smoke_e2e.sh [--cluster NAME] [--image REF] [--keep]
#
# Environment:
#   MODULE_SO       module to package (default: build/ngx_http_markdown_filter_module.so)
#   IMAGE_REF       runtime image tag to build and load (default: markdown-smoke:local)
#   NGINX_BASE_IMAGE NGINX base image pinned by digest
#
# Exit codes:
#   0  the release rolled out and the Service returned converted Markdown
#   1  any step failed, or the response was not a conversion
#   77  kind, helm, kubectl, or docker is unavailable
#
# This script is FAIL-CLOSED: every unexpected outcome is a failure.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

CLUSTER="${CLUSTER:-markdown-helm-smoke}"
IMAGE_REF="${IMAGE_REF:-markdown-smoke:local}"
NGINX_BASE_IMAGE="${NGINX_BASE_IMAGE:-nginx:1.30.4-alpine3.24@sha256:dc5069ad14f19660b141b21236140b91656bf89bbc3e2417c70ae650cd66104c}"
NGINX_BASE_DIGEST="${NGINX_BASE_IMAGE##*@}"
MODULE_SO="${MODULE_SO:-${REPO_ROOT}/build/ngx_http_markdown_filter_module.so}"
MODULE_PATH_IN_IMAGE="/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so"
RELEASE="markdown-smoke"
NAMESPACE="markdown-smoke"
KEEP=0
CREATED_CLUSTER=0
CREATED_NAMESPACE=0
CREATED_RELEASE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cluster) CLUSTER="$2"; shift 2 ;;
        --image) IMAGE_REF="$2"; shift 2 ;;
        --keep) KEEP=1; shift ;;
        -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

for tool in kind helm kubectl docker; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        echo "SKIP: ${tool} is unavailable; the cluster smoke did not run" >&2
        exit 77
    fi
done
if [[ ! -f "${MODULE_SO}" ]]; then
    echo "ERROR: module not found: ${MODULE_SO}" >&2
    exit 1
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/helm-smoke.XXXXXX")"
cleanup() {
    if [[ -n "${PF_PID:-}" ]]; then
        kill "${PF_PID}" >/dev/null 2>&1 || true
        wait "${PF_PID}" 2>/dev/null || true
    fi
    if [[ "${KEEP}" -eq 0 ]]; then
        # Pin the context on uninstall too: a reused cluster is supported, and
        # without --kube-context the release name could resolve against
        # whatever cluster the current context points at.
        if [[ "${CREATED_RELEASE}" -eq 1 ]]; then
            helm uninstall "${RELEASE}" --namespace "${NAMESPACE}" \
                --kube-context "kind-${CLUSTER}" >/dev/null 2>&1 || true
        fi
        # Delete only a cluster this run created.  Reusing an existing cluster
        # is supported, and removing the user's would be destructive.
        if [[ "${CREATED_CLUSTER}" -eq 1 ]]; then
            kind delete cluster --name "${CLUSTER}" >/dev/null 2>&1 || true
        elif [[ "${CREATED_NAMESPACE}" -eq 1 ]]; then
            # A reused cluster keeps its namespace unless this run created it:
            # leaving markdown-smoke behind would poison the next run.
            kubectl --context "kind-${CLUSTER}" \
                delete namespace "${NAMESPACE}" --wait=false >/dev/null 2>&1 || true
        fi
    fi
    rm -rf "${WORK_DIR}"
    return 0
}
trap cleanup EXIT

if ! [[ "${NGINX_BASE_IMAGE}" =~ ^nginx:1[.]30[.]4-alpine3[.]24@sha256:[0-9a-f]{64}$ ]]; then
    echo "ERROR: NGINX_BASE_IMAGE must be nginx:1.30.4-alpine3.24 pinned by sha256 digest" >&2
    exit 1
fi

# A runtime image that carries the module, so the smoke exercises this build
# rather than a previously published image.
cat > "${WORK_DIR}/Dockerfile" <<DOCKERFILE
FROM ${NGINX_BASE_IMAGE}
RUN apk add --no-cache libgcc curl
COPY ngx_http_markdown_filter_module.so ${MODULE_PATH_IN_IMAGE}
DOCKERFILE
cp "${MODULE_SO}" "${WORK_DIR}/ngx_http_markdown_filter_module.so"

echo "=== building ${IMAGE_REF} ===" >&2
docker build -q -t "${IMAGE_REF}" "${WORK_DIR}" >&2

if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER}"; then
    echo "=== creating kind cluster ${CLUSTER} ===" >&2
    kind create cluster --name "${CLUSTER}" --wait 180s >&2
    CREATED_CLUSTER=1
fi

# Ensure the namespace exists on the SAME cluster the release targets.  The
# ignore-not-found query distinguishes an absent namespace from permission or
# connection failures, which remain fatal.  Creating only when absent also
# leaves a reused cluster's existing namespace untouched.
existing_namespace=""
if ! existing_namespace="$(kubectl --context "kind-${CLUSTER}" \
    get namespace "${NAMESPACE}" --ignore-not-found -o name)"; then
    echo "ERROR: unable to determine whether namespace ${NAMESPACE} exists" >&2
    exit 1
fi
if [[ -z "${existing_namespace}" ]]; then
    if ! kubectl --context "kind-${CLUSTER}" create namespace \
        "${NAMESPACE}" >/dev/null; then
        echo "ERROR: unable to create namespace ${NAMESPACE}" >&2
        exit 1
    fi
    # Record ownership only once this run created it, so cleanup never
    # deletes a namespace that pre-existed on a reused cluster.
    CREATED_NAMESPACE=1
fi

# Refuse to adopt a release that belongs to the user.  This check runs before
# the install, so a name that is still free here is ours from this point on:
# ownership is marked before `helm install`, and a failed or timed-out install
# then still leaves cleanup able to uninstall what the attempt created.
# `helm install` also closes the race between this query and creation.
existing_release=""
release_stderr_file="${WORK_DIR}/helm-list.stderr"
# The explicit state set is the version-portable spelling of "any release, in
# any state": helm v3 defaults `list` to deployed+failed only, while helm v4
# REMOVED `--all` ("unknown flag"), so the explicit flags are the one form
# both majors accept.
if ! existing_release="$(helm list --short \
    --filter "^${RELEASE}$" --namespace "${NAMESPACE}" \
    --kube-context "kind-${CLUSTER}" \
    --deployed --failed --pending --uninstalled --uninstalling --superseded \
    2>"${release_stderr_file}")"; then
    echo "ERROR: unable to determine ownership of Helm release ${RELEASE}" >&2
    cat "${release_stderr_file}" >&2 || true
    exit 1
fi
if [[ -n "${existing_release}" ]]; then
    echo "ERROR: pre-existing Helm release ${RELEASE} found in namespace ${NAMESPACE}; refusing to modify or remove it" >&2
    exit 1
fi

echo "=== loading ${IMAGE_REF} into cluster ===" >&2
# Split the tag at the LAST colon so a registry port survives:
#   registry:5000/name:tag -> repository registry:5000/name, tag tag
# A reference with no tag, or one whose "tag" part still contains a slash, is
# rejected rather than silently mis-derived.
IMAGE_REPO="${IMAGE_REF%:*}"
IMAGE_TAG="${IMAGE_REF##*:}"
if [[ "${IMAGE_REPO}" == "${IMAGE_REF}" || "${IMAGE_TAG}" == */* || -z "${IMAGE_REPO}" || -z "${IMAGE_TAG}" || "${IMAGE_REF}" == *"@"* ]]; then
    echo "ERROR: unsupported image reference (expected [registry[:port]/]name[:tag]): ${IMAGE_REF}" >&2
    exit 1
fi

kind load docker-image "${IMAGE_REF}" --name "${CLUSTER}" >&2

echo "=== installing ${RELEASE} ===" >&2
# The name is ours from before the install: the ownership check above proved
# no other release holds it.  A failed or timed-out install exits non-zero
# (and under `set -e` jumps straight to the EXIT trap), so the flag has to be
# set first for cleanup() to remove whatever the attempt left behind.
CREATED_RELEASE=1
helm install "${RELEASE}" "${REPO_ROOT}/charts/nginx-markdown" \
    --kube-context "kind-${CLUSTER}" \
    --namespace "${NAMESPACE}" \
    --set image.repository="${IMAGE_REPO}" \
    --set image.tag="${IMAGE_TAG}" \
    --set image.pullPolicy=IfNotPresent \
    --set markdown.enabled=true \
    --set markdown.loadModule="${MODULE_PATH_IN_IMAGE}" \
    --set metrics.enabled=true \
    --set metrics.sidecar.enabled=true \
    --set metrics.expose=true \
    --set-string metrics.sidecar.image.repository=nginx \
    --set-string "metrics.sidecar.image.digest=${NGINX_BASE_DIGEST}" \
    --set-string metrics.sidecar.resources.requests.cpu=50m \
    --set-string metrics.sidecar.resources.requests.memory=64Mi \
    --set-string metrics.sidecar.resources.limits.cpu=250m \
    --set-string metrics.sidecar.resources.limits.memory=128Mi \
    --wait --timeout 180s \
    --atomic >&2

echo "=== rollout status ===" >&2
kubectl --context "kind-${CLUSTER}" --namespace "${NAMESPACE}" \
    rollout status deployment --timeout=120s >&2

# Serve a small document from the container's own docroot and convert it through
# the Service, so the request path is pod -> module -> client.
POD="$(kubectl --context "kind-${CLUSTER}" --namespace "${NAMESPACE}" \
    get pods -l app.kubernetes.io/instance="${RELEASE}" -o jsonpath='{.items[0].metadata.name}')"
if [[ -z "${POD}" ]]; then
    echo "ERROR: no pod found for release ${RELEASE}" >&2
    exit 1
fi

# The container's root filesystem is read-only, so the document is the one the
# image already serves rather than a file this check writes.

echo "=== resolving the Service ===" >&2
SVC="$(kubectl --context "kind-${CLUSTER}" --namespace "${NAMESPACE}" \
    get service -l app.kubernetes.io/instance="${RELEASE}" \
    -o jsonpath='{.items[0].metadata.name}')"
if [[ -z "${SVC}" ]]; then
    echo "ERROR: no Service found for release ${RELEASE}" >&2
    exit 1
fi

# Request the Service, not the pod's loopback interface: a forwarding port on
# the host keeps the Service selector, port, and endpoints in the request path,
# so a miswired Service fails this smoke instead of silently passing.  The
# forwarding target is the Service's own http port, resolved from its spec, so
# the check follows the chart's configured port instead of assuming one.
SVC_PORT="$(kubectl --context "kind-${CLUSTER}" --namespace "${NAMESPACE}" \
    get service "${SVC}" -o jsonpath='{.spec.ports[?(@.name=="http")].port}')"
if [[ -z "${SVC_PORT}" ]]; then
    echo "ERROR: Service ${SVC} does not expose the named http port" >&2
    exit 1
fi
PF_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
METRICS_SVC_PORT="$(kubectl --context "kind-${CLUSTER}" --namespace "${NAMESPACE}" \
    get service "${SVC}" -o jsonpath='{.spec.ports[?(@.name=="metrics")].port}')"
if [[ -z "${METRICS_SVC_PORT}" ]]; then
    echo "ERROR: Service ${SVC} does not expose the metrics sidecar port" >&2
    exit 1
fi
METRICS_PF_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
kubectl --context "kind-${CLUSTER}" --namespace "${NAMESPACE}" \
    port-forward "service/${SVC}" "${PF_PORT}:${SVC_PORT}" \
    "${METRICS_PF_PORT}:${METRICS_SVC_PORT}" >"${WORK_DIR}/port-forward.log" 2>&1 &
PF_PID=$!

forward_ready=0
for _ in $(seq 1 60); do
    if curl -sS -o /dev/null "http://127.0.0.1:${PF_PORT}/" 2>/dev/null; then
        forward_ready=1
        break
    fi
    sleep 0.5
done
if [[ "${forward_ready}" -ne 1 ]]; then
    echo "ERROR: the Service port-forward never became ready" >&2
    cat "${WORK_DIR}/port-forward.log" >&2 || true
    exit 1
fi

echo "=== requesting the conversion ===" >&2
BODY="$(curl -sS -H 'Accept: text/markdown' "http://127.0.0.1:${PF_PORT}/index.html")"

if [[ -z "${BODY}" ]]; then
    echo "ERROR: the Service returned no body" >&2
    kubectl --context "kind-${CLUSTER}" --namespace "${NAMESPACE}" logs "${POD}" 2>&1 | tail -20 >&2 || true
    exit 1
fi
# The stock page is stable across NGINX images, so its title in the converted
# output proves the body was converted rather than passed through.
if ! printf '%s' "${BODY}" | grep -qi 'welcome to nginx'; then
    echo "ERROR: the converted response lost the document title: ${BODY}" >&2
    exit 1
fi
if printf '%s' "${BODY}" | grep -q '<h1>'; then
    echo "ERROR: the response is still HTML, so the module did not convert it" >&2
    exit 1
fi
if ! printf '%s' "${BODY}" | grep -q '^# '; then
    echo "ERROR: the converted response is missing the Markdown heading: ${BODY}" >&2
    exit 1
fi

echo "=== scraping the metrics sidecar ===" >&2
METRICS="$(curl -fsS "http://127.0.0.1:${METRICS_PF_PORT}/metrics")"
if ! printf '%s\n' "${METRICS}" | grep -Eq '^nginx_markdown_requests_total\{[^}]*outcome="converted"[^}]*\} [1-9]'; then
    echo "ERROR: /metrics did not expose a converted request sample from the module" >&2
    exit 1
fi

echo "PASS: the chart deployed, converted the document, and exposed the module metrics family through the sidecar" >&2
exit 0
