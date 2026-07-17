# ClusterFuzzLite build environment for nginx-markdown-for-agents fuzz targets.
#
# This Dockerfile is Linux-only and runs exclusively inside ClusterFuzzLite CI
# containers. It does NOT require macOS compatibility.

FROM gcr.io/oss-fuzz-base/base-builder-rust

ARG FUZZ_UID=1001
ARG FUZZ_GID=1001

# Install Rust nightly toolchain (required by cargo-fuzz)
RUN rustup toolchain install nightly --profile minimal

# Install cargo-fuzz
RUN cargo +nightly install cargo-fuzz

# Match the GitHub-hosted runner UID/GID used for ClusterFuzzLite bind mounts.
# The OSS-Fuzz base image keeps /src, /work, and /out as its mount contract;
# /rust is the shared Cargo/rustup home provided by base-builder-rust. The
# compile wrapper stages Rust standard-library sources under /rustc and
# installs libFuzzer at a fixed /usr/lib path after startup, so pre-create only
# those paths with non-root ownership.
RUN groupadd --gid "${FUZZ_GID}" fuzzer \
    && useradd --uid "${FUZZ_UID}" --gid "${FUZZ_GID}" \
        --create-home --shell /bin/bash fuzzer \
    && chown -R fuzzer:fuzzer /rust /src /work /out \
    && mkdir -p /rustc \
    && chown fuzzer:fuzzer /rustc \
    && touch /usr/lib/libFuzzingEngine.a \
    && chown fuzzer:fuzzer /usr/lib/libFuzzingEngine.a

WORKDIR /src
COPY --chown=fuzzer:fuzzer . /src

USER fuzzer
