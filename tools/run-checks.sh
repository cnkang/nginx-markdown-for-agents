#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-all}"

run_docs() {
  python3 "$ROOT/tools/docs/check_docs.py"
  return 0
}

run_licenses() {
  python3 "$ROOT/tools/ci/check_c_licenses.py"
  python3 "$ROOT/tools/ci/check_rust_licenses.py"
  python3 "$ROOT/tools/ci/check_third_party_notices.py"
  return 0
}

run_corpus() {
  bash "$ROOT/tools/corpus/validate_corpus.sh"
  return 0
}

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
