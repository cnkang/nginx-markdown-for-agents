#!/usr/bin/env bash
set -euo pipefail

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
