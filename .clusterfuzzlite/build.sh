#!/bin/bash -eu
# ClusterFuzzLite build script for nginx-markdown-for-agents fuzz targets.
#
# This script is Linux-only and runs exclusively inside the ClusterFuzzLite CI
# container (gcr.io/oss-fuzz-base/base-builder-rust). It does NOT need to be
# compatible with macOS bash 3.2 or any non-Linux environment.
#
# Requirements: 1.1, 1.3, 1.4

set -o pipefail

cd components/rust-converter

# Auto-discover all fuzz targets by globbing fuzz_targets/*.rs.
# New targets are picked up automatically — no hardcoded list required.
for target_file in fuzz/fuzz_targets/*.rs; do
    target_name=$(basename "${target_file}" .rs)

    # Build the fuzz target with cargo-fuzz (requires nightly)
    cargo +nightly fuzz build "${target_name}"

    # Copy the compiled binary to $OUT/
    cp "fuzz/target/x86_64-unknown-linux-gnu/release/${target_name}" "${OUT}/"
    chmod +x "${OUT}/${target_name}"

    # Package seed corpus if the directory exists
    corpus_dir="fuzz/corpus/${target_name}"
    if [ -d "${corpus_dir}" ]; then
        zip -j "${OUT}/${target_name}_seed_corpus.zip" "${corpus_dir}"/*
    fi
done
