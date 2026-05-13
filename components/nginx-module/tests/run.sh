#!/usr/bin/env bash
set -euo pipefail

# Test runner script for the nginx-markdown module test suite.
#
# Usage: run.sh [unit|integration|e2e|smoke|all]
#
# Modes:
#   unit        - Run unit tests only
#   integration - Run both C and NGINX integration tests
#   e2e         - Run end-to-end tests
#   smoke       - Run unit smoke tests (quick subset)
#   all         - Run unit and C integration tests (default)
#
# Exit codes:
#   0 - all tests passed
#   2 - invalid mode argument

MODE="${1:-all}"
case "$MODE" in
  unit)
    make -C "$(dirname "$0")" unit
    ;;
  integration)
    make -C "$(dirname "$0")" integration-c
    make -C "$(dirname "$0")" integration-nginx
    ;;
  e2e)
    make -C "$(dirname "$0")" e2e
    ;;
  smoke)
    make -C "$(dirname "$0")" unit-smoke
    ;;
  all)
    make -C "$(dirname "$0")" unit
    make -C "$(dirname "$0")" integration-c
    ;;
  *)
    echo "Usage: $0 [unit|integration|e2e|smoke|all]" >&2
    exit 2
    ;;
esac
