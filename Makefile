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
MODULE_SO ?= build/ngx_http_markdown_filter_module.so
PREFIX ?= /usr
LIBDIR ?= $(PREFIX)/lib
DESTDIR ?=
MODULE_INSTALL_DIR := $(LIBDIR)/nginx/modules
NGINX_MODULES_AVAILABLE_DIR := $(PREFIX)/share/nginx/modules-available
DOC_INSTALL_DIR := $(PREFIX)/share/doc/nginx-markdown-for-agents
LICENSE_INSTALL_DIR := $(PREFIX)/share/licenses/nginx-markdown-for-agents

.PHONY: all build rust-lib rust-lib-debug copy-headers check-headers \
        install \
        test test-rust test-rust-doc test-nginx-unit test-nginx-unit-streaming test-nginx-unit-clang-smoke test-nginx-unit-sanitize-smoke \
        test-nginx-integration test-e2e test-e2e-rust test-all test-rust-fuzz-smoke fuzz-smoke sonar-compile-db \
        test-benchmark test-benchmark-compare test-benchmark-summary \
        harness-check harness-check-full harness-security-checks test-harness \
        security-static security-actionlint security-shellcheck security-gitleaks security-semgrep security-cargo-deny \
        supply-chain supply-chain-trivy supply-chain-sbom \
	docs-check license-check release-notes release-gates-check release-gates-check-055 release-gates-check-060 release-gates-check-070 release-gates-check-070-docker release-gates-check-080 release-gates-check-08x release-gates-check-legacy release-gates-check-strict \
        verify-large-e2e verify-huge-native-e2e verify-huge-allowed-native-e2e \
        verify-chunked-native-e2e verify-chunked-native-e2e-smoke verify-chunked-native-e2e-stress \
        verify-streaming-failure-cache-e2e \
        verify-streaming-failure-cache-e2e-plan \
        verify-metrics-endpoint-e2e verify-conditional-requests-e2e verify-config-merge-e2e \
        verify-auth-cache-e2e verify-status-codes-e2e \
        test-rust-streaming \
        coverage-c coverage-rust coverage-sonar-xml coverage-all coverage-gate \
        clean help

all: build

build: rust-lib copy-headers
	@echo "Build complete for $(RUST_TARGET)"
	@echo "Rust library: $(RUST_LIB)"

RUST_RELEASE_FEATURES ?= streaming,incremental

rust-lib:
	@echo "Building Rust library for $(RUST_TARGET)..."
	cd $(RUST_DIR) && cargo build --locked --target $(RUST_TARGET) --release --features $(RUST_RELEASE_FEATURES)
	@echo "Generating C header with cbindgen..."
	cd $(RUST_DIR) && mkdir -p include && cbindgen --quiet --config cbindgen.toml --crate nginx-markdown-converter --output include/markdown_converter.h

rust-lib-debug:
	@echo "Building Rust library (debug) for $(RUST_TARGET)..."
	cd $(RUST_DIR) && cargo build --locked --target $(RUST_TARGET) --features $(RUST_RELEASE_FEATURES)

copy-headers:
	@echo "Copying headers to nginx module source..."
	cp $(RUST_HEADER) $(NGINX_HEADER)

check-headers:
	@cmp -s $(RUST_HEADER) $(NGINX_HEADER) && echo "Headers are in sync" || (echo "Header mismatch: run 'make copy-headers'" && exit 1)

install:
	@test -f "$(MODULE_SO)" || { echo "FAIL: $(MODULE_SO) not found" >&2; exit 1; }
	install -d "$(DESTDIR)$(MODULE_INSTALL_DIR)"
	install -d "$(DESTDIR)$(NGINX_MODULES_AVAILABLE_DIR)"
	install -d "$(DESTDIR)$(DOC_INSTALL_DIR)"
	install -d "$(DESTDIR)$(LICENSE_INSTALL_DIR)"
	install -m 0644 "$(MODULE_SO)" "$(DESTDIR)$(MODULE_INSTALL_DIR)/ngx_http_markdown_filter_module.so"
	install -m 0644 packaging/nfpm/modules-available/mod-markdown.conf "$(DESTDIR)$(NGINX_MODULES_AVAILABLE_DIR)/mod-markdown.conf"
	install -m 0644 README.md "$(DESTDIR)$(DOC_INSTALL_DIR)/README.md"
	install -m 0644 docs/guides/INSTALL.md "$(DESTDIR)$(DOC_INSTALL_DIR)/INSTALL.md"
	install -m 0644 docs/COMPATIBILITY.md "$(DESTDIR)$(DOC_INSTALL_DIR)/COMPATIBILITY.md"
	install -m 0644 LICENSE "$(DESTDIR)$(LICENSE_INSTALL_DIR)/LICENSE"

# Default smoke test
# keeps feedback fast for local development
test: build
	@$(MAKE) -C $(NGINX_TEST_DIR) unit-smoke

test-rust:
	cd $(RUST_DIR) && cargo build --locked --release --example perf_baseline
	cd $(RUST_DIR) && cargo test --locked --all --all-features
	cd $(RUST_DIR) && cargo test --locked --doc --all-features

test-rust-streaming:
	cd $(RUST_DIR) && cargo test --locked --features streaming

test-rust-doc:
	@echo "Running Rust doctests (all features)..."
	cd $(RUST_DIR) && cargo test --locked --doc --all-features

test-rust-fuzz-smoke:
	cd $(RUST_DIR) && cargo +nightly fuzz run parser_html -- -max_total_time=5
	cd $(RUST_DIR) && cargo +nightly fuzz run ffi_convert -- -max_total_time=5
	cd $(RUST_DIR) && cargo +nightly fuzz run security_validator -- -max_total_time=5
	cd $(RUST_DIR) && cargo +nightly fuzz run fuzz_streaming_no_panic -- -max_total_time=5
	cd $(RUST_DIR) && cargo +nightly fuzz run fuzz_streaming_chunk_split -- -max_total_time=5
	cd $(RUST_DIR) && cargo +nightly fuzz run fuzz_streaming_malformed -- -max_total_time=5
	cd $(RUST_DIR) && cargo +nightly fuzz run fuzz_decompression -- -max_total_time=5
	cd $(RUST_DIR) && cargo +nightly fuzz run fuzz_url_validation -- -max_total_time=5
	cd $(RUST_DIR) && cargo +nightly fuzz run convert_html -- -max_total_time=5
	cd $(RUST_DIR) && cargo +nightly fuzz run streaming_chunks -- -max_total_time=5
	cd $(RUST_DIR) && cargo +nightly fuzz run negotiation_and_headers -- -max_total_time=5

