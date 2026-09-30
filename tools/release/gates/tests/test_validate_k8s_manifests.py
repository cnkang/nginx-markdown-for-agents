"""Tests for v0.7.0 Kubernetes and Helm release gate expectations."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from tools.release.gates.validate_k8s_manifests import (  # noqa: E402
    GATE4_LOCAL_REQUIRED_SNIPPETS,
    HELM_CANONICAL_LIMIT_KEYS,
    HELM_CONFIG_REQUIRED_SNIPPETS,
    HELM_DEPLOYMENT_FORBIDDEN_SNIPPETS,
    HELM_DEPLOYMENT_REQUIRED_SNIPPETS,
    HELM_LEGACY_VALUE_KEYS,
    HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS,
    HELM_VALUES_REQUIRED_SNIPPETS,
    ValidationResult,
)
from tools.release.gates.validate_config_directives import (  # noqa: E402
    CURRENT_LIMIT_KEYS,
)
from tools.release.gates import validate_k8s_manifests as validator  # noqa: E402


def _valid_metrics_render() -> str:
    """Return a rendered ConfigMap and sidecar with explicit resources."""
    return """apiVersion: v1
kind: ConfigMap
data:
  nginx.conf: |
    http {
        markdown_metrics_shm_size 8m;
        server {
            location = /_markdown_metrics {
                markdown_metrics;
            }
        }
    }
---
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
        - name: metrics-sidecar
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 250m
              memory: 128Mi
"""


def test_helm_defaults_are_stock_nginx_safe() -> None:
    """Default Helm values must not require the markdown module."""
    assert "enabled: false" in HELM_VALUES_REQUIRED_SNIPPETS
    assert 'loadModule: ""' in HELM_VALUES_REQUIRED_SNIPPETS
    assert "load_module" in HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS
    assert "markdown_filter on;" in HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS
    assert "markdown_metrics" in HELM_RENDER_FORBIDDEN_DEFAULT_SNIPPETS


def test_helm_public_surface_uses_canonical_module_vocabulary() -> None:
    """Values, schema, and templates must expose one limits vocabulary."""
    result = ValidationResult()
    validator.validate_helm_public_surface(result)

    assert not result.has_failures, result.results
    assert set(HELM_CANONICAL_LIMIT_KEYS.values()) == set(CURRENT_LIMIT_KEYS)
    assert set(HELM_CANONICAL_LIMIT_KEYS) == {
        validator._helm_limit_key(key) for key in CURRENT_LIMIT_KEYS
    }
    assert HELM_LEGACY_VALUE_KEYS == {"maxSize", "timeout", "etag", "budget"}


def test_helm_digest_resolution_checks_both_supported_forms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit digest and repository@digest must both remain immutable."""
    responses = iter(
        [
            subprocess.CompletedProcess(
                args=["helm", "template"],
                returncode=0,
                stdout=(
                    'image: "nginx@sha256:'
                    '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"'
                ),
            ),
            subprocess.CompletedProcess(
                args=["helm", "template"],
                returncode=0,
                stdout=(
                    'image: "nginx@sha256:'
                    'fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210"'
                ),
            ),
        ]
    )
    monkeypatch.setattr(validator, "_run_helm_template", lambda *args: next(responses))
    result = ValidationResult()
    validator._validate_image_digest_render(result, "helm", Path("chart"))
    assert not result.has_failures, result.results


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
                stdout="Error: image.tag or image.digest must be set explicitly",
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


def test_chart_default_leaves_the_streaming_directive_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chart default must not override the module's frozen streaming default."""
    responses = iter(
        [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="markdown_filter on;\n"),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="markdown_filter on;\nmarkdown_streaming auto;\n",
            ),
        ]
    )
    monkeypatch.setattr(validator, "_run_helm_template", lambda *args: next(responses))
    result = ValidationResult()
    validator._validate_streaming_default_parity(
        result, "helm", Path("charts/nginx-markdown")
    )
    assert all(status == "PASS" for status, _, _ in result.results)


