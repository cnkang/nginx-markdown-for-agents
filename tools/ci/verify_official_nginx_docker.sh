#!/usr/bin/env bash
set -euo pipefail

NGINX_TAG="${NGINX_TAG:-mainline}"
EXPECTED_NGINX_VERSION="${EXPECTED_NGINX_VERSION:-}"
IMAGE_REFERENCE="${IMAGE_REFERENCE:-}"
IMAGE_DIGEST="${IMAGE_DIGEST:-}"
MATRIX_ROW_ID="${MATRIX_ROW_ID:-}"
MATRIX_OS="${MATRIX_OS:-}"
MATRIX_LIBC="${MATRIX_LIBC:-}"
MATRIX_ARCH="${MATRIX_ARCH:-}"
PORT="${PORT:-18080}"
KEEP_IMAGE="${KEEP_IMAGE:-0}"
MODULE_REPO="${MODULE_REPO:-https://github.com/cnkang/nginx-markdown-for-agents.git}"
MODULE_REF="${MODULE_REF:-main}"
MODULE_SHA="${MODULE_SHA:-}"
SKIP_BUILD="${SKIP_BUILD:-0}"
ARTIFACT_DIR="${ARTIFACT_DIR:-}"
ALLOW_UNVERIFIED_IMAGE_BINDING="${ALLOW_UNVERIFIED_IMAGE_BINDING:-0}"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_NAME=""
IMAGE_BUILT=0
IMAGE_BINDING_ESTABLISHED=0
IMAGE_BINDING_SOURCE=""
CONTAINER_NAME=""
TMP_DIR=""
ACTUAL_NGINX_VERSION=""

# OCI labels the reused image must carry before --skip-build can claim the
# digest binding.  A local, stale, or hand-built image carries neither label,
# so treating their absence as "not established" (rather than silently
# accepting the image) is what keeps the release gate fail-closed.
OCI_LABEL_REVISION="org.opencontainers.image.revision"
OCI_LABEL_BASE_NAME="org.opencontainers.image.base.name"
OCI_LABEL_BASE_DIGEST="org.opencontainers.image.base.digest"

