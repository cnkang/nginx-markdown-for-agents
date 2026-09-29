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
        printf '%b' "$MISSING_TOOLS" >&2
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
CREATED_CLUSTER=0
CREATED_NAMESPACE=0
CREATED_RELEASE=0

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
                # The sibling helm smoke enforces kind's grammar on this
                # value; the same bound here turns an invalid name into one
                # clear error instead of a later kind/kubectl refusal.
                if [[ ! "${CLUSTER_NAME}" =~ ^[a-z][a-z0-9-]{0,62}$ ]]; then
                    die "invalid cluster name: ${CLUSTER_NAME}"
                fi
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

    CREATED_CLUSTER=1
    info "Cluster created successfully"
    return 0
}

delete_cluster() {
    if [[ "$KEEP_CLUSTER" -eq 1 ]]; then
        info "Keeping cluster ${CLUSTER_NAME} (--keep-cluster)"
        info "Delete manually: kind delete cluster --name ${CLUSTER_NAME}"
        return 0
    fi

    if [[ "$CREATED_CLUSTER" -ne 1 ]]; then
        info "Not deleting pre-existing cluster: ${CLUSTER_NAME}"
        return 0
    fi

    info "Deleting kind cluster: ${CLUSTER_NAME}"
    kind delete cluster --name "${CLUSTER_NAME}" >/dev/null 2>&1 || true
    # Clear ownership after the delete so the EXIT trap's second call is a
    # no-op instead of a second delete of an already-gone cluster.
    CREATED_CLUSTER=0
    return 0
}

cleanup_owned_helm_resources() {
    if [[ "$KEEP_CLUSTER" -eq 1 ]]; then
        info "Keeping Helm release and namespace for inspection"
        return 0
    fi

    if [[ "$CREATED_RELEASE" -eq 1 ]]; then
        helm uninstall "${HELM_RELEASE_NAME}" --kube-context "kind-${CLUSTER_NAME}" \
            --namespace "${HELM_NAMESPACE}" >/dev/null 2>&1 || true
        CREATED_RELEASE=0
    fi
    if [[ "$CREATED_NAMESPACE" -eq 1 ]]; then
        # Bounded wait: a namespace this run created is ours to remove, and
        # returning while it is still terminating would let a subsequent
        # run reuse a name that is about to disappear (or collide with the
        # finalizer).  The wait stays best-effort with a bound so cleanup
        # can never hang a stuck cluster.
        kubectl --context "kind-${CLUSTER_NAME}" delete namespace "${HELM_NAMESPACE}" \
            --wait=true --timeout=120s >/dev/null 2>&1 || true
        CREATED_NAMESPACE=0
    fi
    return 0
}

