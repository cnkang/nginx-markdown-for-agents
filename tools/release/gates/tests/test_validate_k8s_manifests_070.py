"""Tests for v0.7.0 Kubernetes and Helm release gate expectations."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from tools.release.gates.validate_k8s_manifests_070 import (  # noqa: E402
    GATE4_LOCAL_REQUIRED_SNIPPETS,
    HELM_CONFIG_REQUIRED_SNIPPETS,
    HELM_DEPLOYMENT_FORBIDDEN_SNIPPETS,
    HELM_DEPLOYMENT_REQUIRED_SNIPPETS,
    HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS,
    HELM_VALUES_REQUIRED_SNIPPETS,
)


def test_helm_defaults_are_stock_nginx_safe() -> None:
    """Default Helm values must not require the markdown module."""
    assert "enabled: false" in HELM_VALUES_REQUIRED_SNIPPETS
    assert 'loadModule: ""' in HELM_VALUES_REQUIRED_SNIPPETS
    assert "load_module" in HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS
    assert "markdown_filter on;" in HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS
    assert "markdown_metrics" in HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS


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