# usage displays command syntax, examples, and supported environment variables.
usage() {
  cat <<EOF
Usage: $(basename "$0") [--nginx-tag REF] --expected-nginx-version VERSION [--image-reference REF] --image-digest DIGEST [--module-repo URL] [--module-ref REF] [--module-sha SHA] [--image-name NAME] [--artifact-dir DIR] [--port PORT] [--skip-build] [--keep-image]

Builds the official NGINX-based Docker example from source and validates
runtime Markdown negotiation behavior.

Examples:
  $(basename "$0") --nginx-tag 1.31.5 --expected-nginx-version 1.31.5 \
    --image-reference nginx:1.31.5 --image-digest sha256:DIGEST \
    --module-sha FULL_40_HEX_COMMIT_SHA
  $(basename "$0") --skip-build --expected-nginx-version 1.31.5 \
    --image-reference nginx:1.31.5 --image-digest sha256:DIGEST \
    --module-sha FULL_40_HEX_COMMIT_SHA \
    --image-name nginx-markdown-official-check:1.31.5-debian12-glibc-amd64
  $(basename "$0") --nginx-tag 1.31.5 --expected-nginx-version 1.31.5 \
    --image-digest sha256:DIGEST \
    --module-sha FULL_40_HEX_COMMIT_SHA --artifact-dir /tmp/official-nginx-docker/row

Environment variables:
  NGINX_TAG   Default: mainline
  EXPECTED_NGINX_VERSION Required exact x.y.z version from release-matrix.json
  IMAGE_REFERENCE Exact immutable image tag associated with the matrix row
  IMAGE_DIGEST Required pinned multi-architecture image digest
  MATRIX_ROW_ID Release-matrix row identity for evidence
  MATRIX_OS / MATRIX_LIBC / MATRIX_ARCH Target row platform metadata
  MODULE_REPO Default: https://github.com/cnkang/nginx-markdown-for-agents.git
  MODULE_REF  Reachability hint. Default: main
  MODULE_SHA  Required full 40-character lowercase commit ID
  IMAGE_NAME  Default: nginx-markdown-official-check:<sanitized-tag>
  ARTIFACT_DIR Default: empty
  PORT        Default: 18080
  SKIP_BUILD  Default: 0 (1 = reuse an image that carries the OCI labels
              org.opencontainers.image.revision == MODULE_SHA and
              org.opencontainers.image.base.digest == IMAGE_DIGEST; the run
              fails closed when those labels are missing or disagree)
  ALLOW_UNVERIFIED_IMAGE_BINDING Default: 0 (1 = local/non-gating runs only;
              continue when --skip-build cannot prove the image binding)
  KEEP_IMAGE  Default: 0
EOF
  return 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --nginx-tag)
      NGINX_TAG="$2"
      shift 2
      ;;
    --expected-nginx-version)
      EXPECTED_NGINX_VERSION="$2"
      shift 2
      ;;
    --image-reference)
      IMAGE_REFERENCE="$2"
      shift 2
      ;;
    --image-digest)
      IMAGE_DIGEST="$2"
      shift 2
      ;;
    --matrix-row-id)
      MATRIX_ROW_ID="$2"
      shift 2
      ;;
    --arch)
      MATRIX_ARCH="$2"
      shift 2
      ;;
    --libc)
      MATRIX_LIBC="$2"
      shift 2
      ;;
    --module-repo)
      MODULE_REPO="$2"
      shift 2
      ;;
    --module-ref)
      MODULE_REF="$2"
      shift 2
      ;;
    --module-sha)
      MODULE_SHA="$2"
      shift 2
      ;;
    --image-name)
      IMAGE_NAME="$2"
      shift 2
      ;;
    --artifact-dir)
      ARTIFACT_DIR="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --allow-unverified-image-binding)
      # Local/CI escape hatch for images built without a Git context (no
      # OCI revision label) or without BuildKit git labels.  A release gate
      # must never pass this flag: the point of the check is to fail closed.
      ALLOW_UNVERIFIED_IMAGE_BINDING=1
      shift
      ;;
    --keep-image)
      KEEP_IMAGE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! printf '%s' "${MODULE_SHA}" | grep -Eq '^[0-9a-f]{40}$'; then
  echo "MODULE_SHA must be a full 40-character lowercase commit ID" >&2
  exit 2
fi
if ! printf '%s' "${EXPECTED_NGINX_VERSION}" \
  | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "EXPECTED_NGINX_VERSION must be an exact x.y.z version" >&2
  exit 2
fi
if ! printf '%s' "${IMAGE_DIGEST}" | grep -Eq '^sha256:[0-9a-f]{64}$'; then
  echo "IMAGE_DIGEST must be a full sha256 digest" >&2
  exit 2
fi
if [[ -z "${IMAGE_REFERENCE}" ]]; then
  IMAGE_REFERENCE="nginx:${NGINX_TAG}"
fi

# Verify that a required command is available in PATH.
#
# Arguments:
#   $1 - command name to check
#
# Outputs:
#   None on success; error message to stderr on failure.
#
# Returns:
#   0 when the command is found; exits with status 1 otherwise.
need_cmd() {
  local cmd_name="$1"
  command -v "${cmd_name}" >/dev/null 2>&1 || {
    echo "Missing required command: ${cmd_name}" >&2
    exit 1
  }
  return 0
}

