#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
WRAPPER_SCRIPT="${WORKSPACE_ROOT}/tools/sonar/cc_wrapper.sh"

# shellcheck disable=SC1090
source "${NATIVE_BUILD_HELPER}"
markdown_ensure_native_apple_silicon "${SCRIPT_PATH}" "$@"

usage() {
  cat <<'USAGE'
Usage: tools/sonar/generate_compile_commands.sh [options]

Generate compile_commands.json for SonarQube for VS Code C/C++ analysis.

Options:
  --nginx-version <stable|mainline|x.y.z>  NGINX version channel or explicit version (default: stable)
  --output <path>                          Output compile_commands.json path (default: <repo>/compile_commands.json)
  --skip-rust-build                        Skip Rust library rebuild step
  --help                                   Show this help
USAGE
  return 0
}

resolve_nginx_version() {
  local requested="$1"
  local page
  local version

  case "${requested}" in
    stable|mainline)
      page="$(curl -fsSL https://nginx.org/en/download.html)"
      version="$({
        NGINX_DOWNLOAD_HTML="${page}" CHANNEL="${requested}" python3 - <<'PY'
import os
import re

html = os.environ.get("NGINX_DOWNLOAD_HTML", "")
channel = os.environ.get("CHANNEL", "")

if channel == "mainline":
    pattern = r"Mainline version.*?nginx-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz"
elif channel == "stable":
    pattern = r"Stable version.*?nginx-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz"
else:
    raise SystemExit(1)

match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
if not match:
    raise SystemExit(1)

print(match.group(1))
PY
      })"

      if [[ -z "${version}" ]]; then
        echo "Failed to resolve NGINX version for channel: ${requested}" >&2
        exit 1
      fi

      printf '%s\n' "${version}"
      ;;
    *)
      printf '%s\n' "${requested}"
      ;;
  esac
  return 0
}

NGINX_VERSION="stable"
OUTPUT_FILE="${WORKSPACE_ROOT}/compile_commands.json"
SKIP_RUST_BUILD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --nginx-version)
      if [[ $# -lt 2 ]] || [[ -z "${2:-}" ]]; then
        echo "Error: --nginx-version requires a value" >&2
        usage >&2
        exit 1
      fi
      NGINX_VERSION="$2"
      shift 2
      ;;
    --output)
      if [[ $# -lt 2 ]] || [[ -z "${2:-}" ]]; then
        echo "Error: --output requires a value" >&2
        usage >&2
        exit 1
      fi
      OUTPUT_FILE="$2"
      shift 2
      ;;
    --skip-rust-build)
      SKIP_RUST_BUILD=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

for cmd in curl tar make python3 cc; do
  markdown_need_cmd "${cmd}"
done

if [[ ${SKIP_RUST_BUILD} -eq 0 ]]; then
  markdown_need_cmd cargo
fi

if [[ ! -x "${WRAPPER_SCRIPT}" ]]; then
  echo "Missing wrapper script: ${WRAPPER_SCRIPT}" >&2
  exit 1
fi

REAL_NGINX_VERSION="$(resolve_nginx_version "${NGINX_VERSION}")"
SONAR_ROOT="${WORKSPACE_ROOT}/.sonar/cfamily"
BUILDROOT="${SONAR_ROOT}/nginx-${REAL_NGINX_VERSION}"
TARBALL="${SONAR_ROOT}/nginx-${REAL_NGINX_VERSION}.tar.gz"
CAPTURE_FILE="${SONAR_ROOT}/compile_commands.jsonl"

mkdir -p "${SONAR_ROOT}"

if [[ ${SKIP_RUST_BUILD} -eq 0 ]]; then
  RUST_TARGET="$(markdown_detect_rust_target)"
  echo "==> Building Rust converter static library (${RUST_TARGET})"
  markdown_prepare_rust_converter_release "${WORKSPACE_ROOT}" "${RUST_TARGET}" --locked
else
  echo "==> Skipping Rust converter build (--skip-rust-build)"
fi

if [[ ! -d "${BUILDROOT}" ]]; then
  echo "==> Downloading NGINX ${REAL_NGINX_VERSION}"
  curl -fsSL "https://nginx.org/download/nginx-${REAL_NGINX_VERSION}.tar.gz" -o "${TARBALL}"
  mkdir -p "${BUILDROOT}"
  tar -xzf "${TARBALL}" -C "${BUILDROOT}" --strip-components=1
else
  # Reset build root to ensure a clean tree for compile command capture
  echo "==> Resetting existing NGINX build root for clean capture"
  rm -rf "${BUILDROOT}"
  mkdir -p "${BUILDROOT}"
  if [[ ! -f "${TARBALL}" ]]; then
    echo "==> Downloading NGINX ${REAL_NGINX_VERSION}"
    curl -fsSL "https://nginx.org/download/nginx-${REAL_NGINX_VERSION}.tar.gz" -o "${TARBALL}"
  fi
  tar -xzf "${TARBALL}" -C "${BUILDROOT}" --strip-components=1
fi

: > "${CAPTURE_FILE}"

echo "==> Configuring NGINX with markdown module"
(
  cd "${BUILDROOT}"
  SONAR_REAL_CC="$(command -v cc)" \
  SONAR_CC_CAPTURE_FILE="${CAPTURE_FILE}" \
  CC="${WRAPPER_SCRIPT}" \
  ./configure \
    --with-compat \
    --without-http_rewrite_module \
    --add-dynamic-module="${WORKSPACE_ROOT}/components/nginx-module" \
    >/dev/null
)

echo "==> Building NGINX module and capturing compile commands"
(
  cd "${BUILDROOT}"
  SONAR_REAL_CC="$(command -v cc)" \
  SONAR_CC_CAPTURE_FILE="${CAPTURE_FILE}" \
  CC="${WRAPPER_SCRIPT}" \
  make -j1 modules >/dev/null
)

echo "==> Writing filtered compilation database"
python3 - "${CAPTURE_FILE}" "${WORKSPACE_ROOT}/components/nginx-module" "${OUTPUT_FILE}" <<'PY'
import json
import os
import sys

capture_file = sys.argv[1]
module_root = os.path.realpath(sys.argv[2])
output_file = os.path.realpath(sys.argv[3])

entries_by_file = {}

with open(capture_file, "r", encoding="utf-8") as handle:
    for raw in handle:
        raw = raw.strip()
        if not raw:
            continue

        item = json.loads(raw)
        directory = item.get("directory", "")
        file_path = item.get("file", "")
        arguments = item.get("arguments", [])

        if not directory or not file_path or not arguments:
            continue

        absolute_file = file_path if os.path.isabs(file_path) else os.path.realpath(os.path.join(directory, file_path))
        absolute_file = os.path.realpath(absolute_file)

        if not absolute_file.endswith(".c"):
            continue

        if not (absolute_file == module_root or absolute_file.startswith(module_root + os.sep)):
            continue

        entries_by_file[absolute_file] = {
            "directory": directory,
            "arguments": arguments,
            "file": absolute_file,
        }

if not entries_by_file:
    raise SystemExit("No module C compile commands were captured. Build likely failed or build output changed.")

ordered = [entries_by_file[key] for key in sorted(entries_by_file)]

os.makedirs(os.path.dirname(output_file), exist_ok=True)
with open(output_file, "w", encoding="utf-8") as handle:
    json.dump(ordered, handle, indent=2)
    handle.write("\n")

print(f"Captured {len(ordered)} C translation units -> {output_file}")
PY

echo "==> Done"
echo "Compilation database: ${OUTPUT_FILE}"
