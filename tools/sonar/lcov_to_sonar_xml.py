#!/usr/bin/env python3
"""Convert one or more lcov files to SonarQube Generic Coverage XML.

Usage:
    python3 tools/sonar/lcov_to_sonar_xml.py -o coverage.xml coverage/*.lcov

The output XML follows the SonarQube Generic Test Data format:
https://docs.sonarsource.com/sonarqube-cloud/enriching/test-coverage/generic-test-data/
"""

from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # noqa: E402
from lib.path_validation import validate_read_path


def _parse_da_line(
    line: str,
    result: dict[str, dict[int, int]],
    current_file: str,
) -> None:
    """Parse a single DA: (line-data) record and merge into result."""
    parts = line[3:].split(",", 2)
    if len(parts) < 2:
        return
    try:
        lineno = int(parts[0])
        hits = int(parts[1])
    except ValueError:
        return
    file_lines = result.setdefault(current_file, {})
    file_lines[lineno] = max(file_lines.get(lineno, 0), hits)


def parse_lcov(path: str) -> dict[str, dict[int, int]]:
    """Parse an lcov file and return {filepath: {line: hit_count}}."""
    result: dict[str, dict[int, int]] = {}
    current_file: str | None = None

    validated = validate_read_path(path, purpose="lcov input")
    with open(validated, encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            if line.startswith("SF:"):
                current_file = line[3:]
            elif line.startswith("DA:") and current_file is not None:
                _parse_da_line(line, result, current_file)
            elif line == "end_of_record":
                current_file = None

    return result


def merge_coverage(
    target: dict[str, dict[int, int]],
    source: dict[str, dict[int, int]],
) -> None:
    """Merge source coverage into target, taking max hit count per line."""
    for filepath, lines in source.items():
        if filepath not in target:
            target[filepath] = {}
        for lineno, hits in lines.items():
            existing = target[filepath].get(lineno, 0)
            target[filepath][lineno] = max(existing, hits)


def _resolve_to_workspace_path(filepath: str, workspace: str) -> str | None:
    """Resolve lcov source path to a workspace-relative file path.

    lcov records may come from temporary NGINX build roots (for example
    /tmp/nginx-coverage-build.*/...) that mirror repository subpaths. This
    helper re-roots known repo suffixes back into the current workspace so
    Sonar can import those file entries.
    """
    workspace_abs = os.path.abspath(workspace)
    path_abs = os.path.abspath(filepath)

    if path_abs.startswith(workspace_abs + os.sep):
        return path_abs

    anchored_suffixes = [
        os.path.join("components", "nginx-module") + os.sep,
        os.path.join("components", "rust-converter") + os.sep,
        os.path.join("tools") + os.sep,
        os.path.join("tests") + os.sep,
    ]

    for suffix in anchored_suffixes:
        marker = os.sep + suffix
        idx = path_abs.find(marker)
        if idx == -1:
            continue
        candidate = os.path.join(workspace_abs, path_abs[idx + 1 :])
        if os.path.isfile(candidate):
            return candidate

    return None


def to_sonar_xml(
    coverage: dict[str, dict[int, int]],
    workspace: str,
) -> ET.Element:
    """Build SonarQube Generic Coverage XML element tree."""
    root = ET.Element("coverage", version="1")

    for filepath in sorted(coverage):
        resolved = _resolve_to_workspace_path(filepath, workspace)
        if resolved is None:
            continue
        rel = os.path.relpath(resolved, workspace)
        if rel.startswith(".."):
            continue
        file_el = ET.SubElement(root, "file", path=rel)
        for lineno in sorted(coverage[filepath]):
            hits = coverage[filepath][lineno]
            covered = "true" if hits > 0 else "false"
            ET.SubElement(
                file_el,
                "lineToCover",
                lineNumber=str(lineno),
                covered=covered,
            )

    return root


def main() -> int:
    """Parse CLI arguments, merge lcov files, and write SonarQube XML."""
    parser = argparse.ArgumentParser(
        description="Convert lcov files to SonarQube Generic Coverage XML",
    )
    parser.add_argument(
        "lcov_files",
        nargs="+",
        help="One or more lcov files to merge and convert",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="Output XML file path",
    )
    parser.add_argument(
        "--workspace",
        default=os.getcwd(),
        help="Workspace root for computing relative paths (default: cwd)",
    )
    args = parser.parse_args()

    merged: dict[str, dict[int, int]] = {}
    for lcov_path in args.lcov_files:
        if not os.path.isfile(lcov_path):
            print(f"WARNING: skipping missing file: {lcov_path}", file=sys.stderr)
            continue
        data = parse_lcov(lcov_path)
        merge_coverage(merged, data)

    if not merged:
        print("WARNING: no coverage data found", file=sys.stderr)
        root = ET.Element("coverage", version="1")
    else:
        root = to_sonar_xml(merged, args.workspace)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    tree.write(args.output, encoding="unicode", xml_declaration=True)
    print(f"Wrote {args.output} ({len(merged)} files)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
