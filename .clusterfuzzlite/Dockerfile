# ClusterFuzzLite build environment for nginx-markdown-for-agents fuzz targets.
#
# This Dockerfile is Linux-only and runs exclusively inside ClusterFuzzLite CI
# containers. It does NOT require macOS compatibility.

FROM gcr.io/oss-fuzz-base/base-builder-rust

# Install Rust nightly toolchain (required by cargo-fuzz)
RUN rustup toolchain install nightly --profile minimal

# Install cargo-fuzz
RUN cargo +nightly install cargo-fuzz

# Set working directory and copy source
WORKDIR /src
COPY . /src