def test_chart_default_rejects_a_rendered_streaming_directive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chart default that renders markdown_streaming must fail closed."""
    rendered = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="markdown_streaming auto;\n"
    )
    monkeypatch.setattr(validator, "_run_helm_template", lambda *args: rendered)
    result = ValidationResult()
    validator._validate_streaming_default_parity(
        result, "helm", Path("charts/nginx-markdown")
    )
    assert any(status == "FAIL" for status, _, _ in result.results)


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


def test_gate4_cleanup_removes_only_resources_created_by_the_run() -> None:
    """Gate 4 must preserve a pre-existing release and namespace."""
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/release/gates/gate4_local_k8s_smoke.sh"
    ).read_text(encoding="utf-8")
    cleanup = script.split("cleanup_owned_helm_resources() {", 1)[1].split(
        "\n}", 1
    )[0]

    assert "CREATED_RELEASE=0" in script
    assert "CREATED_NAMESPACE=0" in script
    assert "CREATED_RELEASE=1" in script
    assert "CREATED_NAMESPACE=1" in script
    assert cleanup.index('if [[ "$CREATED_RELEASE" -eq 1 ]]') < (
        cleanup.index("helm uninstall")
    )
    assert cleanup.index('if [[ "$CREATED_NAMESPACE" -eq 1 ]]') < (
        cleanup.index("delete namespace")
    )
    assert "helm list --short" in script
    assert "helm install" in script
    assert "helm upgrade --install" not in script


def test_gate4_ownership_query_uses_the_version_portable_state_set() -> None:
    """The ownership query must work on Helm v3 and v4 alike.

    The all-releases flag exists on Helm v3 but was removed in v4, so the
    explicit state set is the portable spelling of "any release, in any
    state".  A missing state flag would let a failed or pending release from
    an earlier run go unnoticed and the install would adopt it.
    """
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/release/gates/gate4_local_k8s_smoke.sh"
    ).read_text(encoding="utf-8")
    query = script.split('existing_release="$(helm list', 1)[1].split(
        '2>"$release_stderr_file")', 1
    )[0]

    for flag in (
        "--deployed",
        "--failed",
        "--pending",
        "--uninstalled",
        "--uninstalling",
        "--superseded",
    ):
        assert flag in query, (
            f"gate4 ownership query is missing {flag}; the explicit state "
            f"set is required because the all-releases flag is gone in Helm v4"
        )
    assert "--all" not in query, (
        "gate4 must not use --all: it is removed in Helm v4"
    )
    # stderr is captured to a file and only shown when the query fails, so a
    # warning cannot be mistaken for a pre-existing release.
    assert '2>"$release_stderr_file"' in script
    assert 'cat "$release_stderr_file" >&2' in script


def test_gate4_installs_with_the_version_selected_rollback_flag() -> None:
    """A failed or timed-out gate4 install must not leak a release.

    The rollback flag is chosen by the installed helm's major version:
    Helm 3 spells it ``--atomic``, Helm 4 ``--rollback-on-failure``.  The
    script must call the selector rather than hard-code one spelling.
    """
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/release/gates/gate4_local_k8s_smoke.sh"
    ).read_text(encoding="utf-8")
    install = script.split(
        'if ! install_output="$(helm install "${HELM_RELEASE_NAME}"', 1
    )[1]
    install = install.split("2>&1)\"; then", 1)[0]

    assert '"${rollback_flag}"' in install, (
        "gate4 helm install must pass the selected rollback flag"
    )
    assert "rollback_flag=\"$(helm_rollback_flag)\"" in script, (
        "gate4 must obtain the rollback flag from the version selector"
    )
    # The selector itself maps the majors correctly.  The body contains
    # case terminators, so cut at the function's closing line explicitly.
    selector = script.split("helm_rollback_flag() {", 1)[1]
    selector = selector.split("\n}\n", 1)[0]
    assert "--rollback-on-failure" in selector
    assert "--atomic" in selector
    assert "v4*|4.*" in selector


def test_gate4_traps_abnormal_termination_into_its_cleanup() -> None:
    """Termination traps run cleanup once and cannot repeat it."""
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/release/gates/gate4_local_k8s_smoke.sh"
    ).read_text(encoding="utf-8")

    # A single guarded entry point performs the cleanup for EXIT and for the
    # signal handlers, so cleanup runs at most once even when a signal lands
    # after main() already cleaned up.
    assert "CLEANUP_DONE=0" in script
    run_cleanup_once = (
        script.split("run_cleanup_once() {", 1)[1].split("\n}", 1)[0]
    )
    guard = run_cleanup_once.index("CLEANUP_DONE}")
    assert run_cleanup_once.index('"${CLEANUP_DONE}" -eq 1') < guard
    assert "CLEANUP_DONE=1" in run_cleanup_once
    assert "cleanup_owned_helm_resources" in run_cleanup_once
    assert "delete_cluster" in run_cleanup_once

    # A returning INT/TERM handler resumes the script after the interrupt and
    # a terminated run could still reach its success path; the signal
    # handlers must exit with the conventional statuses instead.
    assert "trap 'exit_on_signal 130' INT" in script, (
        "SIGINT must exit with status 130 after cleanup"
    )
    assert "trap 'exit_on_signal 143' TERM" in script, (
        "SIGTERM must exit with status 143 after cleanup"
    )
    exit_on_signal = (
        script.split("exit_on_signal() {", 1)[1].split("\n}", 1)[0]
    )
    assert "run_cleanup_once" in exit_on_signal
    assert "exit \"$signal_status\"" in exit_on_signal

    # The traps are installed only after parsing and prerequisites succeed:
    # an early usage error must not emit cleanup messages for resources this
    # run never touched.
    assert "install_termination_traps() {" in script
    main = script.split("main() {", 1)[1].split("\n}", 1)[0]
    assert "install_termination_traps" in main
    assert main.index("parse_args") < main.index("install_termination_traps")
    assert main.index("check_prerequisites") < main.index(
        "install_termination_traps"
    )

    # main's own cleanup runs the helpers BEFORE marking the guard: a signal
    # arriving mid-cleanup re-runs them (safe, idempotent) instead of
    # skipping what is left.  The guard is then set so the EXIT trap is a
    # no-op after the work completed.
    assert "CLEANUP_DONE=1" in main
    cleanup_call = main.index("cleanup_owned_helm_resources")
    delete_call = main.index("delete_cluster", cleanup_call)
    guard_set = main.index("CLEANUP_DONE=1")
    assert cleanup_call < delete_call < guard_set, (
        "the cleanup helpers must run before the guard is marked"
    )

    # Idempotence: the delete clears its ownership flag so a second run of
    # the helper is a no-op.
    delete_cluster = script.split("delete_cluster() {", 1)[1].split("\n}", 1)[0]
    delete = delete_cluster.index("kind delete cluster")
    assert delete_cluster.index("CREATED_CLUSTER=0", delete) > delete


def test_module_metrics_render_rejects_invalid_directives_with_valid_sidecar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid sidecar cannot mask an invalid NGINX metrics directive."""
    invalid_render = _valid_metrics_render().replace(
        "markdown_metrics;", "markdown_metrics on;", 1
    )
    completed = subprocess.CompletedProcess(
        args=["helm", "template"],
        returncode=0,
        stdout=invalid_render,
    )
    monkeypatch.setattr(validator, "_run_helm_template", lambda *args: completed)

    result = ValidationResult()
    validator._validate_module_metrics_render(result, "helm", Path("chart"))

    assert any(
        status == "FAIL"
        and check_id == validator._CHECK_HELM_RENDER_MODULE_METRICS
        for status, check_id, _ in result.results
    ), result.results
    assert any(
        status == "PASS"
        and check_id == validator._CHECK_HELM_RENDER_SIDECAR_RESOURCES
        for status, check_id, _ in result.results
    ), result.results