# After a failed install, settle what this run still owns.  The rollback
# flag removes a failed release during the install; when the rollback
# itself fails, the release survives in a failed or pending state and
# cleanup must uninstall it before deleting the namespace.  The pre-install
# ownership check proved the name free, so a surviving release was created
# during this run's install window.
#
# $1 - the captured install output, used to recognize a name collision
settle_failed_install_ownership() {
    local install_error="${1:-}"
    # The captured install error classifies first: a name collision means a
    # concurrent creator holds the name, so both claims clear regardless of
    # what any state query can see (a transient query failure must not turn
    # a settled collision back into "this run's release").  Two error shapes
    # report it: the name check refuses with "cannot re-use a name that is
    # still in use" (Helm 3) or "cannot reuse ..." (Helm 4), and the
    # storage-layer create that follows its own availability check reports
    # "release: already exists" when both racers passed that check.
    if [[ "$install_error" == *"name that is still in use"* ]] \
        || [[ "$install_error" == *"release: already exists"* ]]; then
        # The other creator's release lives in this cluster: deleting the
        # cluster would take it down with the release, so the cluster is
        # preserved along with the release and its namespace.
        info "Install failed; another creator holds the release name, cleanup preserves it"
        CREATED_RELEASE=0
        CREATED_NAMESPACE=0
        CREATED_CLUSTER=0
        return 0
    fi
    # A failed install is followed by one of three states.  The pre-install
    # ownership check proved the name free, so a surviving release was
    # created during this run's install window: it is this run's attempt,
    # unless a concurrent creator now holds the name with a DEPLOYED
    # release, whose resources must be preserved.  A pending release cannot
    # be another creator's: this run held the name while its install ran,
    # so a concurrent creator would have been refused instead.
    local live
    if ! live="$(helm list --short \
        --filter "^${HELM_RELEASE_NAME}$" \
        --namespace "${HELM_NAMESPACE}" \
        --kube-context "kind-${CLUSTER_NAME}" \
        --deployed \
        2>/dev/null)"; then
        # The state cannot be checked: keep every claim so cleanup removes
        # only what this run may have created.
        info "Install failed; the release state could not be checked, cleanup keeps both claims"
        return 0
    fi
    if [[ -n "$live" ]]; then
        # A live release (another creator's) holds the name: preserve it
        # and the namespace content with it.
        info "Install failed; a live release holds the name, cleanup preserves it"
        CREATED_RELEASE=0
        CREATED_NAMESPACE=0
        return 0
    fi
    local surviving
    if ! surviving="$(helm list --short \
        --filter "^${HELM_RELEASE_NAME}$" \
        --namespace "${HELM_NAMESPACE}" \
        --kube-context "kind-${CLUSTER_NAME}" \
        --pending --failed --uninstalled --uninstalling --superseded \
        2>/dev/null)"; then
        info "Install failed; the release state could not be checked, cleanup keeps both claims"
        return 0
    fi
    if [[ -z "$surviving" ]]; then
        # The rollback removed the release: nothing of this run survives,
        # and the owned namespace is still deleted by cleanup.
        info "Install failed and rolled back; cleanup removes the owned namespace"
        CREATED_RELEASE=0
        return 0
    fi
    # The release survives in a non-deployed state (pending, failed, or
    # mid-teardown): cleanup must uninstall it before deleting the owned
    # namespace.
    info "Install failed; the release survives, cleanup removes it and the owned namespace"
    return 0
}

##############################################################################
# Termination handling
##############################################################################

# Without a trap, an abnormal termination (SIGINT/SIGTERM) skips main's
# cleanup path and leaks the Helm release, the namespace, and the kind
# cluster.  The handlers are idempotent — each clears its own ownership flag
# after acting and no-ops on a cleared flag — so a second run of the trap
# after main's normal cleanup is safe.  A concurrent run's resources are
# still preserved: the helpers only act on state this run recorded as its own.
#
# EXIT alone runs the cleanup for every exit path; INT and TERM additionally
# exit with the conventional signal status.  A returning INT/TERM handler
# would resume the script after the interrupt (bash completes the trap and
# continues the next command), letting a terminated validation reach its
# success path and report PASS.
# CLEANUP_DONE makes cleanup run at most once: main() performs its own
# ordered cleanup and the EXIT trap is a no-op afterwards, and a signal that
# arrives after cleanup cannot re-run the helpers.
CLEANUP_DONE=0

run_cleanup_once() {
    if [[ "${CLEANUP_DONE}" -eq 1 ]]; then
        return 0
    fi
    # Run the helpers BEFORE latching: if a signal re-enters the trap while
    # cleanup is in progress, the re-entry repeats the helpers (each is
    # idempotent and clears its own ownership flag) instead of returning
    # early and leaving the cluster behind.  The latch is set last.
    cleanup_owned_helm_resources
    delete_cluster
    CLEANUP_DONE=1
    return 0
}

cleanup_on_exit() {
    run_cleanup_once
    return 0
}

exit_on_signal() {
    local signal_status="$1"
    run_cleanup_once
    exit "$signal_status"
}

# The traps are installed by main() after argument parsing and prerequisite
# checks succeed, so a usage error or a missing tool exits without cleanup
# messages for a cluster this run never touched.

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

