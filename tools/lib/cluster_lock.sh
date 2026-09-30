#!/usr/bin/env bash
#
# Shared per-cluster serialization lock for the cluster smokes.
#
# Both the Helm chart smoke (tools/e2e/verify_helm_cluster_smoke_e2e.sh) and
# the local K8s gate (tools/release/gates/gate4_local_k8s_smoke.sh) create,
# use, and delete resources in a kind cluster, so two concurrent runs that
# target one cluster would delete each other's namespace, release, or the
# cluster itself.  This library holds one lock per cluster for the span that
# touches it.
#
# Contract:
#   - The caller sets LOCK_CLUSTER (the target cluster's name; both
#     scripts must set it, or they would not serialize against each
#     other) before calling acquire_cluster_lock, and calls
#     release_cluster_lock after the last cluster-touching step.
#   - LOCK_MODE, LOCK_PATH, and LOCK_OWNER_FILE are set by
#     acquire_cluster_lock (lazily: a caller may learn the cluster name
#     from its argument parser after sourcing).
#   - flock is used when available (Linux); the directory fallback works
#     everywhere else and records the owner PID, so a lock is broken only
#     when its owner no longer exists - an age threshold would let a second
#     run in while a legitimate long run still held the lock.
#   - A refusal exits with status 1; the caller never continues unlocked.
#
# One implementation on purpose: two copies of this protocol drifted apart
# in review, and a stale-claim / reaper / readback fix has to apply to
# every user.

# Serialize concurrent runs against the same cluster.  Two runs can both
# observe the fixed release name as free and the loser's cleanup would then
# uninstall the winner's release (its ownership flag is set before its own
# install attempt).  The lock spans the ownership check, the install, and
# cleanup, so a second run waits until the first has released everything it
# owns.  flock is used when available (Linux); the directory fallback works
# everywhere else and records the owner PID, so a lock is broken only when
# its owner no longer exists - an age threshold would let a second run in
# while a legitimate long smoke still held the lock.
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
    # The lock file is keyed by the cluster name alone, so every script
    # that targets this cluster contends on the same path.
    if [[ -z "${LOCK_CLUSTER:-}" ]]; then
        echo "ERROR: LOCK_CLUSTER must be set before acquire_cluster_lock" >&2
        exit 1
    fi
    LOCK_PATH="${TMPDIR:-/tmp}/cluster-smoke-${LOCK_CLUSTER}"
    LOCK_OWNER_FILE="${LOCK_PATH}.d/pid"
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
        echo "ERROR: timed out waiting for the ${LOCK_CLUSTER} cluster lock" >&2
        exit 1
    fi
    local waited=0
    while :; do
        if mkdir "${LOCK_PATH}.d" 2>/dev/null; then
            # Publish the owner with an EXCLUSIVE create, then read it
            # back: a plain redirect could overwrite the record of a run
            # that reclaimed this path during a pause (the reclaim grace
            # treats an ownerless directory as stale), and a writer
            # paused across the reclaim can have its write land where the
            # canonical path no longer holds it.  Only the run whose pid
            # the canonical record actually carries returns as owner; any
            # other outcome waits again.
            if ( set -o noclobber; printf '%s\n' "$$" > "${LOCK_OWNER_FILE}" ) 2>/dev/null \
                && [[ "$(cat "${LOCK_OWNER_FILE}" 2>/dev/null || true)" == "$$" ]]; then
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
                # The claim name carries the loop counter so a parked
                # claim is never a rename target again.
                local stale_claim="${LOCK_PATH}.stale.$$.${waited}"
                if mv "${LOCK_PATH}.d" "${stale_claim}" 2>/dev/null; then
                    # Re-read the claimed owner: a creator that paused
                    # past the age grace can publish its pid between the
                    # stale check and the rename.  A live owner's claim is
                    # restored when the canonical path is still free, or
                    # parked when another acquisition took it - never
                    # deleted, because deleting it would let a second run
                    # enter the critical section while the owner still
                    # runs.
                    local claimed_owner
                    claimed_owner="$(cat "${stale_claim}/pid" 2>/dev/null || true)"
                    if [[ -n "${claimed_owner}" ]] \
                        && kill -0 "${claimed_owner}" 2>/dev/null; then
                        # Restore only into a free canonical path: `mv`
                        # onto an existing directory nests the claim
                        # inside it (rc=0 on BSD and GNU), which would
                        # bury the live owner's record under the new
                        # acquisition and admit two owners.  The reaper
                        # restore guards the same way.
                        if [[ ! -e "${LOCK_PATH}.d" ]]; then
                            mv "${stale_claim}" "${LOCK_PATH}.d" 2>/dev/null || true
                        fi
                    else
                        rm -rf "${stale_claim}"
                    fi
                    release_lock_reaper
                    continue
                fi
            fi
            release_lock_reaper
        fi
        waited=$((waited + 2))
        if [[ "${waited}" -ge 600 ]]; then
            echo "ERROR: timed out waiting for the ${LOCK_CLUSTER} cluster lock" >&2
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
            echo "ERROR: timed out waiting for the ${LOCK_CLUSTER} cluster lock" >&2
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
