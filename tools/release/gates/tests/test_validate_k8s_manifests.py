"""Tests for v0.7.0 Kubernetes and Helm release gate expectations."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from tools.release.gates.validate_k8s_manifests import (  # noqa: E402
    GATE4_LOCAL_REQUIRED_SNIPPETS,
    HELM_CONFIG_REQUIRED_SNIPPETS,
    HELM_DEPLOYMENT_FORBIDDEN_SNIPPETS,
    HELM_DEPLOYMENT_REQUIRED_SNIPPETS,
    HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS,
    HELM_VALUES_REQUIRED_SNIPPETS,
    ValidationResult,
)
from tools.release.gates import validate_k8s_manifests as validator  # noqa: E402


def test_helm_defaults_are_stock_nginx_safe() -> None:
    """Default Helm values must not require the markdown module."""
    assert "enabled: false" in HELM_VALUES_REQUIRED_SNIPPETS
    assert 'loadModule: ""' in HELM_VALUES_REQUIRED_SNIPPETS
    assert "load_module" in HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS
    assert "markdown_filter on;" in HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS
    assert "markdown_metrics" in HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS


def test_empty_image_render_is_expected_but_explicit_image_must_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chart keeps empty image defaults while explicit images render."""
    explicit_output = (
        "apiVersion: v1\n"
        "data:\n"
        "  nginx.conf: |\n"
        + "\n".join(
            f"    {snippet}" for snippet in validator.HELM_RENDER_REQUIRED_SNIPPETS
        )
        + "\n"
    )
    responses = iter(
        [
            subprocess.CompletedProcess(
                args=["helm", "template"],
                returncode=1,
                stdout="Error: image.repository must be set explicitly",
            ),
            subprocess.CompletedProcess(
                args=["helm", "template"],
                returncode=0,
                stdout=explicit_output,
            ),
        ]
    )
    monkeypatch.setattr(validator, "_run_helm_template", lambda *args: next(responses))

    result = ValidationResult()
    rendered = validator._validate_default_helm_template(
        result, "helm", Path("chart")
    )

    assert rendered == explicit_output
    assert not result.has_failures, result.results
    assert any(
        check_id == validator._CHECK_HELM_TEMPLATE
        and "required image contract" in message
        for status, check_id, message in result.results
        if status == "PASS"
    )


def test_empty_image_render_rejects_unrelated_helm_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An arbitrary default-render failure must not be accepted as expected."""
    completed = subprocess.CompletedProcess(
        args=["helm", "template"],
        returncode=1,
        stdout="Error: unrelated chart rendering failure",
    )
    monkeypatch.setattr(validator, "_run_helm_template", lambda *args: completed)

    result = ValidationResult()
    assert validator._validate_default_helm_template(
        result, "helm", Path("chart")
    ) is None
    assert result.has_failures


def test_helm_module_enablement_requires_explicit_module_path() -> None:
    """markdown.enabled=true must fail clearly when loadModule is absent."""
    assert (
        "markdown.loadModule is required when markdown.enabled=true"
        in HELM_CONFIG_REQUIRED_SNIPPETS
    )


def test_helm_metrics_require_module_enablement() -> None:
    """metrics.enabled=true must not render module directives without module."""
    assert (
        "metrics.enabled=true requires markdown.enabled=true"
        in HELM_CONFIG_REQUIRED_SNIPPETS
    )
    assert (
        "and .Values.markdown.enabled .Values.metrics.enabled"
        in HELM_CONFIG_REQUIRED_SNIPPETS
    )


def test_helm_deployment_uses_explicit_extra_volumes_only() -> None:
    """The chart must not auto-mount host paths from markdown.loadModule."""
    assert "with .Values.extraVolumes" in HELM_DEPLOYMENT_REQUIRED_SNIPPETS
    assert "with .Values.extraVolumeMounts" in HELM_DEPLOYMENT_REQUIRED_SNIPPETS
    assert "hostPath:" in HELM_DEPLOYMENT_FORBIDDEN_SNIPPETS
    assert (
        "mountPath: {{ dir .Values.markdown.loadModule }}"
        in HELM_DEPLOYMENT_FORBIDDEN_SNIPPETS
    )


def test_gate4_documents_stock_nginx_smoke_scope() -> None:
    """Local K8s smoke should stay explicit about its stock-image scope."""
    assert "stock-nginx chart deployment path" in GATE4_LOCAL_REQUIRED_SNIPPETS


def test_module_metrics_render_rejects_invalid_directives_and_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The release gate must reject the legacy invalid Helm metrics layout."""
    invalid_nginx_config = """
http {
    server {
        markdown_metrics on;
        markdown_metrics_uri /_markdown_metrics;
        markdown_metrics_format auto;
        markdown_metrics_shm_size 8m;
    }
}
"""
    completed = subprocess.CompletedProcess(
        args=["helm", "template"],
        returncode=0,
        stdout=invalid_nginx_config,
    )
    monkeypatch.setattr(validator, "_run_helm_template", lambda *args: completed)

    result = ValidationResult()
    validator._validate_module_metrics_render(result, "helm", Path("chart"))

    assert result.has_failures


def test_module_metrics_render_accepts_http_and_location_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The release gate must accept the NGINX metrics directive contract."""
    valid_nginx_config = """
    http {
        markdown_metrics_shm_size 8m;
        server {
            location = /_markdown_metrics {
                markdown_metrics;
        }
    }
}
"""
    completed = subprocess.CompletedProcess(
        args=["helm", "template"],
        returncode=0,
        stdout=valid_nginx_config,
    )
    monkeypatch.setattr(validator, "_run_helm_template", lambda *args: completed)

    result = ValidationResult()
    validator._validate_module_metrics_render(result, "helm", Path("chart"))

    assert not result.has_failures


def test_module_enabled_render_rejects_duplicate_markdown_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chart must emit one combined markdown_limits directive."""
    rendered_config = """
load_module /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so;
server {
    markdown_filter on;
    markdown_limits conversion_memory=64m conversion_timeout=5s;
    markdown_streaming auto;
    markdown_limits streaming_buffer=2m;
}
"""
    completed = subprocess.CompletedProcess(
        args=["helm", "template"],
        returncode=0,
        stdout=rendered_config,
    )
    monkeypatch.setattr(validator, "_run_helm_template", lambda *args: completed)

    result = ValidationResult()
    validator._validate_module_enabled_render(result, "helm", Path("chart"))

    assert result.has_failures
    # The failure must be the specific duplicate-markdown_limits contract
    # violation, not an unrelated render error: match the check identifier
    # and the exact "exactly one markdown_limits directive" message.
    assert any(
        check_id == validator._CHECK_HELM_RENDER_MODULE_ENABLED
        and "exactly one markdown_limits directive" in message
        for status, check_id, message in result.results
        if status == "FAIL"
    ), f"expected duplicate-markdown_limits failure, got: {result.results}"
