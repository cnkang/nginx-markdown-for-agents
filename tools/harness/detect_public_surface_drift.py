#!/usr/bin/env python3
"""Detect drift between the public surface inventory and actual source code."""

from __future__ import print_function

import argparse
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from lib.path_validation import validate_read_path  # noqa: E402

INVENTORY_PATH = os.path.join(
    ROOT, "docs", "harness", "public-surface-inventory.json"
)
DIRECTIVES_PATH = os.path.join(
    ROOT,
    "components",
    "nginx-module",
    "src",
    "ngx_http_markdown_config_directives_impl.h",
)
REASON_CODE_PATH = os.path.join(
    ROOT,
    "components",
    "rust-converter",
    "src",
    "decision",
    "reason_code.rs",
)
FFI_EXPORTS_PATH = os.path.join(
    ROOT,
    "components",
    "rust-converter",
    "src",
    "ffi",
    "exports.rs",
)
FFI_INCREMENTAL_PATH = os.path.join(
    ROOT,
    "components",
    "rust-converter",
    "src",
    "ffi",
    "incremental.rs",
)
FFI_STREAMING_PATH = os.path.join(
    ROOT,
    "components",
    "rust-converter",
    "src",
    "ffi",
    "streaming.rs",
)

DIRECTIVE_RE = re.compile(r'ngx_string\("(markdown_[^"]+)"\)')
REASON_CODE_RE = re.compile(
    r'^\s+(\w+)\s*=\s*(\d+)\s*,', re.MULTILINE
)
FFI_FN_RE = re.compile(
    r'pub\s+(?:unsafe\s+)?extern\s+"C"\s+fn\s+(markdown_\w+)\s*\('
)


def read_text(path):
    validated = validate_read_path(path, purpose="public surface drift")
    return validated.read_text(encoding="utf-8")


def load_inventory():
    text = read_text(INVENTORY_PATH)
    return json.loads(text)


def extract_directives_from_c():
    text = read_text(DIRECTIVES_PATH)
    return DIRECTIVE_RE.findall(text)


def extract_reason_codes_from_rust():
    text = read_text(REASON_CODE_PATH)
    found = {}
    for m in REASON_CODE_RE.finditer(text):
        name = m.group(1)
        disc = int(m.group(2))
        found[disc] = name
    return found


def extract_ffi_exports_from_rust():
    names = set()
    for path in (FFI_EXPORTS_PATH, FFI_INCREMENTAL_PATH, FFI_STREAMING_PATH):
        text = read_text(path)
        for m in FFI_FN_RE.finditer(text):
            names.add(m.group(1))
    return sorted(names)


def check_directives(inventory, actual_names):
    inv_names = set(d["name"] for d in inventory["directives"])
    inv_reject = set(d["name"] for d in inventory["reject_only_directives"])
    inv_otel_active = set(inventory["otel"]["directives"])
    inv_otel_reject = set(inventory["otel"]["reject_only"])
    inv_all = inv_names | inv_reject | inv_otel_active | inv_otel_reject

    actual = set(actual_names)
    drift = []

    added = actual - inv_all
    if added:
        drift.append("directives in source but not in inventory: {}".format(
            ", ".join(sorted(added))))

    removed = inv_all - actual
    if removed:
        drift.append("directives in inventory but not in source: {}".format(
            ", ".join(sorted(removed))))

    return drift


def check_reason_codes(inventory, actual_codes):
    inv_by_disc = {rc["discriminant"]: rc["name"] for rc in inventory["reason_codes"]}
    drift = []

    added = set(actual_codes.keys()) - set(inv_by_disc.keys())
    if added:
        for disc in sorted(added):
            drift.append("reason code in source but not in inventory: {}={}".format(
                disc, actual_codes[disc]))

    removed = set(inv_by_disc.keys()) - set(actual_codes.keys())
    if removed:
        for disc in sorted(removed):
            drift.append("reason code in inventory but not in source: {}={}".format(
                disc, inv_by_disc[disc]))

    common = set(actual_codes.keys()) & set(inv_by_disc.keys())
    for disc in sorted(common):
        if actual_codes[disc] != inv_by_disc[disc]:
            drift.append("reason code name mismatch at discriminant {}: "
                         "inventory={} source={}".format(
                             disc, inv_by_disc[disc], actual_codes[disc]))

    return drift


def check_ffi_exports(inventory, actual_exports):
    inv_exports = set(inventory["ffi_exports"])
    actual = set(actual_exports)
    drift = []

    added = actual - inv_exports
    if added:
        drift.append("FFI exports in source but not in inventory: {}".format(
            ", ".join(sorted(added))))

    removed = inv_exports - actual
    if removed:
        drift.append("FFI exports in inventory but not in source: {}".format(
            ", ".join(sorted(removed))))

    return drift


def main():
    parser = argparse.ArgumentParser(
        description="Detect drift between public surface inventory and source code")
    parser.add_argument(
        "--inventory", default=INVENTORY_PATH,
        help="Path to public-surface-inventory.json")
    args = parser.parse_args()

    inventory = load_inventory()
    all_drift = []

    actual_directives = extract_directives_from_c()
    all_drift.extend(check_directives(inventory, actual_directives))

    actual_reason_codes = extract_reason_codes_from_rust()
    all_drift.extend(check_reason_codes(inventory, actual_reason_codes))

    actual_ffi = extract_ffi_exports_from_rust()
    all_drift.extend(check_ffi_exports(inventory, actual_ffi))

    if all_drift:
        for msg in all_drift:
            print("DRIFT: {}".format(msg), file=sys.stderr)
        print("public surface drift detected ({} issue(s))".format(
            len(all_drift)), file=sys.stderr)
        return 1

    print("public surface inventory is synchronized with source code")
    return 0


if __name__ == "__main__":
    sys.exit(main())