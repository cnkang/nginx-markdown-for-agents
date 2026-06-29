#!/usr/bin/env bash
# generate-large-fixtures.sh — Generate large HTML fixtures for streaming parity tests.
#
# Creates deterministic large HTML files (1 MB, 10 MB, 64 MB) with
# corresponding .meta.json sidecars in the tests/corpus/large/ directory.
# Each fixture uses a repeating section block to fill to the target size.
#
# Usage:
#   tests/corpus/large/generate-large-fixtures.sh
#
# Prerequisites:
#   python3
#
# Exit behaviour:
#   0 on success; non-zero if the Python generator fails.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 - "$SCRIPT_DIR" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])

targets = [
    ("large-1mb", 1 * 1024 * 1024),
    ("large-10mb", 10 * 1024 * 1024),
    ("large-64mb", 64 * 1024 * 1024),
]

block = (
    "<section>"
    "<h2>Repeated Heading</h2>"
    "<p>This paragraph is repeated to build deterministic large fixtures for streaming parity testing.</p>"
    "<ul><li>alpha</li><li>beta</li><li>gamma</li></ul>"
    "</section>\n"
)

for fixture_id, target_size in targets:
    html_path = root / f"{fixture_id}.html"
    meta_path = root / f"{fixture_id}.meta.json"

    parts = [
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"UTF-8\"><title>",
        fixture_id,
        "</title></head><body>\n",
    ]
    current_size = sum(len(p.encode("utf-8")) for p in parts)
    block_size = len(block.encode("utf-8"))

    while current_size + block_size + len("</body></html>\n") <= target_size:
        parts.append(block)
        current_size += block_size

    parts.append("</body></html>\n")
    content = "".join(parts)

    html_path.write_text(content, encoding="utf-8")

    meta = {
        "fixture-id": f"large/{fixture_id}",
        "page-type": "complex-common",
        "expected-conversion-result": "converted",
        "input-size-bytes": len(content.encode("utf-8")),
        "source-description": "Generated large fixture for streaming differential and bounded-memory tests",
        "failure-corpus": False,
        "streaming_notes": {
            "expected_fallback": False,
            "known_diff_ids": ["DIFF-PARITY-WHITESPACE-ONLY"],
            "high_risk_structures": ["large-document", "memory-boundary"]
        }
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    print(f"generated {html_path.name} ({meta['input-size-bytes']} bytes)")
PY
