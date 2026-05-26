#!/bin/bash
# ---------------------------------------------------------------------------
# gate4_local_k8s_smoke.sh — Local K8s validation via kind
#
# PURPOSE:
#   Validates Gate 4 (K8s/Ingress) locally by creating a temporary kind
#   cluster, deploying the Helm chart, and verifying the pod starts with
#   the expected security context and configuration.
#
# PREREQUISITES:
#   - Docker (docker or podman)
#   - kind (Kubernetes IN Docker)
#   - kubectl
#   - helm
#
# USAGE:
#   gate4_local_k8s_smoke.sh [--keep-cluster] [--cluster-name NAME]
#
# OPTIONS:
#   --keep-cluster      Do not delete the kind cluster after validation
#   --cluster-name NAME Override cluster name (default: gate4-smoke)
#   -h, --help          Show this help message
#
# EXIT CODES:
#   0   All checks passed
#   1   One or more checks failed
#   2   Usage error
#   3   Prerequisites missing (prints install instructions)
#
# ENVIRONMENT:
#   DOCKER              Override docker binary (default: auto-detect)
#   KUBECONFIG          Override kubeconfig path (set automatically by kind)
#
# NOTES:
#   - macOS bash 3.2 compatible
#   - Creates and destroys a temporary kind cluster (~60s overhead)
#   - Uses --keep-cluster to inspect failures interactively
#   - Does NOT require a pre-built module image; validates chart structure,
#     security context, and config rendering only
# ---------------------------------------------------------------------------

set -euo pipefail

##############################################################################
# Constants
##############################################################################

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly SCRIPT_DIR PROJECT_ROOT

readonly DEFAULT_CLUSTER_NAME="gate4-smoke"
readonly CHART_DIR="${PROJECT_ROOT}/charts/nginx-markdown"
readonly HELM_RELEASE_NAME="gate4-test"
readonly HELM_NAMESPACE="gate4-smoke"
readonly POD_WAIT_TIMEOUT="120s"

##############################################################################
# Helpers
##############################################################################

info() {
    local msg="$1"
    printf '[gate4] %s\n' "$msg" >&2
    return 0
}

pass() {
    local msg="$1"
    printf '[PASS]  %s\n' "$msg" >&2
    return 0
}

fail() {
    local msg="$1"
    printf '[FAIL]  %s\n' "$msg" >&2
    return 0
}

die() {
    local msg="$1"
    printf '[FATAL] %s\n' "$msg" >&2
    exit 1
}

