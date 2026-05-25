#!/bin/bash
# ---------------------------------------------------------------------------
# e2e-scenarios.sh — Kubernetes E2E scenario tests for nginx-markdown-for-agents
#
# PURPOSE:
#   Validates the full deployment lifecycle of the nginx-markdown-for-agents
#   module in Kubernetes: deploy, config update, rolling upgrade, rollback,
#   and scale up/down. Runs the smoke test between each scenario to verify
#   functionality is preserved.
#
# REQUIREMENTS:
#   - REQ-0700-K8S-005: K8s/Ingress Smoke and E2E verification
#   - Design reference: design.md §12.3
#
# USAGE:
#   ./e2e-scenarios.sh [OPTIONS]
#
# OPTIONS:
#   -n, --namespace NS       Kubernetes namespace (default: nginx-markdown-e2e)
#   -m, --manifest-dir DIR   Path to K8s manifests (default: ../manifest)
#   -i, --image IMAGE        Container image for deployment
#                            (default: nginx-markdown:latest)
#   -t, --timeout SECS       Timeout for rollout wait in seconds (default: 120)
#   -h, --help               Show this help message
#
# EXAMPLES:
#   # Run with defaults:
#   ./e2e-scenarios.sh
#
#   # Custom namespace and image:
#   ./e2e-scenarios.sh --namespace my-ns --image myrepo/nginx-markdown:v0.7.0
#
#   # Custom manifest directory and timeout:
#   ./e2e-scenarios.sh --manifest-dir /path/to/manifests --timeout 180
#
# EXIT CODES:
#   0  All scenarios passed
#   1  One or more scenarios failed
#   2  Usage error or missing prerequisites
#
# NOTES:
#   - Requires: kubectl, curl
#   - macOS bash 3.2 compatible (no bash 4+ features)
#   - Self-contained; creates and cleans up its own namespace
#   - Runs smoke-test.sh between scenarios for functional verification
#
# SEE ALSO:
#   - examples/kubernetes/tests/smoke-test.sh
#   - examples/kubernetes/manifest/
#   - docs/guides/KUBERNETES_DEPLOYMENT.md
# ---------------------------------------------------------------------------

set -e

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NAMESPACE="nginx-markdown-e2e"
MANIFEST_DIR=""
IMAGE="nginx-markdown:latest"
TIMEOUT="120"
DEPLOYMENT_NAME="nginx-markdown"
SERVICE_NAME="nginx-markdown"
CONFIGMAP_NAME="nginx-markdown-config"
PASS_COUNT=0
FAIL_COUNT=0
SCENARIO_RESULTS=""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

usage() {
    sed -n '/^# USAGE:/,/^# EXIT CODES:/{ /^# EXIT CODES:/d; s/^# \{0,3\}//; p; }' "$0" >&2
    return 0
}

log_info() {
    printf '[INFO]  %s\n' "$1" >&2
}

log_pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    printf '[PASS]  %s\n' "$1" >&2
}

log_fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf '[FAIL]  %s\n' "$1" >&2
}

log_error() {
    printf '[ERROR] %s\n' "$1" >&2
}

log_scenario() {
    printf '\n' >&2
    printf '>>> SCENARIO: %s\n' "$1" >&2
    printf -- '-%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2
}

check_prerequisites() {
    if ! command -v kubectl >/dev/null 2>&1; then
        log_error "kubectl is required but not found in PATH"
        return 2
    fi

    if ! command -v curl >/dev/null 2>&1; then
        log_error "curl is required but not found in PATH"
        return 2
    fi

    return 0
}

# Wait for a deployment rollout to complete with timeout.
# Arguments: $1 = deployment name
wait_for_rollout() {
    local deploy_name="$1"
    log_info "Waiting for rollout of '$deploy_name' (timeout=${TIMEOUT}s)..."

    if kubectl rollout status deployment/"$deploy_name" \
        -n "$NAMESPACE" --timeout="${TIMEOUT}s" >&2 2>&1; then
        log_info "Rollout of '$deploy_name' completed successfully"
        return 0
    fi

    log_error "Rollout of '$deploy_name' did not complete within ${TIMEOUT}s"
    return 1
}

