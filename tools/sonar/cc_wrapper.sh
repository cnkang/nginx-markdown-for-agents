#!/usr/bin/env bash
# cc_wrapper.sh — Compiler wrapper for capturing compile commands.
#
# Wraps the real C compiler to intercept compilation invocations and
# append each compile command as a JSON entry to a capture file.  Used
# by generate_compile_commands.sh to build a compile_commands.json
# database for SonarQube / VS Code C/C++ analysis.
#
# Environment variables:
#   SONAR_REAL_CC          Path to the real C compiler (default: cc)
#   SONAR_CC_CAPTURE_FILE  Path to the JSONL capture file (default: none,
#                          meaning no capture is performed)
#
# The wrapper passes all arguments through to the real compiler unchanged.
# It only records commands that include -c (compile-only) and a .c source
# file argument.
#
# Exit behaviour:
#   Delegates to the real compiler; exits with its return code.
set -euo pipefail

REAL_CC="${SONAR_REAL_CC:-}"
CAPTURE_FILE="${SONAR_CC_CAPTURE_FILE:-}"

if [[ -z "${REAL_CC}" ]]; then
  REAL_CC="$(command -v cc)"
fi

if [[ -n "${CAPTURE_FILE}" ]]; then
  is_compile=0
  c_file=""

  for arg in "$@"; do
    if [[ "${arg}" == "-c" ]]; then
      is_compile=1
    fi

    case "${arg}" in
      *.c)
        c_file="${arg}"
        ;;
      *)
        ;;
    esac
  done

  if [[ ${is_compile} -eq 1 && -n "${c_file}" ]]; then
    python3 - "${CAPTURE_FILE}" "${PWD}" "${REAL_CC}" "$@" <<'PY'
import json
import os
import sys

capture_file = sys.argv[1]
cwd = sys.argv[2]
real_cc = sys.argv[3]
args = sys.argv[4:]

source_file = ""
for value in reversed(args):
    if value.endswith(".c"):
        source_file = value
        break

if source_file:
    file_path = source_file if os.path.isabs(source_file) else os.path.normpath(os.path.join(cwd, source_file))
    entry = {
        "directory": cwd,
        "arguments": [real_cc, *args],
        "file": file_path,
    }
    with open(capture_file, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
PY
  fi
fi

exec "${REAL_CC}" "$@"
