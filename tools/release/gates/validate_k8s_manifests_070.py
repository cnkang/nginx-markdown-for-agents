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

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

CHART_YAML = PROJECT_ROOT / "charts" / "nginx-markdown" / "Chart.yaml"
VALUES_YAML = PROJECT_ROOT / "charts" / "nginx-markdown" / "values.yaml"
DEPLOYMENT_TEMPLATE = (
    PROJECT_ROOT / "charts" / "nginx-markdown" / "templates" / "deployment.yaml"
)
CONFIGMAP_TEMPLATE = (
    PROJECT_ROOT / "charts" / "nginx-markdown" / "templates" / "configmap.yaml"
)
K8S_MANIFEST_DIR = PROJECT_ROOT / "examples" / "kubernetes" / "manifest"
GATE4_LOCAL_SCRIPT = PROJECT_ROOT / "tools" / "release" / "gates" / (
    "gate4_local_k8s_smoke.sh"
)

CHART_REQUIRED_FIELDS = ["apiVersion", "name", "version", "appVersion"]
HELM_VALUES_REQUIRED_SNIPPETS = [
    'tag: ""',
    "runAsNonRoot: true",
    "readOnlyRootFilesystem: true",
    'drop: ["ALL"]',
    "extraVolumes: []",
    "extraVolumeMounts: []",
    "enabled: false",
    'loadModule: ""',
]
HELM_CONFIG_REQUIRED_SNIPPETS = [
    "markdown.loadModule is required when markdown.enabled=true",
    "pid /var/run/nginx.pid;",
    "client_body_temp_path /var/cache/nginx/client_body_temp;",
    "proxy_temp_path /var/cache/nginx/proxy_temp;",
    "fastcgi_temp_path /var/cache/nginx/fastcgi_temp;",
    "uwsgi_temp_path /var/cache/nginx/uwsgi_temp;",
    "scgi_temp_path /var/cache/nginx/scgi_temp;",
    "listen 8080;",
]
HELM_DEPLOYMENT_REQUIRED_SNIPPETS = [
    "containerPort: 8080",
    "mountPath: /var/cache/nginx",
    "mountPath: /var/run",
    "mountPath: /tmp",
    "emptyDir: {}",
    "with .Values.extraVolumes",
    "with .Values.extraVolumeMounts",
]
HELM_DEPLOYMENT_FORBIDDEN_SNIPPETS = [
    "hostPath:",
    "mountPath: {{ dir .Values.markdown.loadModule }}",
    "path: {{ dir .Values.markdown.loadModule }}",
]
HELM_RENDER_REQUIRED_SNIPPETS = [
    "listen 8080;",
    "pid /var/run/nginx.pid;",
    "client_body_temp_path /var/cache/nginx/client_body_temp;",
    "readOnlyRootFilesystem: true",
    "runAsNonRoot: true",
    "containerPort: 8080",
    "mountPath: /var/cache/nginx",
    "mountPath: /var/run",
    "mountPath: /tmp",
]
HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS = [
    "load_module",
    "markdown_filter on;",
]
HELM_MODULE_LOAD_PATH = "/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so"
GATE4_LOCAL_REQUIRED_SNIPPETS = [
    "--set markdown.enabled=false",
    "--kube-context \"$kube_context\"",
    "kubectl --context \"$kube_context\"",
    "for volume_name in nginx-cache nginx-run nginx-tmp; do",
    "CREATED_CLUSTER=1",
    "Not deleting pre-existing cluster",
    "stock-nginx chart deployment path",
]

_CHECK_HELM_LINT = "helm:lint"
_CHECK_HELM_TEMPLATE = "helm:template"


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


def _is_within_project(path: Path) -> bool:
    """Return True if resolved path is within PROJECT_ROOT."""
    try:
        path.resolve().relative_to(PROJECT_ROOT.resolve())
        return True
    except ValueError:
        return False


def read_safe(path: Path) -> str:
    """Read file content safely, returning empty string if missing."""
    resolved = path.resolve()
    if not _is_within_project(path):
        return ""
    return resolved.read_text(encoding="utf-8") if resolved.is_file() else ""


