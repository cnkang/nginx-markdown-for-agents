include make/targets.mk

.PHONY: unit unit-smoke $(addprefix unit-,$(UNIT_NAMES)) integration-c integration-nginx e2e clean

build/unit build/integration build/generated:
	@mkdir -p $@

build/unit/%: unit/%_test.c include/test_common.h | build/unit build/generated
	@extra_srcs=""; \
	extra_cflags=""; \
	extra_ldflags=""; \
	case "$*" in \
	  headers) extra_srcs="helpers/headers_standalone.c" ;; \
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

$(addprefix unit-,$(UNIT_NAMES)): unit-%: build/unit/%
	@"$<"

$(INTEGRATION_C_BIN): integration/nginx_runtime_integration_test.c include/test_common.h | build/integration
	$(CC) $(CFLAGS_COMMON) -o "$@" "$<"

integration-c: $(INTEGRATION_C_BIN)
	@"$(INTEGRATION_C_BIN)"

integration-nginx:
	@bash integration/run_integration_tests.sh

e2e:
	@bash e2e/run_e2e_tests.sh

clean:
	rm -rf build
