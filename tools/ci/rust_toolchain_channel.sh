#!/usr/bin/env bash
# Print the Rust channel pinned by rust-toolchain.toml.
#
# rust-toolchain.toml is the single source of truth for the stable toolchain.
# `dtolnay/rust-toolchain` cannot read it -- the `toolchain` input is required
# and the action drives `rustup toolchain install` and `rustup default` from
# that input alone -- so every workflow resolved the channel by hand and a bump
# in one file could silently leave the workflows on another. This script is the
# single read both the workflows and the drift guard use.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MANIFEST="${REPO_ROOT}/rust-toolchain.toml"

if [[ ! -f "${MANIFEST}" ]]; then
    echo "ERROR: rust-toolchain.toml not found at ${MANIFEST}" >&2
    exit 1
fi

channel="$(sed -nE 's/^[[:space:]]*channel[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' \
    "${MANIFEST}" | head -1)"

if [[ -z "${channel}" ]]; then
    echo "ERROR: no channel pinned in ${MANIFEST}" >&2
    exit 1
fi

echo "${channel}"
