#!/usr/bin/env bash
set -euo pipefail

NGINX_TAG="${NGINX_TAG:-mainline}"
PORT="${PORT:-18080}"
KEEP_IMAGE="${KEEP_IMAGE:-0}"
MODULE_REPO="${MODULE_REPO:-https://github.com/cnkang/nginx-markdown-for-agents.git}"
MODULE_REF="${MODULE_REF:-main}"
MODULE_SHA="${MODULE_SHA:-}"
SKIP_BUILD="${SKIP_BUILD:-0}"
ARTIFACT_DIR="${ARTIFACT_DIR:-}"
WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_NAME=""
CONTAINER_NAME=""
TMP_DIR=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [--nginx-tag TAG] [--module-repo URL] [--module-ref REF] [--module-sha SHA] [--image-name NAME] [--artifact-dir DIR] [--port PORT] [--skip-build] [--keep-image]

Builds the official NGINX-based Docker example from source and validates
runtime Markdown negotiation behavior.

Examples:
  $(basename "$0") --nginx-tag mainline
  $(basename "$0") --nginx-tag stable-alpine --port 18083
  $(basename "$0") --nginx-tag mainline --module-ref main
  $(basename "$0") --nginx-tag mainline --module-sha abc1234
  $(basename "$0") --skip-build --image-name nginx-markdown-official-check:stable-amd64
  $(basename "$0") --artifact-dir /tmp/official-nginx-docker/mainline-amd64

Environment variables:
  NGINX_TAG   Default: mainline
  MODULE_REPO Default: https://github.com/cnkang/nginx-markdown-for-agents.git
  MODULE_REF  Default: main
  MODULE_SHA  Default: empty
  IMAGE_NAME  Default: nginx-markdown-official-check:<sanitized-tag>
  ARTIFACT_DIR Default: empty
  PORT        Default: 18080
  SKIP_BUILD  Default: 0
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

need_cmd() {
  local cmd_name="$1"
  command -v "${cmd_name}" >/dev/null 2>&1 || {
    echo "Missing required command: ${cmd_name}" >&2
    exit 1
  }
  return 0
}

build_image() {
  local -a build_cmd

  if docker buildx version >/dev/null 2>&1; then
    build_cmd=(docker buildx build --load)
  else
    build_cmd=(docker build)
  fi

  "${build_cmd[@]}" \
    --build-arg "NGINX_IMAGE=nginx:${NGINX_TAG}" \
    --build-arg "MODULE_REPO=${MODULE_REPO}" \
    --build-arg "MODULE_REF=${MODULE_REF}" \
    --build-arg "MODULE_SHA=${MODULE_SHA}" \
    -f "${WORKSPACE_ROOT}/examples/docker/Dockerfile.official-nginx-source-build" \
    -t "${IMAGE_NAME}" \
    "${WORKSPACE_ROOT}"

  return 0
}

sanitize_tag() {
  local raw_tag="$1"
  # Force C collation and keep "-" last so tag normalization is locale-stable.
  printf '%s' "${raw_tag}" | LC_ALL=C tr -c '[:alnum:].:_-' '-'

  return 0
}

append_step_summary() {
  local status="$1"
  [[ -n "${GITHUB_STEP_SUMMARY:-}" ]] || return 0

  {
    echo "### Official NGINX Docker Validation"
    echo
    echo "- Status: ${status}"
    echo "- Tag: \`${NGINX_TAG}\`"
    echo "- Image: \`${IMAGE_NAME}\`"
    echo "- Module ref: \`${MODULE_REF}\`"
    echo "- Module sha: \`${MODULE_SHA:-<none>}\`"
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
}

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
}

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

  if [[ "${KEEP_IMAGE}" -ne 1 && -n "${IMAGE_NAME}" ]]; then
    docker rmi -f "${IMAGE_NAME}" >/dev/null 2>&1 || true
  fi

  if [[ -n "${TMP_DIR}" && -d "${TMP_DIR}" ]]; then
    rm -rf "${TMP_DIR}"
  fi

  return 0
}
trap cleanup EXIT

for cmd in docker curl awk grep sed; do
  need_cmd "${cmd}"
done

safe_tag="$(sanitize_tag "${NGINX_TAG}")"
if [[ -z "${IMAGE_NAME}" ]]; then
  IMAGE_NAME="nginx-markdown-official-check:${safe_tag}"
fi
CONTAINER_NAME="nginx-markdown-official-check-${safe_tag}-${RANDOM}"
TMP_DIR="$(mktemp -d /tmp/nginx-official-docker-check.XXXXXX)"
if [[ -n "${ARTIFACT_DIR}" ]]; then
  mkdir -p "${ARTIFACT_DIR}"
fi

if [[ "${SKIP_BUILD}" -eq 1 ]]; then
  echo "==> Reusing prebuilt image ${IMAGE_NAME}"
else
  echo "==> Building image from official nginx:${NGINX_TAG}"
  build_image
fi

echo "==> Starting container on 127.0.0.1:${PORT}"
docker run -d \
  --name "${CONTAINER_NAME}" \
  -p "127.0.0.1:${PORT}:80" \
  "${IMAGE_NAME}" >/dev/null

echo "==> Waiting for nginx to become ready"
ready=0
for _ in $(seq 1 30); do
  code="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/" || true)"
  if [[ "${code}" == "200" ]]; then
    ready=1
    break
  fi
  sleep 1
done
[[ "${ready}" -eq 1 ]] || {
  echo "Container did not become ready on port ${PORT}" >&2
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
echo "  module_sha=${MODULE_SHA:-<none>}"
echo "  image_name=${IMAGE_NAME}"
echo "  markdown_status=${markdown_code}"
echo "  html_status=${html_code}"
echo "==> Official NGINX Docker source-build validation passed"
append_step_summary "passed"
