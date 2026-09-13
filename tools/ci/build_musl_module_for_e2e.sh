#!/bin/sh
# Build the module inside the same Alpine release the end-to-end checks load it
# into.  A module compiled on the GNU runner cannot load in those images, so the
# release-candidate gate would never produce real evidence.
#
# Runs INSIDE a rust:alpine container with the repository mounted at /src:
#   docker run --rm -v "$PWD:/src" -w /src -e NGINX_VERSION=1.30.4 \
#     -e RUST_TARGET=x86_64-unknown-linux-musl \
#     -e NGX_MARKDOWN_RUST_FEATURES=streaming,prune_noise_regions \
#     rust:<version>-alpine<release> sh /src/tools/ci/build_musl_module_for_e2e.sh
set -eux

apk add --no-cache build-base brotli-dev curl gzip openssl-dev pcre-dev perl zlib-dev

curl -fsSL -o /tmp/nginx.tar.gz "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz"
sh /src/packaging/scripts/verify-checksum.sh \
  -f /tmp/nginx.tar.gz -c /src/packaging/checksums.sha256 -i "nginx-${NGINX_VERSION}"

tar xzf /tmp/nginx.tar.gz -C /tmp

# The module configure looks for the converter archive built for the target it is
# told about, so build that archive first.
cd /src/components/rust-converter
cargo build --release --locked --target "${RUST_TARGET}" \
  --features "${NGX_MARKDOWN_RUST_FEATURES}"

cd "/tmp/nginx-${NGINX_VERSION}"
./configure --with-compat --add-dynamic-module=/src/components/nginx-module
make -j"$(nproc)" build
test -f objs/ngx_http_markdown_filter_module.so
cp objs/ngx_http_markdown_filter_module.so /src/build/ngx_http_markdown_filter_module.so