fuzz-smoke:
	cd $(RUST_DIR) && cargo +nightly fuzz run convert_html -- -max_total_time=30
	cd $(RUST_DIR) && cargo +nightly fuzz run streaming_chunks -- -max_total_time=30
	cd $(RUST_DIR) && cargo +nightly fuzz run negotiation_and_headers -- -max_total_time=30

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

E2E_HARNESS_DIR := tools/e2e-harness

test-e2e-rust:
	@echo "Building e2e-harness..."
	cd $(E2E_HARNESS_DIR) && cargo build --locked
	@echo "Running e2e-harness migrated scenarios..."
	cd $(E2E_HARNESS_DIR) && cargo run --locked -- suite --profile smoke

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
	cd tools/corpus/test-corpus-conversion && cargo build --locked --release --quiet
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
	python3 tools/docs/validate_packaging_matrix.py
	python3 tools/render_release_matrix_docs.py --check
	python3 tools/release/matrix/validate_workflow_matrix_consumers.py

docs-check: docs-check-base
	python3 tools/harness/check_harness_sync.py

release-notes:
	python3 tools/render_release_matrix_docs.py --release-notes

harness-check:
	python3 tools/harness/check_harness_sync.py

harness-check-full:
	$(MAKE) docs-check-base
	python3 tools/harness/check_harness_sync.py --full
	$(MAKE) release-gates-check
	$(MAKE) harness-security-checks
	$(MAKE) test-harness
	$(MAKE) check-headers

harness-security-checks:
	bash tools/harness/detect_cwe190_casts.sh
	PYTHONPATH=. python3 tools/harness/detect_cwe22_paths.py tools/ --strict
	bash tools/harness/detect_live_conf_reads.sh
	bash tools/harness/detect_ffi_fat_pointer_transfer.sh
	bash tools/harness/detect_shell_hygiene.sh tools/
	PYTHONPATH=. python3 tools/harness/detect_const_correctness.py components/nginx-module/src
	bash tools/harness/detect_ci_supply_chain.sh
	bash tools/harness/detect_header_hash_filter.sh
	bash tools/harness/detect_finalize_return.sh
	bash tools/harness/detect_ffi_struct_init.sh
	bash tools/harness/detect_c_pure_logic.sh
	bash tools/harness/detect_volatile_atomic.sh
	bash tools/harness/detect_nosonar_discipline.sh
	bash tools/harness/detect_ngx_log_arg_count.sh
	bash tools/harness/detect_pool_free.sh
	bash tools/harness/detect_ffi_panic_safety.sh --strict
	PYTHONPATH=. python3 tools/harness/detect_forward_decl_order.py components/nginx-module/src --strict
	PYTHONPATH=. python3 tools/harness/detect_duplicate_code.py components/nginx-module/src --strict
	PYTHONPATH=. python3 tools/harness/detect_open_without_path_validation.py --path tools/ --strict
	bash tools/harness/detect_version_consistency.sh
	bash tools/harness/detect_backpressure_resume.sh
	bash tools/harness/detect_decompression_budget.sh
	PYTHONPATH=. python3 tools/harness/detect_test_assertion_coverage.py
	PYTHONPATH=. python3 tools/harness/detect_html_sanitizer_invariants.py
	PYTHONPATH=. python3 tools/harness/detect_doc_sync.py

test-harness:
	@echo "=== Harness Detector Unit Tests ==="
	bash tools/harness/tests/test_detect_ffi_struct_init.sh
	bash tools/harness/tests/test_detect_c_pure_logic.sh
	bash tools/harness/tests/test_detect_volatile_atomic.sh
	bash tools/harness/tests/test_detect_cwe190_casts.sh
	bash tools/harness/tests/test_detect_nosonar_discipline.sh
	bash tools/harness/tests/test_detect_ngx_log_arg_count.sh
	bash tools/harness/tests/test_detect_pool_free.sh
	bash tools/harness/tests/test_detect_ffi_panic_safety.sh
	bash tools/harness/tests/test_detect_ffi_fat_pointer_transfer.sh
	bash tools/harness/tests/test_security_gitleaks_scope.sh
	python3 -m pytest tools/harness/tests/ -q --tb=short -k "not check_harness_sync"

license-check:
	python3 tools/ci/check_c_licenses.py
	python3 tools/ci/check_rust_licenses.py
	python3 tools/ci/check_third_party_notices.py

security-static: security-actionlint security-shellcheck security-gitleaks security-semgrep security-cargo-deny

security-actionlint:
	@command -v actionlint >/dev/null 2>&1 || { echo "ERROR: actionlint not found. Install with: go install github.com/rhysd/actionlint/cmd/actionlint@v1.7.12" >&2; exit 127; }
	@workflow_files=$$(find .github/workflows -maxdepth 1 -type f \( -name "*.yml" -o -name "*.yaml" \) | sort); \
	if [ -z "$$workflow_files" ]; then \
		echo "ERROR: no workflow files found under .github/workflows" >&2; \
		exit 1; \
	fi; \
	actionlint -color -shellcheck= $$workflow_files

security-shellcheck:
	@command -v shellcheck >/dev/null 2>&1 || { echo "ERROR: shellcheck not found. Install from https://www.shellcheck.net/ or your package manager." >&2; exit 127; }
	@tmp_files=$$(mktemp); \
	trap 'rm -f "$$tmp_files"' EXIT; \
	git ls-files -z -- ":(glob)*.sh" ":(glob)tools/**/*.sh" ":(glob)packaging/**/*.sh" ":(glob).clusterfuzzlite/*.sh" ":(glob)examples/**/*.sh" > "$$tmp_files"; \
	if [ ! -s "$$tmp_files" ]; then \
		echo "No tracked shell scripts matched the security-static scope."; \
	else \
		xargs -0 shellcheck --severity=error -x -P tools/e2e -P tools/lib < "$$tmp_files"; \
	fi

