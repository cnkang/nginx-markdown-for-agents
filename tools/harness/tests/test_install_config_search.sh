#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INSTALL_SCRIPT="${REPO_ROOT}/tools/install.sh"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "${TMP_DIR}"
  return 0
}
trap cleanup EXIT

if grep -Eq 'grep[[:space:]].*--include' "${INSTALL_SCRIPT}"; then
  echo "FAIL: install.sh must not require GNU grep --include" >&2
  exit 1
fi

sed -n '/^conf_tree_contains_pattern()/,/^}/p' "${INSTALL_SCRIPT}" \
  > "${TMP_DIR}/config-search-function.sh"
if [[ ! -s "${TMP_DIR}/config-search-function.sh" ]]; then
  echo "FAIL: conf_tree_contains_pattern helper not found" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "${TMP_DIR}/config-search-function.sh"
mkdir -p "${TMP_DIR}/conf.d"
printf '%s\n' 'load_module modules/example.so;' \
  > "${TMP_DIR}/conf.d/example.conf"
printf '%s\n' 'markdown_filter on;' \
  > "${TMP_DIR}/conf.d/odd
name.conf"

conf_tree_contains_pattern "${TMP_DIR}" '*.conf' \
  '^[[:space:]]*load_module[[:space:]]+.*example\.so[[:space:]]*;'
conf_tree_contains_pattern "${TMP_DIR}" '*.conf' \
  '^[[:space:]]*markdown_filter[[:space:]]+on[[:space:]]*;'
if conf_tree_contains_pattern "${TMP_DIR}" '*.conf' 'not_present'; then
  echo "FAIL: config search reported a missing pattern" >&2
  exit 1
fi

echo "install config search portability test passed"
