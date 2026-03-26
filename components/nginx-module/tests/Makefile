include make/targets.mk

CLANG ?= clang
SANITIZER_FLAGS ?= -O1 -fno-omit-frame-pointer -fsanitize=address,undefined
UNAME_S ?= $(shell uname -s)
ASAN_OPTIONS_BASE ?= abort_on_error=1
ifeq ($(UNAME_S),Linux)
ASAN_OPTIONS_BASE := $(strip $(ASAN_OPTIONS_BASE))
ifeq ($(ASAN_OPTIONS_BASE),)
ASAN_OPTIONS_BASE := detect_leaks=1
else
ASAN_OPTIONS_BASE := $(ASAN_OPTIONS_BASE):detect_leaks=1
endif
endif
SANITIZER_ENV ?= ASAN_OPTIONS=$(ASAN_OPTIONS_BASE) UBSAN_OPTIONS=print_stacktrace=1:halt_on_error=1

.PHONY: unit unit-smoke unit-clang-smoke unit-sanitize-smoke $(addprefix unit-,$(UNIT_NAMES)) integration-c integration-nginx e2e clean

build/unit build/integration build/generated:
	@mkdir -p $@

build/unit/%: unit/%_test.c include/test_common.h include/test_metrics_snapshot.h | build/unit build/generated
	@extra_srcs=""; \
	extra_cflags=""; \
	extra_ldflags=""; \
	case "$*" in \
	  headers) extra_srcs="helpers/headers_standalone.c" ;; \
	  header_compile|reason_code) extra_cflags="-I../src -Iinclude/nginx_stubs" ;; \
	  gzip_deflate_decompression) extra_ldflags="-lz" ;; \
	esac; \
	$(CC) $(CFLAGS_COMMON) $(LDFLAGS_COMMON) $$extra_cflags -o "$@" "$<" $$extra_srcs $$extra_ldflags

unit: $(UNIT_BINS)
	@set -e; \
	for t in $(UNIT_BINS); do \
	  echo "Running $$t"; \
	  "$$t"; \
	done

# fast smoke subset used by top-level make test
unit-smoke: build/unit/body_filter build/unit/eligibility build/unit/headers
	@build/unit/body_filter
	@build/unit/eligibility
	@build/unit/headers

unit-clang-smoke:
	@$(MAKE) clean
	@$(MAKE) unit-smoke CC=$(CLANG)

unit-sanitize-smoke:
	@$(MAKE) clean
	@$(SANITIZER_ENV) $(MAKE) unit-smoke \
		CC=$(CLANG) \
		CFLAGS_COMMON="-Wall -Wextra -Werror=implicit-function-declaration -Werror=incompatible-pointer-types -g $(SANITIZER_FLAGS) -std=$(CSTD) -Iinclude" \
		LDFLAGS_COMMON="$(SANITIZER_FLAGS)"

$(addprefix unit-,$(UNIT_NAMES)): unit-%: build/unit/%
	@"$<"

$(INTEGRATION_C_BIN): integration/nginx_runtime_integration_test.c include/test_common.h | build/integration
	$(CC) $(CFLAGS_COMMON) -o "$@" "$<"

integration-c: $(INTEGRATION_C_BIN)
	@"$(INTEGRATION_C_BIN)"

integration-nginx:
	@bash integration/run_integration_tests.sh

e2e:
	@bash ../../../tools/e2e/run_e2e_suite.sh

clean:
	rm -rf build