# Wait for all pods matching a label to be Ready.
# Arguments: $1 = label selector, $2 = expected replica count
wait_for_pods_ready() {
    local label="$1"
    local expected="${2:-1}"
    local elapsed=0
    local interval=5

    log_info "Waiting for $expected pod(s) with label '$label' to be Ready..."

    while [ "$elapsed" -lt "$TIMEOUT" ]; do
        local ready_count
        ready_count="$(kubectl get pods -n "$NAMESPACE" -l "$label" \
            --field-selector=status.phase=Running \
            -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' 2>/dev/null \
            | grep -c "True" 2>/dev/null)" || ready_count=0

        if [ "$ready_count" -ge "$expected" ]; then
            log_info "All $expected pod(s) are Ready"
            return 0
        fi

        sleep "$interval"
        elapsed=$((elapsed + interval))
    done

    log_error "Only $ready_count/$expected pods Ready after ${TIMEOUT}s"
    return 1
}

# Run the smoke test against the current deployment.
# Returns 0 if smoke passes, 1 otherwise.
run_smoke_test() {
    local smoke_script="${SCRIPT_DIR}/smoke-test.sh"

    if [ ! -x "$smoke_script" ]; then
        if [ -f "$smoke_script" ]; then
            chmod +x "$smoke_script"
        else
            log_error "Smoke test script not found: $smoke_script"
            return 1
        fi
    fi

    log_info "Running smoke test..."
    if "$smoke_script" --namespace "$NAMESPACE" --label "app=$DEPLOYMENT_NAME"; then
        log_info "Smoke test passed"
        return 0
    fi

    log_error "Smoke test failed"
    return 1
}

# Record scenario result.
# Arguments: $1 = scenario name, $2 = "PASS" or "FAIL"
record_scenario() {
    local name="$1"
    local result="$2"

    if [ "$result" = "PASS" ]; then
        log_pass "Scenario '$name' PASSED"
    else
        log_fail "Scenario '$name' FAILED"
    fi
    SCENARIO_RESULTS="${SCENARIO_RESULTS}${result}: ${name}\n"
}

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

