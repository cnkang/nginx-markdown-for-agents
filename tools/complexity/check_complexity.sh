#!/usr/bin/env bash
# Complexity check harness for nginx-markdown-for-agents.
#
# Runs lizard (CCN/length/params) on C, Rust, and Python source,
# complexipy (cognitive complexity) on Python tooling, and shellcheck
# on shell scripts.  Compares violations against a checked-in baseline
# to prevent new complexity growth without requiring immediate cleanup
# of all historical complex functions.
#
# Output lands in target/complexity/ (not committed).
#
# Dependencies:
#   python3 -m lizard          (pip install lizard)
#   python3 -m complexipy.main (pip install complexipy)
#   the `shellcheck` CLI      (brew install shellcheck, apt install shellcheck)
#
# Usage:
#   bash tools/complexity/check_complexity.sh
#   make complexity-check

set -euo pipefail

# Resolve repo root regardless of cwd
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

OUTDIR="target/complexity"
mkdir -p "$OUTDIR"

# ── Thresholds ──────────────────────────────────────────────────────
# C: generous thresholds — NGINX glue layers have inherent complexity
#    from lifecycle, error branches, macros, and state machines.
#    Goal: prevent further growth, not zero out all complex functions.
C_CCN_THRESHOLD=25
C_LENGTH_THRESHOLD=180
C_PARAMS_THRESHOLD=8

# Rust: moderate thresholds — pure-logic functions should be simpler,
#        but streaming state machines and HTML traversal are inherently
#        complex.  Goal: catch new hot spots.
RUST_CCN_THRESHOLD=25
RUST_LENGTH_THRESHOLD=200
RUST_PARAMS_THRESHOLD=8

# Python: tighter thresholds — tooling scripts should stay simple.
PY_CCN_THRESHOLD=15
PY_LENGTH_THRESHOLD=200
PY_PARAMS_THRESHOLD=8

# Python cognitive complexity (complexipy)
PY_COGNITIVE_THRESHOLD=15

# ── Source paths ─────────────────────────────────────────────────────
C_SRC="components/nginx-module/src"
RUST_SRC="components/rust-converter/src"
PY_SRC="tools"

# ── Shell scripts ───────────────────────────────────────────────────
# Discover tracked shell scripts (same scope as security-shellcheck)
SHELL_FILES="$(mktemp)"
trap 'rm -f "$SHELL_FILES"' EXIT
git ls-files -z -- ":(glob)*.sh" ":(glob)tools/**/*.sh" ":(glob)packaging/**/*.sh" ":(glob).clusterfuzzlite/*.sh" ":(glob)examples/**/*.sh" > "$SHELL_FILES" 2>/dev/null || true

# ── Tool checks ─────────────────────────────────────────────────────
MISSING_TOOLS=()

if ! python3 -c "import lizard" 2>/dev/null; then
    MISSING_TOOLS+=("lizard (pip install lizard)")
fi

if ! python3 -m complexipy.main --version >/dev/null 2>&1; then
    MISSING_TOOLS+=("complexipy (pip install complexipy)")
fi

if ! command -v shellcheck >/dev/null 2>&1; then
    MISSING_TOOLS+=("shellcheck (brew install shellcheck, apt install shellcheck)")
fi

