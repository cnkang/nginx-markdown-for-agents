#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-all}"

run_docs() {
  python3 "$ROOT/tools/docs/check_docs.py"
}

run_corpus() {
  bash "$ROOT/tools/corpus/validate_corpus.sh"
}

run_smoke() {
  make -C "$ROOT" test
}

case "$MODE" in
  docs)
    run_docs
    ;;
  corpus)
    run_corpus
    ;;
  smoke)
    run_smoke
    ;;
  all)
    run_docs
    run_corpus
    run_smoke
    ;;
  *)
    echo "Usage: $0 [docs|corpus|smoke|all]" >&2
    exit 2
    ;;
esac
