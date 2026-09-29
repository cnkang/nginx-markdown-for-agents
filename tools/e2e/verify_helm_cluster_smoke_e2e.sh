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
# The release name is run-unique: the lock serializes runs of THIS script,
# but an external actor can still create a fixed name between the ownership
# check and the install, and cleanup would then uninstall a release it does
# not own.  A unique name makes the ownership check race-free by
# construction (the name cannot pre-exist), and the namespace stays fixed
# so the reuse/cleanup contract for it is unchanged.
RELEASE="markdown-smoke-$$"
NAMESPACE="markdown-smoke"
KEEP=0
CREATED_CLUSTER=0
CREATED_NAMESPACE=0
CREATED_RELEASE=0
LOCK_MODE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --cluster) CLUSTER="$2"; shift 2 ;;
        --image) IMAGE_REF="$2"; shift 2 ;;
        --keep) KEEP=1; shift ;;
        -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

# The cluster name reaches kind, kubectl contexts, and the lock path, so it
# must satisfy kind's own grammar (lowercase alphanumerics and dashes,
# starting with a letter) before any of those use it.  The length bound
# matches kind's DNS-label limit and keeps every lock path's filename
# component inside the common 255-byte filesystem limit: an overlong name
# would make the fallback lock's mkdir fail silently and retry until its
# timeout instead of reaching kind.  The check covers the CLUSTER
# environment default and the --cluster flag alike.
if ! [[ "${CLUSTER}" =~ ^[a-z][a-z0-9-]{0,62}$ ]]; then
    echo "ERROR: invalid cluster name: ${CLUSTER}" >&2
    exit 1
fi

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
            # leaving markdown-smoke behind would poison the next run.  Wait
            # for the deletion (bounded, best-effort) BEFORE the lock below
            # is released: the next waiter would otherwise acquire the lock
            # while this namespace is still terminating and find the name
            # neither free nor usable.
            kubectl --context "kind-${CLUSTER}" \
                delete namespace "${NAMESPACE}" --wait=true --timeout=120s \
                >/dev/null 2>&1 || true
        fi
    fi
    rm -rf "${WORK_DIR}"
    release_cluster_lock
    return 0
}
trap cleanup EXIT

# Serialize concurrent runs against the same cluster.  Two runs can both
# observe the fixed release name as free and the loser's cleanup would then
# uninstall the winner's release (its ownership flag is set before its own
# install attempt).  The lock spans the ownership check, the install, and
# cleanup, so a second run waits until the first has released everything it
# owns.  flock is used when available (Linux); the directory fallback works
# everywhere else and records the owner PID, so a lock is broken only when
# its owner no longer exists - an age threshold would let a second run in
# while a legitimate long smoke still held the lock.
LOCK_PATH="${TMPDIR:-/tmp}/helm-cluster-smoke-${CLUSTER}"
LOCK_OWNER_FILE="${LOCK_PATH}.d/pid"

dir_lock_owner_alive() {
    local owner
    owner="$(cat "${LOCK_OWNER_FILE}" 2>/dev/null || true)"
    if [[ -n "${owner}" ]]; then
        kill -0 "${owner}" 2>/dev/null
        return
    fi
    # No readable owner yet: a live run may be in the short window between
    # creating the lock directory and writing its pid.  Treat a recent lock
    # as live; a lock older than a minute with no owner record is a crashed
    # run's leftover.  The age is re-checked after a short pause so a run
    # that was merely paused across the first check (and has since
    # published its pid) is never reclaimed.
    if [[ -n "$(find "${LOCK_PATH}.d" -maxdepth 0 -mmin +1 2>/dev/null)" ]]; then
        sleep 2
        if [[ -s "${LOCK_OWNER_FILE}" ]]; then
            return 0
        fi
        if [[ -z "$(find "${LOCK_PATH}.d" -maxdepth 0 -mmin +1 2>/dev/null)" ]]; then
            return 0
        fi
        return 1
    fi
    return 0
}

