# NGINX Markdown for Agents - Top-level build/test entrypoints

UNAME_S := $(shell uname -s)
UNAME_M := $(shell uname -m)

ifeq ($(UNAME_S),Darwin)
  ifeq ($(UNAME_M),arm64)
    RUST_TARGET = aarch64-apple-darwin
  else ifeq ($(UNAME_M),x86_64)
    RUST_TARGET = x86_64-apple-darwin
  else
    $(error Unsupported Darwin architecture: $(UNAME_M))
  endif
else ifeq ($(UNAME_S),Linux)
  ifeq ($(UNAME_M),aarch64)
    RUST_TARGET = aarch64-unknown-linux-gnu
  else ifeq ($(UNAME_M),x86_64)
    RUST_TARGET = x86_64-unknown-linux-gnu
  else
    $(error Unsupported Linux architecture: $(UNAME_M))
  endif
else
  $(error Unsupported OS/architecture: $(UNAME_S)/$(UNAME_M))
endif

RUST_DIR := components/rust-converter
NGINX_MODULE_DIR := components/nginx-module
NGINX_TEST_DIR := $(NGINX_MODULE_DIR)/tests
CORPUS_DIR := tests/corpus
RUST_LIB := $(RUST_DIR)/target/$(RUST_TARGET)/release/libnginx_markdown_converter.a
RUST_HEADER := $(RUST_DIR)/include/markdown_converter.h
NGINX_HEADER := $(NGINX_MODULE_DIR)/src/markdown_converter.h

.PHONY: all build rust-lib rust-lib-debug copy-headers check-headers \
        test test-rust test-nginx-unit test-nginx-integration test-e2e test-all \
        docs-check verify-large-e2e verify-huge-native-e2e verify-huge-allowed-native-e2e \
        verify-chunked-native-e2e verify-chunked-native-e2e-smoke verify-chunked-native-e2e-stress \
        clean help

all: build

build: rust-lib copy-headers
	@echo "Build complete for $(RUST_TARGET)"
	@echo "Rust library: $(RUST_LIB)"

rust-lib:
	@echo "Building Rust library for $(RUST_TARGET)..."
	cd $(RUST_DIR) && cargo build --target $(RUST_TARGET) --release
	@echo "Generating C header with cbindgen..."
	cd $(RUST_DIR) && mkdir -p include && cbindgen --config cbindgen.toml --crate nginx-markdown-converter --output include/markdown_converter.h

rust-lib-debug:
	@echo "Building Rust library (debug) for $(RUST_TARGET)..."
	cd $(RUST_DIR) && cargo build --target $(RUST_TARGET)

copy-headers:
	@echo "Copying headers to nginx module source..."
	cp $(RUST_HEADER) $(NGINX_HEADER)

check-headers:
	@cmp -s $(RUST_HEADER) $(NGINX_HEADER) && echo "Headers are in sync" || (echo "Header mismatch: run 'make copy-headers'" && exit 1)

# Default smoke test
# keeps feedback fast for local development
test: build
	@$(MAKE) -C $(NGINX_TEST_DIR) unit-smoke

test-rust:
	cd $(RUST_DIR) && cargo test --all

test-nginx-unit:
	$(MAKE) -C $(NGINX_TEST_DIR) unit

test-nginx-integration:
	$(MAKE) -C $(NGINX_TEST_DIR) integration-c
	$(MAKE) -C $(NGINX_TEST_DIR) integration-nginx

test-e2e:
	$(MAKE) -C $(NGINX_TEST_DIR) e2e

test-all: build test-rust test-nginx-unit

docs-check:
	python3 tools/docs/check_docs.py

verify-large-e2e:
	./tools/e2e/verify_large_markdown_response_e2e.sh

verify-huge-native-e2e:
	./tools/e2e/verify_huge_body_native_e2e.sh

verify-huge-allowed-native-e2e:
	./tools/e2e/verify_huge_body_allowed_native_e2e.sh

verify-chunked-native-e2e:
	./tools/e2e/verify_chunked_streaming_native_e2e.sh

verify-chunked-native-e2e-smoke:
	./tools/e2e/verify_chunked_streaming_native_e2e.sh --profile smoke

verify-chunked-native-e2e-stress:
	./tools/e2e/verify_chunked_streaming_native_e2e.sh --profile stress

clean:
	cd $(RUST_DIR) && cargo clean
	$(MAKE) -C $(NGINX_TEST_DIR) clean || true
	find $(NGINX_MODULE_DIR) -name '*.dSYM' -type d -prune -exec rm -rf {} +

help:
	@echo "NGINX Markdown for Agents - Build/Test"
	@echo ""
	@echo "Targets:"
	@echo "  build                    - Build Rust library + sync header"
	@echo "  test                     - Fast smoke tests"
	@echo "  test-rust                - Run Rust test suite"
	@echo "  test-nginx-unit          - Run nginx C unit tests"
	@echo "  test-nginx-integration   - Run integration tests"
	@echo "  test-e2e                 - Run end-to-end tests"
	@echo "  test-all                 - Run build + rust + unit tests"
	@echo "  docs-check               - Validate documentation links/style"
	@echo "  clean                    - Clean build artifacts"