# validate_helm_template renders the Helm chart and verifies required image values, security settings, port configuration, and emptyDir volumes, returning success only when all checks pass.
validate_helm_template() {
    info "Running helm template (dry-run render)..."

    # Zero-override render MUST fail: the chart has no default runtime
    # image and Helm refuses to render the Deployment until both
    # image.repository and image.tag are set (Helm image contract).
    local zero_override_out
    if zero_override_out="$(helm template "${HELM_RELEASE_NAME}" "${CHART_DIR}" \
        --namespace "${HELM_NAMESPACE}" 2>&1)"; then
        fail "helm template with zero overrides unexpectedly succeeded (image must be required)"
        printf '%s\n' "$zero_override_out" >&2
        return 1
    fi
    # The failure must be the expected image-required validation error,
    # not an unrelated template defect.  Assert the rendered output
    # names at least one of the required image values before passing
    # (the first missing field is named; which one depends on template
    # evaluation order).
    if ! grep -qF "image.repository" <<< "$zero_override_out" \
        && ! grep -qF "image.tag" <<< "$zero_override_out"; then
        fail "helm template with zero overrides failed for an unexpected reason (neither image.repository nor image.tag mentioned)"
        printf '%s\n' "$zero_override_out" >&2
        return 1
    fi
    pass "helm template with zero overrides rejected (image.repository/image.tag required)"

    # Repository-only render must still fail and name the next required
    # value: the chart requires an explicit tag or digest, so a partial
    # image reference must not render.
    local repo_only_out
    if repo_only_out="$(helm template "${HELM_RELEASE_NAME}" "${CHART_DIR}" \
        --namespace "${HELM_NAMESPACE}" \
        --set image.repository=nginx 2>&1)"; then
        fail "helm template with only image.repository set unexpectedly succeeded (tag/digest must be required)"
        printf '%s\n' "$repo_only_out" >&2
        return 1
    fi
    if ! grep -qE 'image\.(tag|digest)' <<< "$repo_only_out"; then
        fail "repository-only render failed without naming image.tag/image.digest"
        printf '%s\n' "$repo_only_out" >&2
        return 1
    fi
    pass "repository-only render rejected (image.tag/image.digest required)"

    # Tag-only render must still fail and name the missing repository
    # reference: image.tag alone cannot satisfy the image contract.
    local tag_only_out
    if tag_only_out="$(helm template "${HELM_RELEASE_NAME}" "${CHART_DIR}" \
        --namespace "${HELM_NAMESPACE}" \
        --set image.tag=latest 2>&1)"; then
        fail "helm template with only image.tag set unexpectedly succeeded (repository must be required)"
        printf '%s\n' "$tag_only_out" >&2
        return 1
    fi
    if ! grep -qF "image.repository" <<< "$tag_only_out"; then
        fail "tag-only render failed without naming image.repository"
        printf '%s\n' "$tag_only_out" >&2
        return 1
    fi
    pass "tag-only render rejected (image.repository required)"

    # Render with an explicit stock-nginx image (the supported
    # markdown.enabled=false path) and validate the rendered output.
    local rendered
    if ! rendered="$(helm template "${HELM_RELEASE_NAME}" "${CHART_DIR}" \
        --namespace "${HELM_NAMESPACE}" \
        --set image.repository=nginx \
        --set image.tag=1.26.3 \
        --set markdown.enabled=false 2>&1)"; then
        fail "helm template with explicit image failed"
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

    if printf '%s' "$rendered" | grep -Eq "image:[[:space:]]*(\"nginx:1\\.26\\.3\"|'nginx:1\\.26\\.3'|nginx:1\\.26\\.3)[[:space:]]*$"; then
        checks_passed=$((checks_passed + 1))
    else
        fail "Rendered template missing explicit image nginx:1.26.3"
        checks_failed=$((checks_failed + 1))
    fi

    if [[ "$checks_failed" -eq 0 ]]; then
        pass "helm template security context validated (${checks_passed} checks)"
        return 0
    fi

    return 1
}