# Read one OCI image config label from a ``docker image inspect`` JSON payload.
#
# Args:
#   $1 - path to the inspect JSON file
#   $2 - label key (e.g. org.opencontainers.image.revision)
#
# Outputs:
#   The label value on stdout, or nothing when absent/unreadable.
#
# Returns:
#   0 when a non-empty value was printed, 1 otherwise.
read_oci_label() {
  local inspect_json="$1"
  local label_key="$2"

  [[ -s "${inspect_json}" ]] || return 1
  python3 - "${inspect_json}" "${label_key}" <<'PY'
import json
import sys

path, key = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
except (OSError, json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit(1)

records = payload if isinstance(payload, list) else [payload]
for record in records:
    if not isinstance(record, dict):
        continue
    config = record.get("Config")
    if not isinstance(config, dict):
        continue
    labels = config.get("Labels")
    if not isinstance(labels, dict):
        continue
    value = labels.get(key)
    if isinstance(value, str) and value.strip():
        print(value)
        raise SystemExit(0)
raise SystemExit(1)
PY
}

# Decide whether a reused image may claim the matrix digest binding.
#
# The image is only bound to the reviewed candidate when its OCI labels say
# so: the revision label must equal MODULE_SHA, and the base-image labels
# must name IMAGE_REFERENCE and IMAGE_DIGEST.  Anything else (missing labels,
# a local rebuild, a stale tag, or a different base) leaves the binding
# unestablished, and the caller decides whether that is fatal.
#
# Globals read:
#   OCI_LABEL_REVISION / OCI_LABEL_BASE_NAME / OCI_LABEL_BASE_DIGEST
#   MODULE_SHA / IMAGE_REFERENCE / IMAGE_DIGEST
#
# Args:
#   $1 - path to the ``docker image inspect`` JSON payload
#
# Outputs:
#   A human-readable explanation on stdout and stderr.
#
# Returns:
#   0 when the binding is established, 1 otherwise.
verify_reused_image_binding() {
  local inspect_json="$1"
  local label_revision=""
  local label_base_name=""
  local label_base_digest=""

  label_revision="$(read_oci_label "${inspect_json}" "${OCI_LABEL_REVISION}" || true)"
  label_base_name="$(read_oci_label "${inspect_json}" "${OCI_LABEL_BASE_NAME}" || true)"
  label_base_digest="$(read_oci_label "${inspect_json}" "${OCI_LABEL_BASE_DIGEST}" || true)"

  echo "==> Reused image OCI labels:" >&2
  echo "      ${OCI_LABEL_REVISION}=${label_revision:-<absent>}" >&2
  echo "      ${OCI_LABEL_BASE_NAME}=${label_base_name:-<absent>}" >&2
  echo "      ${OCI_LABEL_BASE_DIGEST}=${label_base_digest:-<absent>}" >&2

  if [[ "${label_revision}" != "${MODULE_SHA}" ]]; then
    echo "Image binding not established: ${OCI_LABEL_REVISION} '${label_revision:-<absent>}' != MODULE_SHA '${MODULE_SHA}'" >&2
    return 1
  fi
  if [[ -z "${label_base_digest}" || "${label_base_digest}" != "${IMAGE_DIGEST}" ]]; then
    echo "Image binding not established: ${OCI_LABEL_BASE_DIGEST} '${label_base_digest:-<absent>}' != IMAGE_DIGEST '${IMAGE_DIGEST}'" >&2
    return 1
  fi
  # The base name label is optional in practice (BuildKit only records it when
  # the frontend can resolve a registry name); when present it must still
  # agree with the matrix reference.
  if [[ -n "${label_base_name}" && "${label_base_name}" != "${IMAGE_REFERENCE}" ]]; then
    echo "Image binding not established: ${OCI_LABEL_BASE_NAME} '${label_base_name}' != IMAGE_REFERENCE '${IMAGE_REFERENCE}'" >&2
    return 1
  fi

  echo "Image binding established from OCI labels (${OCI_LABEL_REVISION}=${MODULE_SHA})" >&2
  return 0
}

# Build the Docker image from the official NGINX source-build Dockerfile.
#
# Uses docker buildx when available for consistent multi-platform builds,
# falling back to plain docker build otherwise.
#
# Arguments:
#   (none; uses global NGINX_TAG, MODULE_REPO, MODULE_REF, MODULE_SHA, IMAGE_NAME)
#
# Outputs:
#   Docker build progress to stderr.
#
# Returns:
#   0 on success; non-zero if the build fails.
build_image() {
  local -a build_cmd

  if docker buildx version >/dev/null 2>&1; then
    build_cmd=(docker buildx build --load)
  else
    build_cmd=(docker build)
  fi

  # Self-attestation: stamp the same OCI labels the --skip-build path reads
  # back, so an image built here carries its matrix binding and can be reused
  # (e.g. by a later --skip-build verification) without loss of evidence.
  "${build_cmd[@]}" \
    --label "${OCI_LABEL_REVISION}=${MODULE_SHA}" \
    --label "${OCI_LABEL_BASE_NAME}=${IMAGE_REFERENCE}" \
    --label "${OCI_LABEL_BASE_DIGEST}=${IMAGE_DIGEST}" \
    --build-arg "NGINX_IMAGE=${IMAGE_REFERENCE}@${IMAGE_DIGEST}" \
    --build-arg "MODULE_REPO=${MODULE_REPO}" \
    --build-arg "MODULE_REF=${MODULE_REF}" \
    --build-arg "MODULE_SHA=${MODULE_SHA}" \
    -f "${WORKSPACE_ROOT}/examples/docker/Dockerfile.official-nginx-source-build" \
    -t "${IMAGE_NAME}" \
    "${WORKSPACE_ROOT}"

  IMAGE_BUILT=1
  IMAGE_BINDING_ESTABLISHED=1

  return 0
}

# Sanitize a Docker tag string for safe use as an image name component.
#
# Replaces all non-alphanumeric characters (except . _ -) with hyphens.
# Colons are intentionally replaced so the result is safe as a container name.
# using C locale for deterministic behaviour across platforms.
#
# Arguments:
#   $1 - raw tag string (e.g. "mainline", "stable-alpine")
#
# Outputs:
#   Writes the sanitized tag to stdout.
#
# Returns:
#   0 always.
sanitize_tag() {
  local raw_tag="$1"
  # Force C collation and keep "-" last so tag normalization is locale-stable.
  printf '%s' "${raw_tag}" | LC_ALL=C tr -c '[:alnum:]._-' '-'

  return 0
}

# Append a GitHub Actions step summary with validation results.
#
# Only writes when GITHUB_STEP_SUMMARY is set. Includes the exact release
# matrix identity, version binding, image digest, module ref, nginx -t status,
# and content negotiation results.
#
# Globals read:
#   GITHUB_STEP_SUMMARY  - GitHub Actions step summary file path
#   TMP_DIR              - temporary directory for build artifacts
#   IMAGE_NAME           - Docker image name
#   MODULE_GIT_REF       - module Git reference
#   runtime_uid          - effective UID used by the running container
#   markdown_code        - exit code from markdown content negotiation
#   html_code            - exit code from HTML content negotiation
#
# Arguments:
#   $1 - status string ("passed" or "failed")
#
# Outputs:
#   Appends Markdown to GITHUB_STEP_SUMMARY if set.
#
# Returns:
#   0 always.
append_step_summary() {
  local status="$1"
  [[ -n "${GITHUB_STEP_SUMMARY:-}" ]] || return 0

  {
    echo "### Official NGINX Docker Validation"
    echo
    echo "- Status: ${status}"
    echo "- Matrix row: \`${MATRIX_ROW_ID:-<not-provided>}\`"
    echo "- Expected NGINX version: \`${EXPECTED_NGINX_VERSION}\`"
    echo "- Actual NGINX version: \`${ACTUAL_NGINX_VERSION:-<not-checked>}\`"
    echo "- Image reference: \`${IMAGE_REFERENCE}\`"
    if [[ "${IMAGE_BINDING_ESTABLISHED}" -eq 1 ]]; then
      echo "- Image digest: \`${IMAGE_DIGEST}\` (binding: ${IMAGE_BINDING_SOURCE:-build-input})"
    else
      echo "- Image digest binding: not established"
    fi
    echo "- Platform: \`${MATRIX_OS:-<not-provided>}/${MATRIX_LIBC:-<not-provided>}/${MATRIX_ARCH:-<not-provided>}\`"
    echo "- Image: \`${IMAGE_NAME}\`"
    echo "- Module ref: \`${MODULE_REF}\`"
    echo "- Module sha: \`${MODULE_SHA}\`"
    echo "- Runtime UID: \`${runtime_uid:-<not-checked>}\`"
    if [[ -f "${TMP_DIR}/nginx-t.stderr" ]]; then
      if grep -q "test is successful" "${TMP_DIR}/nginx-t.stderr"; then
        echo "- nginx -t: ok"
      else
        echo "- nginx -t: see artifacts/logs"
      fi
    fi
    if [[ "${status}" == "passed" ]]; then
      echo "- Markdown request: \`${markdown_code}\`"
      echo "- HTML request: \`${html_code}\`"
      echo "- Markdown content-type: \`$(tr -d '\r' < "${TMP_DIR}/markdown.headers" | awk 'BEGIN{IGNORECASE=1} /^Content-Type:/ {print substr($0,15); exit}')\`"
      echo "- HTML content-type: \`$(tr -d '\r' < "${TMP_DIR}/html.headers" | awk 'BEGIN{IGNORECASE=1} /^Content-Type:/ {print substr($0,15); exit}')\`"
    fi
    if [[ -n "${ARTIFACT_DIR}" ]]; then
      echo "- Failure artifacts dir: \`${ARTIFACT_DIR}\`"
    fi
    echo
  } >> "${GITHUB_STEP_SUMMARY}"

  return 0
}

# Record the immutable matrix binding before the functional requests run.
# This file is uploaded on failure and remains useful in successful step logs.
write_release_evidence() {
  [[ -n "${ARTIFACT_DIR}" ]] || return 0

  mkdir -p "${ARTIFACT_DIR}"
  {
    printf 'matrix_row_id=%s\n' "${MATRIX_ROW_ID}"
    printf 'expected_nginx_version=%s\n' "${EXPECTED_NGINX_VERSION}"
    printf 'actual_nginx_version=%s\n' "${ACTUAL_NGINX_VERSION}"
    printf 'image_reference=%s\n' "${IMAGE_REFERENCE}"
    if [[ "${IMAGE_BINDING_ESTABLISHED}" -eq 1 ]]; then
      printf 'image_digest=%s\n' "${IMAGE_DIGEST}"
      printf 'image_digest_binding=%s\n' "${IMAGE_BINDING_SOURCE:-build-input}"
    else
      printf 'image_digest_binding=not-established\n'
    fi
    printf 'image_arch=%s\n' "${MATRIX_ARCH}"
    printf 'image_libc=%s\n' "${MATRIX_LIBC}"
    printf 'image_os=%s\n' "${MATRIX_OS}"
    printf 'module_source_sha=%s\n' "${MODULE_SHA}"
    printf 'built_image_id=%s\n' "${IMAGE_ID:-}"
  } > "${ARTIFACT_DIR}/release-matrix-evidence.txt"
  return 0
}

# Capture container logs, inspect data, and temp files into ARTIFACT_DIR.
#
# Only runs when ARTIFACT_DIR is set.  Collects docker logs, nginx -T
# output, nginx -t output, and copies temporary header/body files.
#
# Arguments:
#   (none; uses global CONTAINER_NAME, TMP_DIR, ARTIFACT_DIR)
#
# Outputs:
#   Writes diagnostic files into ARTIFACT_DIR.
#
# Returns:
#   0 always (errors are tolerated).
capture_failure_artifacts() {
  [[ -n "${ARTIFACT_DIR}" ]] || return 0

  mkdir -p "${ARTIFACT_DIR}"

  if [[ -n "${CONTAINER_NAME}" ]] && docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
    docker logs "${CONTAINER_NAME}" >"${ARTIFACT_DIR}/container.log" 2>&1 || true
    docker inspect "${CONTAINER_NAME}" >"${ARTIFACT_DIR}/container.inspect.json" 2>&1 || true
    docker exec "${CONTAINER_NAME}" nginx -T >"${ARTIFACT_DIR}/nginx-T.stdout" 2>"${ARTIFACT_DIR}/nginx-T.stderr" || true
    docker exec "${CONTAINER_NAME}" nginx -t >"${ARTIFACT_DIR}/nginx-t.stdout" 2>"${ARTIFACT_DIR}/nginx-t.stderr" || true
  fi

  if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
    cp -R "${TMP_DIR}/." "${ARTIFACT_DIR}/" 2>/dev/null || true
  fi

  return 0
}

# Cleanup handler: capture failure artifacts, append step summary,
# remove Docker container and image, and delete temp directory.
#
# Globals read:
#   CONTAINER_NAME  - Docker container name to remove
#   KEEP_IMAGE      - if 1, skip image removal
#   IMAGE_NAME      - Docker image name to remove
#   TMP_DIR         - temporary directory to delete
#   ARTIFACT_DIR    - directory to copy artifacts into on failure
#
# Side effects:
#   Calls capture_failure_artifacts and append_step_summary on failure.
#   Removes Docker container and optionally the image.
#   Removes TMP_DIR.
#
# Returns:
#   0 always.
cleanup() {
  local rc=$?

  if [[ $rc -ne 0 ]]; then
    capture_failure_artifacts
    append_step_summary "failed"
  fi

  if [[ -n "${CONTAINER_NAME}" ]] && docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
    if [[ $rc -ne 0 ]]; then
      echo "==> Container logs (${CONTAINER_NAME})" >&2
      docker logs "${CONTAINER_NAME}" >&2 || true
    fi
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
  fi

  if [[ "${KEEP_IMAGE}" != "1" && "${IMAGE_BUILT}" -eq 1 && -n "${IMAGE_NAME}" ]]; then
    docker rmi -f "${IMAGE_NAME}" >/dev/null 2>&1 || true
  fi

  if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
    rm -rf "${TMP_DIR}"
  fi

  return 0
}
trap cleanup EXIT

for cmd in docker curl awk grep sed head; do
  need_cmd "${cmd}"
done

safe_tag="$(sanitize_tag "${NGINX_TAG}")"
if [[ -z "${IMAGE_NAME}" ]]; then
  IMAGE_NAME="nginx-markdown-official-check:${safe_tag}"
fi
CONTAINER_NAME="nginx-markdown-official-check-${safe_tag}-${RANDOM}"
TMP_DIR="$(mktemp -d /tmp/nginx-official-docker-check.XXXXXX)"
IMAGE_ID=""
if [[ -n "${ARTIFACT_DIR}" ]]; then
  mkdir -p "${ARTIFACT_DIR}"
fi

if [[ "${SKIP_BUILD}" -eq 1 ]]; then
  echo "==> Reusing prebuilt image ${IMAGE_NAME}"
  # The reused image must prove it is the image built for this matrix row:
  # the OCI labels carry the reviewed module revision and the pinned base
  # digest.  Without that evidence the run is only exercising some local
  # image, so the binding stays unestablished and the gate fails closed.
  reused_inspect="${TMP_DIR}/reused-image.inspect.json"
  if docker image inspect "${IMAGE_NAME}" >"${reused_inspect}" 2>/dev/null \
    && verify_reused_image_binding "${reused_inspect}"; then
    IMAGE_BINDING_ESTABLISHED=1
    IMAGE_BINDING_SOURCE="oci-labels"
  else
    IMAGE_BINDING_ESTABLISHED=0
    IMAGE_BINDING_SOURCE="not-established"
    if [[ -n "${ARTIFACT_DIR}" ]]; then
      cp "${reused_inspect}" "${ARTIFACT_DIR}/reused-image.inspect.json" 2>/dev/null || true
    fi
    if [[ "${ALLOW_UNVERIFIED_IMAGE_BINDING}" == "1" ]]; then
      echo "WARNING: image binding could not be established from OCI labels; continuing because --allow-unverified-image-binding was given" >&2
    else
      echo "Image binding not established for reused image ${IMAGE_NAME}." >&2
      echo "The --skip-build path only accepts an image that carries the reviewed" >&2
      echo "module revision and pinned base digest as OCI labels.  Rebuild the" >&2
      echo "image in this job (drop --skip-build) or pass" >&2
      echo "--allow-unverified-image-binding for local, non-gating runs." >&2
      exit 1
    fi
  fi
else
  echo "==> Building image from ${IMAGE_REFERENCE}@${IMAGE_DIGEST}"
  build_image
fi

echo "==> Verifying exact NGINX version before functional tests"
version_output=""
version_rc=0
version_output="$(docker run --rm --entrypoint nginx "${IMAGE_NAME}" -v 2>&1)" \
  || version_rc=$?
if [[ "${version_rc}" -ne 0 ]]; then
  echo "Unable to run nginx -v in ${IMAGE_NAME}" >&2
  printf '%s\n' "${version_output}" >&2
  exit 1
fi
ACTUAL_NGINX_VERSION="$(printf '%s\n' "${version_output}" \
  | sed -n 's#.*nginx/\([0-9][0-9.]*\).*#\1#p' | head -1)"
IMAGE_ID="$(docker image inspect "${IMAGE_NAME}" --format '{{.Id}}' 2>/dev/null || true)"
write_release_evidence
if [[ "${ACTUAL_NGINX_VERSION}" != "${EXPECTED_NGINX_VERSION}" ]]; then
  echo "NGINX version mismatch: expected ${EXPECTED_NGINX_VERSION}, " \
    "actual ${ACTUAL_NGINX_VERSION:-<unparseable>}" >&2
  exit 1
fi
if [[ "${IMAGE_BINDING_ESTABLISHED}" -eq 1 ]]; then
  echo "Exact NGINX version ${ACTUAL_NGINX_VERSION} verified from ${IMAGE_REFERENCE}@${IMAGE_DIGEST}"
else
  echo "Exact NGINX version ${ACTUAL_NGINX_VERSION} verified from reused image; digest binding was not established by --skip-build"
fi
echo "Built image ID: ${IMAGE_ID:-<unavailable>}"

echo "==> Starting container on 127.0.0.1:${PORT}"
docker run -d \
  --name "${CONTAINER_NAME}" \
  -p "127.0.0.1:${PORT}:8080" \
  "${IMAGE_NAME}" >/dev/null

echo "==> Waiting for nginx to become ready"
ready=0
for _ in $(seq 1 30); do
  code="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/" || true)"
  if [[ "${code}" == "200" ]]; then
    ready=1
    break
  fi
  # Retry while the container runtime finishes creating the container, so
  # a not-yet-running container does not fail the exec below.
  sleep 1
