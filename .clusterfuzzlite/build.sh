#!/bin/bash -eu
# ClusterFuzzLite build script for nginx-markdown-for-agents fuzz targets.
#
# This script is Linux-only and runs exclusively inside the ClusterFuzzLite CI
# container (gcr.io/oss-fuzz-base/base-builder-rust). It does NOT need to be
# compatible with macOS bash 3.2 or any non-Linux environment.
#
# Requirements: 1.1, 1.3, 1.4

set -o pipefail

if [[ -z "${OUT:-}" ]]; then
    echo "ERROR: OUT is not set" >&2
    exit 1
fi
if [[ ! -d "${OUT}" || ! -w "${OUT}" ]]; then
    echo "ERROR: OUT is not a writable directory: ${OUT}" >&2
    exit 1
fi
if [[ ! -w components/rust-converter ]]; then
    echo "ERROR: Rust converter source is not writable" >&2
    exit 1
fi

cd components/rust-converter

# Auto-discover registered fuzz targets, not every helper .rs file.
# Helper modules such as streaming_utils.rs are intentionally not binaries.
cargo +nightly fuzz list | while IFS= read -r target_name; do
    if [[ -z "${target_name}" ]]; then
        continue
    fi

    # Build the fuzz target with cargo-fuzz (requires nightly)
    cargo +nightly fuzz build "${target_name}"

    # Copy the compiled binary to $OUT/
    binary=$(find fuzz/target -type f -name "${target_name}" | grep '/release/' | head -n 1 || true)
    if [[ -z "${binary}" || ! -f "${binary}" ]]; then
        echo "ERROR: built fuzz target not found: ${target_name}" >&2
        exit 1
    fi

    cp "${binary}" "${OUT}/"
    chmod +x "${OUT}/${target_name}"

    # Package seed corpus if the directory exists
    corpus_dir="fuzz/corpus/${target_name}"
    if [[ -d "${corpus_dir}" && -n "$(find "${corpus_dir}" -type f -print -quit)" ]]; then
        (cd "${corpus_dir}" && zip -qr "${OUT}/${target_name}_seed_corpus.zip" .)
    fi
done