# Compare the opened lock descriptor's file with the path.  macOS exposes
# the descriptor through the fdescfs device, so only the inode number is
# comparable there; Linux reports the underlying device too and both are
# compared.  A failed stat is a refusal (fail closed).
lock_descriptor_matches_path() {
    local fd_id path_id
    if [[ "$(uname -s)" == "Darwin" ]]; then
        fd_id="$(stat -L -f %i /dev/fd/9 2>/dev/null)" || return 1
        path_id="$(stat -L -f %i "${LOCK_PATH}.flock" 2>/dev/null)" || return 1
    else
        fd_id="$(stat -L -c %d:%i /dev/fd/9 2>/dev/null)" || return 1
        path_id="$(stat -L -c %d:%i "${LOCK_PATH}.flock" 2>/dev/null)" || return 1
    fi
    [[ -n "${fd_id}" && "${fd_id}" == "${path_id}" ]]
}


acquire_cluster_lock() {
    # Refuse a symlinked or otherwise non-regular lock path before any
    # open: `exec 9>` follows a symlink and would truncate its target.  The
    # checks run before the flock branch so the fallback path is covered
    # too.  First creation below uses noclobber, which never creates
    # through a link.
    if [[ -L "${LOCK_PATH}.flock" ]] \
        || { [[ -e "${LOCK_PATH}.flock" ]] && [[ ! -f "${LOCK_PATH}.flock" ]]; }; then
        echo "ERROR: refusing non-regular lock path: ${LOCK_PATH}.flock" >&2
        exit 1
    fi
    if command -v flock >/dev/null 2>&1; then
        if [[ ! -f "${LOCK_PATH}.flock" ]]; then
            # First creation, race-free against a planted link: the
            # noclobber redirect never creates through one.
            ( set -o noclobber; : >"${LOCK_PATH}.flock" ) 2>/dev/null || true
        fi
        # Re-verify after the creation attempt: a path swapped in between
        # the check above and here is still refused, never opened.
        if [[ -L "${LOCK_PATH}.flock" ]] || [[ ! -f "${LOCK_PATH}.flock" ]]; then
            echo "ERROR: refusing non-regular lock path: ${LOCK_PATH}.flock" >&2
            exit 1
        fi
        # Open read-write WITHOUT truncation (<>): a symlink swapped in
        # between the check above and this open would otherwise be
        # followed and its target truncated by the O_TRUNC form.  The
        # post-open identity check then proves the descriptor's file is
        # still the same inode as the path: a swap that landed after the
        # open leaves the opened inode untouched and is refused.
        exec 9<>"${LOCK_PATH}.flock"
        if [[ -L "${LOCK_PATH}.flock" ]] || [[ ! -f "${LOCK_PATH}.flock" ]] \
            || ! lock_descriptor_matches_path; then
            echo "ERROR: refusing non-regular lock path: ${LOCK_PATH}.flock" >&2
            exit 1
        fi
        if flock -w 600 9; then
            LOCK_MODE="flock"
            return 0
        fi
        echo "ERROR: timed out waiting for the ${CLUSTER} smoke lock" >&2
        exit 1
    fi
    local waited=0
    while :; do
        if mkdir "${LOCK_PATH}.d" 2>/dev/null; then
            # Publish the owner with an EXCLUSIVE create.  A plain
            # redirect could overwrite the record of a run that reclaimed
            # this path during a pause (the reclaim grace treats an
            # ownerless directory as stale), and both runs would then
            # enter the critical section.  A failed publish means the
            # path is no longer this wait's: wait again.
            if ( set -o noclobber; printf '%s\n' "$$" > "${LOCK_OWNER_FILE}" ) 2>/dev/null; then
                LOCK_MODE="dir"
                return 0
            fi
        else
            # Breaking a stale lock is serialized by a short-lived reaper
            # mutex.  Without it, two waiters can both see a stale lock,
            # the winner re-acquires the canonical path, and the slower
            # waiter's rename then displaces that LIVE owner's lock; a
            # third waiter could take the freed canonical path while the
            # displaced owner still ran.  Under the reaper only one
            # waiter examines and reclaims a stale lock at a time, so a
            # live owner's lock is never displaced.
            acquire_lock_reaper
            if ! dir_lock_owner_alive; then
                local stale_claim="${LOCK_PATH}.stale.$$"
                if mv "${LOCK_PATH}.d" "${stale_claim}" 2>/dev/null; then
                    rm -rf "${stale_claim}"
                    release_lock_reaper
                    continue
                fi
            fi
            release_lock_reaper
        fi
        waited=$((waited + 2))
        if [[ "${waited}" -ge 600 ]]; then
            echo "ERROR: timed out waiting for the ${CLUSTER} smoke lock" >&2
            exit 1
        fi
        sleep 2
    done
}