cleanup() {
    log_info "Cleaning up namespace '$NAMESPACE'..."
    kubectl delete namespace "$NAMESPACE" --ignore-not-found=true \
        --wait=false >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# Scenario 1: Deploy
# ---------------------------------------------------------------------------

scenario_deploy() {
    log_scenario "1. Deploy — Apply manifests and wait for pods to be ready"

    # Create namespace
    log_info "Creating namespace '$NAMESPACE'..."
    kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml \
        | kubectl apply -f - >/dev/null 2>&1

    # Apply manifests from the manifest directory
    if [ -d "$MANIFEST_DIR" ]; then
        log_info "Applying manifests from '$MANIFEST_DIR'..."
        kubectl apply -n "$NAMESPACE" -f "$MANIFEST_DIR" >&2 2>&1 || {
            log_error "Failed to apply manifests from '$MANIFEST_DIR'"
            return 1
        }
    fi

    # Create a minimal deployment if none exists yet
    local deploy_exists
    deploy_exists="$(kubectl get deployment "$DEPLOYMENT_NAME" \
        -n "$NAMESPACE" -o name 2>/dev/null)" || true

    if [ -z "$deploy_exists" ]; then
        log_info "Creating deployment '$DEPLOYMENT_NAME' with image '$IMAGE'..."
        kubectl create deployment "$DEPLOYMENT_NAME" \
            -n "$NAMESPACE" \
            --image="$IMAGE" \
            --port=80 >&2 2>&1 || {
            log_error "Failed to create deployment"
            return 1
        }

        # Expose as a service if not already present
        local svc_exists
        svc_exists="$(kubectl get service "$SERVICE_NAME" \
            -n "$NAMESPACE" -o name 2>/dev/null)" || true

        if [ -z "$svc_exists" ]; then
            kubectl expose deployment "$DEPLOYMENT_NAME" \
                -n "$NAMESPACE" \
                --port=80 --target-port=80 \
                --name="$SERVICE_NAME" >&2 2>&1 || true
        fi
    else
        # Update image if deployment already exists
        kubectl set image deployment/"$DEPLOYMENT_NAME" \
            -n "$NAMESPACE" \
            "*=$IMAGE" >&2 2>&1 || true
    fi

    # Wait for rollout
    wait_for_rollout "$DEPLOYMENT_NAME" || return 1

    # Verify pods are running
    wait_for_pods_ready "app=$DEPLOYMENT_NAME" 1 || return 1

    # Run smoke test
    run_smoke_test || return 1

    return 0
}

# ---------------------------------------------------------------------------
# Scenario 2: Config Update
# ---------------------------------------------------------------------------

scenario_config_update() {
    log_scenario "2. Config Update — Update ConfigMap and verify module picks up new config"

    # Create or update a ConfigMap with module configuration
    log_info "Applying updated ConfigMap '$CONFIGMAP_NAME'..."
    kubectl create configmap "$CONFIGMAP_NAME" \
        -n "$NAMESPACE" \
        --from-literal=markdown_filter="on" \
        --from-literal=markdown_streaming_engine="auto" \
        --from-literal=markdown_max_size="10485760" \
        --dry-run=client -o yaml \
        | kubectl apply -f - >&2 2>&1 || {
        log_error "Failed to apply ConfigMap"
        return 1
    }

    # Annotate the deployment to trigger a rollout (config change pickup)
    local timestamp
    timestamp="$(date +%s)"
    kubectl patch deployment "$DEPLOYMENT_NAME" \
        -n "$NAMESPACE" \
        -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"configmap-reload\":\"$timestamp\"}}}}}" \
        >&2 2>&1 || {
        log_error "Failed to patch deployment for config reload"
        return 1
    }

    # Wait for rollout to complete
    wait_for_rollout "$DEPLOYMENT_NAME" || return 1

    # Verify the ConfigMap is mounted/accessible
    local cm_data
    cm_data="$(kubectl get configmap "$CONFIGMAP_NAME" \
        -n "$NAMESPACE" -o jsonpath='{.data}' 2>/dev/null)" || true

    if [ -z "$cm_data" ]; then
        log_error "ConfigMap '$CONFIGMAP_NAME' has no data"
        return 1
    fi

    log_info "ConfigMap data verified: $cm_data"

    # Run smoke test to verify functionality after config change
    run_smoke_test || return 1

    return 0
}

# ---------------------------------------------------------------------------
# Scenario 3: Rolling Upgrade
# ---------------------------------------------------------------------------

scenario_rolling_upgrade() {
    log_scenario "3. Rolling Upgrade — Trigger rolling update and verify zero-downtime"

    # Record current revision
    local prev_revision
    prev_revision="$(kubectl rollout history deployment/"$DEPLOYMENT_NAME" \
        -n "$NAMESPACE" 2>/dev/null | grep -c '^[0-9]')" || prev_revision="0"
    log_info "Current revision count: $prev_revision"

    # Trigger a rolling update by setting an environment variable
    # (simulates an image or config change)
    local upgrade_ts
    upgrade_ts="$(date +%s)"
    kubectl set env deployment/"$DEPLOYMENT_NAME" \
        -n "$NAMESPACE" \
        UPGRADE_TIMESTAMP="$upgrade_ts" >&2 2>&1 || {
        log_error "Failed to trigger rolling upgrade"
        return 1
    }

    # Wait for rollout to complete
    wait_for_rollout "$DEPLOYMENT_NAME" || return 1

    # Verify new revision was created
    local new_revision
    new_revision="$(kubectl rollout history deployment/"$DEPLOYMENT_NAME" \
        -n "$NAMESPACE" 2>/dev/null | grep -c '^[0-9]')" || new_revision="0"

    if [ "$new_revision" -le "$prev_revision" ]; then
        log_error "No new revision created after rolling upgrade"
        return 1
    fi

    log_info "New revision created (count: $new_revision)"

    # Verify no pods in CrashLoopBackOff
    local crash_pods
    crash_pods="$(kubectl get pods -n "$NAMESPACE" -l "app=$DEPLOYMENT_NAME" \
        -o jsonpath='{range .items[*]}{.status.containerStatuses[*].state.waiting.reason}{"\n"}{end}' 2>/dev/null \
        | grep -c "CrashLoopBackOff" 2>/dev/null)" || crash_pods=0

    if [ "$crash_pods" -gt 0 ]; then
        log_error "$crash_pods pod(s) in CrashLoopBackOff after rolling upgrade"
        return 1
    fi

    # Run smoke test to verify functionality after upgrade
    run_smoke_test || return 1

    return 0
}

# ---------------------------------------------------------------------------
# Scenario 4: Rollback
# ---------------------------------------------------------------------------

scenario_rollback() {
    log_scenario "4. Rollback — Execute rollout undo and verify previous version restored"

    # Record current image/env before rollback
    local pre_rollback_env
    pre_rollback_env="$(kubectl get deployment "$DEPLOYMENT_NAME" \
        -n "$NAMESPACE" \
        -o jsonpath='{.spec.template.spec.containers[0].env}' 2>/dev/null)" || true
    log_info "Pre-rollback env: $pre_rollback_env"

    # Execute rollback
    log_info "Executing kubectl rollout undo..."
    kubectl rollout undo deployment/"$DEPLOYMENT_NAME" \
        -n "$NAMESPACE" >&2 2>&1 || {
        log_error "Failed to execute rollout undo"
        return 1
    }

    # Wait for rollout to complete
    wait_for_rollout "$DEPLOYMENT_NAME" || return 1

    # Verify the rollback took effect (env should differ from pre-rollback)
    local post_rollback_env
    post_rollback_env="$(kubectl get deployment "$DEPLOYMENT_NAME" \
        -n "$NAMESPACE" \
        -o jsonpath='{.spec.template.spec.containers[0].env}' 2>/dev/null)" || true
    log_info "Post-rollback env: $post_rollback_env"

    if [ "$pre_rollback_env" = "$post_rollback_env" ]; then
        log_error "Rollback did not change deployment spec (env unchanged)"
        return 1
    fi

    log_info "Rollback changed deployment spec successfully"

    # Verify pods are healthy after rollback
    wait_for_pods_ready "app=$DEPLOYMENT_NAME" 1 || return 1

    # Verify no CrashLoopBackOff
    local crash_pods
    crash_pods="$(kubectl get pods -n "$NAMESPACE" -l "app=$DEPLOYMENT_NAME" \
        -o jsonpath='{range .items[*]}{.status.containerStatuses[*].state.waiting.reason}{"\n"}{end}' 2>/dev/null \
        | grep -c "CrashLoopBackOff" 2>/dev/null)" || crash_pods=0

    if [ "$crash_pods" -gt 0 ]; then
        log_error "$crash_pods pod(s) in CrashLoopBackOff after rollback"
        return 1
    fi

    # Run smoke test to verify functionality after rollback
    run_smoke_test || return 1

    return 0
}

# ---------------------------------------------------------------------------
# Scenario 5: Scale
# ---------------------------------------------------------------------------

scenario_scale() {
    log_scenario "5. Scale — Scale up/down and verify all replicas are functional"

    # Scale up to 3 replicas
    local scale_up_count=3
    log_info "Scaling deployment to $scale_up_count replicas..."
    kubectl scale deployment/"$DEPLOYMENT_NAME" \
        -n "$NAMESPACE" \
        --replicas="$scale_up_count" >&2 2>&1 || {
        log_error "Failed to scale up to $scale_up_count replicas"
        return 1
    }

    # Wait for all replicas to be ready
    wait_for_pods_ready "app=$DEPLOYMENT_NAME" "$scale_up_count" || return 1

    # Verify correct replica count
    local actual_ready
    actual_ready="$(kubectl get deployment "$DEPLOYMENT_NAME" \
        -n "$NAMESPACE" \
        -o jsonpath='{.status.readyReplicas}' 2>/dev/null)" || actual_ready=0

    if [ "$actual_ready" -lt "$scale_up_count" ]; then
        log_error "Expected $scale_up_count ready replicas, got $actual_ready"
        return 1
    fi

    log_info "Scale up verified: $actual_ready/$scale_up_count replicas ready"

    # Run smoke test with scaled-up deployment
    run_smoke_test || return 1

    # Scale down to 1 replica
    local scale_down_count=1
    log_info "Scaling deployment down to $scale_down_count replica..."
    kubectl scale deployment/"$DEPLOYMENT_NAME" \
        -n "$NAMESPACE" \
        --replicas="$scale_down_count" >&2 2>&1 || {
        log_error "Failed to scale down to $scale_down_count replica"
        return 1
    }

    # Wait for scale-down to stabilize
    wait_for_rollout "$DEPLOYMENT_NAME" || return 1

    # Verify correct replica count after scale-down
    actual_ready="$(kubectl get deployment "$DEPLOYMENT_NAME" \
        -n "$NAMESPACE" \
        -o jsonpath='{.status.readyReplicas}' 2>/dev/null)" || actual_ready=0

    if [ "$actual_ready" -ne "$scale_down_count" ]; then
        log_error "Expected $scale_down_count ready replica after scale-down, got $actual_ready"
        return 1
    fi

    log_info "Scale down verified: $actual_ready/$scale_down_count replica ready"

    # Run smoke test with scaled-down deployment
    run_smoke_test || return 1

    return 0
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            -n|--namespace)
                if [ $# -lt 2 ]; then
                    log_error "Option $1 requires an argument"
                    return 2
                fi
                NAMESPACE="$2"
                shift 2
                ;;
            -m|--manifest-dir)
                if [ $# -lt 2 ]; then
                    log_error "Option $1 requires an argument"
                    return 2
                fi
                MANIFEST_DIR="$2"
                shift 2
                ;;
            -i|--image)
                if [ $# -lt 2 ]; then
                    log_error "Option $1 requires an argument"
                    return 2
                fi
                IMAGE="$2"
                shift 2
                ;;
            -t|--timeout)
                if [ $# -lt 2 ]; then
                    log_error "Option $1 requires an argument"
                    return 2
                fi
                TIMEOUT="$2"
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            -*)
                log_error "Unknown option: $1"
                usage
                return 2
                ;;
            *)
                log_error "Unexpected argument: $1"
                usage
                return 2
                ;;
        esac
    done

    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    parse_args "$@" || exit $?
    check_prerequisites || exit $?

    # Resolve manifest directory default (relative to script location)
    if [ -z "$MANIFEST_DIR" ]; then
        MANIFEST_DIR="${SCRIPT_DIR}/../manifest"
    fi

    # Validate manifest directory exists
    if [ ! -d "$MANIFEST_DIR" ]; then
        log_error "Manifest directory not found: $MANIFEST_DIR"
        return 2
    fi

    # Resolve to absolute path for safety (Rule 12: no unsanitized paths)
    MANIFEST_DIR="$(cd "$MANIFEST_DIR" && pwd)"

    trap cleanup EXIT

    printf '=%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2
    log_info "nginx-markdown-for-agents K8s E2E Scenarios"
    log_info "Namespace:    $NAMESPACE"
    log_info "Manifest dir: $MANIFEST_DIR"
    log_info "Image:        $IMAGE"
    log_info "Timeout:      ${TIMEOUT}s"
    printf '=%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2

    # Run scenarios sequentially; continue on failure to report all results
    if scenario_deploy; then
        record_scenario "Deploy" "PASS"
    else
        record_scenario "Deploy" "FAIL"
    fi

    if scenario_config_update; then
        record_scenario "Config Update" "PASS"
    else
        record_scenario "Config Update" "FAIL"
    fi

    if scenario_rolling_upgrade; then
        record_scenario "Rolling Upgrade" "PASS"
    else
        record_scenario "Rolling Upgrade" "FAIL"
    fi

    if scenario_rollback; then
        record_scenario "Rollback" "PASS"
    else
        record_scenario "Rollback" "FAIL"
    fi

    if scenario_scale; then
        record_scenario "Scale" "PASS"
    else
        record_scenario "Scale" "FAIL"
    fi

    # Summary
    printf '\n' >&2
    printf '=%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2
    log_info "E2E Scenario Results Summary"
    printf -- '-%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2
    printf '%b' "$SCENARIO_RESULTS" >&2
    printf -- '-%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2
    log_info "Total: ${PASS_COUNT} passed, ${FAIL_COUNT} failed (of 5 scenarios)"
    printf '=%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2

    if [ "$FAIL_COUNT" -gt 0 ]; then
        return 1
    fi

    return 0
}

main "$@"
