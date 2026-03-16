#!/usr/bin/env python3
"""Validate consistency between release-matrix.json and install.sh.

Ensures that:
  1. All os_type values in the matrix are detectable by install.sh
  2. All arch values in the matrix are detectable by install.sh
  3. The SUPPORTED_ARCHITECTURES variable in install.sh matches the matrix
  4. The asset naming convention is consistent between the matrix and install.sh

Exit code 0 = consistent, exit code 1 = inconsistencies found.

Usage:
    python3 tools/release/validate_matrix_install_consistency.py
"""

import json
import re
import sys
from pathlib import Path

# Paths relative to the repository root
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
MATRIX_PATH = REPO_ROOT / "tools" / "release-matrix.json"
INSTALL_SCRIPT_PATH = REPO_ROOT / "tools" / "install.sh"

# Known values that install.sh can detect
INSTALL_DETECTABLE_OS_TYPES = {"glibc", "musl"}
INSTALL_DETECTABLE_ARCHS = {"x86_64", "aarch64"}

# The asset naming template used by completeness_check.py
EXPECTED_ASSET_TEMPLATE = (
    "ngx_http_markdown_filter_module-{nginx}-{os_type}-{arch}.tar.gz"
)


def load_matrix(path: Path) -> list[dict]:
    """
    Load the release matrix from the given JSON file and return its entries.
    
    If the file contains a top-level "matrix" key, return its value; otherwise return an empty list.
    
    Returns:
        list[dict]: The list of matrix entries (each entry is a dict), or an empty list if no "matrix" key is present.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("matrix", [])


def extract_matrix_values(matrix: list[dict]) -> tuple[set[str], set[str]]:
    """
    Collects unique `os_type` and `arch` values from the release matrix.
    
    Parameters:
        matrix (list[dict]): Iterable of matrix entries where each entry may contain
            `"os_type"` and `"arch"` keys.
    
    Returns:
        tuple[set[str], set[str]]: A tuple with two sets: the first is the set of unique
        `os_type` strings found, the second is the set of unique `arch` strings found.
    """
    os_types = {entry["os_type"] for entry in matrix if "os_type" in entry}
    archs = {entry["arch"] for entry in matrix if "arch" in entry}
    return os_types, archs


def parse_install_script(path: Path) -> dict:
    """
    Extract detection and naming information from an install.sh script.
    
    Returns:
        dict: A mapping with the following keys:
            - supported_architectures (set[str]): Architecture identifiers listed in SUPPORTED_ARCHITECTURES.
            - asset_name_template (str): ASSET_NAME pattern normalized to Python-style placeholders (`{nginx}`, `{os_type}`, `{arch}`).
            - detectable_os_types (set[str]): OS type values the script can produce (e.g., `"glibc"`, `"musl"`).
            - detectable_archs (set[str]): Architecture values the script can produce (e.g., `"x86_64"`, `"aarch64"`).
    """
    content = path.read_text(encoding="utf-8")

    # Extract SUPPORTED_ARCHITECTURES="x86_64, aarch64"
    supported_archs_match = re.search(
        r'^SUPPORTED_ARCHITECTURES="([^"]*)"', content, re.MULTILINE
    )
    supported_archs: set[str] = set()
    if supported_archs_match:
        raw = supported_archs_match.group(1)
        supported_archs = {a.strip() for a in raw.split(",") if a.strip()}

    # Extract the ASSET_NAME pattern
    # ASSET_NAME="ngx_http_markdown_filter_module-${NGINX_VERSION}-${OS_TYPE}-${ARCH}.tar.gz"
    asset_name_match = re.search(
        r'^ASSET_NAME="([^"]*)"', content, re.MULTILINE
    )
    asset_name_template = ""
    if asset_name_match:
        raw_template = asset_name_match.group(1)
        # Normalize shell variables to Python format placeholders
        asset_name_template = raw_template.replace(
            "${NGINX_VERSION}", "{nginx}"
        ).replace(
            "${OS_TYPE}", "{os_type}"
        ).replace(
            "${ARCH}", "{arch}"
        )

    # Detect which OS_TYPE values the script can produce
    # The script sets OS_TYPE="glibc" by default, and OS_TYPE="musl" on musl detection
    detectable_os: set[str] = set()
    if re.search(r'OS_TYPE="glibc"', content):
        detectable_os.add("glibc")
    if re.search(r'OS_TYPE="musl"', content):
        detectable_os.add("musl")

    # Detect which ARCH values the script can produce
    # The script normalizes uname -m to "aarch64" or "x86_64"
    detectable_arch: set[str] = set()
    if re.search(r'ARCH="aarch64"', content):
        detectable_arch.add("aarch64")
    if re.search(r'ARCH="x86_64"', content):
        detectable_arch.add("x86_64")

    return {
        "supported_architectures": supported_archs,
        "asset_name_template": asset_name_template,
        "detectable_os_types": detectable_os,
        "detectable_archs": detectable_arch,
    }


def validate(
    matrix: list[dict], install_info: dict
) -> list[str]:
    """
    Validate consistency between the release matrix and the install script and collect any mismatch messages.
    
    Performs these checks: detectable os_type values, detectable arch values, SUPPORTED_ARCHITECTURES alignment, asset naming template match, and per-entry validity for entries with support_tier == "full".
    
    Returns:
        list[str]: Error messages for each inconsistency found; empty if no inconsistencies.
    """
    errors: list[str] = []

    installable = [e for e in matrix if e.get("support_tier") == "full"]
    matrix_os_types, matrix_archs = extract_matrix_values(installable)

    # Check 1: All matrix os_type values are detectable by install.sh
    unknown_os = matrix_os_types - install_info["detectable_os_types"]
    if unknown_os:
        errors.append(
            f"Matrix contains os_type values not detectable by install.sh: "
            f"{sorted(unknown_os)}"
        )

    # Check 2: All matrix arch values are detectable by install.sh
    unknown_arch = matrix_archs - install_info["detectable_archs"]
    if unknown_arch:
        errors.append(
            f"Matrix contains arch values not detectable by install.sh: "
            f"{sorted(unknown_arch)}"
        )

    # Check 3: SUPPORTED_ARCHITECTURES in install.sh matches matrix arch values
    if install_info["supported_architectures"] != matrix_archs:
        only_in_script = install_info["supported_architectures"] - matrix_archs
        only_in_matrix = matrix_archs - install_info["supported_architectures"]
        parts = []
        if only_in_script:
            parts.append(
                f"in install.sh but not in matrix: {sorted(only_in_script)}"
            )
        if only_in_matrix:
            parts.append(
                f"in matrix but not in install.sh SUPPORTED_ARCHITECTURES: "
                f"{sorted(only_in_matrix)}"
            )
        errors.append(
            f"SUPPORTED_ARCHITECTURES mismatch: {'; '.join(parts)}"
        )

    # Check 4: Asset naming convention matches
    if install_info["asset_name_template"] != EXPECTED_ASSET_TEMPLATE:
        errors.append(
            f"Asset naming template mismatch:\n"
            f"  install.sh: {install_info['asset_name_template']}\n"
            f"  expected:   {EXPECTED_ASSET_TEMPLATE}"
        )

    # Check 5: Verify every matrix combination produces a valid asset name
    for entry in installable:
        os_type = entry.get("os_type", "")
        arch = entry.get("arch", "")
        if os_type not in install_info.get("detectable_os_types", set()):
            errors.append(
                f"Matrix entry has unrecognized os_type '{os_type}': {entry}"
            )
        if arch not in install_info.get("detectable_archs", set()):
            errors.append(
                f"Matrix entry has unrecognized arch '{arch}': {entry}"
            )

    return errors


def main() -> int:
    """
    Run consistency checks between release-matrix.json and install.sh and report the result.
    
    Performs file existence checks, loads the release matrix and install script information,
    runs validations, prints a failure report when inconsistencies are found, and prints a
    brief summary on success.
    
    Returns:
        int: 0 if no inconsistencies were found; 1 if files are missing or inconsistencies were detected.
    """
    if not MATRIX_PATH.exists():
        print(f"ERROR: Matrix file not found: {MATRIX_PATH}", file=sys.stderr)
        return 1

    if not INSTALL_SCRIPT_PATH.exists():
        print(
            f"ERROR: Install script not found: {INSTALL_SCRIPT_PATH}",
            file=sys.stderr,
        )
        return 1

    matrix = load_matrix(MATRIX_PATH)
    install_info = parse_install_script(INSTALL_SCRIPT_PATH)

    errors = validate(matrix, install_info)

    if errors:
        print(
            "Consistency check FAILED — found inconsistencies between "
            "release-matrix.json and install.sh:",
            file=sys.stderr,
        )
        for i, err in enumerate(errors, 1):
            print(f"  {i}. {err}", file=sys.stderr)
        return 1

    # Summary on success
    matrix_os_types, matrix_archs = extract_matrix_values(matrix)
    print("Consistency check PASSED.")
    print(f"  os_type values: {sorted(matrix_os_types)}")
    print(f"  arch values:    {sorted(matrix_archs)}")
    print(f"  Matrix entries: {len(matrix)}")
    print(
        f"  Asset template: {EXPECTED_ASSET_TEMPLATE}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
