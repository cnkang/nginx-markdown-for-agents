#!/usr/bin/env python3
"""C-side license policy checks for nginx-module sources.

Checks:
1. Block strong copyleft markers in production C source/header files.
2. Enforce an allowlist for explicit linker libraries in `components/nginx-module/config`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "components/nginx-module/src"
MODULE_CONFIG = ROOT / "components/nginx-module/config"

STRONG_COPYLEFT_PATTERNS = [
    re.compile(r"SPDX-License-Identifier:.*\b(GPL|AGPL|LGPL|SSPL)\b", re.IGNORECASE),
    re.compile(r"GNU\s+(AFFERO\s+)?GENERAL\s+PUBLIC\s+LICENSE", re.IGNORECASE),
    re.compile(r"GNU\s+LESSER\s+GENERAL\s+PUBLIC\s+LICENSE", re.IGNORECASE),
]

ALLOWED_LINK_LIBS = {"pthread", "dl", "m"}


def scan_c_sources() -> list[str]:
    violations: list[str] = []
    for path in sorted(SRC_DIR.rglob("*")):
        if path.suffix not in {".c", ".h"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in STRONG_COPYLEFT_PATTERNS:
            if pattern.search(text):
                violations.append(f"{path}: matched forbidden license marker `{pattern.pattern}`")
                break
    return violations


def check_module_link_libs() -> list[str]:
    violations: list[str] = []
    cfg = MODULE_CONFIG.read_text(encoding="utf-8", errors="ignore")
    libs = sorted(set(re.findall(r"-l([A-Za-z0-9_+.-]+)", cfg)))
    unexpected = [lib for lib in libs if lib not in ALLOWED_LINK_LIBS]
    for lib in unexpected:
        violations.append(
            f"{MODULE_CONFIG}: unexpected linker library '-l{lib}' (allowed: {sorted(ALLOWED_LINK_LIBS)})"
        )
    return violations


def main() -> int:
    violations = []
    violations.extend(scan_c_sources())
    violations.extend(check_module_link_libs())

    if violations:
        print("C license policy check failed:")
        for item in violations:
            print(f"  - {item}")
        print("")
        print("Policy: production C code must not include GPL/AGPL/LGPL/SSPL license markers,")
        print("and explicit module linker libraries must stay within the reviewed allowlist.")
        return 1

    print("C license policy check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