def test_module_metrics_render_accepts_http_and_location_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The release gate must accept the NGINX metrics directive contract."""
    completed = subprocess.CompletedProcess(
        args=["helm", "template"],
        returncode=0,
        stdout=_valid_metrics_render(),
    )
    monkeypatch.setattr(validator, "_run_helm_template", lambda *args: completed)

    result = ValidationResult()
    validator._validate_module_metrics_render(result, "helm", Path("chart"))

    assert not result.has_failures


def test_module_metrics_render_rejects_sidecar_resource_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The enabled Helm render must retain exact sidecar requests and limits."""
    rendered = _valid_metrics_render().replace("cpu: 50m", "cpu: 75m", 1)
    completed = subprocess.CompletedProcess(
        args=["helm", "template"],
        returncode=0,
        stdout=rendered,
    )
    monkeypatch.setattr(validator, "_run_helm_template", lambda *args: completed)

    result = ValidationResult()
    validator._validate_module_metrics_render(result, "helm", Path("chart"))

    assert any(
        status == "FAIL"
        and check_id == validator._CHECK_HELM_RENDER_SIDECAR_RESOURCES
        for status, check_id, _ in result.results
    ), result.results
    assert any(
        "requests.cpu" in message
        for status, check_id, message in result.results
        if status == "FAIL"
        and check_id == validator._CHECK_HELM_RENDER_SIDECAR_RESOURCES
    ), result.results


