#!/usr/bin/env bash
# detect_workflow_input_injection.sh — Detect GitHub Actions workflow input injection
#
# Rule (security-cwe, ci-gating): Direct interpolation of GitHub Actions inputs/outputs
# into shell run blocks allows command injection via crafted input values.
# Inputs must be routed through environment variables and referenced only as
# env vars in shell scripts, never via ${{ inputs.* }} or ${{ github.event.* }}
# direct interpolation in run blocks.
#
# This detector flags:
#   - ${{ inputs.* }} used directly inside run: blocks without env routing
#   - ${{ github.event.* }} used directly inside run: blocks without env routing
#
# Allowlist: ${{ inputs.* }} used inside env: blocks is safe and not flagged.
#   ${{ github.sha }}, ${{ github.ref }}, and ${{ github.event_name }} are
#   considered low-risk and not flagged (they are not user-controlled).
#
# Compatibility: macOS bash 3.2 (Rule 11), [[ ]] (Rule 18),
# POSIX ERE via grep -E (Rule 41).
#
# Usage:
#   bash tools/harness/detect_workflow_input_injection.sh [directory]
#     directory defaults to .github/workflows
#
# Exit codes:
#   0 — no actionable findings
#   1 — one or more input injection patterns found

set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORKFLOWS_DIR="${REPO_ROOT}/.github/workflows"

for arg in "$@"; do
    case "$arg" in
        --help|-h)
            cat <<USAGE
Usage: $0 [directory]
  directory defaults to ${WORKFLOWS_DIR}
  --help   show this help
USAGE
            exit 0
            ;;
        *)
            WORKFLOWS_DIR="$arg"
            ;;
    esac
done

if [[ ! -d "$WORKFLOWS_DIR" ]]; then
    echo "ERROR: directory not found: $WORKFLOWS_DIR" >&2
    exit 1
fi

findings=0

# Process each workflow YAML file
while IFS= read -r -d '' file; do
    rel_path="${file#${REPO_ROOT}/}"
    in_run_block=0
    in_env_block=0
    line_num=0

    while IFS= read -r line || [[ -n "$line" ]]; do
        line_num=$((line_num + 1))

        # Detect start/end of run: blocks (line starts with "run:" or contains "run: |")
        # YAML structure: we look for lines with "run:" that start a multiline block
        # or inline run: command
        if [[ "$line" =~ ^[[:space:]]*run:[[:space:]]*$ ]] || \
           [[ "$line" =~ ^[[:space:]]*run:[[:space:]]*\|[[:space:]]*$ ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*run:[[:space:]]*$ ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*run:[[:space:]]*\|[[:space:]]*$ ]]; then
            in_run_block=1
            in_env_block=0
            continue
        fi

        # Detect env: blocks (which are safe for input interpolation)
        if [[ "$line" =~ ^[[:space:]]*env:[[:space:]]*$ ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*env:[[:space:]]*$ ]]; then
            in_env_block=1
            in_run_block=0
            continue
        fi

        # Detect new step or job boundary (dedent to job/step level)
        if [[ "$line" =~ ^[[:space:]]*-[[:space:]]*name: ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*uses: ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*id: ]] || \
           [[ "$line" =~ ^[[:space:]]*steps: ]] || \
           [[ "$line" =~ ^[[:space:]]*jobs: ]]; then
            in_run_block=0
            in_env_block=0
            continue
        fi

        # Check for input interpolation inside run blocks
        if [[ $in_run_block -eq 1 ]]; then
            # Flag ${{ inputs.* }} inside run blocks
            if [[ "$line" =~ \$\{\{[[:space:]]*inputs\.[a-zA-Z_]+[[:space:]]*\}\} ]]; then
                echo "ERROR: ${rel_path}:${line_num}: inputs.* directly interpolated in run block" >&2
                echo "  ${line}" >&2
                echo "  Fix: route through env: INPUT_VAR: \${{ inputs.var }} and use \${INPUT_VAR} in shell" >&2
                findings=$((findings + 1))
            fi

            # Flag ${{ github.event.* }} inside run blocks (except release.created_at etc)
            # github.event.inputs.* is user-controlled
            if [[ "$line" =~ \$\{\{[[:space:]]*github\.event\.inputs\.[a-zA-Z_]+[[:space:]]*\}\} ]]; then
                echo "ERROR: ${rel_path}:${line_num}: github.event.inputs.* directly interpolated in run block" >&2
                echo "  ${line}" >&2
                echo "  Fix: route through env: INPUT_VAR: \${{ github.event.inputs.var }} and use \${INPUT_VAR}" >&2
                findings=$((findings + 1))
            fi
        fi

    done < "$file"
done < <(find "$WORKFLOWS_DIR" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) -print0)

if [[ $findings -gt 0 ]]; then
    echo "FAIL: found ${findings} workflow input injection pattern(s)" >&2
    exit 1
fi

echo "OK: no workflow input injection patterns found"
exit 0