CC ?= gcc
CFLAGS_COMMON ?= -Wall -Wextra -g -std=c99 -Iinclude
LDFLAGS_COMMON ?=

UNIT_NAMES := $(patsubst %_test.c,%,$(notdir $(wildcard unit/*_test.c)))
UNIT_BINS := $(addprefix build/unit/,$(UNIT_NAMES))

INTEGRATION_C_BIN := build/integration/nginx_runtime_integration_test