def try_parse_yaml(content: str) -> tuple[bool, str]:
    """Attempt to parse YAML content, return (success, error_message)."""
    try:
        import yaml  # noqa: F401 — used for parsing

        list(yaml.safe_load_all(content))
        return True, ""
    except ImportError:
        return next(
            (
                (False, f"line {i}: tab indentation (YAML requires spaces)")
                for i, line in enumerate(content.splitlines(), 1)
                if line.startswith("\t")
            ),
            (True, ""),
        )
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
    if not _is_within_project(K8S_MANIFEST_DIR):
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
        if not _is_within_project(yf):
            result.fail(
                f"k8s:path:{fname}",
                "manifest file path outside project",
            )
            continue
        content = yf.read_text(encoding="utf-8")
        ok, err = try_parse_yaml(content)
        sid = f"k8s:syntax:{fname}"
        if ok:
            result.pass_(sid, f"{fname} is valid YAML")
        else:
            result.fail(sid, f"{fname} YAML error: {err}")


def _check_file_snippets(
    path: Path, snippets: list[str], prefix: str, label: str,
    result: ValidationResult,
) -> None:
    """Read a file and check that it contains all required snippets."""
    content = read_safe(path)
    if not content:
        result.fail(f"{prefix}_exists", f"{label} not found")
        return
    result.pass_(f"{prefix}_exists", f"{label} exists")
    for snippet in snippets:
        sid = f"{prefix}:{snippet[:24]}"
        if snippet in content:
            result.pass_(sid, f"{label} contains {snippet}")
        else:
            result.fail(sid, f"{label} missing {snippet}")


def _validate_helm_deployment(result: ValidationResult) -> None:
    """Validate Helm deployment template has required security/mount snippets."""
    deployment = read_safe(DEPLOYMENT_TEMPLATE)
    if not deployment:
        result.fail("helm:deployment_exists", "templates/deployment.yaml not found")
        return
    result.pass_("helm:deployment_exists", "deployment template exists")
    for snippet in HELM_DEPLOYMENT_REQUIRED_SNIPPETS:
        sid = f"helm:deployment:{snippet[:24]}"
        if snippet in deployment:
            result.pass_(sid, f"deployment template contains {snippet}")
        else:
            result.fail(sid, f"deployment template missing {snippet}")
    for snippet in HELM_DEPLOYMENT_FORBIDDEN_SNIPPETS:
        sid = f"helm:deployment-forbidden:{snippet[:24]}"
        if snippet in deployment:
            result.fail(sid, f"deployment template must not contain {snippet}")
        else:
            result.pass_(sid, f"deployment template omits {snippet}")
    empty_dir_count = deployment.count("emptyDir: {}")
    if empty_dir_count >= 3:
        result.pass_(
            "helm:deployment:emptydir-count",
            "deployment has writable runtime/temp emptyDir mounts",
        )
    else:
        result.fail(
            "helm:deployment:emptydir-count",
            "deployment must mount writable emptyDir volumes for runtime/temp paths",
        )


def validate_helm_secure_defaults(result: ValidationResult) -> None:
    """Validate Helm defaults can run under the default restricted pod context."""
    _check_file_snippets(
        VALUES_YAML, HELM_VALUES_REQUIRED_SNIPPETS,
        "helm:values", "values.yaml", result,
    )
    _check_file_snippets(
        CONFIGMAP_TEMPLATE, HELM_CONFIG_REQUIRED_SNIPPETS,
        "helm:configmap", "configmap template", result,
    )
    _validate_helm_deployment(result)


def validate_gate4_local_smoke(result: ValidationResult) -> None:
    """Validate local Gate 4 smoke does not deploy stock NGINX with module config."""
    _check_file_snippets(
        GATE4_LOCAL_SCRIPT, GATE4_LOCAL_REQUIRED_SNIPPETS,
        "gate4:local", "gate4_local_k8s_smoke.sh", result,
    )


_MAX_HELM_OUTPUT = 2048  # Truncate helm output in failure messages


