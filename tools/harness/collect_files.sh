#!/bin/bash
# Shared fail-closed NUL-delimited file enumeration for shell detectors.
# Callers pass an output file followed by the find arguments.  Keeping find in
# the caller's process preserves its exit status on macOS Bash 3.2 as well as
# on GNU Bash.

harness_collect_find0() {
    local output="$1"
    shift
    if ! find "$@" -print0 >"$output"; then
        return 1
    fi
    return 0
}