done
[[ "${ready}" -eq 1 ]] || {
  echo "Container did not become ready on port ${PORT}" >&2
  exit 1
}

echo "==> Verifying container runs as a non-root user"
runtime_uid="$(docker exec "${CONTAINER_NAME}" id -u)"
[[ "${runtime_uid}" != "0" ]] || {
  echo "Container must not run as root" >&2
  exit 1
}

echo "==> Verifying nginx config with loaded module"
docker exec "${CONTAINER_NAME}" test -f /etc/nginx/modules/ngx_http_markdown_filter_module.so
docker exec "${CONTAINER_NAME}" nginx -t >"${TMP_DIR}/nginx-t.stdout" 2>"${TMP_DIR}/nginx-t.stderr"

markdown_code="$(curl -sS -D "${TMP_DIR}/markdown.headers" -o "${TMP_DIR}/markdown.body" \
  -H 'Accept: text/markdown' \
  "http://127.0.0.1:${PORT}/" \
  -w '%{http_code}')"

html_code="$(curl -sS -D "${TMP_DIR}/html.headers" -o "${TMP_DIR}/html.body" \
  -H 'Accept: text/html' \
  "http://127.0.0.1:${PORT}/" \
  -w '%{http_code}')"

[[ "${markdown_code}" == "200" ]] || {
  echo "Expected markdown request status 200, got ${markdown_code}" >&2
  exit 1
}
[[ "${html_code}" == "200" ]] || {
  echo "Expected html request status 200, got ${html_code}" >&2
  exit 1
}

