# NGINX Markdown for Agents - Top-level build/test entrypoints

UNAME_S := $(shell uname -s)
UNAME_M := $(shell uname -m)

LINUX_LIBC := $(shell if command -v ldd >/dev/null 2>&1 && ldd --version 2>&1 | grep -qi musl; then echo musl; elif command -v ldd >/dev/null 2>&1 && ldd /bin/sh 2>&1 | grep -qi musl; then echo musl; else echo gnu; fi)

ifeq ($(UNAME_S),Darwin)
  MACOS_MIN_VERSION ?= 11.0
  MACOSX_DEPLOYMENT_TARGET ?= $(MACOS_MIN_VERSION)
  export MACOSX_DEPLOYMENT_TARGET
endif

ifndef RUST_TARGET
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
      ifeq ($(LINUX_LIBC),musl)
        RUST_TARGET = aarch64-unknown-linux-musl
      else
        RUST_TARGET = aarch64-unknown-linux-gnu
      endif
    else ifeq ($(UNAME_M),x86_64)
      ifeq ($(LINUX_LIBC),musl)
        RUST_TARGET = x86_64-unknown-linux-musl
      else
        RUST_TARGET = x86_64-unknown-linux-gnu
      endif
    else
      $(error Unsupported Linux architecture: $(UNAME_M))
    endif
  else
    $(error Unsupported OS/architecture: $(UNAME_S)/$(UNAME_M))
  endif
endif

RUST_DIR := components/rust-converter
NGINX_MODULE_DIR := components/nginx-module
NGINX_TEST_DIR := $(NGINX_MODULE_DIR)/tests
CORPUS_DIR := tests/corpus
RUST_LIB := $(RUST_DIR)/target/$(RUST_TARGET)/release/libnginx_markdown_converter.a
RUST_HEADER := $(RUST_DIR)/include/markdown_converter.h
NGINX_HEADER := $(NGINX_MODULE_DIR)/src/markdown_converter.h

.PHONY: all build rust-lib rust-lib-debug copy-headers check-headers \
        test test-rust test-rust-doc test-nginx-unit test-nginx-unit-streaming test-nginx-unit-clang-smoke test-nginx-unit-sanitize-smoke \
        test-nginx-integration test-e2e test-all test-rust-fuzz-smoke sonar-compile-db \
        test-benchmark test-benchmark-compare test-benchmark-summary \
        harness-check harness-check-full \
        docs-check license-check release-gates-check release-gates-check-legacy release-gates-check-strict \
        verify-large-e2e verify-huge-native-e2e verify-huge-allowed-native-e2e \
        verify-chunked-native-e2e verify-chunked-native-e2e-smoke verify-chunked-native-e2e-stress \
        verify-streaming-failure-cache-e2e \
        verify-streaming-failure-cache-e2e-plan \
        test-rust-streaming \
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
	cd $(RUST_DIR) && cargo build --release --example perf_baseline
	cd $(RUST_DIR) && cargo test --all
	cd $(RUST_DIR) && cargo test --doc --all-features

test-rust-streaming:
	cd $(RUST_DIR) && cargo test --features streaming

test-rust-doc:
	@echo "Running Rust doctests (all features)..."
	cd $(RUST_DIR) && cargo test --doc --all-features

test-rust-fuzz-smoke:
	cd $(RUST_DIR) && cargo +nightly fuzz run parser_html -- -max_total_time=5
	cd $(RUST_DIR) && cargo +nightly fuzz run ffi_convert -- -max_total_time=5
	cd $(RUST_DIR) && cargo +nightly fuzz run security_validator -- -max_total_time=5
	cd $(RUST_DIR) && cargo +nightly fuzz run fuzz_streaming_no_panic -- -max_total_time=5
	cd $(RUST_DIR) && cargo +nightly fuzz run fuzz_streaming_chunk_split -- -max_total_time=5

test-nginx-unit:
	$(MAKE) -C $(NGINX_TEST_DIR) unit

test-nginx-unit-streaming:
	$(MAKE) -C $(NGINX_TEST_DIR) unit-streaming

test-nginx-unit-clang-smoke:
	$(MAKE) -C $(NGINX_TEST_DIR) unit-clang-smoke

test-nginx-unit-sanitize-smoke:
	$(MAKE) -C $(NGINX_TEST_DIR) unit-sanitize-smoke

test-nginx-integration:
	$(MAKE) -C $(NGINX_TEST_DIR) integration-c
	$(MAKE) -C $(NGINX_TEST_DIR) integration-nginx

test-e2e:
	$(MAKE) -C $(NGINX_TEST_DIR) e2e

test-all: build test-rust test-nginx-unit

sonar-compile-db:
	./tools/sonar/generate_compile_commands.sh