usage() {
    sed -n '3,30p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

##############################################################################
# Prerequisite checks
##############################################################################

MISSING_TOOLS=""

check_tool() {
    local tool_name="$1"
    local install_hint="$2"

    if ! command -v "$tool_name" >/dev/null 2>&1; then
        MISSING_TOOLS="${MISSING_TOOLS}  - ${tool_name}: ${install_hint}\n"
    fi
    return 0
}

check_prerequisites() {
    local docker_bin="${DOCKER:-}"

    if [[ -z "$docker_bin" ]]; then
        if command -v docker >/dev/null 2>&1; then
            docker_bin="docker"
        elif command -v podman >/dev/null 2>&1; then
            docker_bin="podman"
        fi
    fi

    if [[ -z "$docker_bin" ]]; then
        MISSING_TOOLS="${MISSING_TOOLS}  - docker: brew install --cask docker (macOS) or https://docs.docker.com/engine/install/\n"
    elif ! "$docker_bin" info >/dev/null 2>&1; then
        printf '[gate4] Docker found (%s) but daemon is not running.\n' "$docker_bin" >&2
        printf '[gate4] Start Docker Desktop or run: sudo systemctl start docker\n' >&2
        exit 3
    else
        DOCKER="$docker_bin"
    fi

    check_tool "kind" "brew install kind (macOS) or go install sigs.k8s.io/kind@latest"
    check_tool "kubectl" "brew install kubectl (macOS) or https://kubernetes.io/docs/tasks/tools/"
    check_tool "helm" "brew install helm (macOS) or https://helm.sh/docs/intro/install/"

    if [[ -n "$MISSING_TOOLS" ]]; then
        printf '\n' >&2
        printf '╔══════════════════════════════════════════════════════════════╗\n' >&2
        printf '║  Gate 4 requires the following tools to run locally:        ║\n' >&2
        printf '╚══════════════════════════════════════════════════════════════╝\n' >&2
        printf '\n' >&2
        printf 'Missing tools:\n' >&2
        printf "$MISSING_TOOLS" >&2
        printf '\n' >&2
        printf 'Quick install (macOS with Homebrew):\n' >&2
        printf '  brew install --cask docker && brew install kind kubectl helm\n' >&2
        printf '\n' >&2
        exit 3
    fi

    info "All prerequisites found"
    return 0
}

##############################################################################
# Argument parsing
##############################################################################

KEEP_CLUSTER=0
CLUSTER_NAME="${DEFAULT_CLUSTER_NAME}"

parse_args() {
    while [[ $# -gt 0 ]]; do
        local arg="$1"
        case "$arg" in
            --keep-cluster)
                KEEP_CLUSTER=1
                shift
                ;;
            --cluster-name)
                [[ $# -ge 2 ]] || die "--cluster-name requires a value"
                CLUSTER_NAME="$2"
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "Unknown option: $arg"
                ;;
        esac
    done
    return 0
}

##############################################################################
# Cluster lifecycle
##############################################################################

create_cluster() {
    info "Creating kind cluster: ${CLUSTER_NAME}"

    if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
        info "Cluster ${CLUSTER_NAME} already exists, reusing"
        return 0
    fi

    kind create cluster --name "${CLUSTER_NAME}" --wait 60s >&2 \
        || die "Failed to create kind cluster"

    info "Cluster created successfully"
    return 0
}

delete_cluster() {
    if [[ "$KEEP_CLUSTER" -eq 1 ]]; then
        info "Keeping cluster ${CLUSTER_NAME} (--keep-cluster)"
        info "Delete manually: kind delete cluster --name ${CLUSTER_NAME}"
        return 0
    fi

    info "Deleting kind cluster: ${CLUSTER_NAME}"
    kind delete cluster --name "${CLUSTER_NAME}" >/dev/null 2>&1 || true
    return 0
}

##############################################################################
# Helm validation
##############################################################################

validate_helm_lint() {
    info "Running helm lint..."
    if helm lint "${CHART_DIR}" >&2; then
        pass "helm lint passed"
        return 0
    fi

    fail "helm lint failed"
    return 1
}

validate_helm_template() {
    info "Running helm template (dry-run render)..."
    local rendered
    if ! rendered="$(helm template "${HELM_RELEASE_NAME}" "${CHART_DIR}" \
        --namespace "${HELM_NAMESPACE}" 2>&1)"; then
        fail "helm template failed"
        printf '%s\n' "$rendered" >&2
        return 1
    fi

    # Verify security context in rendered output
    local checks_passed=0
    local checks_failed=0

    if printf '%s' "$rendered" | grep -q "readOnlyRootFilesystem: true"; then
        checks_passed=$((checks_passed + 1))
    else
        fail "Rendered template missing readOnlyRootFilesystem: true"
        checks_failed=$((checks_failed + 1))
    fi

    if printf '%s' "$rendered" | grep -q "runAsNonRoot: true"; then
        checks_passed=$((checks_passed + 1))
    else
        fail "Rendered template missing runAsNonRoot: true"
        checks_failed=$((checks_failed + 1))
    fi

    if printf '%s' "$rendered" | grep -q "containerPort: 8080"; then
        checks_passed=$((checks_passed + 1))
    else
        fail "Rendered template missing containerPort: 8080"
        checks_failed=$((checks_failed + 1))
    fi

    if printf '%s' "$rendered" | grep -q "emptyDir: {}"; then
        checks_passed=$((checks_passed + 1))
    else
        fail "Rendered template missing emptyDir volumes"
        checks_failed=$((checks_failed + 1))
    fi

    if [[ "$checks_failed" -eq 0 ]]; then
        pass "helm template security context validated (${checks_passed} checks)"
        return 0
    fi

    return 1
}

deploy_and_verify() {
    info "Deploying Helm chart to kind cluster..."

    # Create namespace
    kubectl create namespace "${HELM_NAMESPACE}" --dry-run=client -o yaml \
        | kubectl apply -f - >&2

    # Install chart with a known-good nginx image (no module, just config validation)
    if ! helm install "${HELM_RELEASE_NAME}" "${CHART_DIR}" \
        --namespace "${HELM_NAMESPACE}" \
        --set image.repository=nginx \
        --set image.tag=1.26.3 \
        --set image.pullPolicy=IfNotPresent \
        --wait \
        --timeout "${POD_WAIT_TIMEOUT}" \
        >&2 2>&1; then
        fail "helm install failed"
        info "Pod status:"
        kubectl get pods -n "${HELM_NAMESPACE}" >&2 || true
        info "Pod events:"
        kubectl describe pods -n "${HELM_NAMESPACE}" >&2 || true
        return 1
    fi

    pass "Helm chart deployed successfully"

    # Verify pod is running
    local pod_name
    pod_name="$(kubectl get pods -n "${HELM_NAMESPACE}" \
        -l "app.kubernetes.io/instance=${HELM_RELEASE_NAME}" \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"

    if [[ -z "$pod_name" ]]; then
        fail "No pod found for release ${HELM_RELEASE_NAME}"
        return 1
    fi

    info "Pod running: ${pod_name}"
    local had_failure=0

    # Verify security context is applied
    local read_only
    read_only="$(kubectl get pod "$pod_name" -n "${HELM_NAMESPACE}" \
        -o jsonpath='{.spec.containers[0].securityContext.readOnlyRootFilesystem}' 2>/dev/null || true)"

    if [[ "$read_only" == "true" ]]; then
        pass "Pod has readOnlyRootFilesystem: true"
    else
        fail "Pod missing readOnlyRootFilesystem (got: ${read_only:-<empty>})"
        had_failure=1
    fi

    # Verify writable volumes are mounted
    local volume_count
    volume_count="$(kubectl get pod "$pod_name" -n "${HELM_NAMESPACE}" \
        -o jsonpath='{.spec.volumes}' 2>/dev/null | grep -c "emptyDir" || true)"

    if [[ "$volume_count" -ge 3 ]]; then
        pass "Pod has ${volume_count} emptyDir volumes for writable paths"
    else
        fail "Pod has only ${volume_count} emptyDir volumes (expected >= 3)"
        had_failure=1
    fi

    # Cleanup helm release
    helm uninstall "${HELM_RELEASE_NAME}" --namespace "${HELM_NAMESPACE}" >/dev/null 2>&1 || true
    kubectl delete namespace "${HELM_NAMESPACE}" --wait=false >/dev/null 2>&1 || true

    return "$had_failure"
}

##############################################################################
# Main
##############################################################################

main() {
    parse_args "$@"
    check_prerequisites

    local had_failure=0

    # Stage 1: Helm lint + template validation (no cluster needed)
    validate_helm_lint || had_failure=1
    validate_helm_template || had_failure=1

    # Stage 2: Deploy to kind cluster
    if [[ "$had_failure" -eq 0 ]]; then
        create_cluster || had_failure=1
    fi

    if [[ "$had_failure" -eq 0 ]]; then
        # Use kind's kubeconfig context
        kubectl cluster-info --context "kind-${CLUSTER_NAME}" >/dev/null 2>&1 \
            || die "Cannot connect to kind cluster"

        deploy_and_verify || had_failure=1
    fi

    # Cleanup
    delete_cluster

    # Summary
    printf '\n' >&2
    if [[ "$had_failure" -eq 0 ]]; then
        pass "Gate 4 local validation PASSED"
        return 0
    fi

    fail "Gate 4 local validation FAILED"
    return 1
}

main "$@"