if [[ ${#MISSING_TOOLS[@]} -gt 0 ]]; then
    echo "ERROR: missing required tools:" >&2
    for tool in "${MISSING_TOOLS[@]}"; do
        echo "  - $tool" >&2
    done
    echo "" >&2
    echo "Install with:" >&2
    echo "  pip install lizard complexipy" >&2
    echo "  brew install shellcheck   # macOS" >&2
    echo "  apt install shellcheck    # Debian/Ubuntu" >&2
    exit 127
fi

# ── Version info ────────────────────────────────────────────────────
echo "=== Complexity Check ==="
echo "Tools:"
echo "  lizard: $(python3 -m lizard --version 2>&1)"
echo "  complexipy: $(python3 -m complexipy.main --version 2>&1)"
echo "  shellcheck: $(shellcheck --version 2>&1 | head -1)"
echo ""

# ── C: lizard ───────────────────────────────────────────────────────
C_WARNINGS="$OUTDIR/c-lizard-warnings.txt"
C_REPORT="$OUTDIR/c-lizard-report.txt"
echo "--- C (lizard) ---"
if [[ -d "$C_SRC" ]]; then
    python3 -m lizard "$C_SRC" -l cpp \
        --CCN "$C_CCN_THRESHOLD" \
        --length "$C_LENGTH_THRESHOLD" \
        --arguments "$C_PARAMS_THRESHOLD" \
        > "$C_REPORT" 2>/dev/null || true
    python3 -m lizard "$C_SRC" -l cpp \
        --CCN "$C_CCN_THRESHOLD" \
        --length "$C_LENGTH_THRESHOLD" \
        --arguments "$C_PARAMS_THRESHOLD" \
        -w 2>&1 | grep "warning:" > "$C_WARNINGS" || true
    c_count=$(wc -l < "$C_WARNINGS" | tr -d ' ')
    echo "  C violations: $c_count (thresholds: CCN=$C_CCN_THRESHOLD length=$C_LENGTH_THRESHOLD params=$C_PARAMS_THRESHOLD)"
    echo "  Report: $C_REPORT"
else
    echo "  SKIP: C source directory not found: $C_SRC"
    echo "" > "$C_WARNINGS"
fi
echo ""

# ── Rust: lizard ────────────────────────────────────────────────────
RUST_WARNINGS="$OUTDIR/rust-lizard-warnings.txt"
RUST_REPORT="$OUTDIR/rust-lizard-report.txt"
echo "--- Rust (lizard) ---"
if [[ -d "$RUST_SRC" ]]; then
    python3 -m lizard "$RUST_SRC" -l rust \
        --CCN "$RUST_CCN_THRESHOLD" \
        --length "$RUST_LENGTH_THRESHOLD" \
        --arguments "$RUST_PARAMS_THRESHOLD" \
        > "$RUST_REPORT" 2>/dev/null || true
    python3 -m lizard "$RUST_SRC" -l rust \
        --CCN "$RUST_CCN_THRESHOLD" \
        --length "$RUST_LENGTH_THRESHOLD" \
        --arguments "$RUST_PARAMS_THRESHOLD" \
        -w 2>&1 | grep "warning:" > "$RUST_WARNINGS" || true
    rust_count=$(wc -l < "$RUST_WARNINGS" | tr -d ' ')
    echo "  Rust violations: $rust_count (thresholds: CCN=$RUST_CCN_THRESHOLD length=$RUST_LENGTH_THRESHOLD params=$RUST_PARAMS_THRESHOLD)"
    echo "  Report: $RUST_REPORT"
else
    echo "  SKIP: Rust source directory not found: $RUST_SRC"
    echo "" > "$RUST_WARNINGS"
fi
echo ""

# ── Python: lizard ──────────────────────────────────────────────────
PY_LIZARD_WARNINGS="$OUTDIR/py-lizard-warnings.txt"
PY_LIZARD_REPORT="$OUTDIR/py-lizard-report.txt"
echo "--- Python (lizard) ---"
if [[ -d "$PY_SRC" ]]; then
    python3 -m lizard "$PY_SRC" -l python \
        --CCN "$PY_CCN_THRESHOLD" \
        --length "$PY_LENGTH_THRESHOLD" \
        --arguments "$PY_PARAMS_THRESHOLD" \
        > "$PY_LIZARD_REPORT" 2>/dev/null || true
    python3 -m lizard "$PY_SRC" -l python \
        --CCN "$PY_CCN_THRESHOLD" \
        --length "$PY_LENGTH_THRESHOLD" \
        --arguments "$PY_PARAMS_THRESHOLD" \
        -w 2>&1 | grep "warning:" > "$PY_LIZARD_WARNINGS" || true
    py_lizard_count=$(wc -l < "$PY_LIZARD_WARNINGS" | tr -d ' ')
    echo "  Python lizard violations: $py_lizard_count (thresholds: CCN=$PY_CCN_THRESHOLD length=$PY_LENGTH_THRESHOLD params=$PY_PARAMS_THRESHOLD)"
    echo "  Report: $PY_LIZARD_REPORT"
else
    echo "  SKIP: Python source directory not found: $PY_SRC"
    echo "" > "$PY_LIZARD_WARNINGS"
fi
echo ""

# ── Python: complexipy (cognitive complexity) ───────────────────────
PY_COMPLEXIPY_OUT="$OUTDIR/python-complexipy.txt"
echo "--- Python (complexipy — cognitive complexity) ---"
if [[ -d "$PY_SRC" ]]; then
    set +e
    python3 -m complexipy.main "$PY_SRC" \
        --max-complexity-allowed "$PY_COGNITIVE_THRESHOLD" \
        --failed --color no \
        > "$PY_COMPLEXIPY_OUT" 2>&1
    complexipy_rc=$?
    set -e
    if [[ $complexipy_rc -eq 0 ]]; then
        echo "  Python cognitive complexity: PASS (all functions <= $PY_COGNITIVE_THRESHOLD)"
    else
        py_cog_count=$(grep -c "FAILED" "$PY_COMPLEXIPY_OUT" 2>/dev/null || echo "?")
        echo "  Python cognitive complexity violations: ~$py_cog_count (threshold: $PY_COGNITIVE_THRESHOLD)"
    fi
    echo "  Output: $PY_COMPLEXIPY_OUT"
else
    echo "  SKIP: Python source directory not found: $PY_SRC"
    echo "" > "$PY_COMPLEXIPY_OUT"
fi
echo ""

# ── Shell: shellcheck ───────────────────────────────────────────────
SHELLCHECK_OUT="$OUTDIR/shellcheck.txt"
echo "--- Shell (shellcheck) ---"
if [[ -s "$SHELL_FILES" ]]; then
    set +e
    xargs -0 shellcheck --severity=error -x -P tools/e2e -P tools/lib < "$SHELL_FILES" > "$SHELLCHECK_OUT" 2>&1
    shellcheck_rc=$?
    set -e
    if [[ $shellcheck_rc -eq 0 ]]; then
        echo "  Shell: PASS (no shellcheck errors)"
    else
        shellcheck_issues=$(grep -c ":" "$SHELLCHECK_OUT" 2>/dev/null || echo "?")
        echo "  Shell: $shellcheck_issues shellcheck issue(s) found"
        echo "  Output: $SHELLCHECK_OUT"
    fi
else
    echo "  SKIP: no tracked shell scripts found"
    echo "" > "$SHELLCHECK_OUT"
fi
echo ""

# ── Baseline comparison ─────────────────────────────────────────────
BASELINE="$SCRIPT_DIR/baseline.json"
BASELINE_REPORT="$OUTDIR/baseline-report.txt"

echo "--- Baseline Comparison ---"
if [[ -f "$BASELINE" ]]; then
    python3 "$SCRIPT_DIR/_compare_baseline.py" \
        --baseline "$BASELINE" \
        --lizard-c "$C_WARNINGS" \
        --lizard-rust "$RUST_WARNINGS" \
        --lizard-py "$PY_LIZARD_WARNINGS" \
        --complexipy "$PY_COMPLEXIPY_OUT" \
        --output "$BASELINE_REPORT"
    baseline_rc=$?
    echo "  Report: $BASELINE_REPORT"
else
    echo "  WARNING: baseline.json not found at $BASELINE — running without baseline gating" >&2
    echo "  All violations are reported but not gated. Create a baseline with:" >&2
    echo "    python3 tools/complexity/_generate_baseline.py" >&2
    baseline_rc=0
fi
echo ""

# ── Final summary ───────────────────────────────────────────────────
echo "=== Complexity Check Summary ==="
echo "Reports: $OUTDIR/"
ls -la "$OUTDIR/" 2>/dev/null || true

if [[ $baseline_rc -ne 0 ]]; then
    echo ""
    echo "FAIL: complexity check found new violations not in baseline"
    echo "  Review: $BASELINE_REPORT"
    echo "  If the new violations are acceptable, update the baseline:"
    echo "    python3 tools/complexity/_generate_baseline.py"
    exit 1
fi

if [[ ${shellcheck_rc:-0} -ne 0 ]]; then
    echo ""
    echo "FAIL: shellcheck found errors"
    echo "  Review: $SHELLCHECK_OUT"
    exit 1
fi

echo ""
echo "PASS: complexity check complete"
exit 0
