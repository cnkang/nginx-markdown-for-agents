#!/usr/bin/env python3
"""Map one SemVer release version onto Debian and RPM package versions.

The public release identity is SemVer (``1.0.0-rc.1``), but neither package
family can use that string verbatim:

* Debian orders a prerelease **below** the final release only through the
  ``~`` separator, so ``1.0.0-rc.1`` becomes upstream version ``1.0.0~rc1``.
  Written as ``1.0.0-rc1`` it would sort *above* ``1.0.0``.
* RPM uses the same ``~`` marker in the ``Version`` field, because
  ``rpmvercmp`` treats a numeric leading segment as newer than a letter
  segment, so a ``Release``-first marker such as ``0.rc1`` sorts *above* the
  existing ``nginx<ver>.1`` release.  ``Version: 1.0.0~rc1`` with the
  unchanged release sorts correctly and keeps every stable package's metadata
  identical to what earlier releases published.

The mapping is deliberately a pure function of the version string and the
target NGINX version, so the release workflow, the local packaging target, and
the ordering tests all derive the same answer.

Usage:
    release_version.py --version 1.0.0-rc.1 --nginx-version 1.28.3
    release_version.py --version 1.0.0-rc.1 --nginx-version 1.28.3 --field deb_version
    release_version.py --version 1.0.0-rc.1 --nginx-version 1.28.3 --format shell
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Dict, Optional

CORE_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
IDENTIFIER_RE = re.compile(r"^[0-9A-Za-z-]+$")


def _split_semver(version: str) -> Dict[str, Any]:
    """Split and validate a SemVer 2.0.0 string, rejecting ambiguous forms."""
    if not isinstance(version, str) or not version:
        raise ValueError("version must be a non-empty string")
    if "+" in version:
        raise ValueError(
            "build metadata is not part of the package identity: {}".format(version)
        )
    core, separator, prerelease = version.partition("-")
    match = CORE_RE.match(core)
    if match is None:
        raise ValueError(
            "version core must be MAJOR.MINOR.PATCH without leading zeros: {}".format(
                core
            )
        )
    if separator and not prerelease:
        raise ValueError("version has a prerelease separator with no identifiers")
    identifiers = []
    if prerelease:
        for identifier in prerelease.split("."):
            if not identifier or not IDENTIFIER_RE.match(identifier):
                raise ValueError(
                    "prerelease identifier is empty or invalid: {}".format(identifier)
                )
            if identifier.isdigit() and len(identifier) > 1 and identifier[0] == "0":
                raise ValueError(
                    "numeric prerelease identifier has a leading zero: {}".format(
                        identifier
                    )
                )
            identifiers.append(identifier)
    return {
        "major": int(match.group(1)),
        "minor": int(match.group(2)),
        "patch": int(match.group(3)),
        "core": core,
        "prerelease": ".".join(identifiers),
    }


def _debian_prerelease(prerelease: str) -> str:
    """Return the Debian upstream suffix, preserving identifier boundaries.

    Debian upstream versions may contain letters, digits, dots, plus signs,
    tildes, and hyphens, so the SemVer text survives unchanged.  Collapsing the
    separators would map ``rc.1`` and ``rc1`` onto the same package version.
    """
    return prerelease


def _rpm_prerelease(prerelease: str) -> str:
    """Return the RPM version suffix, which may not contain a hyphen.

    A SemVer identifier allows only letters, digits, and hyphens, so replacing
    a hyphen with an underscore cannot collide with a valid identifier.
    """
    return prerelease.replace("-", "_")


def packaging_versions(version: str, nginx_version: Optional[str] = None) -> Dict[str, Any]:
    """Return the Debian and RPM version mapping for one release version."""
    parsed = _split_semver(version)
    prerelease = parsed["prerelease"]
    is_prerelease = bool(prerelease)
    deb_version = "{}{}".format(
        parsed["core"],
        "~" + _debian_prerelease(prerelease) if is_prerelease else "",
    )
    result: Dict[str, Any] = {
        "input": version,
        "core": parsed["core"],
        "prerelease": prerelease,
        "is_prerelease": is_prerelease,
        "deb_version": deb_version,
        "rpm_version": "{}{}".format(
            parsed["core"],
            "~" + _rpm_prerelease(prerelease) if is_prerelease else "",
        ),
        "rpm_release": "",
        "deb_release": "",
    }
    if nginx_version:
        if not re.match(r"^[0-9]+\.[0-9]+\.[0-9]+$", nginx_version):
            raise ValueError(
                "nginx version must be MAJOR.MINOR.PATCH: {}".format(nginx_version)
            )
        result["rpm_release"] = "nginx{}.1".format(nginx_version)
        result["deb_release"] = "nginx{}".format(nginx_version)
        result["deb_package_version"] = "{}-{}".format(
            deb_version, result["deb_release"]
        )
        result["rpm_nvr"] = "{}-{}".format(
            result["rpm_version"], result["rpm_release"]
        )
    return result


def _emit(result: Dict[str, Any], fmt: str) -> str:
    """Render the mapping in the requested format."""
    if fmt == "json":
        return json.dumps(result, indent=2, sort_keys=True)
    if fmt == "shell":
        lines = []
        for key in sorted(result):
            value = result[key]
            if isinstance(value, bool):
                value = "true" if value else "false"
            lines.append("{}={}".format(key.upper(), value))
        return "\n".join(lines)
    raise ValueError("unsupported output format: {}".format(fmt))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="release version (SemVer)")
    parser.add_argument(
        "--nginx-version",
        default="",
        help="target NGINX version; required for the package release fields",
    )
    parser.add_argument("--format", default="json", choices=["json", "shell"])
    parser.add_argument("--field", default="", help="print one field only")
    return parser


def main(argv: Optional[list] = None) -> int:
    """Run the mapping and print the result."""
    args = _parser().parse_args(argv)
    try:
        result = packaging_versions(args.version, args.nginx_version or None)
    except ValueError as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        return 1
    if args.field:
        key = args.field
        if key not in result:
            print(
                "ERROR: unknown field {} (available: {})".format(
                    key, ", ".join(sorted(result))
                ),
                file=sys.stderr,
            )
            return 1
        value = result[key]
        if isinstance(value, bool):
            value = "true" if value else "false"
        print(value)
        return 0
    print(_emit(result, args.format))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