def test_module_metrics_sidecar_allows_additional_resource_limits() -> None:
    """Required CPU/memory values allow valid extra Kubernetes resources."""
    sidecar = {
        "resources": {
            "requests": {
                "cpu": "50m",
                "memory": "64Mi",
                "ephemeral-storage": "1Gi",
            },
            "limits": {
                "cpu": "250m",
                "memory": "128Mi",
                "ephemeral-storage": "2Gi",
            },
        }
    }

    assert validator._sidecar_resources_match(sidecar)


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
    # and the "must omit markdown_limits" message.
    assert any(
        check_id == validator._CHECK_HELM_RENDER_MODULE_ENABLED
        and "must omit markdown_limits" in message
        for status, check_id, message in result.results
        if status == "FAIL"
    ), f"expected duplicate-markdown_limits failure, got: {result.results}"


def test_helm_legacy_values_survive_null_streaming_properties() -> None:
    """`streaming: {properties: null}` must not raise during the
    legacy-value scan; budget stays exposed via the nested mapping only."""
    result = validator.ValidationResult()
    values_yaml = "markdown:\n  enabled: true\n"
    markdown_properties = {
        "enabled": {"type": "boolean"},
        "streaming": {"properties": None},
    }
    validator._validate_helm_legacy_values(
        result, values_yaml, markdown_properties, configmap=""
    )
    assert not result.has_failures


def test_gate4_bounds_the_cluster_name_like_its_sibling() -> None:
    """An invalid --cluster-name fails with one clear error.

    The sibling helm smoke enforces kind's grammar on the same value; the
    bound here turns an invalid name into a direct message instead of a
    later kind/kubectl refusal.
    """
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/release/gates/gate4_local_k8s_smoke.sh"
    ).read_text(encoding="utf-8")
    assert '--cluster-name)' in script
    assert "invalid cluster name" in script
    assert "^[a-z][a-z0-9-]{0,62}$" in script


def test_gate4_uses_a_run_unique_release_name() -> None:
    """A fixed release name lets concurrent runs race the settle window.

    gate4 holds no lock, so two runs would share the name: one wins Helm's
    storage create while the loser's post-failure settle window can
    attribute the winner's pending release to itself and uninstall it.
    The run-unique name removes the collision by construction.
    """
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/release/gates/gate4_local_k8s_smoke.sh"
    ).read_text(encoding="utf-8")
    assert 'readonly HELM_RELEASE_NAME="gate4-test-$$"' in script, (
        "the release name must be run-unique"
    )
    # The namespace stays fixed: the reuse contract for it is unchanged.
    assert 'readonly HELM_NAMESPACE="gate4-smoke"' in script


def test_gate4_query_failures_keep_the_cluster() -> None:
    """A transient query error must not delete a cluster in use.

    Both ownership queries can fail after this run created the cluster,
    and another actor can already be using it; the cluster claim therefore
    clears on either failure while the namespace claim keeps its existing
    contract (kept on the release-query failure).
    """
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/release/gates/gate4_local_k8s_smoke.sh"
    ).read_text(encoding="utf-8")
    ns_branch = script.split(
        "Unable to determine ownership of namespace", 1
    )[1].split("\n    fi", 1)[0]
    assert "CREATED_CLUSTER=0" in ns_branch
    rel_branch = script.split(
        "Unable to determine ownership of Helm release", 1
    )[1].split("\n    fi", 1)[0]
    assert "CREATED_CLUSTER=0" in rel_branch
    assert "CREATED_NAMESPACE=0" not in rel_branch, (
        "the release-query failure keeps the namespace claim"
    )


