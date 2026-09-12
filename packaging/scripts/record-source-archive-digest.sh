#!/usr/bin/env bash
# record-source-archive-digest.sh — Record a release tag's source-archive digest.
#
# Usage:
#   record-source-archive-digest.sh TAG [--repo OWNER/REPO] [--registry FILE] [-h]
#
# Records the SHA256 of https://github.com/OWNER/REPO/archive/refs/tags/TAG.tar.gz
# in the checked-in registry as "source-TAG".  When the registry already holds an
# entry for the tag, the download must match it: a mismatch means the archive
# changed, which is exactly the drift this registry exists to catch.
#
# Run this after the tag is published, review the diff, and commit the result:
# the entry lives in a later commit, so the tag's own tree is unaffected.
#
# Exit codes:
#   0  Digest recorded or already matches
#   1  Error (download failed, mismatch with the recorded digest, bad registry)
#
# This script is FAIL-CLOSED: it never overwrites a differing recorded digest.
# Pass --refresh only after a reviewed decision that the archive legitimately
# changed; the flag is required so an accidental overwrite is impossible.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

DEFAULT_REGISTRY="${REPO_ROOT}/packaging/source-archive-digests.sha256"
DEFAULT_REPO="cnkang/nginx-markdown-for-agents"

usage() {
    sed -n '2,20p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

die() {
    printf 'ERROR: [record-source-archive-digest] %s\n' "$1" >&2
    exit 1
}

info() {
    printf '[record-source-archive-digest] %s\n' "$1" >&2
}

TAG=""
REPO="${DEFAULT_REPO}"
REGISTRY="${DEFAULT_REGISTRY}"
REFRESH=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --repo)
            [[ $# -ge 2 ]] || die "--repo requires a value"
            REPO="$2"
            shift 2
            ;;
        --registry)
            [[ $# -ge 2 ]] || die "--registry requires a value"
            REGISTRY="$2"
            shift 2
            ;;
        --refresh)
            REFRESH=1
            shift
            ;;
        -*)
            die "unknown option: $1"
            ;;
        *)
            [[ -z "${TAG}" ]] || die "only one TAG may be given"
            TAG="$1"
            shift
            ;;
    esac
done

[[ -n "${TAG}" ]] || { usage; die "TAG is required"; }
[[ "${TAG}" =~ ^[A-Za-z0-9._-]+$ ]] || die "TAG contains unsupported characters: ${TAG}"
[[ -f "${REGISTRY}" ]] || die "registry not found: ${REGISTRY}"

IDENTIFIER="source-${TAG}"
URL="https://github.com/${REPO}/archive/refs/tags/${TAG}.tar.gz"

info "downloading ${URL}"
TMP_ARCHIVE="$(mktemp "${TMPDIR:-/tmp}/source-archive-${TAG}.XXXXXX.tar.gz")"
trap 'rm -f "${TMP_ARCHIVE}"' EXIT
curl -fsSL --retry 3 --max-time 300 -o "${TMP_ARCHIVE}" "${URL}" \
    || die "download failed: ${URL}"

ACTUAL="$(shasum -a 256 "${TMP_ARCHIVE}" 2>/dev/null | awk '{print $1}' || sha256sum "${TMP_ARCHIVE}" | awk '{print $1}')"
[[ "${ACTUAL}" =~ ^[0-9a-f]{64}$ ]] || die "unexpected digest shape: ${ACTUAL}"

RECORDED="$(awk -v id="${IDENTIFIER}" '$2 == id { print $1 }' "${REGISTRY}")"
if [[ -n "${RECORDED}" ]]; then
    if [[ "${RECORDED}" == "${ACTUAL}" ]]; then
        info "${IDENTIFIER} already recorded and still matches"
        exit 0
    fi
    [[ "${REFRESH}" -eq 1 ]] \
        || die "recorded ${IDENTIFIER}=${RECORDED} differs from ${ACTUAL}; \
review the archive change and rerun with --refresh if it is intended"
    info "refreshing ${IDENTIFIER}: ${RECORDED} -> ${ACTUAL}"
fi

TMP_REGISTRY="$(mktemp "${TMPDIR:-/tmp}/source-registry.XXXXXX")"
trap 'rm -f "${TMP_ARCHIVE}" "${TMP_REGISTRY}"' EXIT
{
    awk -v id="${IDENTIFIER}" '$2 != id' "${REGISTRY}" || true
    printf '%s  %s\n' "${ACTUAL}" "${IDENTIFIER}"
} > "${TMP_REGISTRY}"
mv "${TMP_REGISTRY}" "${REGISTRY}"

info "recorded ${IDENTIFIER}=${ACTUAL} in ${REGISTRY}"
