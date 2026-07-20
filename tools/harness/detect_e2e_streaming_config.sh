#!/usr/bin/env bash
# detect_e2e_streaming_config.sh — Detect contradictory streaming config in E2E tests
#
# Rule 60 (e2e-runner): E2E nginx.conf location blocks that use
# markdown_cache_validation full must have an explicit markdown_streaming
# directive. Using the implicit default (auto) with a blocking directive
# generates a startup warning and obscures the test's intent.
#
# Detection strategy:
#   1. Scan shell E2E scripts and Rust E2E harness source for location blocks
#      containing markdown_cache_validation full.
#   2. Check whether the same block contains an explicit markdown_streaming
#      directive.
#   3. Flag blocks that rely on the implicit auto default (missing directive).
#   4. Also flag explicit markdown_streaming auto combined with
#      markdown_cache_validation full UNLESS an adjacent comment documents
#      the intentional runtime-block test.
#
# Compatibility: macOS bash 3.2 (Rule 11), [[ ]] (Rule 18).
#
# Usage:
#   bash tools/harness/detect_e2e_streaming_config.sh [directory] [--strict]
#     directory defaults to the repository root
#
# Exit codes:
#   0 — no findings (advisory by default)
#   1 — usage error or findings in --strict mode

set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCAN_DIR="${REPO_ROOT}"
STRICT=0

for arg in "$@"; do
    case "$arg" in
        --strict)
            STRICT=1
            ;;
        --help|-h)
            cat <<USAGE >&2
Usage: $0 [directory] [--strict]
  directory defaults to ${REPO_ROOT}
  --strict  exit 1 on findings
USAGE
            exit 0
            ;;
        *)
            SCAN_DIR="$arg"
            ;;
    esac
done

if [[ ! -d "$SCAN_DIR" ]]; then
    echo "ERROR: directory not found: $SCAN_DIR" >&2
    exit 1
fi

findings=0

# --- Shell E2E scripts ---
# Look for files that embed nginx.conf heredocs with markdown_cache_validation full
while IFS= read -r -d '' file; do
    rel_path="${file#"${REPO_ROOT}"/}"

    # Find lines with markdown_cache_validation full
    while IFS= read -r match; do
        line_num="${match%%:*}"

        # Look backward from this line for a location block start and check
        # if markdown_streaming is present between location { and this line.
        # Use a window of 15 lines before and 15 lines after to capture the block.
        block_start=$((line_num - 15))
        if [[ $block_start -lt 1 ]]; then
            block_start=1
        fi
        block_end=$((line_num + 15))

        block_text=$(sed -n "${block_start},${block_end}p" "$file")

        # Check if this block has an explicit markdown_streaming directive
        if echo "$block_text" | grep -q 'markdown_streaming '; then
            # Has explicit directive — check if it's "auto" with full validation
            if echo "$block_text" | grep -qE 'markdown_streaming[[:space:]]+auto'; then
                # Check for intentional comment nearby
                if echo "$block_text" | grep -qiE 'intentional|deliberately|runtime\.block|forces full-buffer|selects the full-buffer|out of the streaming path'; then
                    continue
                fi
                echo "WARN: ${rel_path}:${line_num}: markdown_streaming auto + markdown_cache_validation full" >&2
                echo "  This combination generates a startup warning. Use 'off' unless testing the runtime-block mechanism." >&2
                findings=$((findings + 1))
            fi
        else
            # No explicit markdown_streaming — implicit auto + full = warning
            echo "WARN: ${rel_path}:${line_num}: markdown_cache_validation full without explicit markdown_streaming" >&2
            echo "  Implicit default is 'auto' which generates a startup warning with cache_validation full." >&2
            echo "  Add 'markdown_streaming off;' to silence the warning and clarify intent." >&2
            findings=$((findings + 1))
        fi

    done < <(grep -nF 'markdown_cache_validation full' "$file" || true)
done < <(find "$SCAN_DIR/tools/e2e" -name "*.sh" -type f -print0 2>/dev/null || true)

# --- Rust E2E harness ---
while IFS= read -r -d '' file; do
    rel_path="${file#"${REPO_ROOT}"/}"

    while IFS= read -r match; do
        line_num="${match%%:*}"

        block_start=$((line_num - 15))
        if [[ $block_start -lt 1 ]]; then
            block_start=1
        fi
        block_end=$((line_num + 15))

        block_text=$(sed -n "${block_start},${block_end}p" "$file")

        if echo "$block_text" | grep -q 'markdown_streaming '; then
            if echo "$block_text" | grep -qE 'markdown_streaming[[:space:]]+auto'; then
                if echo "$block_text" | grep -qiE 'intentional|deliberately|runtime\.block|forces full-buffer|selects the full-buffer|out of the streaming path'; then
                    continue
                fi
                echo "WARN: ${rel_path}:${line_num}: markdown_streaming auto + markdown_cache_validation full" >&2
                echo "  This combination generates a startup warning. Use 'off' unless testing the runtime-block mechanism." >&2
                findings=$((findings + 1))
            fi
        else
            echo "WARN: ${rel_path}:${line_num}: markdown_cache_validation full without explicit markdown_streaming" >&2
            echo "  Implicit default is 'auto' which generates a startup warning with cache_validation full." >&2
            echo "  Add 'markdown_streaming off' to silence the warning and clarify intent." >&2
            findings=$((findings + 1))
        fi

    done < <(grep -nF 'markdown_cache_validation full' "$file" || true)
done < <(find "$SCAN_DIR/tools/e2e-harness" -name "*.rs" -type f -print0 2>/dev/null || true)

if [[ $findings -gt 0 ]]; then
    if [[ $STRICT -eq 1 ]]; then
        echo "FAIL: found ${findings} contradictory E2E streaming config(s)" >&2
        exit 1
    else
        echo "WARN: found ${findings} contradictory E2E streaming config(s) (advisory)" >&2
    fi
else
    echo "OK: no contradictory E2E streaming configs found" >&2
fi

exit 0
