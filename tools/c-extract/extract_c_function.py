#!/usr/bin/env python3
"""Extract a C function definition by name using simple brace matching.

Intended for test harnesses that compile an extracted function from a production
source file without depending on brittle line-number or grep/sed pipelines.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="Path to C source file")
    parser.add_argument("--function-name", required=True, help="Function name to extract")
    parser.add_argument(
        "--signature-prefix",
        default="",
        help="Exact prefix line immediately before the function name (for example 'static ngx_int_t')",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    src = Path(args.source).read_text(encoding="utf-8")

    target_line = f"{args.function_name}("
    if args.signature_prefix:
        needle = f"{args.signature_prefix}\n{target_line}"
    else:
        needle = target_line

    start = src.find(needle)
    if start == -1:
        print(f"failed to find function signature for {args.function_name}", file=sys.stderr)
        return 1

    brace_start = src.find("{", start)
    if brace_start == -1:
        print(f"failed to find function body start for {args.function_name}", file=sys.stderr)
        return 1

    depth = 0
    end = None
    for i, ch in enumerate(src[brace_start:], start=brace_start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end is None:
        print(f"failed to find function body end for {args.function_name}", file=sys.stderr)
        return 1

    sys.stdout.write(src[start:end] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