def test_gate4_clears_the_cluster_claim_when_the_namespace_is_not_ours() -> None:
    """A namespace we did not create lives in a cluster we must keep.

    gate4 holds no lock, so a concurrent run can hold the namespace of a
    cluster this run created (a fresh cluster has none).  Both the failed
    create and the reuse branch clear the cluster claim with the namespace
    claim: deleting the shared cluster would destroy their workloads.
    """
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/release/gates/gate4_local_k8s_smoke.sh"
    ).read_text(encoding="utf-8")
    create_fail = script.split(
        "Unable to create namespace", 1
    )[1].split("return 1", 1)[0]
    assert "CREATED_CLUSTER=0" in create_fail, (
        "a namespace-create race must preserve the cluster"
    )
    reuse = script.split(
        "Reusing pre-existing namespace", 1
    )[1].split("fi", 1)[0]
    assert "CREATED_CLUSTER=0" in reuse, (
        "a pre-existing namespace must preserve its cluster"
    )


def test_gate4_preserves_the_cluster_on_a_preexisting_release() -> None:
    """A pre-existing release lives in this cluster; keep it.

    Two gate4 runs share the fixed cluster and release names and gate4
    holds no lock, so a concurrent run's release can appear at the
    preflight after this run created the cluster.  Deleting the cluster on
    exit would take that release down, so the branch clears the cluster
    claim with the namespace claim.
    """
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/release/gates/gate4_local_k8s_smoke.sh"
    ).read_text(encoding="utf-8")
    branch = script.split(
        "fail \"Pre-existing Helm release", 1
    )[1].split("return 1", 1)[0]
    assert "CREATED_NAMESPACE=0" in branch
    assert "CREATED_CLUSTER=0" in branch, (
        "a pre-existing release must keep its cluster"
    )


def test_gate4_keeps_namespace_ownership_after_list_failure() -> None:
    """A list failure must not release a namespace this run created.

    Regression: the helm-list failure branch reset CREATED_NAMESPACE, so a
    namespace created moments earlier was left behind on a reused cluster -
    the sibling helm smoke treats list failure the same way (its cleanup
    still owns its namespace).  Nothing of ours is installed yet at that
    point, so cleanup must remove what this run made.
    """
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/release/gates/gate4_local_k8s_smoke.sh"
    ).read_text(encoding="utf-8")

    branch = script.split(
        'fail "Unable to determine ownership of Helm release', 1
    )[1].split("fi\n", 1)[0]
    assert "CREATED_NAMESPACE=0" not in branch, (
        "the list-failure branch must keep the namespace ownership flag"
    )


def test_gate4_settles_ownership_after_a_failed_install() -> None:
    """A failed install resolves ownership instead of clearing both claims.

    Regression: the failure branch cleared CREATED_NAMESPACE and
    CREATED_RELEASE unconditionally, so a release that survived a failed
    rollback was never uninstalled (and a namespace this run created was
    never deleted).  The branch now consults the release state: a live
    release (another creator) clears both claims, a rolled-back install
    clears only the release claim, and a surviving release keeps both so
    cleanup removes them.
    """
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/release/gates/gate4_local_k8s_smoke.sh"
    ).read_text(encoding="utf-8")

    assert "settle_failed_install_ownership" in script
    branch = script.split('fail "helm install failed"', 1)[1].split(
        "return 1", 1
    )[0]
    assert "CREATED_NAMESPACE=0" not in branch, (
        "the failure branch must not clear the namespace claim unconditionally"
    )
    assert "CREATED_RELEASE=0" not in branch, (
        "the failure branch must not clear the release claim unconditionally"
    )

    resolver = script.split("settle_failed_install_ownership() {", 1)[1].split(
        "\n}", 1
    )[0]
    # Live release: preserve another creator's resources (both claims clear).
    live = resolver.split("if [[ -n \"$live\" ]]", 1)[1].split("\n    fi", 1)[0]
    assert "CREATED_RELEASE=0" in live
    assert "CREATED_NAMESPACE=0" in live
    # Rolled back: the release claim clears, the namespace claim stays.
    rolled = resolver.split(
        'if [[ -z "$surviving" ]]', 1
    )[1].split("\n    fi", 1)[0]
    assert "CREATED_RELEASE=0" in rolled
    assert "CREATED_NAMESPACE=0" not in rolled
    # A surviving release keeps both claims for cleanup.
    assert "the release survives, cleanup removes it" in resolver