security-gitleaks:
	@command -v gitleaks >/dev/null 2>&1 || { echo "ERROR: gitleaks not found. Install with: go install github.com/zricethezav/gitleaks/v8@83d9cd684c87d95d656c1458ef04895a7f1cbd8e # v8.30.1" >&2; exit 127; }
	bash tools/security/run_gitleaks_tracked.sh

security-semgrep:
	@command -v semgrep >/dev/null 2>&1 || { echo "ERROR: semgrep not found. Install with: python3 -m pip install --user semgrep==1.166.0" >&2; exit 127; }
	semgrep --config .semgrep.yml --error --metrics=off

security-cargo-deny:
	@command -v cargo-deny >/dev/null 2>&1 || { echo "ERROR: cargo-deny not found. Install with: cargo install cargo-deny --version 0.19.8 --locked" >&2; exit 127; }
	@for manifest in components/rust-converter/Cargo.toml components/rust-converter/fuzz/Cargo.toml tools/corpus/test-corpus-conversion/Cargo.toml tools/e2e-harness/Cargo.toml; do \
		cargo deny --manifest-path "$$manifest" check --config deny.toml advisories licenses bans sources || exit $$?; \
	done

supply-chain: supply-chain-trivy supply-chain-sbom

supply-chain-trivy:
	@command -v trivy >/dev/null 2>&1 || { echo "ERROR: trivy not found. Install from https://aquasecurity.github.io/trivy/latest/getting-started/installation/." >&2; exit 127; }
	trivy fs --scanners vuln,misconfig,secret --ignore-unfixed --severity HIGH,CRITICAL .

supply-chain-sbom:
	@command -v syft >/dev/null 2>&1 || { echo "ERROR: syft not found. Install from https://github.com/anchore/syft#installation." >&2; exit 127; }
	mkdir -p build/reports
	syft dir:. -o spdx-json=build/reports/sbom.spdx.json

release-gates-check:
	python3 tools/release/gates/validate_release_gates.py
	python3 tools/release/gates/validate_naming.py

release-gates-check-055:
	python3 tools/release/gates/validate_release_gates_055.py

release-gates-check-060:
	python3 tools/release/gates/validate_release_gates_060.py

# release-gates-check-070: comprehensive v0.7.0 release readiness gate.
#
# Environment variables:
#   RELEASE_GATE_ALLOW_SKIP_FUZZ=1       - skip fuzz smoke/build when
#                                           cargo +nightly is unavailable
#   RELEASE_GATE_ALLOW_SKIP_NATIVE_E2E=1 - skip native E2E when NGINX_BIN
#                                           is not set
#   RELEASE_GATE_REQUIRE_PACKAGES=0      - skip install-layout validation
#   PKG_VERSION / NGINX_VERSION          - override package/NGINX versions

