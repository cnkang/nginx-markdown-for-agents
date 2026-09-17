#!/usr/bin/env bash
# detect_workflow_input_injection.sh — Detect GitHub Actions workflow input injection
#
# Rule (security-cwe, ci-gating): Direct interpolation of GitHub Actions inputs or
# command-bearing step outputs into shell run blocks allows command injection via
# crafted values.
# Inputs must be routed through environment variables and referenced only as
# env vars in shell scripts, never via ${{ inputs.* }} or ${{ github.event.* }}
# direct interpolation in run blocks.
#
# This detector flags:
#   - ${{ inputs.* }} used directly inside run: blocks without env routing
#   - ${{ github.event.* }} used directly inside run: blocks without env routing
#   - ${{ steps.*.outputs.command }} used directly inside run: blocks
#   - the same injection classes inside an action step's command-bearing with:
#     inputs (args:, command:, script:, entrypoint:, options:, cli-args:,
#     build-args:, exec:, cmd:).  Those values reach the action's command line
#     the way a run block does, so "${{ inputs.* }}" there is the same
#     command-injection surface.  Other with: keys
#     (ref:, python-version:, tags:, title:, ...) are structured action inputs,
#     not command lines, and are not judged here; step outputs inside a with:
#     value are also out of scope for this detector.
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
. "${SCRIPT_DIR}/collect_files.sh"

# `${1+"$@"}` keeps an argument-less invocation safe under `set -u` on
# macOS bash 3.2, where a bare `"$@"` in a for-list is treated as unset.
for arg in ${1+"$@"}; do
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
file_list="$(mktemp "${TMPDIR:-/tmp}/workflow-input-files.XXXXXX")" || {
    echo "ERROR: cannot create the workflow file list" >&2
    exit 2
}
trap 'rm -f "$file_list"' EXIT

if ! harness_collect_find0 "$file_list" "$WORKFLOWS_DIR" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) 2>/dev/null; then
    echo "ERROR: cannot enumerate workflow files in $WORKFLOWS_DIR" >&2
    exit 2
fi

