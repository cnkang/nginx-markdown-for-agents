#!/bin/bash
#
# Generate large HTML sample files for performance testing.
#
# Produces ~100KB (large-100k) and ~5MB (large-5m) HTML files in four
# variants: plain HTML, HTML + front matter, HTML + token estimation
# markers, and HTML + nested tables + code blocks.
#
# Usage:
#   tools/corpus/generate_large_samples.sh [--output-dir <dir>] [--tier <name>]
#
# Options:
#   --output-dir <dir>  Output directory (default: tools/corpus/samples/)
#   --tier <name>       Generate only the specified tier (e.g. large-100k, large-5m)
#
# The script is idempotent — running it again overwrites previous output.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
OUTPUT_DIR="${ROOT}/tools/corpus/samples"
TIER=""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# require_optval validates that an option's value is present and not another flag, printing an error and exiting if the value is missing or looks like a flag.
require_optval() {
    local flag="$1"
    local val="${2:-}"
    if [[ -z "$val" || "$val" == -* ]]; then
        echo "error: $flag requires a value" >&2
        exit 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir)
            require_optval "$1" "${2:-}"
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --tier)
            require_optval "$1" "${2:-}"
            TIER="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--output-dir <dir>] [--tier <name>]"
            echo ""
            echo "Options:"
            echo "  --output-dir <dir>  Output directory (default: tools/corpus/samples/)"
            echo "  --tier <name>       Generate only the specified tier (e.g. large-100k, large-5m, large-10m, extra-large-64m)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

mkdir -p "$OUTPUT_DIR"

# ---------------------------------------------------------------------------
# Colors (only when stdout is a terminal)
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
    GREEN='\033[0;32m'
    NC='\033[0m'
else
    GREEN=''
    NC=''
fi

# ---------------------------------------------------------------------------
# Tier target sizes (bytes)
# ---------------------------------------------------------------------------
TIER_100K=102400    # ~100 KB
TIER_5M=5242880     # ~5 MB
TIER_10M=10485760   # ~10 MB — streaming bounded-memory validation
TIER_64M=67108864   # ~64 MB — critical bounded-memory validation point

# ---------------------------------------------------------------------------
# Content building blocks
# ---------------------------------------------------------------------------

# emit_header emits an HTML document header to stdout using the provided title.
emit_header() {
    local title="$1"
    cat <<EOF
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>${title}</title>
</head>
<body>
EOF
    return 0
}

# emit_footer writes the HTML document closing tags (`</body>` and `</html>`) to stdout.
emit_footer() {
    cat <<'EOF'
</body>
</html>
EOF
    return 0
}

# emit_front_matter outputs a YAML front matter block used to preface generated HTML files for the front-matter variant.
emit_front_matter() {
    cat <<'EOF'
---
title: "Large Performance Test Document"
author: "perf-generator"
date: "2026-01-01"
tags:
  - performance
  - large-document
  - benchmark
description: "Auto-generated large HTML document for performance benchmarking."
---
EOF
    return 0
}

# Emit token estimation markers (for token-estimation variant).
emit_token_markers() {
    cat <<'EOF'
<!-- token-estimate: start -->
<!-- token-budget: 50000 -->
EOF
    return 0
}

# emit_token_markers_end outputs the closing HTML comment marker that signals the end of a token-estimation region.
emit_token_markers_end() {
    cat <<'EOF'
<!-- token-estimate: end -->
EOF
    return 0
}

# emit_prose_section outputs an HTML <section> of ~300 bytes with a heading and two paragraphs, using its single argument as the section index inserted into the element id and heading.
emit_prose_section() {
    local idx="$1"
    cat <<EOF
    <section id="section-${idx}">
        <h2>Section ${idx}: Performance Analysis</h2>
        <p>This section covers performance characteristics of large document processing. The converter must handle arbitrarily large inputs while maintaining bounded memory usage and consistent throughput.</p>
        <p>Paragraph ${idx} adds realistic prose to reach the target file size for benchmarking purposes.</p>
    </section>
EOF
    return 0
}

# emit_table_code_section emits a complex HTML section (nested table plus Rust code block) whose id and sample values incorporate the given index.
emit_table_code_section() {
    local idx="$1"
    cat <<EOF
    <section id="complex-${idx}">
        <h2>Complex Section ${idx}</h2>
        <table>
            <thead>
                <tr><th>Metric</th><th>Value</th><th>Unit</th></tr>
            </thead>
            <tbody>
                <tr>
                    <td>Latency P50</td>
                    <td>
                        <table>
                            <tr><td>small</td><td>0.${idx}ms</td></tr>
                            <tr><td>large</td><td>${idx}.5ms</td></tr>
                        </table>
                    </td>
                    <td>ms</td>
                </tr>
                <tr><td>Throughput</td><td>${idx}00</td><td>req/s</td></tr>
            </tbody>
        </table>
        <pre><code class="language-rust">fn process_chunk_${idx}(data: &amp;[u8]) -&gt; Result&lt;Vec&lt;u8&gt;, Error&gt; {
    let mut buf = Vec::with_capacity(data.len());
    for byte in data.iter() {
        buf.push(*byte);
    }
    Ok(buf)
}</code></pre>
    </section>
EOF
    return 0
}