acquire_lock_reaper() {
    # Short-lived mutex around stale-lock examination and reclaim.  A
    # reaper held by a dead pid (a crashed run) is broken by the next
    # waiter, so the mutex cannot wedge the smoke.  Breaking is atomic: a
    # waiter CLAIMS the stale directory with a rename before deleting it,
    # so two waiters cannot both reclaim it and one cannot remove a
    # replacement mutex the other has just created.  An ownerless
    # directory (a waiter died between mkdir and writing its pid) is
    # reclaimed only after a grace period.  After taking the mutex each
    # waiter re-reads the recorded pid and proceeds only while it names
    # this wait, and a claimed directory whose owner is alive is restored
    # or parked instead of deleted: its owner may still be recovering,
    # and removing a live owner's mutex would admit two examiners at once.
    local waited=0
    while true; do
        if mkdir "${LOCK_PATH}.reaper" 2>/dev/null; then
            # Publish the PID with an EXCLUSIVE create (noclobber): a
            # directory a recovery swapped in already carries its owner's
            # record, and overwriting it through the canonical path would
            # let two waiters believe they hold the mutex.  The readback
            # confirms this wait's publication survived to the return.
            if ( set -o noclobber; printf '%s\n' "$$" > "${LOCK_PATH}.reaper/pid" ) 2>/dev/null \
                && [[ "$(cat "${LOCK_PATH}.reaper/pid" 2>/dev/null || true)" == "$$" ]]; then
                return 0
            fi
        fi
        local reaper_pid
        reaper_pid="$(cat "${LOCK_PATH}.reaper/pid" 2>/dev/null || true)"
        if [[ "${reaper_pid}" == "$$" ]]; then
            # The record names this wait: an earlier attempt of ours holds
            # the mutex (its directory was restored after a displacement),
            # so enter rather than waiting on ourselves.
            return 0
        fi
        local stale=0
        if [[ -n "${reaper_pid}" ]]; then
            if ! kill -0 "${reaper_pid}" 2>/dev/null; then
                stale=1
            fi
        elif [[ -n "$(find "${LOCK_PATH}.reaper" -maxdepth 0 -mmin +1 2>/dev/null)" ]]; then
            sleep 2
            if [[ -s "${LOCK_PATH}.reaper/pid" ]]; then
                reaper_pid="$(cat "${LOCK_PATH}.reaper/pid" 2>/dev/null || true)"
                if [[ -n "${reaper_pid}" ]] && ! kill -0 "${reaper_pid}" 2>/dev/null; then
                    stale=1
                fi
            elif [[ -n "$(find "${LOCK_PATH}.reaper" -maxdepth 0 -mmin +1 2>/dev/null)" ]]; then
                stale=1
            fi
        fi
        if [[ "${stale}" -eq 1 ]]; then
            # The claim name carries the loop counter so a parked claim
            # from an earlier iteration is never a rename target again.
            local reaper_claim="${LOCK_PATH}.reaper.stale.$$.${waited}"
            if mv "${LOCK_PATH}.reaper" "${reaper_claim}" 2>/dev/null; then
                local claimed_pid
                claimed_pid="$(cat "${reaper_claim}/pid" 2>/dev/null || true)"
                if [[ -n "${claimed_pid}" ]] && kill -0 "${claimed_pid}" 2>/dev/null; then
                    # The claim took a directory whose owner is alive (the
                    # staleness read raced its publication): give it back,
                    # or park it when the path is already re-taken.  A
                    # live owner's mutex is never deleted.
                    if [[ ! -e "${LOCK_PATH}.reaper" ]]; then
                        mv "${reaper_claim}" "${LOCK_PATH}.reaper" 2>/dev/null || true
                    fi
                else
                    rm -rf "${reaper_claim}"
                fi
            fi
        fi
        waited=$((waited + 1))
        if [[ "${waited}" -ge 600 ]]; then
            echo "ERROR: timed out waiting for the ${CLUSTER} smoke lock" >&2
            exit 1
        fi
        sleep 1
    done
}