# Process each workflow YAML file
while IFS= read -r -d '' file; do
    rel_path="${file#${REPO_ROOT}/}"
    in_run_block=0
    inline_run_command=0
    line_num=0
    run_indent=0
    # Action-step `with:` tracking.  in_with_block marks that the
    # current line is inside a step's with: mapping; with_indent is that
    # mapping's key indentation; with_key tracks the innermost key so a nested
    # command-bearing input (with: { args: >- ... }) is judged, while wiring
    # keys (ref:, python-version:, tags:, ...) are not.  A reusable-workflow
    # job's with: is wiring, not an action command line, and is excluded
    # through in_reusable_job.
    in_with_block=0
    with_indent=0
    with_key=""
    steps_indent=-1
    in_reusable_job=0

    while IFS= read -r line || [[ -n "$line" ]]; do
        line_num=$((line_num + 1))
        inline_run_command=0

        # Compute the line's leading whitespace (tabs count as one column
        # for boundary purposes; YAML block content is always indented
        # deeper than its key, so the comparison stays conservative).
        indent_len=0
        while [[ "${line:indent_len:1}" == " " || "${line:indent_len:1}" == $'\t' ]]; do
            indent_len=$((indent_len + 1))
        done

        # ── Action-step with: tracking ──
        # A step's with: mapping is a sibling key of the step's uses: (written
        # either `- with:` or an undashed `with:` indented under the step).
        # A reusable-workflow job ALSO uses an undashed with: — at job-key
        # indentation, shallower than the steps: key — and that one is wiring,
        # not an action command line.  The steps: indent separates the two.
        if [[ "$line" =~ ^[[:space:]]*steps:[[:space:]]*$ ]]; then
            steps_indent=$indent_len
        fi
        if [[ "$line" =~ ^[[:space:]]*jobs:[[:space:]]*$ ]]; then
            steps_indent=-1
        fi
        if [[ "$line" =~ ^[[:space:]]*-[[:space:]]*with:[[:space:]]*$ ]]; then
            in_with_block=1
            with_indent=$indent_len
            with_key=""
            in_run_block=0
            continue
        fi
        if [[ "$line" =~ ^[[:space:]]*with:[[:space:]]*$ ]]; then
            with_key=""
            in_run_block=0
            if [[ "$steps_indent" -ge 0 && "$indent_len" -gt "$steps_indent" ]]; then
                in_with_block=1
                with_indent=$indent_len
            else
                # Job-level with: of a reusable workflow — wiring, not a command.
                in_with_block=0
            fi
            continue
        fi
        if [[ "$in_with_block" -eq 1 && -n "$line" ]]; then
            # A line at or shallower than the with: key ends the mapping.
            if [[ "$indent_len" -le "$with_indent" ]]; then
                in_with_block=0
                with_key=""
            elif [[ "$line" =~ ^[[:space:]]*([A-Za-z0-9_.-]+): ]]; then
                with_key="${BASH_REMATCH[1]}"
            fi
        fi

        # Detect start/end of run: blocks (line starts with "run:" or contains "run: |")
        # YAML structure: we look for lines with "run:" that start a multiline block
        # or inline run: command.  Recognize all YAML block scalar indicators:
        # |, |-, |+, >, >-, >+, plus YAML indentation indicators in
        # either order (|2, |2-, |-2) and trailing comments.
        block_scalar_re='^[[:space:]]*(-[[:space:]]*)?run:[[:space:]]*[|>][-+]?([0-9][-+]?)?([[:space:]]*#.*)?$'
        if [[ "$line" =~ ^[[:space:]]*run:[[:space:]]*$ ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*run:[[:space:]]*$ ]] || \
           [[ "$line" =~ $block_scalar_re ]]; then
            in_run_block=1
            run_indent=$indent_len
            continue
        fi

        # An inline run command is also shell source and needs the same
        # interpolation checks as a multiline run block.  Exclude YAML block
        # scalar indicators (|, |-, |+, >, >-) which are handled above as
        # block starts, not inline commands.
        inline_run_re='^[[:space:]]*run:[[:space:]]+[^|>]'
        inline_run_dash_re='^[[:space:]]*-[[:space:]]*run:[[:space:]]+[^|>]'
        if [[ "$line" =~ $inline_run_re ]] || \
           [[ "$line" =~ $inline_run_dash_re ]]; then
            inline_run_command=1
        fi

        # Detect env: blocks (which are safe for input interpolation).
        # A step-level env: key (with or without the list dash) ends any
        # preceding run block; a job-level env: key is a sibling of the
        # step list and also ends it.
        if [[ "$line" =~ ^[[:space:]]*env:[[:space:]]*$ ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*env:[[:space:]]*$ ]]; then
            in_run_block=0
            continue
        fi

        # Detect new step or job boundary (dedent to job/step level).
        # Subsequent step-level keys (if:, with:, shell:, working-directory:,
        # timeout-minutes:, continue-on-error:, env:) end any preceding run
        # block even when the step has no name/uses/id header — otherwise a
        # prior step's run state is carried into wiring keys like with: and
        # safe ${{ inputs.* }} expressions there are misreported.
        if [[ "$line" =~ ^[[:space:]]*-[[:space:]]*name: ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*uses: ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*id: ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*if: ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*with: ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*shell: ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*working-directory: ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*timeout-minutes: ]] || \
           [[ "$line" =~ ^[[:space:]]*-[[:space:]]*continue-on-error: ]] || \
           [[ "$line" =~ ^[[:space:]]*steps: ]] || \
           [[ "$line" =~ ^[[:space:]]*jobs: ]]; then
            in_run_block=0
            continue
        fi

        # A sibling key at the same indentation as the run key (or
        # shallower) ends the run block even when it has no list dash —
        # e.g. an unprefixed `if:` or `env:` continuation key.  Without
        # this boundary the scanner carries run state into wiring keys
        # that are not shell source.  Blank lines never end the block:
        # a blank line inside a block scalar is content, and a blank
        # line between steps must not clear run state before the next
        # step's keys are seen.
        if [[ $in_run_block -eq 1 && $indent_len -le $run_indent && -n "$line" ]]; then
            in_run_block=0
        fi

        # A reusable-workflow job uses a plain `uses:` key (without the
        # step-list dash).  It ends any preceding step run block; its `with:`
        # values are workflow wiring, not shell source.  Without this boundary
        # the scanner can carry a prior step's run state into the next job and
        # report safe module_ref/module_sha wiring as shell interpolation.
        if [[ "$line" =~ ^[[:space:]]*uses:[[:space:]]+ ]]; then
            in_run_block=0
            continue
        fi

        # Action-step `with:` command inputs.  A command-bearing input
        # (args:, command:, script:, entrypoint:, options:, cli-args:,
        # build-args:, exec:, cmd:) is handed to the action's command line, so
        # a direct ${{ inputs.* }} there -- in dot or bracket selector form --
        # is the same command-injection surface as a run block.  Other with:
        # keys are structured inputs (ref:, python-version:, tags:, ...) and
        # are not judged here.
        if [[ "$in_with_block" -eq 1 && -n "$line" ]]; then
            case "$with_key" in
                args|command|script|entrypoint|options|cli-args|build-args|exec|cmd)
                    if [[ "$line" =~ \$\{\{[[:space:]]*inputs(\.[a-zA-Z_][a-zA-Z0-9_-]*|\[[^]]*\]) ]]; then
                        echo "ERROR: ${rel_path}:${line_num}: inputs.* directly interpolated in an action 'with:' command input (${with_key}:)" >&2
                        echo "  ${line}" >&2
                        echo "  Fix: route through env: INPUT_VAR: \${{ inputs.var }}, validate it in a step, and pass the derived value" >&2
                        findings=$((findings + 1))
                    fi
                    if [[ "$line" =~ \$\{\{[[:space:]]*github\.event\.inputs(\.[a-zA-Z_][a-zA-Z0-9_-]*|\[[^]]*\]) ]]; then
                        echo "ERROR: ${rel_path}:${line_num}: github.event.inputs.* directly interpolated in an action 'with:' command input (${with_key}:)" >&2
                        echo "  ${line}" >&2
                        echo "  Fix: route through env: INPUT_VAR: \${{ github.event.inputs.var }} and validate it in a step" >&2
                        findings=$((findings + 1))
                    fi
                    ;;
                *)
                    # Structured action input (ref:, python-version:, tags:, ...):
                    # not a command line, so it is not judged here.
                    ;;
            esac
        fi

        # Check for input interpolation inside run blocks
        if [[ $in_run_block -eq 1 || $inline_run_command -eq 1 ]]; then
            # Flag ${{ inputs.* }} inside run blocks (dot or bracket selector)
            if [[ "$line" =~ \$\{\{[[:space:]]*inputs(\.[a-zA-Z_][a-zA-Z0-9_-]*|\[[^]]*\])[[:space:]]*\}\} ]]; then
                echo "ERROR: ${rel_path}:${line_num}: inputs.* directly interpolated in run block" >&2
                echo "  ${line}" >&2
                echo "  Fix: route through env: INPUT_VAR: \${{ inputs.var }} and use \${INPUT_VAR} in shell" >&2
                findings=$((findings + 1))
            fi

            # Flag ${{ github.event.* }} inside run blocks (except release.created_at etc)
            # github.event.inputs.* is user-controlled
            if [[ "$line" =~ \$\{\{[[:space:]]*github\.event\.inputs(\.[a-zA-Z_][a-zA-Z0-9_-]*|\[[^]]*\])[[:space:]]*\}\} ]]; then
                echo "ERROR: ${rel_path}:${line_num}: github.event.inputs.* directly interpolated in run block" >&2
                echo "  ${line}" >&2
                echo "  Fix: route through env: INPUT_VAR: \${{ github.event.inputs.var }} and use \${INPUT_VAR}" >&2
                findings=$((findings + 1))
            fi

            # Step outputs are executable data unless the selector names a
            # documented benign data value (version, sha, path, name, ...);
            # anything else is treated as command-bearing and flagged.
            # Accept either dot or index syntax for both the step selector
            # and the output selector, including mixed forms such as
            # steps.build.outputs['command'] and steps['build'].outputs.command.
            # Benign selectors such as steps.meta.outputs.version are not
            # command-bearing inputs and are not flagged; unknown selectors
            # fail closed so a new command-shaped output cannot slip through.
            step_output_re='^[^$]*\$(\{\{[[:space:]]*steps(\.[a-zA-Z_][a-zA-Z0-9_-]*|\[[^]]*\])\.[[:space:]]*outputs(\.[a-zA-Z_][a-zA-Z0-9_-]*|\[[^]]*\])[[:space:]]*\}\})'
            remaining_line="$line"
            while [[ -n "$remaining_line" ]]; do
                if [[ "$remaining_line" =~ $step_output_re ]]; then
                    full_match="${BASH_REMATCH[0]}"
                    output_selector="${BASH_REMATCH[3]}"
                    # Normalize the index form (steps['build'].outputs['version'])
                    # to the dot form before the allowlist match: strip the
                    # surrounding brackets AND any single/double quotes so both
                    # forms become the same bare identifier the case patterns
                    # compare against.
                    if [[ "$output_selector" == \[*\] ]]; then
                        output_selector="${output_selector#[}"
                        output_selector="${output_selector%\]}"
                        output_selector="${output_selector//\'/}"
                        output_selector="${output_selector//\"/}"
                    fi
                    benign_data_selector="${output_selector#.}"
                    case "${benign_data_selector}" in
                        sha|version|raw_version|bench_nginx_version|nginx_version|path|repo|buildroot|nginx_bin|deb_filename|rpm_filename|tap_name|pull-request-number|enabled|supported|blocking|changed|has_changes|install_exit|nginx_test_exit|module_found|skip_reason|package_matrix|matrix_entries|smoke_matrix|musl_matrix|nginx_versions|targets|policy_reference)
                            ;;
                        *)
                            echo "ERROR: ${rel_path}:${line_num}: step output directly interpolated in run block" >&2
                            echo "  ${line}" >&2
                            echo "  Fix: map a fixed identifier through env and a shell case statement" >&2
                            findings=$((findings + 1))
                            ;;
                    esac
                    remaining_line="${remaining_line:${#full_match}}"
                    continue
                fi

                # Skip through a non-step interpolation or ordinary text so a
                # later step-output interpolation on the same line is still
                # audited.  This is deliberately anchored to the next dollar
                # sign; it does not treat an unmatched prefix as clean.
                if [[ "$remaining_line" =~ ^[^$]*\$ ]]; then
                    prefix_to_dollar="${BASH_REMATCH[0]}"
                    remaining_line="${remaining_line:${#prefix_to_dollar}}"
                    continue
                fi
                break
            done

            # External event data (PR head ref/title/body, release tag name,
            # issue fields, head_ref, ref_name) is attacker-influenced for
            # pull_request/release/issue events and must never be interpolated
            # into run-block shell source.  Route through env with explicit
            # validation instead.
            # Both selector forms are matched: dot form
            # (github.event.pull_request.head.ref) and index form
            # (github.event['pull_request'].head.ref /
            #  github.event.pull_request['head']['ref']) — the index form
            # bypasses a dot-only pattern while carrying the same
            # attacker-controlled data.
            # Dot-form selector (github.event.pull_request.head.ref),
            # index-form selector (github.event['pull_request'].head.ref /
            # github.event.pull_request['head']['ref']), and the head_ref /
            # ref_name aliases.  Three separate patterns avoid embedding a
            # double-quote literal inside [[ =~ ]] (which would terminate
            # the quoted regex) while still matching both selector forms.
            if [[ "$line" =~ \$\{\{[[:space:]]*github\.event\.[a-zA-Z_][a-zA-Z0-9_]*((\.[a-zA-Z_.]+|\[[^]]*\])+)[[:space:]]*\}\} ]] || \
               [[ "$line" =~ \$\{\{[[:space:]]*github\.event\[[^]]*\](\.[a-zA-Z_.]+|\[[^]]*\])+[[:space:]]*\}\} ]] || \
               [[ "$line" =~ \$\{\{[[:space:]]*(github\.head_ref|github\.ref_name)[[:space:]]*\}\} ]]; then
                echo "ERROR: ${rel_path}:${line_num}: external event data directly interpolated in run block" >&2
                echo "  ${line}" >&2
                echo "  Fix: route through env and validate the value (e.g. against ^[0-9]+$ for refs)" >&2
                findings=$((findings + 1))
            fi
        fi

    done < "$file"
done < "$file_list"

if [[ $findings -gt 0 ]]; then
    echo "FAIL: found ${findings} workflow input injection pattern(s)" >&2
    exit 1
fi

echo "OK: no workflow input injection patterns found"
exit 0
