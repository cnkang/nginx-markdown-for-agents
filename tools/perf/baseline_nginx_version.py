#!/usr/bin/env python3
"""Return the NGINX version the checked-in perf baselines were recorded on.

The release gate's evidence comparison fails closed when the run's
``nginx_version`` differs from the baseline's, so the benchmark must run on the
version the baseline actually recorded. Reading it out of the baseline replaces
"take the first amd64 entry in the release matrix", which silently drifted away
from the baseline and made every run incomparable.

Exits non-zero when baselines disagree with each other: that is a repository
state a benchmark cannot satisfy, and failing here names the conflict instead of
leaving it to surface as a threshold mismatch hours later.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib.path_validation import validate_read_path  # noqa: E402

_VERSION_PREFIX = "nginx version:"


def _recorded_version(path: pathlib.Path) -> str | None:
    """Return the bare ``x.y.z`` version a baseline recorded, if any."""
    try:
        validated = validate_read_path(path, purpose="perf baseline input")
        doc = json.loads(validated.read_text(encoding="utf-8"))
        raw = doc["module_benchmark"]["nginx_version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    text = str(raw).strip()
    if _VERSION_PREFIX in text:
        text = text.split(_VERSION_PREFIX, 1)[1]
    return text.strip().rsplit("/", 1)[-1] or None


def resolve(baseline_dir: pathlib.Path, glob: str) -> str:
    """Return the single NGINX version every matching baseline agrees on."""
    found: dict[str, list[str]] = {}
    try:
        candidates = sorted(validate_read_path(
            baseline_dir, purpose="perf baseline directory", must_exist=False
        ).glob(glob))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"FAIL: cannot read the baseline directory: {exc}") from exc
    for path in candidates:
        version = _recorded_version(path)
        if version:
            found.setdefault(version, []).append(path.name)

    if not found:
        print("")
        return ""
    if len(found) > 1:
        detail = "; ".join(
            f"{version} in {', '.join(sorted(names))}"
            for version, names in sorted(found.items())
        )
        raise SystemExit(
            f"FAIL: perf baselines disagree on the NGINX version: {detail}. "
            "Regenerate them on one environment before releasing."
        )
    return next(iter(found))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline-dir",
        default="perf/baselines",
        help="directory holding the checked-in baselines",
    )
    parser.add_argument(
        "--require-glob",
        default="module-baseline-*.json",
        help="glob selecting the baselines that must agree",
    )
    args = parser.parse_args()

    # Rule 33: validate the caller-supplied directory before any Path is built
    # from it, so a path outside the repository cannot be globbed.
    baseline_dir = validate_read_path(
        args.baseline_dir, purpose="perf baseline directory", must_exist=False
    )
    print(resolve(baseline_dir, args.require_glob))
    return 0


if __name__ == "__main__":
    sys.exit(main())