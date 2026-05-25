#!/usr/bin/env python3
"""
Kubernetes manifest and Helm chart validator for v0.7.0 release gates.

Validates that K8s deployment artifacts exist and are well-formed:

1. charts/nginx-markdown/Chart.yaml exists and has required fields
   (apiVersion, name, version, appVersion)
2. examples/kubernetes/manifest/ directory exists with at least one
   .yaml file
3. Basic YAML syntax validation (parse each file without errors)

Exit codes:
  0 - All checks passed
  1 - One or more checks failed

Security: All file reads use Path.resolve() within PROJECT_ROOT.
No user-supplied patterns are compiled at runtime.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

CHART_YAML = PROJECT_ROOT / "charts" / "nginx-markdown" / "Chart.yaml"
K8S_MANIFEST_DIR = PROJECT_ROOT / "examples" / "kubernetes" / "manifest"

CHART_REQUIRED_FIELDS = ["apiVersion", "name", "version", "appVersion"]


class ValidationResult:
    """Accumulates PASS/FAIL results for reporting."""

    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def pass_(self, check_id: str, message: str) -> None:
        self.results.append(("PASS", check_id, message))

    def fail(self, check_id: str, message: str) -> None:
        self.results.append(("FAIL", check_id, message))

    @property
    def has_failures(self) -> bool:
        return any(s == "FAIL" for s, _, _ in self.results)


def read_safe(path: Path) -> str:
    """Read file content safely, returning empty string if missing."""
    resolved = path.resolve()
    if not str(resolved).startswith(str(PROJECT_ROOT)):
        return ""
    if resolved.is_file():
        return resolved.read_text(encoding="utf-8")
    return ""


def try_parse_yaml(content: str) -> tuple[bool, str]:
    """Attempt to parse YAML content, return (success, error_message)."""
    try:
        import yaml  # noqa: F401 — used for parsing

        list(yaml.safe_load_all(content))
        return True, ""
    except ImportError:
        # PyYAML not available; fall back to basic syntax check
        # Verify no obvious syntax errors (tabs in indentation)
        for i, line in enumerate(content.splitlines(), 1):
            if line.startswith("\t"):
                return False, f"line {i}: tab indentation (YAML requires spaces)"
        return True, ""
    except Exception as exc:
        return False, str(exc)


def validate_chart_yaml(result: ValidationResult) -> None:
    """Validate the Helm Chart.yaml exists and has required fields."""
    check_id = "helm:chart_exists"
    content = read_safe(CHART_YAML)
    if not content:
        result.fail(check_id, "charts/nginx-markdown/Chart.yaml not found")
        return
    result.pass_(check_id, "Chart.yaml exists")

    for field in CHART_REQUIRED_FIELDS:
        fid = f"helm:field:{field}"
        # Simple check: field appears as a top-level key
        if any(
            line.startswith(f"{field}:")
            for line in content.splitlines()
        ):
            result.pass_(fid, f"{field} field present")
        else:
            result.fail(fid, f"{field} field missing from Chart.yaml")

    # YAML syntax check
    ok, err = try_parse_yaml(content)
    if ok:
        result.pass_("helm:yaml_syntax", "Chart.yaml is valid YAML")
    else:
        result.fail("helm:yaml_syntax", f"Chart.yaml YAML error: {err}")


def validate_k8s_manifests(result: ValidationResult) -> None:
    """Validate the K8s manifest directory exists with YAML files."""
    check_id = "k8s:dir_exists"
    resolved = K8S_MANIFEST_DIR.resolve()
    if not str(resolved).startswith(str(PROJECT_ROOT)):
        result.fail(check_id, "manifest directory path outside project")
        return
    if not resolved.is_dir():
        result.fail(check_id, "examples/kubernetes/manifest/ not found")
        return
    result.pass_(check_id, "manifest directory exists")

    yaml_files = list(resolved.glob("*.yaml")) + list(resolved.glob("*.yml"))
    fid = "k8s:has_yaml_files"
    if not yaml_files:
        result.fail(fid, "no .yaml/.yml files in manifest directory")
        return
    result.pass_(fid, f"{len(yaml_files)} YAML file(s) found")

    # Validate each YAML file syntax
    for yf in sorted(yaml_files):
        fname = yf.name
        content = yf.read_text(encoding="utf-8")
        ok, err = try_parse_yaml(content)
        sid = f"k8s:syntax:{fname}"
        if ok:
            result.pass_(sid, f"{fname} is valid YAML")
        else:
            result.fail(sid, f"{fname} YAML error: {err}")


def print_report(result: ValidationResult) -> None:
    """Print a formatted validation report."""
    print("v0.7.0 K8s Manifest & Helm Chart Validation Report")
    print("=" * 60)
    for status, check_id, message in result.results:
        print(f"  {status:4s}  {check_id:40s}  {message}")
    print()
    p = sum(s == "PASS" for s, _, _ in result.results)
    f = sum(s == "FAIL" for s, _, _ in result.results)
    print(f"Summary: {p} passed, {f} failed")


def main() -> int:
    """CLI entry point for K8s manifest validation."""
    result = ValidationResult()
    validate_chart_yaml(result)
    validate_k8s_manifests(result)
    print_report(result)
    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
