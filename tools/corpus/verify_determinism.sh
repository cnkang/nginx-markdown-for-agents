#!/bin/bash
#
# Determinism Corpus Verification Script
#
# Verifies the output determinism contract (Requirement 13.4):
# identical effective inputs produce byte-identical response bodies within
# the same module version and build feature set.
#
# The script converts every HTML fixture in tests/corpus twice in two
# independent converter processes and compares the Markdown output
# byte-for-byte.  Repeated runs must produce identical bytes; unrelated
# request headers are not part of the determinism identity and are not
# varied here (they are exercised by the HTTP-level E2E determinism checks).
#
# Usage:
#   bash tools/corpus/verify_determinism.sh
#
# Prerequisites:
#   - cargo: builds the Rust converter test-corpus-conversion binary.
#   - python3: writes the machine-readable report.
#
# Exit codes:
#   0 - every fixture produced byte-identical output across repeated runs.
#   1 - build failure, missing fixtures, or a byte mismatch.
#

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONVERTER_DIR="$ROOT/tools/corpus/test-corpus-conversion"
CONVERTER_BIN="$CONVERTER_DIR/target/release/test-corpus-conversion"
REPORT="$ROOT/perf/reports/corpus-determinism-report.json"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if ! command -v cargo >/dev/null 2>&1; then
    echo "ERROR: cargo is required to build the corpus converter" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 is required to write the determinism report" >&2
    exit 1
fi

echo "Building test-corpus-conversion binary..."
(cd "$CONVERTER_DIR" && cargo build --locked --release --quiet)
if [[ ! -x "$CONVERTER_BIN" ]]; then
    echo "ERROR: cargo build did not produce an executable converter" >&2
    exit 1
fi

CORPUS_DIR="$ROOT/tests/corpus"
if ! find "$CORPUS_DIR" -name '*.html' -type f -print0 2>/dev/null \
    > "$TMP_DIR/fixtures.list"; then
    echo "ERROR: find failed while discovering HTML fixtures under $CORPUS_DIR" >&2
    exit 1
fi
if [[ ! -s "$TMP_DIR/fixtures.list" ]]; then
    echo "ERROR: no HTML fixtures found under $CORPUS_DIR" >&2
    exit 1
fi

FIXTURE_COUNT="$(tr -cd '\0' < "$TMP_DIR/fixtures.list" | wc -c | tr -d ' ')"
echo "Verifying determinism for $FIXTURE_COUNT corpus fixtures..."
TOTAL=0
FAILED=0
: > "$TMP_DIR/failed-files"

record_failed_fixture() {
    local failed_fixture="$1"
    printf '%s\0' "$failed_fixture" >> "$TMP_DIR/failed-files"
    return 0
}

while IFS= read -r -d '' fixture; do
    TOTAL=$((TOTAL + 1))
    rel="${fixture#"$ROOT"/}"

    if ! "$CONVERTER_BIN" "$fixture" < /dev/null > "$TMP_DIR/run1.md" 2> "$TMP_DIR/run1.err"; then
        echo "FAIL: $rel: conversion run 1 failed ($(cat "$TMP_DIR/run1.err"))" >&2
        FAILED=$((FAILED + 1))
        record_failed_fixture "$rel"
        continue
    fi
    if ! "$CONVERTER_BIN" "$fixture" < /dev/null > "$TMP_DIR/run2.md" 2> "$TMP_DIR/run2.err"; then
        echo "FAIL: $rel: conversion run 2 failed ($(cat "$TMP_DIR/run2.err"))" >&2
        FAILED=$((FAILED + 1))
        record_failed_fixture "$rel"
        continue
    fi

    if ! cmp -s "$TMP_DIR/run1.md" "$TMP_DIR/run2.md"; then
        echo "FAIL: $rel: byte mismatch between repeated runs" >&2
        FAILED=$((FAILED + 1))
        record_failed_fixture "$rel"
        continue
    fi
done < "$TMP_DIR/fixtures.list"

mkdir -p "$(dirname "$REPORT")"
python3 - "$REPORT" "$TOTAL" "$FAILED" "$TMP_DIR/failed-files" <<'PYEOF'
import json
import sys

report_path, total, failed, failed_files_path = (
    sys.argv[1],
    int(sys.argv[2]),
    int(sys.argv[3]),
    sys.argv[4],
)
with open(failed_files_path, "rb") as f:
    failed_files = [
        item.decode("utf-8") for item in f.read().split(b"\0") if item
    ]

report = {
    "schema_version": "release.determinism-corpus.v1",
    "total_fixtures": total,
    "failed_fixtures": failed,
    "passed": failed == 0,
    "failed_files": failed_files,
    "method": "two independent converter processes, byte-for-byte cmp",
    "contract": "identical effective inputs produce byte-identical output "
                "within the same module version and build feature set",
}
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, sort_keys=True)
    f.write("\n")
PYEOF

echo "Determinism report: $REPORT"
if [[ "$FAILED" -eq 0 ]]; then
    echo "PASS: all $TOTAL corpus fixtures produced byte-identical output"
    exit 0
fi

echo "FAIL: $FAILED of $TOTAL fixtures produced non-identical output:" >&2
while IFS= read -r -d '' f; do
    echo "  $f" >&2
done < "$TMP_DIR/failed-files"
exit 1