# ---------------------------------------------------------------------------
# File generation helpers
# ---------------------------------------------------------------------------

# Build a reusable content block (~10 KB) for the given variant.
# Writing one large block and repeating it is orders of magnitude faster
# _build_block builds a reusable content block of approximately 10 KB for the specified variant and writes it to stdout.
_build_block() {
    local variant="$1"
    local block=""
    local idx=1
    while [[ ${#block} -lt 10240 ]]; do
        # NOTE: ${#block} counts characters, not bytes. This is fine for the
        # current ASCII-only content but would undercount if Unicode samples
        # are added in the future.  Switch to $(printf '%s' "$block" | wc -c)
        # if non-ASCII content is introduced.
        case "$variant" in
            nested-tables-code)
                block+="$(emit_table_code_section "$idx")"
                ;;
            *)
                block+="$(emit_prose_section "$idx")"
                ;;
        esac
        block+=$'\n'
        idx=$((idx + 1))
    done
    printf '%s' "$block"
    return 0
}

# Generate a file by repeating content blocks until the target size is met.
#   $1 = output file path
#   $2 = target size in bytes
# generate_file creates an HTML file at the given path filled to approximately the requested byte size using the specified variant (plain-html, front-matter, token-estimation, or nested-tables-code).
generate_file() {
    local outfile="$1"
    local target_bytes="$2"
    local variant="$3"

    local tmpfile
    tmpfile="$(mktemp)"
    trap 'rm -f "$tmpfile"' RETURN

    # --- preamble ---
    case "$variant" in
        front-matter)   emit_front_matter > "$tmpfile" ;;
        token-estimation) emit_token_markers > "$tmpfile" ;;
        *)              : > "$tmpfile" ;;
    esac

    emit_header "Large Sample — ${variant}" >> "$tmpfile"

    # --- body: write ~10 KB blocks until we reach the target ---
    local block
    block="$(_build_block "$variant")"

    # Reserve space for footer + closing markers (~100 bytes is generous)
    local body_target=$((target_bytes - 100))

    while true; do
        local current_size
        current_size=$(wc -c < "$tmpfile")
        if [[ "$current_size" -ge "$body_target" ]]; then
            break
        fi
        printf '%s' "$block" >> "$tmpfile"
    done

    # --- footer ---
    emit_footer >> "$tmpfile"

    if [[ "$variant" == "token-estimation" ]]; then
        emit_token_markers_end >> "$tmpfile"
    fi

    mv "$tmpfile" "$outfile"
    trap - RETURN
    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

VARIANTS=("plain-html" "front-matter" "token-estimation" "nested-tables-code")
TIERS=(
  "large-100k:${TIER_100K}"
  "large-5m:${TIER_5M}"
  "large-10m:${TIER_10M}"
  "extra-large-64m:${TIER_64M}"
)

# Validate --tier value if provided.
if [[ -n "$TIER" ]]; then
    TIER_VALID=false
    for tier_entry in "${TIERS[@]}"; do
        if [[ "${tier_entry%%:*}" == "$TIER" ]]; then
            TIER_VALID=true
            break
        fi
    done
    if [[ "$TIER_VALID" == false ]]; then
        echo "error: unknown tier '$TIER'. Available tiers: $(printf '%s ' "${TIERS[@]%%:*}")" >&2
        exit 1
    fi
fi

SEPARATOR="========================================"

echo "$SEPARATOR"
echo "Large Sample Generator"
echo "$SEPARATOR"
echo "Output directory: ${OUTPUT_DIR}"
if [[ -n "$TIER" ]]; then
    echo "Tier filter:      ${TIER}"
fi
echo ""

FILE_COUNT=0

for tier_entry in "${TIERS[@]}"; do
    tier_name="${tier_entry%%:*}"
    tier_bytes="${tier_entry##*:}"

    # Skip tiers that don't match the filter.
    if [[ -n "$TIER" && "$tier_name" != "$TIER" ]]; then
        continue
    fi

    for variant in "${VARIANTS[@]}"; do
        outfile="${OUTPUT_DIR}/${tier_name}_${variant}.html"
        printf "  Generating %-45s " "${tier_name}_${variant}.html ..."
        generate_file "$outfile" "$tier_bytes" "$variant"
        actual_size=$(wc -c < "$outfile")
        printf "${GREEN}done${NC} (%s bytes)\n" "$actual_size"
        FILE_COUNT=$((FILE_COUNT + 1))
    done
done

echo ""
echo "$SEPARATOR"
echo "Generated ${FILE_COUNT} sample files in ${OUTPUT_DIR}"
echo "$SEPARATOR"