grep -qi '^Content-Type: text/markdown; charset=utf-8' "${TMP_DIR}/markdown.headers" || {
  echo "Missing markdown Content-Type in response headers" >&2
  exit 1
}
grep -qi '^Vary: .*Accept' "${TMP_DIR}/markdown.headers" || {
  echo "Missing Vary: Accept in markdown response headers" >&2
  exit 1
}
grep -q '^# Docker Example Heading$' "${TMP_DIR}/markdown.body" || {
  echo "Converted markdown body missing expected heading" >&2
  exit 1
}

grep -qi '^Content-Type: text/html' "${TMP_DIR}/html.headers" || {
  echo "HTML response Content-Type is not text/html" >&2
  exit 1
}
grep -q '<h1>Docker Example Heading</h1>' "${TMP_DIR}/html.body" || {
  echo "HTML response body missing expected heading" >&2
  exit 1
}

echo "Validation summary:"
echo "  tag=${NGINX_TAG}"
echo "  module_repo=${MODULE_REPO}"
echo "  module_ref=${MODULE_REF}"
echo "  module_sha=${MODULE_SHA}"
echo "  image_name=${IMAGE_NAME}"
echo "  runtime_uid=${runtime_uid}"
echo "  markdown_status=${markdown_code}"
echo "  html_status=${html_code}"
echo "==> Official NGINX Docker source-build validation passed"
append_step_summary "passed"