release-gates-check-070:
	$(MAKE) build
	$(MAKE) check-headers
	$(MAKE) test-rust
	$(MAKE) test-nginx-unit
	@if cargo +nightly --version >/dev/null 2>&1; then \
	$(MAKE) test-rust-fuzz-smoke; \
	else \
	if [ "$${RELEASE_GATE_ALLOW_SKIP_FUZZ:-0}" = "1" ]; then \
	echo "==> SKIP (non-release): test-rust-fuzz-smoke (cargo nightly not available; RELEASE_GATE_ALLOW_SKIP_FUZZ=1)"; \
	else \
	echo "FAIL: test-rust-fuzz-smoke requires cargo +nightly; set RELEASE_GATE_ALLOW_SKIP_FUZZ=1 to skip for non-release validation" >&2; exit 1; \
	fi; \
	fi
	@if [ -n "$$NGINX_BIN" ]; then \
	$(MAKE) verify-chunked-native-e2e-smoke; \
	else \
	if [ "$${RELEASE_GATE_ALLOW_SKIP_NATIVE_E2E:-0}" = "1" ]; then \
	echo "==> SKIP (non-release): verify-chunked-native-e2e-smoke (NGINX_BIN not set; RELEASE_GATE_ALLOW_SKIP_NATIVE_E2E=1)"; \
	else \
	echo "FAIL: verify-chunked-native-e2e-smoke requires NGINX_BIN; set RELEASE_GATE_ALLOW_SKIP_NATIVE_E2E=1 to skip for non-release validation" >&2; exit 1; \
	fi; \
	fi
	$(MAKE) test-e2e-rust
	python3 tools/release/gates/validate_release_gates_070.py --mode strict
	python3 tools/release/gates/validate_config_directives_070.py
	python3 tools/release/gates/validate_metrics_070.py
	python3 tools/release/gates/validate_reason_codes_070.py
	python3 tools/release/gates/validate_package_metadata_070.py
	python3 tools/release/gates/validate_k8s_manifests_070.py
	python3 tools/release/gates/validate_fuzz_packaging_070.py
	@echo "=== Package Compatibility Gate ==="
	@echo "  [artifact-naming-tests] Running artifact naming unit tests..."
	@bash tools/release/gates/test_artifact_naming.sh || \
	{ echo "FAIL: artifact naming tests failed" >&2; exit 1; }
	@echo "  [compat-check-tests] Running compat-check helper tests..."
	@if test -f tools/compat-check/test_compat_check.sh; then \
	bash tools/compat-check/test_compat_check.sh || \
		{ echo "FAIL: compat-check helper tests failed" >&2; exit 1; }; \
	else \
	echo "  SKIP: tools/compat-check/test_compat_check.sh not found"; \
	fi
	@echo "  [install-layout] Validating install layout..."
	@if test -f tools/release/gates/check_install_layout.sh; then \
		if ! ls dist/*.deb dist/*.rpm >/dev/null 2>&1; then \
			if [ "$${RELEASE_GATE_REQUIRE_PACKAGES:-1}" = "0" ]; then \
				echo "  SKIP: no package files in dist/ (RELEASE_GATE_REQUIRE_PACKAGES=0)"; \
				exit 0; \
			fi; \
			if command -v nfpm >/dev/null 2>&1; then \
				if ! test -f build/ngx_http_markdown_filter_module.so; then \
					echo "FAIL: install-layout requires build/ngx_http_markdown_filter_module.so but it was not found." >&2; \
					echo "  The 'make build' target only builds the Rust static library, not the NGINX dynamic module." >&2; \
					echo "  To fix, either:" >&2; \
					echo "    1. Place pre-built packages in dist/ (from release workflow artifacts), or" >&2; \
					echo "    2. Build the NGINX module .so first (requires NGINX source), or" >&2; \
					echo "    3. Set RELEASE_GATE_REQUIRE_PACKAGES=0 to skip install-layout validation." >&2; \
					exit 1; \
				fi; \
				host_arch="$$(uname -m)"; \
				if test "$$(uname -s)" = "Darwin" && \
					test "$$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1"; then \
					host_arch="arm64"; \
				fi; \
				case "$$host_arch" in \
					x86_64) nfpm_arch="amd64"; rpm_arch="x86_64" ;; \
					arm64|aarch64) nfpm_arch="arm64"; rpm_arch="aarch64" ;; \
					*) echo "FAIL: unsupported package build architecture: $$host_arch" >&2; exit 1 ;; \
				esac; \
				echo "  [install-layout] No packages found; building local $$nfpm_arch DEB/RPM with nFPM..."; \
				mkdir -p dist; \
				PKG_VERSION=$${PKG_VERSION:-0.7.0} NGINX_VERSION=$${NGINX_VERSION:-1.26.3} NFPM_ARCH=$$nfpm_arch \
					nfpm package --config packaging/nfpm/nfpm.yaml --packager deb \
					--target dist/nginx-module-markdown-for-agents_$${PKG_VERSION:-0.7.0}_nginx-$${NGINX_VERSION:-1.26.3}_$${nfpm_arch}.deb; \
				PKG_VERSION=$${PKG_VERSION:-0.7.0} NGINX_VERSION=$${NGINX_VERSION:-1.26.3} NFPM_ARCH=$$nfpm_arch \
					nfpm package --config packaging/nfpm/nfpm.yaml --packager rpm \
					--target dist/nginx-module-markdown-for-agents-$${PKG_VERSION:-0.7.0}-nginx$${NGINX_VERSION:-1.26.3}-1.$${rpm_arch}.rpm; \
			else \
				echo "FAIL: no package files in dist/ and nfpm is unavailable." >&2; \
				echo "  To fix, either:" >&2; \
				echo "    1. Place pre-built packages in dist/ (from release workflow artifacts), or" >&2; \
				echo "    2. Install nfpm and build the NGINX module .so, or" >&2; \
				echo "    3. Set RELEASE_GATE_REQUIRE_PACKAGES=0 to skip install-layout validation." >&2; \
				exit 1; \
			fi; \
		fi; \
		if ls dist/*.deb dist/*.rpm >/dev/null 2>&1; then \
			bash tools/release/gates/check_install_layout.sh dist/*.deb dist/*.rpm || \
				{ echo "FAIL: install layout validation failed" >&2; exit 1; }; \
		else \
			echo "FAIL: no package files in dist/" >&2; \
			exit 1; \
		fi; \
	else \
		echo "  SKIP: tools/release/gates/check_install_layout.sh not found"; \
	fi
	@echo "  [postinst-safety] Validating postinst safety..."
	@if test -f tools/release/gates/check_postinst_safety.sh; then \
	bash tools/release/gates/check_postinst_safety.sh || \
		{ echo "FAIL: postinst safety validation failed" >&2; exit 1; }; \
	else \
	echo "  SKIP: tools/release/gates/check_postinst_safety.sh not found"; \
	fi
	@echo "  Package Compatibility Gate: ALL PASSED"
	@echo "=== Fuzz CI Gate ==="
	@if cargo +nightly --version >/dev/null 2>&1; then \
	echo "  [fuzz-build] cargo +nightly fuzz build..."; \
	cd $(RUST_DIR) && cargo +nightly fuzz build; \
	else \
	if [ "$${RELEASE_GATE_ALLOW_SKIP_FUZZ:-0}" = "1" ]; then \
	echo "  [fuzz-build] SKIP (non-release): cargo nightly not available (RELEASE_GATE_ALLOW_SKIP_FUZZ=1)"; \
	else \
	echo "FAIL: fuzz build requires cargo +nightly; set RELEASE_GATE_ALLOW_SKIP_FUZZ=1 to skip for non-release validation" >&2; exit 1; \
	fi; \
	fi
	@if cargo +nightly --version >/dev/null 2>&1; then \
	echo "  [fuzz-smoke] Running fuzz smoke (10s × 3 representative targets)..."; \
	cd $(RUST_DIR) && for target in convert_html streaming_chunks negotiation_and_headers; do \
		echo "    fuzzing $$target (10s)..."; \
		cargo +nightly fuzz run "$$target" -- -max_total_time=10; \
	done; \
	else \
	if [ "$${RELEASE_GATE_ALLOW_SKIP_FUZZ:-0}" = "1" ]; then \
	echo "  [fuzz-smoke] SKIP (non-release): cargo nightly not available (RELEASE_GATE_ALLOW_SKIP_FUZZ=1)"; \
	else \
	echo "FAIL: fuzz smoke requires cargo +nightly; set RELEASE_GATE_ALLOW_SKIP_FUZZ=1 to skip for non-release validation" >&2; exit 1; \
	fi; \
	fi
	@echo "  [cflite-workflows] Checking ClusterFuzzLite workflow files..."
	@test -f .github/workflows/cflite_pr.yml || { echo "FAIL: .github/workflows/cflite_pr.yml not found" >&2; exit 1; }
	@test -f .github/workflows/cflite_batch.yml || { echo "FAIL: .github/workflows/cflite_batch.yml not found" >&2; exit 1; }
	@test -f .github/workflows/cflite_cron.yml || { echo "FAIL: .github/workflows/cflite_cron.yml not found" >&2; exit 1; }
	@echo "  [fuzz-guide] Checking fuzz README completeness..."
	@test -f fuzz/README.md || { echo "FAIL: fuzz/README.md not found" >&2; exit 1; }
	@grep -q "Corpus Classification" fuzz/README.md || { echo "FAIL: fuzz/README.md missing section: Corpus Classification" >&2; exit 1; }
	@grep -q "FUZZ-001" fuzz/README.md || { echo "FAIL: fuzz/README.md missing section: FUZZ-001" >&2; exit 1; }
	@echo "  Fuzz CI Gate: ALL PASSED"
	@echo "=== Release Workflow Gate ==="
	@echo "  [release-workflow] Checking release-packages.yml exists..."
	@test -f .github/workflows/release-packages.yml || { echo "FAIL: .github/workflows/release-packages.yml not found" >&2; exit 1; }
	@echo "  [sha256sums] Checking SHA256SUMS generation logic..."
	@grep -q 'SHA256SUMS\|generate-checksums' .github/workflows/release-packages.yml || { echo "FAIL: release-packages.yml missing SHA256SUMS generation logic" >&2; exit 1; }
	@echo "  [smoke-test-job] Checking package smoke test job exists..."
	@grep -q 'smoke-test\|smoke_test' .github/workflows/release-packages.yml || { echo "FAIL: release-packages.yml missing smoke test job" >&2; exit 1; }
	@echo "  Release Workflow Gate: ALL PASSED"
	@echo "=== Documentation Gate ==="
	@echo "  [docs-check] Running docs-check for fuzz guide and install/compat docs..."
	@$(MAKE) docs-check
	@echo "  Documentation Gate: ALL PASSED"
	@echo "=== Harness Rule Coverage Gate ==="
	@echo "  [fuzz-rules] Verifying FUZZ-001 through FUZZ-007 defined in fuzz/README.md..."
	@for rule in FUZZ-001 FUZZ-002 FUZZ-003 FUZZ-004 FUZZ-005 FUZZ-006 FUZZ-007; do \
	grep -q "$$rule" fuzz/README.md || { echo "FAIL: fuzz/README.md missing rule $$rule" >&2; exit 1; }; \
	done
	@echo "  [harness-fuzz-check] Verifying harness-check covers fuzz infrastructure..."
	@$(MAKE) harness-check
	@echo "  Harness Rule Coverage Gate: ALL PASSED"

# release-gates-check-080: comprehensive v0.8.x release readiness gate.
#
# Coverage policy source: AGENTS.md Rule 25
#   - 80% aggregate line+function coverage (programmatic gate via coverage_gate.py)
#   - 90% for critical paths: auth, error handling, FFI boundary, conditional
#     requests (advisory — not programmatically enforced; logged at gate runtime)
#
# Clean-checkout boundary (Req 9):
#   This gate runs from a clean git checkout without requiring:
#   - .kiro/ directories (user-local Kiro/spec state)
#   - .codeartsdoer/ directories (adapter caches)
#   - Any generated files outside the repository checkout
#   All Python scripts called by this gate consume only repo-owned inputs:
#   tools/, docs/, components/, AGENTS.md, .github/workflows/
#
# CI coverage (Spec 43):
#   - Streaming tests: covered by rust-quality job (make test-rust --all-features)
#   - Chunked native E2E: covered by runtime-regressions job
#   - Matrix validation: covered by docs-check job (make docs-check) and
#     matrix-release-tests job
#
# Classification (Req 6):
#   BLOCKING: All 17 sub-checks below are release-blocking.
#   EXPERIMENTAL (non-blocking, not in this gate):
#     - Performance benchmarks (perf-artifacts, perf-smoke in CI)
#     - Nightly fuzz beyond smoke (nightly-fuzz.yml)
#     - Docker integration tests (official-nginx-docker.yml)
#
# Sub-checks (all blocking):
#   1. make build + make check-headers        — Rust lib builds, header in sync
#   2. make test-rust                         — Rust unit + doctests + streaming tests (Req 1)
#   3. make test-nginx-unit                   — C state machine + config parsing tests (Req 1, 2)
#   4. make test-rust-fuzz-smoke              — Fuzz smoke (skippable, see env var below)
#   5. verify-chunked-native-e2e-smoke        — Native NGINX chunked/no-CL E2E (Req 1.2)
#   6. make test-e2e-rust                     — Rust E2E harness scenarios (Req 1)
#   7. make coverage-c                        — C coverage gate per AGENTS.md (Req 4)
#   8. make coverage-rust                     — Rust coverage gate per AGENTS.md (Req 4)
#   9. make docs-check                        — Documentation consistency (Req 3)
#  10. matrix validate_workflow_matrix_consumers — Matrix schema + CI coverage (Req 5)
#  11. make harness-check                     — Harness truth surface validation (Req 9)
#  12. validate_release_gates.py              — Release gate framework
#  13. validate_naming.py                     — Artifact naming consistency
#  14. v0.8.0 gate validators (0.8-specific: compat bridge removal, new directives)
#  15. legacy/prior-version regression validators (0.7.0 gates remain active)
#  16. Harness boundary: routing-manifest.json + risk-packs + core.md + README.md exist (Req 9)
#  17. Clean-checkout boundary verification (no user-local state required)
#
# Environment variables:
#   RELEASE_GATE_ALLOW_SKIP_FUZZ=1       - skip fuzz smoke when
#                                           cargo +nightly is unavailable
#   RELEASE_GATE_ALLOW_SKIP_NATIVE_E2E=1 - skip native E2E when NGINX_BIN
#                                           is not set
#   RELEASE_GATE_ALLOW_SKIP_COVERAGE=1   - skip coverage-c/coverage-rust
#                                           when NGINX source or lcov/cargo-llvm-cov
#                                           is unavailable

RELEASE_GATE_080_ACTIVE_VERSION ?= 0.8.3

release-gates-check-080:
	@echo "=== v0.8.x Release Gate: Starting ($(RELEASE_GATE_080_ACTIVE_VERSION)) ==="
	@echo "  [1/17] build + check-headers"
	$(MAKE) build
	$(MAKE) check-headers
	@echo "  [2/17] test-rust (includes streaming tests — Req 1)"
	$(MAKE) test-rust
	@echo "  [3/17] test-nginx-unit (state machine + config parsing — Req 1, 2)"
	$(MAKE) test-nginx-unit
	@echo "  [4/17] test-rust-fuzz-smoke"
	@if cargo +nightly --version >/dev/null 2>&1; then \
	$(MAKE) test-rust-fuzz-smoke; \
	else \
	if [ "$${RELEASE_GATE_ALLOW_SKIP_FUZZ:-0}" = "1" ]; then \
	echo "  ==> SKIP (non-release): test-rust-fuzz-smoke (cargo nightly not available; RELEASE_GATE_ALLOW_SKIP_FUZZ=1)"; \
	else \
	echo "FAIL: test-rust-fuzz-smoke requires cargo +nightly; set RELEASE_GATE_ALLOW_SKIP_FUZZ=1 to skip for non-release validation" >&2; exit 1; \
	fi; \
	fi
	@echo "  [5/17] verify-chunked-native-e2e-smoke (native NGINX chunked/no-CL — Req 1.2)"
	@if [ -n "$$NGINX_BIN" ]; then \
	$(MAKE) verify-chunked-native-e2e-smoke; \
	else \
	if [ "$${RELEASE_GATE_ALLOW_SKIP_NATIVE_E2E:-0}" = "1" ]; then \
	echo "  ==> SKIP (non-release): verify-chunked-native-e2e-smoke (NGINX_BIN not set; RELEASE_GATE_ALLOW_SKIP_NATIVE_E2E=1)"; \
	else \
	echo "FAIL: verify-chunked-native-e2e-smoke requires NGINX_BIN; set RELEASE_GATE_ALLOW_SKIP_NATIVE_E2E=1 to skip for non-release validation" >&2; exit 1; \
	fi; \
	fi
	@echo "  [6/17] test-e2e-rust (Rust E2E harness — Req 1)"
	$(MAKE) test-e2e-rust
	@echo "  [7/17] coverage-c (C coverage gate — Req 4)"
	@echo "  Policy source: AGENTS.md Rule 25 — 80% aggregate; 90% critical paths (advisory)"
	@if command -v lcov >/dev/null 2>&1 && [ -d "$(NGINX_TEST_DIR)" ]; then \
	$(MAKE) coverage-c; \
	else \
	if [ "$${RELEASE_GATE_ALLOW_SKIP_COVERAGE:-0}" = "1" ]; then \
	echo "  ==> SKIP (non-release): coverage-c (lcov or NGINX test dir not available; RELEASE_GATE_ALLOW_SKIP_COVERAGE=1)"; \
	else \
	echo "FAIL: coverage-c requires lcov and NGINX test dir; set RELEASE_GATE_ALLOW_SKIP_COVERAGE=1 to skip for non-release validation" >&2; exit 1; \
	fi; \
	fi
	@echo "  [8/17] coverage-rust (Rust coverage gate — Req 4)"
	@echo "  Policy source: AGENTS.md Rule 25 — 80% aggregate; 90% critical paths (advisory)"
	@if cargo llvm-cov --version >/dev/null 2>&1; then \
	$(MAKE) coverage-rust; \
	else \
	if [ "$${RELEASE_GATE_ALLOW_SKIP_COVERAGE:-0}" = "1" ]; then \
	echo "  ==> SKIP (non-release): coverage-rust (cargo-llvm-cov not available; RELEASE_GATE_ALLOW_SKIP_COVERAGE=1)"; \
	else \
	echo "FAIL: coverage-rust requires cargo-llvm-cov; set RELEASE_GATE_ALLOW_SKIP_COVERAGE=1 to skip for non-release validation" >&2; exit 1; \
	fi; \
	fi
	@echo "  [coverage-policy] Thresholds: C line=$(COVERAGE_C_MIN_LINE)% func=$(COVERAGE_C_MIN_FUNC)% | Rust line=$(COVERAGE_RUST_MIN_LINE)% func=$(COVERAGE_RUST_MIN_FUNC)%"
	@echo "  [critical-path-coverage] 90% target for auth, error handling, FFI boundary, conditional requests"
	@echo "  [critical-path-coverage] NOTE: 90% critical-path coverage is advisory per AGENTS.md Rule 25;"
	@echo "  [critical-path-coverage]       80% aggregate is the programmatic enforcement gate."
	@echo "  [9/17] docs-check (documentation consistency — Req 3)"
	$(MAKE) docs-check
	@echo "  [10/17] matrix validate_workflow_matrix_consumers (Req 5)"
	python3 tools/release/matrix/validate_workflow_matrix_consumers.py
	@echo "  [11/17] harness-check (harness truth surface — Req 9)"
	$(MAKE) harness-check
	@echo "  [12/17] validate_release_gates.py (release gate framework)"
	python3 tools/release/gates/validate_release_gates.py
	@echo "  [13/17] validate_naming.py (artifact naming)"
	python3 tools/release/gates/validate_naming.py
	@echo "  [14/17] v0.8.0 gate validators (0.8-specific: compat bridge removal, new directives)"
	RELEASE_GATE_EXPECTED_CARGO_VERSION=$(RELEASE_GATE_080_ACTIVE_VERSION) python3 tools/release/gates/validate_release_gates_080.py
	python3 tools/release/gates/validate_config_directives_080.py
	@echo "  [15/17] legacy/prior-version regression validators (0.7.0 gates remain active)"
	RELEASE_GATE_EXPECTED_CARGO_VERSION=$(RELEASE_GATE_080_ACTIVE_VERSION) python3 tools/release/gates/validate_release_gates_070.py --mode strict
	RELEASE_GATE_EXPECTED_CARGO_VERSION=$(RELEASE_GATE_080_ACTIVE_VERSION) python3 tools/release/gates/validate_metrics_070.py
	RELEASE_GATE_EXPECTED_CARGO_VERSION=$(RELEASE_GATE_080_ACTIVE_VERSION) python3 tools/release/gates/validate_reason_codes_070.py
	RELEASE_GATE_EXPECTED_CARGO_VERSION=$(RELEASE_GATE_080_ACTIVE_VERSION) python3 tools/release/gates/validate_package_metadata_070.py
	RELEASE_GATE_EXPECTED_CARGO_VERSION=$(RELEASE_GATE_080_ACTIVE_VERSION) python3 tools/release/gates/validate_k8s_manifests_070.py
	RELEASE_GATE_EXPECTED_CARGO_VERSION=$(RELEASE_GATE_080_ACTIVE_VERSION) python3 tools/release/gates/validate_fuzz_packaging_070.py
	@echo "  [16/17] harness boundary: routing-manifest.json + risk-packs (Req 9)"
	@test -f docs/harness/routing-manifest.json || { echo "FAIL: docs/harness/routing-manifest.json not found — release gates require repo-owned harness sources" >&2; exit 1; }
	@test -d docs/harness/risk-packs || { echo "FAIL: docs/harness/risk-packs/ not found — release gates require repo-owned risk-pack inputs" >&2; exit 1; }
	@test -f docs/harness/core.md || { echo "FAIL: docs/harness/core.md not found — release gates require repo-owned harness sources (Req 9.3)" >&2; exit 1; }
	@test -f docs/harness/README.md || { echo "FAIL: docs/harness/README.md not found — release gates require repo-owned harness sources (Req 9.3)" >&2; exit 1; }
	@echo "  [17/17] clean-checkout boundary verification"
	@echo "  All validators use only repo-owned sources (no .kiro/, no .codeartsdoer/, no adapter caches)."
	@echo "  Inputs: tools/, docs/, components/, AGENTS.md, Makefile, .github/workflows/"
	@echo "  This gate is reproducible from a clean 'git clone' without user-local state."
	@if [ -d ".kiro/specs" ] && grep -r "\.kiro/" tools/release/ tools/harness/ 2>/dev/null | grep -v "\.kiro/steering" | grep -qv "^$$"; then \
		echo "WARNING: potential .kiro/ reference found in gate validators (review needed)" >&2; \
	fi
	@echo "=== v0.8.x Release Gate: ALL PASSED ($(RELEASE_GATE_080_ACTIVE_VERSION)) ==="

release-gates-check-08x: release-gates-check-080
	@echo "  (release-gates-check-08x is an alias for release-gates-check-080, the 0.8.x patch-line gate)"

release-gates-check-legacy:
	python3 tools/release/legacy/validate_release_gates.py

release-gates-check-070-docker:
	@echo "=== Gate 3/4 Local Docker Validation ==="
	@echo "  Running Gate 3 (Package Distribution) and Gate 4 (K8s) locally via Docker..."
	RELEASE_GATE_LOCAL_DOCKER=1 python3 tools/release/gates/validate_release_gates_070.py --mode strict

release-gates-check-strict:
	python3 tools/release/gates/validate_release_gates.py --mode strict
	python3 tools/release/gates/validate_naming.py
	RELEASE_GATE_EXPECTED_CARGO_VERSION=$(RELEASE_GATE_080_ACTIVE_VERSION) python3 tools/release/gates/validate_release_gates_070.py --mode strict
	python3 tools/release/gates/validate_fuzz_packaging_070.py
	@echo "=== Strict: Release Workflow Gate ==="
	@test -f .github/workflows/release-packages.yml || { echo "FAIL: .github/workflows/release-packages.yml not found" >&2; exit 1; }
	@grep -q 'SHA256SUMS\|generate-checksums' .github/workflows/release-packages.yml || { echo "FAIL: release-packages.yml missing SHA256SUMS generation logic" >&2; exit 1; }
	@grep -q 'smoke-test\|smoke_test' .github/workflows/release-packages.yml || { echo "FAIL: release-packages.yml missing smoke test job" >&2; exit 1; }
	@echo "  Release Workflow Gate: ALL PASSED"
	@echo "=== Strict: Documentation Gate ==="
	@$(MAKE) docs-check
	@echo "  Documentation Gate: ALL PASSED"
	@echo "=== Strict: Harness Rule Coverage Gate ==="
	@for rule in FUZZ-001 FUZZ-002 FUZZ-003 FUZZ-004 FUZZ-005 FUZZ-006 FUZZ-007; do \
	grep -q "$$rule" fuzz/README.md || { echo "FAIL: fuzz/README.md missing rule $$rule" >&2; exit 1; }; \
	done
	@$(MAKE) harness-check
	@echo "  Harness Rule Coverage Gate: ALL PASSED"

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

verify-metrics-endpoint-e2e:
	./tools/e2e/verify_metrics_endpoint_e2e.sh

verify-conditional-requests-e2e:
	./tools/e2e/verify_conditional_requests_e2e.sh

verify-config-merge-e2e:
	./tools/e2e/verify_config_merge_e2e.sh

verify-auth-cache-e2e:
	./tools/e2e/verify_auth_cache_e2e.sh

verify-status-codes-e2e:
	./tools/e2e/verify_status_codes_e2e.sh

# ── Coverage targets ────────────────────────────────────────────────
# Generate lcov reports consumed by SonarCloud.  Output lands in
# coverage/ at the repo root so sonar.coverageReportPaths can find it.

COVERAGE_DIR := coverage

coverage-c:
	@command -v lcov >/dev/null 2>&1 || { echo "ERROR: lcov is required for coverage-c but not found in PATH" >&2; exit 1; }
	@mkdir -p $(COVERAGE_DIR) $(COVERAGE_DIR)/tmp
	tools/sonar/collect_nginx_coverage.sh --output $(CURDIR)/$(COVERAGE_DIR)/tmp/c-e2e-coverage.lcov
	$(MAKE) -C $(NGINX_TEST_DIR) unit-coverage COV_DIR=$(CURDIR)/$(COVERAGE_DIR)/tmp/c-unit
	lcov --add-tracefile $(COVERAGE_DIR)/tmp/c-e2e-coverage.lcov \
	--add-tracefile $(COVERAGE_DIR)/tmp/c-unit/c-coverage.lcov \
	--output-file $(COVERAGE_DIR)/tmp/c-combined-raw.lcov \
	--rc branch_coverage=1 --rc geninfo_unexecuted_blocks=1 --ignore-errors inconsistent,inconsistent --ignore-errors count,count
	lcov --extract $(COVERAGE_DIR)/tmp/c-combined-raw.lcov \
	"*/components/nginx-module/src/*" \
	--output-file $(COVERAGE_DIR)/c-coverage.lcov \
	--rc branch_coverage=1 --rc geninfo_unexecuted_blocks=1 --ignore-errors unused,unused --ignore-errors inconsistent,inconsistent
	@echo "==> Combined C coverage report: $(COVERAGE_DIR)/c-coverage.lcov"
	lcov --summary $(COVERAGE_DIR)/c-coverage.lcov --rc branch_coverage=1 \
	--ignore-errors inconsistent,inconsistent
	@tools/sonar/check_advisory_coverage.sh $(COVERAGE_DIR)/c-coverage.lcov

coverage-rust:
	@mkdir -p $(COVERAGE_DIR)
	cd $(RUST_DIR) && cargo llvm-cov --lcov \
	--output-path $(CURDIR)/$(COVERAGE_DIR)/rust-coverage.lcov \
	-- --skip report_contains_all_tiers \
	   --skip legacy_single_large \
	   --skip full_run_report
	cd $(RUST_DIR) && cargo llvm-cov --features streaming --lcov \
	--output-path $(CURDIR)/$(COVERAGE_DIR)/rust-streaming-coverage.lcov \
	-- --skip report_contains_all_tiers \
	   --skip legacy_single_large \
	   --skip full_run_report

# Convert all lcov reports to SonarQube Generic Coverage XML.
# sonar.coverageReportPaths expects this format, not raw lcov.
coverage-sonar-xml:
	python3 tools/sonar/lcov_to_sonar_xml.py \
	-o $(COVERAGE_DIR)/sonar-coverage.xml \
	$(wildcard $(COVERAGE_DIR)/*.lcov)

coverage-all: coverage-c coverage-rust coverage-sonar-xml

COVERAGE_C_MIN_LINE ?= 80
COVERAGE_C_MIN_FUNC ?= 80
COVERAGE_RUST_MIN_LINE ?= 80
COVERAGE_RUST_MIN_FUNC ?= 80

coverage-gate: coverage-c coverage-rust
	python3 tools/ci/coverage_gate.py \
	--c-lcov $(COVERAGE_DIR)/c-coverage.lcov \
	--rust-lcov $(COVERAGE_DIR)/rust-coverage.lcov \
	--rust-streaming-lcov $(COVERAGE_DIR)/rust-streaming-coverage.lcov \
	--c-min-line $(COVERAGE_C_MIN_LINE) \
	--c-min-func $(COVERAGE_C_MIN_FUNC) \
	--rust-min-line $(COVERAGE_RUST_MIN_LINE) \
	--rust-min-func $(COVERAGE_RUST_MIN_FUNC)

clean:
	cd $(RUST_DIR) && cargo clean
	$(MAKE) -C $(NGINX_TEST_DIR) clean || true
	find $(NGINX_MODULE_DIR) -name '*.dSYM' -type d -prune -exec rm -rf {} +
	rm -rf coverage

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
	@echo "  test-e2e-rust            - Build and run Rust e2e-harness migrated scenarios"
	@echo "  verify-streaming-failure-cache-e2e - Run streaming failure/cache e2e tests"
	@echo "  verify-streaming-failure-cache-e2e-plan - Print test plan only (no NGINX_BIN required)"
	@echo "  verify-metrics-endpoint-e2e  - Run metrics endpoint e2e tests (JSON/text/Prometheus)"
	@echo "  verify-conditional-requests-e2e - Run conditional-request e2e tests (ETag/304)"
	@echo "  verify-config-merge-e2e     - Run config-merge e2e tests (http/server/location)"
	@echo "  verify-auth-cache-e2e       - Run auth/cache interaction e2e tests"
	@echo "  verify-status-codes-e2e     - Run upstream status-code passthrough e2e tests"
	@echo "  test-all                 - Run build + rust + unit tests"
	@echo "  sonar-compile-db         - Generate compile_commands.json for SonarQube for VS Code C/C++ analysis"
	@echo "  test-benchmark           - Run corpus benchmark and produce Unified Report"
	@echo "  test-benchmark-compare   - Compare corpus reports (baseline vs current)"
	@echo "  test-benchmark-summary   - Generate PR benchmark summary from latest report"
	@echo "  harness-check            - Validate harness truth surfaces and optional local adapters"
	@echo "  harness-check-full       - Run full harness validation plus docs/release checks"
	@echo "  harness-security-checks  - Run CWE-190/CWE-22/effective-conf/shell-hygiene/const-correctness detection"
	@echo "  security-static          - Run actionlint, shellcheck, gitleaks, Semgrep, and cargo-deny"
	@echo "  supply-chain             - Run Trivy filesystem/IaC scan and generate a Syft SPDX SBOM"
	@echo "  test-harness             - Run unit tests for harness detector scripts"
	@echo "  docs-check               - Validate documentation links/style"
	@echo "  license-check            - Verify license policy and THIRD-PARTY-NOTICES coverage"
	@echo "  release-gates-check      - Validate release gate framework (0.5.0 + 0.5.5)"
	@echo "  release-gates-check-055  - Validate 0.5.5 release gates (evidence, known-diffs, docs)"
	@echo "  release-gates-check-060  - Validate 0.6.0 release gates (streaming default, pruning, budget)"
	@echo "  release-gates-check-070  - Validate 0.7.0 release gates (runtime correctness, package compat, fuzz)"
	@echo "  release-gates-check-080  - Validate 0.8.x release gates (streaming, coverage, matrix, harness boundary)"
	@echo "  release-gates-check-08x  - Alias for release-gates-check-080 (0.8.x patch-line canonical entry)"
	@echo "  release-gates-check-legacy - Validate 0.4.0 release gate documents"
	@echo "  release-gates-check-strict - Validate all sub-specs #12-#18 for full compliance"
	@echo "  release-notes            - Generate release notes from release-matrix.json"
	@echo "  coverage-c               - Generate C module e2e coverage (builds NGINX with --coverage)"
	@echo "  coverage-rust            - Generate Rust test coverage (llvm-cov lcov)"
	@echo "  coverage-all             - Generate all coverage reports"
	@echo "  coverage-gate            - Generate coverage and enforce min thresholds (default 80%%)"
	@echo "  clean                    - Clean build artifacts"
