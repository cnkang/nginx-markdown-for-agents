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

from collections.abc import Sequence
import re
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
    "metrics.enabled=true requires markdown.enabled=true",
    "and .Values.markdown.enabled .Values.metrics.enabled",
    "pid /var/run/nginx.pid;",
    "client_body_temp_path /var/cache/nginx/client_body_temp;",
    "proxy_temp_path /var/cache/nginx/proxy_temp;",
    "fastcgi_temp_path /var/cache/nginx/fastcgi_temp;",
    "uwsgi_temp_path /var/cache/nginx/uwsgi_temp;",
    "scgi_temp_path /var/cache/nginx/scgi_temp;",
    "listen 8080;",
    "markdown_metrics_shm_size {{ .Values.metrics.shmSize }};",
    "location = {{ .Values.metrics.uri }} {",
    "markdown_metrics;",
    "markdown_metrics_format {{ .Values.metrics.format }};",
]
HELM_CONFIG_FORBIDDEN_SNIPPETS = [
    "markdown_metrics on;",
    "markdown_metrics_uri",
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
    "markdown_metrics",
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
_CHECK_HELM_RENDER_YAML = "helm:render:yaml"
_CHECK_HELM_RENDER_MODULE_MISSING = "helm:render-module-missing"
_CHECK_HELM_RENDER_METRICS_WITHOUT_MODULE = "helm:render-metrics-without-module"
_CHECK_HELM_RENDER_MODULE_ENABLED = "helm:render-module-enabled"
_CHECK_HELM_RENDER_MODULE_METRICS = "helm:render-module-metrics"


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
    configmap = read_safe(CONFIGMAP_TEMPLATE)
    for snippet in HELM_CONFIG_FORBIDDEN_SNIPPETS:
        check_id = f"helm:configmap-forbidden:{snippet[:24]}"
        if snippet in configmap:
            result.fail(check_id, f"configmap template must not contain {snippet}")
        else:
            result.pass_(check_id, f"configmap template omits {snippet}")
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


def _run_helm(
    result: ValidationResult,
    check_id: str,
    args: Sequence[str],
) -> subprocess.CompletedProcess[str] | None:
    """Run a Helm command and record timeout failures uniformly."""
    try:
        return subprocess.run(
            list(args),
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        result.fail(check_id, f"helm command timed out: {exc}")
        return None


def _run_helm_template(
    result: ValidationResult,
    check_id: str,
    helm: str,
    chart_dir: Path,
    extra_args: Sequence[str] = (),
) -> subprocess.CompletedProcess[str] | None:
    """Run helm template for the chart under test."""
    return _run_helm(
        result,
        check_id,
        [helm, "template", "test", str(chart_dir), *extra_args],
    )


def _validate_helm_lint(
    result: ValidationResult,
    helm: str,
    chart_dir: Path,
) -> bool:
    """Validate helm lint succeeds before template-specific checks run."""
    lint = _run_helm(result, _CHECK_HELM_LINT, [helm, "lint", str(chart_dir)])
    if lint is None:
        return False
    if lint.returncode == 0:
        result.pass_(_CHECK_HELM_LINT, "helm lint passed")
        return True
    result.fail(
        _CHECK_HELM_LINT,
        f"helm lint failed: {_truncate_output(lint.stdout.strip())}",
    )
    return False


def _validate_default_helm_template(
    result: ValidationResult,
    helm: str,
    chart_dir: Path,
) -> str | None:
    """Render default Helm output and validate stock-image-safe snippets."""
    rendered = _run_helm_template(result, _CHECK_HELM_TEMPLATE, helm, chart_dir)
    if rendered is None:
        return None
    if rendered.returncode != 0:
        result.fail(
            _CHECK_HELM_TEMPLATE,
            f"helm template failed: {_truncate_output(rendered.stdout.strip())}",
        )
        return None
    result.pass_(_CHECK_HELM_TEMPLATE, "helm template rendered successfully")
    _validate_rendered_yaml(result, rendered.stdout)
    _validate_rendered_required_snippets(result, rendered.stdout)
    _validate_rendered_default_forbidden_snippets(result, rendered.stdout)
    return rendered.stdout


def _validate_rendered_yaml(result: ValidationResult, rendered: str) -> None:
    """Validate Helm output remains parseable YAML."""
    ok, err = try_parse_yaml(rendered)
    if ok:
        result.pass_(_CHECK_HELM_RENDER_YAML, "rendered Helm output is valid YAML")
    else:
        result.fail(_CHECK_HELM_RENDER_YAML, f"rendered Helm YAML error: {err}")


def _validate_rendered_required_snippets(
    result: ValidationResult,
    rendered: str,
) -> None:
    """Validate required snippets in default rendered Helm output."""
    for snippet in HELM_RENDER_REQUIRED_SNIPPETS:
        sid = f"helm:render:{snippet[:24]}"
        if snippet in rendered:
            result.pass_(sid, f"rendered Helm output contains {snippet}")
        else:
            result.fail(sid, f"rendered Helm output missing {snippet}")


def _validate_rendered_default_forbidden_snippets(
    result: ValidationResult,
    rendered: str,
) -> None:
    """Validate default Helm output omits optional module directives."""
    for snippet in HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS:
        sid = f"helm:render-default-forbidden:{snippet[:24]}"
        if snippet in rendered:
            result.fail(sid, f"default Helm render must not contain {snippet}")
        else:
            result.pass_(sid, f"default Helm render omits {snippet}")


def _validate_expected_helm_failure(
    result: ValidationResult,
    check_id: str,
    rendered: subprocess.CompletedProcess[str] | None,
    required_message: str,
    pass_message: str,
    fail_message: str,
) -> None:
    """Validate a deliberately invalid Helm values combination fails clearly."""
    if rendered and rendered.returncode != 0 and required_message in rendered.stdout:
        result.pass_(check_id, pass_message)
    else:
        result.fail(check_id, fail_message)


def _validate_missing_module_guard(
    result: ValidationResult,
    helm: str,
    chart_dir: Path,
) -> None:
    """Validate markdown.enabled requires markdown.loadModule."""
    rendered = _run_helm_template(
        result,
        _CHECK_HELM_RENDER_MODULE_MISSING,
        helm,
        chart_dir,
        ["--set", "markdown.enabled=true"],
    )
    if rendered is None:
        return
    _validate_expected_helm_failure(
        result,
        _CHECK_HELM_RENDER_MODULE_MISSING,
        rendered,
        "markdown.loadModule is required when markdown.enabled=true",
        "markdown.enabled=true without loadModule fails clearly",
        "markdown.enabled=true without loadModule must fail clearly",
    )


def _validate_metrics_without_module_guard(
    result: ValidationResult,
    helm: str,
    chart_dir: Path,
) -> None:
    """Validate metrics cannot render without the markdown module enabled."""
    rendered = _run_helm_template(
        result,
        _CHECK_HELM_RENDER_METRICS_WITHOUT_MODULE,
        helm,
        chart_dir,
        ["--set", "metrics.enabled=true"],
    )
    if rendered is None:
        return
    _validate_expected_helm_failure(
        result,
        _CHECK_HELM_RENDER_METRICS_WITHOUT_MODULE,
        rendered,
        "metrics.enabled=true requires markdown.enabled=true",
        "metrics.enabled=true without markdown.enabled fails clearly",
        "metrics.enabled=true without markdown.enabled must fail clearly",
    )


def _module_enabled_args() -> list[str]:
    """Return Helm args that enable the markdown dynamic module."""
    return [
        "--set",
        "markdown.enabled=true",
        "--set-string",
        f"markdown.loadModule={HELM_MODULE_LOAD_PATH}",
    ]


def _validate_module_enabled_render(
    result: ValidationResult,
    helm: str,
    chart_dir: Path,
) -> None:
    """Validate explicit module enablement renders module directives."""
    rendered = _run_helm_template(
        result,
        _CHECK_HELM_RENDER_MODULE_ENABLED,
        helm,
        chart_dir,
        _module_enabled_args(),
    )
    if rendered is None:
        return
    if rendered.returncode != 0:
        result.fail(
            _CHECK_HELM_RENDER_MODULE_ENABLED,
            "module-enabled Helm template failed: "
            f"{_truncate_output(rendered.stdout.strip())}",
        )
        return
    if (
        f"load_module {HELM_MODULE_LOAD_PATH};" in rendered.stdout
        and "markdown_filter on;" in rendered.stdout
    ):
        result.pass_(
            _CHECK_HELM_RENDER_MODULE_ENABLED,
            "module-enabled Helm render includes load_module and markdown directives",
        )
    else:
        result.fail(
            _CHECK_HELM_RENDER_MODULE_ENABLED,
            "module-enabled Helm render missing load_module or markdown directives",
        )


def _validate_module_metrics_render(
    result: ValidationResult,
    helm: str,
    chart_dir: Path,
) -> None:
    """Validate metrics directives render when the module is enabled."""
    rendered = _run_helm_template(
        result,
        _CHECK_HELM_RENDER_MODULE_METRICS,
        helm,
        chart_dir,
        [*_module_enabled_args(), "--set", "metrics.enabled=true"],
    )
    if rendered is None:
        return
    if rendered.returncode != 0:
        result.fail(
            _CHECK_HELM_RENDER_MODULE_METRICS,
            "module metrics Helm template failed: "
            f"{_truncate_output(rendered.stdout.strip())}",
        )
        return
    errors = _metrics_config_errors(rendered.stdout)
    if not errors:
        result.pass_(
            _CHECK_HELM_RENDER_MODULE_METRICS,
            "module-enabled metrics render uses valid HTTP and location scopes",
        )
    else:
        result.fail(
            _CHECK_HELM_RENDER_MODULE_METRICS,
            "module-enabled metrics render violates the NGINX directive contract: "
            + "; ".join(errors),
        )


def _metrics_config_errors(rendered: str) -> list[str]:
    """Return Helm metrics directive syntax and scope violations.

    The rendered YAML contains the ConfigMap's ``nginx.conf`` as a block
    scalar. NGINX block lines remain distinguishable, so the lightweight stack
    below can validate directive ownership without accepting a text-only
    presence check that would miss invalid ``server`` scope or arguments.
    """
    directives = _collect_metrics_directives(rendered)
    entries_by_name = {
        name: [entry for entry in directives if entry[0] == name]
        for name in (
            "markdown_metrics_shm_size",
            "markdown_metrics",
            "markdown_metrics_format",
        )
    }
    errors = _removed_metrics_directive_errors(directives)
    errors.extend(
        _single_metrics_scope_errors(
            entries_by_name["markdown_metrics_shm_size"],
            "markdown_metrics_shm_size",
            "http",
        )
    )
    errors.extend(
        _metrics_handler_errors(entries_by_name["markdown_metrics"])
    )
    errors.extend(
        _single_metrics_scope_errors(
            entries_by_name["markdown_metrics_format"],
            "markdown_metrics_format",
            "location",
        )
    )
    errors.extend(
        _metrics_location_pair_errors(
            entries_by_name["markdown_metrics"],
            entries_by_name["markdown_metrics_format"],
        )
    )
    return errors


def _collect_metrics_directives(
    rendered: str,
) -> list[tuple[str, str, tuple[str, ...]]]:
    """Collect metrics directives with their rendered NGINX block stack."""
    scopes: list[str] = []
    directives: list[tuple[str, str, tuple[str, ...]]] = []
    metric_directive = re.compile(
        r"^(markdown_metrics(?:_[a-z_]+)?)(?:\s+(.*?))?;$"
    )

    for raw_line in rendered.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "}":
            if scopes:
                scopes.pop()
            continue
        if line.endswith("{"):
            scopes.append(line[:-1].strip())
            continue
        match = metric_directive.match(line)
        if match:
            directives.append(
                (match.group(1), match.group(2) or "", tuple(scopes))
            )
    return directives


def _removed_metrics_directive_errors(
    directives: list[tuple[str, str, tuple[str, ...]]],
) -> list[str]:
    """Reject chart output that uses removed or nonexistent directives."""
    if any(name == "markdown_metrics_uri" for name, _, _ in directives):
        return ["markdown_metrics_uri does not exist"]
    return []


def _single_metrics_scope_errors(
    entries: list[tuple[str, str, tuple[str, ...]]],
    directive: str,
    expected_scope: str,
) -> list[str]:
    """Require a metrics directive exactly once in its documented scope."""
    if len(entries) != 1 or _scope_kind(entries[0][2]) != expected_scope:
        return [f"{directive} must appear once in {expected_scope} scope"]
    return []


def _metrics_handler_errors(
    handler_entries: list[tuple[str, str, tuple[str, ...]]],
) -> list[str]:
    """Validate the no-argument location-only metrics content handler."""
    if len(handler_entries) != 1:
        return ["markdown_metrics must appear once"]
    if handler_entries[0][1]:
        return ["markdown_metrics accepts no arguments"]
    if _scope_kind(handler_entries[0][2]) != "location":
        return ["markdown_metrics must be in location scope"]
    return []


def _metrics_location_pair_errors(
    handler_entries: list[tuple[str, str, tuple[str, ...]]],
    format_entries: list[tuple[str, str, tuple[str, ...]]],
) -> list[str]:
    """Require handler and format directives to share the same location."""
    if len(handler_entries) != 1 or len(format_entries) != 1:
        return []
    if handler_entries[0][2] == format_entries[0][2]:
        return []
    return ["markdown_metrics and markdown_metrics_format must share a location"]


def _scope_kind(scopes: tuple[str, ...]) -> str:
    """Return the NGINX context kind for the innermost rendered block."""
    if not scopes:
        return "main"
    return scopes[-1].split(maxsplit=1)[0]


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
    if not _validate_helm_lint(result, helm, chart_dir):
        return
    if _validate_default_helm_template(result, helm, chart_dir) is None:
        return
    _validate_missing_module_guard(result, helm, chart_dir)
    _validate_metrics_without_module_guard(result, helm, chart_dir)
    _validate_module_enabled_render(result, helm, chart_dir)
    _validate_module_metrics_render(result, helm, chart_dir)


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
