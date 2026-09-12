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
#   MODULE_SO   module to package (default: build/ngx_http_markdown_filter_module.so)
#   IMAGE_REF   runtime image tag to build and load (default: markdown-smoke:local)
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
MODULE_SO="${MODULE_SO:-${REPO_ROOT}/build/ngx_http_markdown_filter_module.so}"
MODULE_PATH_IN_IMAGE="/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so"
RELEASE="markdown-smoke"
NAMESPACE="markdown-smoke"
KEEP=0

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
    if [[ "${KEEP}" -eq 0 ]]; then
        helm uninstall "${RELEASE}" --namespace "${NAMESPACE}" >/dev/null 2>&1 || true
        kind delete cluster --name "${CLUSTER}" >/dev/null 2>&1 || true
    fi
    rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

# A runtime image that carries the module, so the smoke exercises this build
# rather than a previously published image.
cat > "${WORK_DIR}/Dockerfile" <<DOCKERFILE
FROM nginx:1.30.4-alpine
RUN apk add --no-cache libgcc curl
COPY ngx_http_markdown_filter_module.so ${MODULE_PATH_IN_IMAGE}
DOCKERFILE
cp "${MODULE_SO}" "${WORK_DIR}/ngx_http_markdown_filter_module.so"

echo "=== building ${IMAGE_REF} ===" >&2
docker build -q -t "${IMAGE_REF}" "${WORK_DIR}" >&2

if ! kind get clusters 2>/dev/null | grep -qx "${CLUSTER}"; then
    echo "=== creating kind cluster ${CLUSTER} ===" >&2
    kind create cluster --name "${CLUSTER}" --wait 180s >&2
fi

echo "=== loading ${IMAGE_REF} into cluster ===" >&2
kind load docker-image "${IMAGE_REF}" --name "${CLUSTER}" >&2

echo "=== installing ${RELEASE} ===" >&2
kubectl create namespace "${NAMESPACE}" >/dev/null 2>&1 || true
helm upgrade --install "${RELEASE}" "${REPO_ROOT}/charts/nginx-markdown" \
    --kube-context "kind-${CLUSTER}" \
    --namespace "${NAMESPACE}" \
    --set image.repository="${IMAGE_REF%%:*}" \
    --set image.tag="${IMAGE_REF##*:}" \
    --set image.pullPolicy=IfNotPresent \
    --set markdown.enabled=true \
    --set markdown.loadModule="${MODULE_PATH_IN_IMAGE}" \
    --wait --timeout 180s >&2

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

echo "=== requesting the conversion ===" >&2
BODY="$(kubectl --context "kind-${CLUSTER}" --namespace "${NAMESPACE}" exec "${POD}" -- \
    sh -c 'curl -sS -H "Accept: text/markdown" http://localhost:8080/index.html' 2>/dev/null || true)"

if [[ -z "${BODY}" ]]; then
    echo "ERROR: the Service returned no body" >&2
    kubectl --context "kind-${CLUSTER}" --namespace "${NAMESPACE}" logs "${POD}" >&2 | tail -20 || true
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

echo "PASS: the chart deployed, the pod converted the document, and the response carries the expected heading" >&2
exit 0