def _truncate_output(text: str, limit: int = _MAX_HELM_OUTPUT) -> str:
    """Truncate text to limit characters, appending a marker if truncated."""
    return text if len(text) <= limit else text[:limit] + "\n[output truncated]"


def validate_helm_render(result: ValidationResult) -> None:
    """Run Helm render checks when helm is available in the local environment."""
    helm = shutil.which("helm")
    if not helm:
        result.fail(
            "helm:render:missing",
            "helm not found; install helm to run render checks",
        )
        return

    chart_dir = PROJECT_ROOT / "charts" / "nginx-markdown"
    try:
        lint = subprocess.run(
            [helm, "lint", str(chart_dir)],
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        result.fail(_CHECK_HELM_LINT, f"helm lint timed out: {exc}")
        return
    if lint.returncode != 0:
        result.fail(
            _CHECK_HELM_LINT,
            f"helm lint failed: {_truncate_output(lint.stdout.strip())}",
        )
        return
    result.pass_(_CHECK_HELM_LINT, "helm lint passed")

    try:
        rendered = subprocess.run(
            [helm, "template", "test", str(chart_dir)],
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        result.fail(_CHECK_HELM_TEMPLATE, f"helm template timed out: {exc}")
        return
    if rendered.returncode != 0:
        result.fail(
            _CHECK_HELM_TEMPLATE,
            f"helm template failed: {_truncate_output(rendered.stdout.strip())}",
        )
        return
    result.pass_(_CHECK_HELM_TEMPLATE, "helm template rendered successfully")

    ok, err = try_parse_yaml(rendered.stdout)
    if ok:
        result.pass_("helm:render:yaml", "rendered Helm output is valid YAML")
    else:
        result.fail("helm:render:yaml", f"rendered Helm YAML error: {err}")

    for snippet in HELM_RENDER_REQUIRED_SNIPPETS:
        sid = f"helm:render:{snippet[:24]}"
        if snippet in rendered.stdout:
            result.pass_(sid, f"rendered Helm output contains {snippet}")
        else:
            result.fail(sid, f"rendered Helm output missing {snippet}")
    for snippet in HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS:
        sid = f"helm:render-default-forbidden:{snippet[:24]}"
        if snippet in rendered.stdout:
            result.fail(sid, f"default Helm render must not contain {snippet}")
        else:
            result.pass_(sid, f"default Helm render omits {snippet}")

    try:
        missing_module = subprocess.run(
            [
                helm, "template", "test", str(chart_dir),
                "--set", "markdown.enabled=true",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        result.fail("helm:render-module-missing", f"helm template timed out: {exc}")
        return
    if (
        missing_module.returncode != 0
        and "markdown.loadModule is required when markdown.enabled=true"
        in missing_module.stdout
    ):
        result.pass_(
            "helm:render-module-missing",
            "markdown.enabled=true without loadModule fails clearly",
        )
    else:
        result.fail(
            "helm:render-module-missing",
            "markdown.enabled=true without loadModule must fail clearly",
        )

    try:
        module_render = subprocess.run(
            [
                helm, "template", "test", str(chart_dir),
                "--set", "markdown.enabled=true",
                "--set-string", f"markdown.loadModule={HELM_MODULE_LOAD_PATH}",
            ],
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        result.fail("helm:render-module-enabled", f"helm template timed out: {exc}")
        return
    if module_render.returncode != 0:
        result.fail(
            "helm:render-module-enabled",
            "module-enabled Helm template failed: "
            f"{_truncate_output(module_render.stdout.strip())}",
        )
    elif (
        f"load_module {HELM_MODULE_LOAD_PATH};" in module_render.stdout
        and "markdown_filter on;" in module_render.stdout
    ):
        result.pass_(
            "helm:render-module-enabled",
            "module-enabled Helm render includes load_module and markdown directives",
        )
    else:
        result.fail(
            "helm:render-module-enabled",
            "module-enabled Helm render missing load_module or markdown directives",
        )


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
    validate_helm_secure_defaults(result)
    validate_gate4_local_smoke(result)
    validate_helm_render(result)
    print_report(result)
    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