def test_gate4_settle_helper_classifies_a_name_collision() -> None:
    """A name collision belongs to a concurrent creator, not this run.

    The install error is captured and inspected: Helm 3 reports "cannot
    re-use a name that is still in use" and Helm 4 "cannot reuse", so the
    shared suffix is matched.  A collision clears both claims (the name is
    the other creator's), while an ordinary failure keeps the release claim
    so cleanup uninstalls this run's own pending/failed release.
    """
    script = (
        Path(__file__).resolve().parents[4]
        / "tools/release/gates/gate4_local_k8s_smoke.sh"
    ).read_text(encoding="utf-8")

    fn = script.split("settle_failed_install_ownership() {", 1)[1].split(
        "\n}\n", 1
    )[0]
    assert 'local install_error="${1:-}"' in fn, (
        "the settle helper needs the install output to classify the failure"
    )
    assert 'settle_failed_install_ownership "$install_output"' in script, (
        "the failure branch must pass the captured install output"
    )
    assert 'install_output="$(helm install' in script, (
        "the install output must be captured for classification"
    )

    collision = fn.split("name that is still in use", 1)[1].split(
        "return 0", 1
    )[0]
    assert 'install_error" == *"name that is still in use' in fn, (
        "the collision check must match the shared suffix of both Helm "
        "spellings"
    )
    # The storage-layer race (both racers pass the availability check, one
    # loses the stored-release create) reports a different error, so the
    # settlement must recognize that shape too.
    assert 'install_error" == *"release: already exists' in fn, (
        "the collision check must also match the storage-layer create "
        "error both Helm majors report"
    )
    assert "CREATED_RELEASE=0" in collision
    assert "CREATED_NAMESPACE=0" in collision
    # The collider's release lives in this cluster: deleting the cluster
    # would take it down too, so the cluster claim clears with the others.
    assert "CREATED_CLUSTER=0" in collision, (
        "a collision must preserve the cluster that holds the other "
        "creator's release"
    )

    # The live query reads deployed state only: a pending release may be
    # this run's own failed install, and treating that as another
    # creator's would preserve our own leak.
    live = fn.split('live="$(helm list', 1)[1].split('2>/dev/null)', 1)[0]
    assert "--deployed" in live
    assert "--pending" not in live

    # The live-release branch preserves everything that holds the other
    # creator's release, including the cluster that contains it.
    live_branch = fn.split('if [[ -n "$live" ]]', 1)[1].split(
        "return 0", 1
    )[0]
    assert "CREATED_CLUSTER=0" in live_branch, (
        "a live release (another creator's) must keep its cluster: "
        "deleting the cluster would take the release down with it"
    )

    # The collision classification runs BEFORE any state query: a transient
    # query failure must not turn a settled collision back into "this run's
    # release" and let cleanup uninstall the concurrent winner.
    collision_at = fn.index("name that is still in use")
    live_query_at = fn.index('live="$(helm list')
    assert collision_at < live_query_at, (
        "the collision check must precede the deployed-state query"
    )

    # The caller reaches the settlement before ANY output: bash runs a
    # deferred TERM trap between foreground commands, so a signal landing
    # on the print or the kubectl queries below would clean up with the
    # collision's ownership flags still set.
    caller = script.split("if ! install_output=", 1)[1].split(
        "return 1", 1
    )[0]
    assert caller.index("settle_failed_install_ownership") < caller.index(
        'fail "helm install failed"'
    ), "the settlement must run before the failure output"
    assert caller.index("settle_failed_install_ownership") < caller.index(
        "get pods"
    ), "the settlement must run before the failure diagnostics"

    # The surviving query includes pending so this run's own pending
    # release is uninstalled by cleanup.
    surviving = fn.split('surviving="$(helm list', 1)[1].split(
        '2>/dev/null)', 1
    )[0]
    for flag in (
        "--pending",
        "--failed",
        "--uninstalled",
        "--uninstalling",
        "--superseded",
    ):
        assert flag in surviving, flag