release_lock_reaper() {
    local reaper_pid
    reaper_pid="$(cat "${LOCK_PATH}.reaper/pid" 2>/dev/null || true)"
    if [[ "${reaper_pid}" == "$$" ]]; then
        rm -rf "${LOCK_PATH}.reaper"
    fi
    return 0
}

release_cluster_lock() {
    if [[ "${LOCK_MODE}" == "dir" ]]; then
        # Remove the lock only when this run still owns it: if the lock was
        # broken (owner gone) and another run has since acquired it, deleting
        # the path here would clear THAT run's lock and admit a third.
        local owner
        owner="$(cat "${LOCK_OWNER_FILE}" 2>/dev/null || true)"
        if [[ "${owner}" == "$$" ]]; then
            rm -rf "${LOCK_PATH}.d"
        fi
    fi
    return 0
}

acquire_cluster_lock

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
# Helm's rollback-on-failure flag was renamed: Helm 3 spells it `--atomic`
# (which also implies --wait), Helm 4 offers `--rollback-on-failure` and keeps
# `--atomic` only as a deprecated alias.  Select by major version so the smoke
# uses each major's own spelling and neither emits deprecation noise.
helm_rollback_flag() {
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

CREATED_RELEASE=1
# The install output is captured so the failure path can classify the
# error; it is reprinted on both paths so the diagnostics stay identical.
if ! install_output="$(helm install "${RELEASE}" "${REPO_ROOT}/charts/nginx-markdown" \
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
    "$(helm_rollback_flag)" 2>&1)"; then
    # A name collision means another actor created this run's release name
    # between the preflight and the install: the name is theirs, so cleanup
    # preserves it (and everything that holds it).  Two error shapes report
    # it: the name check refuses with "cannot re-use a name that is still in
    # use" (Helm 3) or "cannot reuse ..." (Helm 4), and the storage-layer
    # create that follows its own availability check reports "release:
    # already exists" when both racers passed that check.
    #
    # The classification clears the claims BEFORE any output: bash runs a
    # deferred TERM trap between foreground commands, so printing first
    # would leave a window where cleanup still uninstalls the collider's
    # release.  Their release lives in this cluster, so the cluster claim
    # clears with the others - deleting the cluster would take it down.
    collision=0
    if [[ "${install_output}" == *"name that is still in use"* ]] \
        || [[ "${install_output}" == *"release: already exists"* ]]; then
        collision=1
        CREATED_RELEASE=0
        CREATED_NAMESPACE=0
        CREATED_CLUSTER=0
    fi
    printf '%s\n' "${install_output}" >&2
    if [[ "${collision}" -eq 1 ]]; then
        echo "ERROR: another creator holds release ${RELEASE}; cleanup preserves it" >&2
    fi
    exit 1
fi
printf '%s\n' "${install_output}" >&2

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
