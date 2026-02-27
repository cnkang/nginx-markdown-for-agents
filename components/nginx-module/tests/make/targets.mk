CC ?= gcc
# Prefer the newest ratified C standard that the active compiler supports,
# while keeping a compatibility fallback for older toolchains.
CSTD ?= $(shell printf 'int main(void){return 0;}\n' | $(CC) -std=c23 -x c - -o /dev/null >/dev/null 2>&1 && echo c23 || \
	(printf 'int main(void){return 0;}\n' | $(CC) -std=c17 -x c - -o /dev/null >/dev/null 2>&1 && echo c17 || \
	(printf 'int main(void){return 0;}\n' | $(CC) -std=c11 -x c - -o /dev/null >/dev/null 2>&1 && echo c11 || echo c99)))

# Stay close to nginx's strict warning posture and catch unsafe C interop early.
CFLAGS_COMMON ?= -Wall -Wextra -Werror=implicit-function-declaration -Werror=incompatible-pointer-types -g -std=$(CSTD) -Iinclude
LDFLAGS_COMMON ?=

UNIT_NAMES := $(patsubst %_test.c,%,$(notdir $(wildcard unit/*_test.c)))
UNIT_BINS := $(addprefix build/unit/,$(UNIT_NAMES))

INTEGRATION_C_BIN := build/integration/nginx_runtime_integration_test
