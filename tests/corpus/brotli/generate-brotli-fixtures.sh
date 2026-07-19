#!/usr/bin/env bash
# generate-brotli-fixtures.sh — Generate Brotli-compressed test fixtures for E2E testing.
#
# Creates deterministic Brotli-compressed files for the streaming decompression
# E2E test suite for Brotli streaming decompression.
#
# Generated fixtures:
#   small.md         — source Markdown under 4096 bytes
#   small.md.br      — valid Brotli-compressed small Markdown
#   large.md         — source Markdown over 64 KiB
#   large.md.br      — valid Brotli-compressed large Markdown
#   trailing-garbage.md.br — valid Brotli stream with trailing garbage appended
#   truncated.md.br  — truncated (incomplete) Brotli stream
#
# Prerequisites:
#   brotli CLI (libbrotli / brew install brotli)
#
# Usage:
#   tests/corpus/brotli/generate-brotli-fixtures.sh
#
# Exit behaviour:
#   0 on success; non-zero on failure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v brotli >/dev/null 2>&1; then
    echo "ERROR: brotli CLI not found. Install via: brew install brotli" >&2
    exit 1
fi

# --- 1. Small valid Markdown (under 4096 bytes decompressed) ---

cat > "${SCRIPT_DIR}/small.md" <<'EOF'
# Brotli Streaming Test — Small Fixture

This is a small Markdown document used to verify Brotli streaming
decompression produces byte-identical output to the full-buffer path.

## Section One

A paragraph with **bold**, *italic*, and `inline code`.

- List item alpha
- List item beta
- List item gamma

## Section Two

| Column A | Column B | Column C |
|----------|----------|----------|
| row 1    | data     | value    |
| row 2    | data     | value    |
| row 3    | data     | value    |

## Code Block

```python
def hello():
    return "world"
```

End of small fixture.
EOF

brotli --best -f "${SCRIPT_DIR}/small.md"
# brotli compresses in-place by default (small.md → small.md.br), so recreate source
# Actually, brotli --keep preserves the original:
# Regenerate since brotli without --keep removes the source
# Let's use --keep instead

# Remove the .br we just made and redo with --keep
rm -f "${SCRIPT_DIR}/small.md.br"

# Recreate source (brotli without --keep ate it)
cat > "${SCRIPT_DIR}/small.md" <<'EOF'
# Brotli Streaming Test — Small Fixture

This is a small Markdown document used to verify Brotli streaming
decompression produces byte-identical output to the full-buffer path.

## Section One

A paragraph with **bold**, *italic*, and `inline code`.

- List item alpha
- List item beta
- List item gamma

## Section Two

| Column A | Column B | Column C |
|----------|----------|----------|
| row 1    | data     | value    |
| row 2    | data     | value    |
| row 3    | data     | value    |

## Code Block

```python
def hello():
    return "world"
```

End of small fixture.
EOF

brotli --best --keep -f "${SCRIPT_DIR}/small.md"

small_size=$(wc -c < "${SCRIPT_DIR}/small.md" | tr -d ' ')
if [[ "${small_size}" -ge 4096 ]]; then
    echo "ERROR: small.md is ${small_size} bytes (expected < 4096)" >&2
    exit 1
fi
echo "small.md: ${small_size} bytes (decompressed), valid .br created"

# --- 2. Large valid Markdown (over 64 KiB decompressed) ---

python3 - "${SCRIPT_DIR}" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
target_size = 68 * 1024  # 68 KiB — comfortably over the 64 KiB threshold

header = "# Brotli Streaming Test — Large Fixture\n\n"
header += "This document exceeds 64 KiB to exercise streaming decompression\n"
header += "across multiple NGINX buffer boundaries.\n\n"

block = (
    "## Repeated Section\n\n"
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod\n"
    "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam,\n"
    "quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo.\n\n"
    "- Alpha item with descriptive text for padding\n"
    "- Beta item with descriptive text for padding\n"
    "- Gamma item with descriptive text for padding\n"
    "- Delta item with descriptive text for padding\n\n"
    "```\n"
    "code_block_line_1 = value\n"
    "code_block_line_2 = value\n"
    "code_block_line_3 = value\n"
    "```\n\n"
)

parts = [header]
current_size = len(header.encode("utf-8"))
block_size = len(block.encode("utf-8"))

while current_size + block_size + len("End of large fixture.\n") <= target_size:
    parts.append(block)
    current_size += block_size

# Pad remaining space if needed
remaining = target_size - current_size - len("End of large fixture.\n")
if remaining > 0:
    parts.append("x" * remaining + "\n")
    current_size += remaining + 1

parts.append("End of large fixture.\n")
content = "".join(parts)
actual_size = len(content.encode("utf-8"))

out_path = root / "large.md"
out_path.write_text(content, encoding="utf-8")
print(f"large.md: {actual_size} bytes (target >= 65536)")
if actual_size < 65536:
    print(f"ERROR: large.md is only {actual_size} bytes", file=sys.stderr)
    sys.exit(1)
PY

brotli --best --keep -f "${SCRIPT_DIR}/large.md"

large_size=$(wc -c < "${SCRIPT_DIR}/large.md" | tr -d ' ')
echo "large.md: ${large_size} bytes (decompressed), valid .br created"

# --- 3. Brotli with trailing garbage appended ---

# Start with a valid .br of the small fixture, then append random bytes
cp "${SCRIPT_DIR}/small.md.br" "${SCRIPT_DIR}/trailing-garbage.md.br"
# Append 16 bytes of deterministic garbage (not valid Brotli continuation)
printf '\xDE\xAD\xBE\xEF\xCA\xFE\xBA\xBE\x01\x02\x03\x04\x05\x06\x07\x08' \
    >> "${SCRIPT_DIR}/trailing-garbage.md.br"

trailing_br_size=$(wc -c < "${SCRIPT_DIR}/trailing-garbage.md.br" | tr -d ' ')
small_br_size=$(wc -c < "${SCRIPT_DIR}/small.md.br" | tr -d ' ')
echo "trailing-garbage.md.br: ${trailing_br_size} bytes (valid .br = ${small_br_size} + 16 garbage)"

# --- 4. Truncated Brotli stream ---

# Take the large .br file and cut it in half
large_br_size=$(wc -c < "${SCRIPT_DIR}/large.md.br" | tr -d ' ')
half_size=$(( large_br_size / 2 ))
head -c "${half_size}" "${SCRIPT_DIR}/large.md.br" > "${SCRIPT_DIR}/truncated.md.br"

truncated_size=$(wc -c < "${SCRIPT_DIR}/truncated.md.br" | tr -d ' ')
echo "truncated.md.br: ${truncated_size} bytes (truncated at ${half_size} of ${large_br_size})"

# --- Summary ---
echo ""
echo "All Brotli E2E fixtures generated successfully in: ${SCRIPT_DIR}"
echo "  small.md          — source (< 4096 bytes)"
echo "  small.md.br       — valid Brotli-compressed small file"
echo "  large.md          — source (> 64 KiB)"
echo "  large.md.br       — valid Brotli-compressed large file"
echo "  trailing-garbage.md.br — valid Brotli + 16 trailing garbage bytes"
echo "  truncated.md.br   — truncated (incomplete) Brotli stream"