# Corpus benchmark targets
CORPUS_CONVERTER_BIN := tools/corpus/test-corpus-conversion/target/release/test-corpus-conversion
CORPUS_REPORT := perf/reports/corpus-report.json
CORPUS_BASELINE := perf/baselines/corpus-baseline.json
CORPUS_VERDICT := perf/reports/corpus-verdict.json

test-benchmark:
	@echo "Validating corpus metadata..."
	tools/corpus/validate_corpus.sh
	@echo "Building test-corpus-conversion binary..."
	cd tools/corpus/test-corpus-conversion && cargo build --release --quiet
	@echo "Running corpus benchmark..."
	python3 tools/perf/run_corpus_benchmark.py \
		--corpus-dir $(CORPUS_DIR) \
		--converter-bin $(CORPUS_CONVERTER_BIN) \
		--output $(CORPUS_REPORT) \
		--examples-dir perf/reports/examples

test-benchmark-compare:
	python3 tools/perf/compare_reports.py \
		--baseline $(CORPUS_BASELINE) \
		--current $(CORPUS_REPORT) \
		--thresholds perf/quality-thresholds.json \
		--output $(CORPUS_VERDICT)

test-benchmark-summary:
	python3 tools/perf/format_pr_summary.py \
		--report $(CORPUS_REPORT)

docs-check-base:
	python3 tools/docs/check_docs.py
	python3 tools/docs/check_packaging_docs.py
	python3 tools/docs/check_packaging_consistency.py

docs-check: docs-check-base
	python3 tools/harness/check_harness_sync.py

harness-check:
	python3 tools/harness/check_harness_sync.py

harness-check-full:
	$(MAKE) docs-check-base
	python3 tools/harness/check_harness_sync.py --full
	$(MAKE) release-gates-check

license-check:
	python3 tools/ci/check_c_licenses.py
	python3 tools/ci/check_rust_licenses.py
	python3 tools/ci/check_third_party_notices.py

release-gates-check:
	python3 tools/release_gates/validate_release_gates.py
	python3 tools/release_gates/validate_naming.py

release-gates-check-legacy:
	python3 tools/release/validate_release_gates.py

release-gates-check-strict:
	python3 tools/release_gates/validate_release_gates.py --mode strict
	python3 tools/release_gates/validate_naming.py

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

verify-streaming-failure-cache-e2e:
	./tools/e2e/verify_streaming_failure_cache_e2e.sh $(E2E_ARGS)

verify-streaming-failure-cache-e2e-plan:
	./tools/e2e/verify_streaming_failure_cache_e2e.sh --plan

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
	@echo "  test-rust                - Run Rust test suite (unit + doctests)"
	@echo "  test-rust-streaming      - Run Rust streaming feature tests"
	@echo "  test-rust-doc            - Run Rust doctests only (all features)"
	@echo "  test-rust-fuzz-smoke     - Run short cargo-fuzz smoke checks"
	@echo "  test-nginx-unit          - Run nginx C unit tests"
	@echo "  test-nginx-unit-streaming - Run nginx C streaming unit tests"
	@echo "  test-nginx-unit-clang-smoke - Run nginx C smoke tests with clang"
	@echo "  test-nginx-unit-sanitize-smoke - Run nginx C smoke tests with ASan/UBSan"
	@echo "  test-nginx-integration   - Run integration tests"
	@echo "  test-e2e                 - Run end-to-end tests"
	@echo "  verify-streaming-failure-cache-e2e - Run streaming failure/cache e2e tests"
	@echo "  verify-streaming-failure-cache-e2e-plan - Print test plan only (no NGINX_BIN required)"
	@echo "  test-all                 - Run build + rust + unit tests"
	@echo "  sonar-compile-db         - Generate compile_commands.json for SonarQube for VS Code C/C++ analysis"
	@echo "  test-benchmark           - Run corpus benchmark and produce Unified Report"
	@echo "  test-benchmark-compare   - Compare corpus reports (baseline vs current)"
	@echo "  test-benchmark-summary   - Generate PR benchmark summary from latest report"
	@echo "  harness-check            - Validate harness truth surfaces and optional local adapters"
	@echo "  harness-check-full       - Run full harness validation plus docs/release checks"
	@echo "  docs-check               - Validate documentation links/style"
	@echo "  license-check            - Verify license policy and THIRD-PARTY-NOTICES coverage"
	@echo "  release-gates-check      - Validate 0.5.0 release gate framework (spec #12 deliverables)"
	@echo "  release-gates-check-legacy - Validate 0.4.0 release gate documents"
	@echo "  release-gates-check-strict - Validate all sub-specs #12-#18 for full compliance"
	@echo "  clean                    - Clean build artifacts"
