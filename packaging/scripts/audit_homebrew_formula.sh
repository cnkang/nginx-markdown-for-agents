#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
TAP_NAME="codex/homebrew-formula-check-$$"
TAP_CREATED=0
AUDIT_CONFIG="${TMPDIR:-/tmp}/homebrew-formula-check-$$"

cleanup() {
    local exit_code="$?"

    if [[ "${TAP_CREATED}" == "1" ]]; then
        HOMEBREW_NO_AUTO_UPDATE=1 XDG_CONFIG_HOME="${AUDIT_CONFIG}" \
            brew untap "${TAP_NAME}" >/dev/null || true
    fi
    if [[ -f "${AUDIT_CONFIG}/homebrew/trust.json" ]]; then
        rm "${AUDIT_CONFIG}/homebrew/trust.json" || true
    fi
    rmdir "${AUDIT_CONFIG}/homebrew" 2>/dev/null || true
    rmdir "${AUDIT_CONFIG}" 2>/dev/null || true
    return "${exit_code}"
}
trap cleanup EXIT

if ! command -v brew >/dev/null 2>&1; then
    echo "ERROR: Homebrew is required to audit the formula" >&2
    exit 1
fi

mkdir -p "${AUDIT_CONFIG}"
HOMEBREW_NO_AUTO_UPDATE=1 XDG_CONFIG_HOME="${AUDIT_CONFIG}" \
    brew tap-new --no-git "${TAP_NAME}" >/dev/null
TAP_CREATED=1
TAP_PATH="$(HOMEBREW_NO_AUTO_UPDATE=1 XDG_CONFIG_HOME="${AUDIT_CONFIG}" \
    brew --repository "${TAP_NAME}")"
mkdir -p "${TAP_PATH}/Formula"
cp "${REPO_ROOT}/packaging/homebrew/nginx-markdown-module.rb" \
    "${TAP_PATH}/Formula/nginx-markdown-module.rb"

if HOMEBREW_NO_AUTO_UPDATE=1 XDG_CONFIG_HOME="${AUDIT_CONFIG}" \
    brew help trust >/dev/null 2>&1; then
    HOMEBREW_NO_AUTO_UPDATE=1 XDG_CONFIG_HOME="${AUDIT_CONFIG}" \
        brew trust --formula "${TAP_NAME}/nginx-markdown-module"
fi
HOMEBREW_NO_AUTO_UPDATE=1 XDG_CONFIG_HOME="${AUDIT_CONFIG}" \
    brew audit --strict \
    "${TAP_NAME}/nginx-markdown-module"
echo "Homebrew formula strict audit passed"
