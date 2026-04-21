#!/usr/bin/env bash
# run-checks.sh — Run project quality checks (docs, licenses, corpus, smoke).
#
# Usage: tools/run-checks.sh [docs|licenses|corpus|smoke|all]
#   docs     — validate documentation
#   licenses — check C, Rust, and third-party license headers
#   corpus   — validate the test corpus
#   smoke    — run the smoke/integration tests via make
#   all      — run every check (default)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-all}"

# Run the documentation validation check.
#
# Arguments:
#   (none)
#
# Outputs:
#   Pass-through output from the documentation checker script
#
# Returns:
#   0 on success, non-zero if the documentation checker fails
run_docs() {
  python3 "$ROOT/tools/docs/check_docs.py"
  return 0
}

# Run the license header checks for C, Rust, and third-party notices.
#
# Arguments:
#   (none)
#
# Outputs:
#   Pass-through output from the three license checker scripts
#
# Returns:
#   0 on success, non-zero if any license checker fails
run_licenses() {
  python3 "$ROOT/tools/ci/check_c_licenses.py"
  python3 "$ROOT/tools/ci/check_rust_licenses.py"
  python3 "$ROOT/tools/ci/check_third_party_notices.py"
  return 0
}

# Run the test corpus validation.
#
# Arguments:
#   (none)
#
# Outputs:
#   Pass-through output from the corpus validation script
#
# Returns:
#   0 on success, non-zero if the corpus validation fails
run_corpus() {
  bash "$ROOT/tools/corpus/validate_corpus.sh"
  return 0
}

# Run the smoke/integration tests via make.
#
# Arguments:
#   (none)
#
# Outputs:
#   Pass-through output from `make test`
#
# Returns:
#   0 on success, non-zero if the smoke tests fail
run_smoke() {
  make -C "$ROOT" test
  return 0
}

case "$MODE" in
  docs)
    run_docs
    ;;
  licenses)
    run_licenses
    ;;
  corpus)
    run_corpus
    ;;
  smoke)
    run_smoke
    ;;
  all)
    run_docs
    run_licenses
    run_corpus
    run_smoke
    ;;
  *)
    echo "Usage: $0 [docs|licenses|corpus|smoke|all]" >&2
    exit 2
    ;;
esac