helm_rollback_flag() {
    # Helm's rollback-on-failure flag was renamed in Helm 4: `--atomic`
    # (which implies --wait on v3) became `--rollback-on-failure`, with
    # `--atomic` kept only as a deprecated alias.  Select by major version so
    # each supported major runs with its own spelling.
    local version
    version="$(helm version --short 2>/dev/null || true)"
    case "${version}" in
        v4*|4.*)
            printf '%s' "--rollback-on-failure"
            ;;
        *)
            printf '%s' "--atomic"
            ;;
    esac
    return 0
}


deploy_and_verify() {
    info "Deploying Helm chart to kind cluster..."
    local kube_context="kind-${CLUSTER_NAME}"

    # Reuse a namespace without claiming ownership; cleanup must not delete
    # other workloads that already use it.  kubectl stderr is kept out of the
    # captured result so a warning cannot read as an existing namespace, and
    # the temporary file is cleaned on both paths.
    local existing_namespace
    local namespace_stderr_file
    namespace_stderr_file="$(mktemp "${TMPDIR:-/tmp}/gate4-ns-list.XXXXXX")"
    if ! existing_namespace="$(kubectl --context "$kube_context" get namespace \
        "${HELM_NAMESPACE}" --ignore-not-found -o name 2>"$namespace_stderr_file")"; then
        fail "Unable to determine ownership of namespace ${HELM_NAMESPACE}"
        cat "$namespace_stderr_file" >&2 || true
        rm -f -- "$namespace_stderr_file"
        return 1
    fi
    rm -f -- "$namespace_stderr_file"
    if [[ -z "$existing_namespace" ]]; then
        if ! kubectl --context "$kube_context" create namespace \
            "${HELM_NAMESPACE}" >/dev/null 2>&1; then
            fail "Unable to create namespace ${HELM_NAMESPACE}"
            return 1
        fi
        CREATED_NAMESPACE=1
    else
        info "Reusing pre-existing namespace ${HELM_NAMESPACE}"
    fi

    # Refuse to adopt a release from another run.  The explicit state set is
    # the version-portable spelling of "any release, in any state": `--all`
    # exists on Helm v3 but was removed in v4, these six state flags exist in
    # both.  Helm install below also closes the race between this query and
    # creation.
    local existing_release
    local release_stderr_file
    release_stderr_file="$(mktemp "${TMPDIR:-/tmp}/gate4-helm-list.XXXXXX")"
    if ! existing_release="$(helm list --short --filter "^${HELM_RELEASE_NAME}$" \
        --namespace "${HELM_NAMESPACE}" --kube-context "$kube_context" \
        --deployed --failed --pending --uninstalled --uninstalling --superseded \
        2>"$release_stderr_file")"; then
        fail "Unable to determine ownership of Helm release ${HELM_RELEASE_NAME}"
        cat "$release_stderr_file" >&2 || true
        rm -f -- "$release_stderr_file"
        # The ownership flag stays set: no install has been attempted yet, so
        # the namespace this run created holds nothing of ours to preserve,
        # and cleanup must not leave it behind on a reused cluster (the
        # sibling helm smoke cleans up the same way).
        return 1
    fi
    rm -f -- "$release_stderr_file"
    if [[ -n "$existing_release" ]]; then
        fail "Pre-existing Helm release ${HELM_RELEASE_NAME}; refusing to replace it"
        CREATED_NAMESPACE=0
        return 1
    fi

    # Validate the stock-nginx chart deployment path, security context,
    # writable runtime paths, and Helm installability. This smoke test does
    # not validate a module-enabled image, so markdown directives stay off.
    # Helm's rollback-on-failure flag: Helm 3 spells it --atomic (which also
    # implies --wait), Helm 4 offers --rollback-on-failure and keeps --atomic
    # only as a deprecated alias.  Select by major version so the smoke uses
    # each major's own spelling.
    local rollback_flag
    rollback_flag="$(helm_rollback_flag)"
    # Claim the release BEFORE the install: bash defers a TERM trap until the
    # foreground install returns, so a signal that lands after a successful
    # install would otherwise leave the release behind (the flag would still
    # be unset).  The failure branch settles the claim against the actual
    # release state instead of clearing it blindly.
    CREATED_RELEASE=1
    # The install output is captured so the failure branch can classify the
    # error (a name collision belongs to a concurrent creator); it is
    # reprinted on both paths so the diagnostics stay identical.
    local install_output
    if ! install_output="$(helm install "${HELM_RELEASE_NAME}" "${CHART_DIR}" \
        --kube-context "$kube_context" \
        --namespace "${HELM_NAMESPACE}" \
        --set image.repository=nginx \
        --set image.tag=1.26.3 \
        --set image.pullPolicy=IfNotPresent \
        --set markdown.enabled=false \
        --wait \
        --timeout "${POD_WAIT_TIMEOUT}" \
        "${rollback_flag}" \
        2>&1)"; then
        # Settle ownership FIRST: bash runs a deferred TERM trap between
        # foreground commands, so a signal landing on any later command
        # would clean up while a collision's ownership flags were still
        # set (and uninstall the other creator's release).  The settlement
        # classifies the captured error before any output.
        settle_failed_install_ownership "$install_output"
        printf '%s\n' "$install_output" >&2
        fail "helm install failed"
        info "Pod status:"
        kubectl --context "$kube_context" get pods -n "${HELM_NAMESPACE}" \
            >&2 || true
        info "Pod events:"
        kubectl --context "$kube_context" describe pods -n "${HELM_NAMESPACE}" \
            >&2 || true
        return 1
    fi
    printf '%s\n' "$install_output" >&2

    pass "Helm chart deployed successfully"

    # Verify pod is running
    local pod_name
    pod_name="$(kubectl --context "$kube_context" get pods -n "${HELM_NAMESPACE}" \
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
    read_only="$(kubectl --context "$kube_context" get pod "$pod_name" \
        -n "${HELM_NAMESPACE}" \
        -o jsonpath='{.spec.containers[0].securityContext.readOnlyRootFilesystem}' 2>/dev/null || true)"

    if [[ "$read_only" == "true" ]]; then
        pass "Pod has readOnlyRootFilesystem: true"
    else
        fail "Pod missing readOnlyRootFilesystem (got: ${read_only:-<empty>})"
        had_failure=1
    fi

    # Verify writable volumes are mounted
    local volume_count
    volume_count=0
    local volume_name
    local empty_dir
    for volume_name in nginx-cache nginx-run nginx-tmp; do
        empty_dir="$(kubectl --context "$kube_context" get pod "$pod_name" \
            -n "${HELM_NAMESPACE}" \
            -o "jsonpath={.spec.volumes[?(@.name=='${volume_name}')].emptyDir}" \
            2>/dev/null || true)"
        if [[ -n "$empty_dir" ]]; then
            volume_count=$((volume_count + 1))
        fi
    done

    if [[ "$volume_count" -ge 3 ]]; then
        pass "Pod has ${volume_count} emptyDir volumes for writable paths"
    else
        fail "Pod has only ${volume_count} emptyDir volumes (expected >= 3)"
        had_failure=1
    fi

    return "$had_failure"
}

##############################################################################
# Main
##############################################################################

install_termination_traps() {
    trap cleanup_on_exit EXIT
    trap 'exit_on_signal 130' INT
    trap 'exit_on_signal 143' TERM
    return 0
}

main() {
    parse_args "$@"
    check_prerequisites
    install_termination_traps

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
        kubectl --context "kind-${CLUSTER_NAME}" cluster-info >/dev/null 2>&1 \
            || die "Cannot connect to kind cluster"

        deploy_and_verify || had_failure=1
        cleanup_owned_helm_resources
    fi

    # Cleanup: run the helpers first, then mark the guard.  A signal that
    # arrives between the two calls makes the handler re-run them, which is
    # safe (each clears its own ownership flag); marking the guard first
    # would instead let a signal skip the remaining cleanup entirely and
    # leave the cluster behind.
    cleanup_owned_helm_resources
    delete_cluster
    CLEANUP_DONE=1

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
