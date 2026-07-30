"""Regression tests for public-surface inventory drift detection."""

from __future__ import annotations

from tools.harness import detect_public_surface_drift as detector


def test_inventory_loader_honors_explicit_path(monkeypatch) -> None:
    requested = "/tmp/custom-public-surface.json"
    seen = []

    def fake_read_text(path):
        seen.append(path)
        return '{"dynconf_keys": [], "metrics": []}'

    monkeypatch.setattr(detector, "read_text", fake_read_text)

    assert detector.load_inventory(requested)["dynconf_keys"] == []
    assert seen == [detector.os.path.realpath(requested)]


def test_inventory_loader_resolves_explicit_symlink_path(tmp_path, monkeypatch) -> None:
    target = tmp_path / "public-surface.json"
    link = tmp_path / "public-surface-link.json"
    target.write_text('{"dynconf_keys": [], "metrics": []}', encoding="utf-8")
    link.symlink_to(target)
    seen = []

    def fake_read_text(path):
        seen.append(path)
        return '{"dynconf_keys": [], "metrics": []}'

    monkeypatch.setattr(detector, "read_text", fake_read_text)

    assert detector.load_inventory(str(link))["dynconf_keys"] == []
    assert seen == [str(target.resolve())]


def test_dynconf_key_drift_is_reported() -> None:
    inventory = {"dynconf_keys": ["markdown_filter", "memory_budget"]}

    drift = detector.check_dynconf_keys(
        inventory,
        ["markdown_filter", "streaming_budget"],
    )

    assert drift == [
        "dynconf keys in source but not in inventory: streaming_budget",
        "dynconf keys in inventory but not in source: memory_budget",
    ]


def test_metric_drift_is_reported() -> None:
    inventory = {
        "metrics": [
            {"name": "nginx_markdown_requests_total"},
            {"name": "nginx_markdown_conversions_total"},
        ]
    }

    drift = detector.check_metrics(
        inventory,
        ["nginx_markdown_requests_total", "nginx_markdown_failures_total"],
    )

    assert drift == [
        "metrics in source but not in inventory: nginx_markdown_failures_total",
        "metrics in inventory but not in source: nginx_markdown_conversions_total",
    ]


def test_live_inventory_matches_all_extracted_surfaces() -> None:
    inventory = detector.load_inventory()

    assert detector.check_dynconf_keys(
        inventory, detector.extract_dynconf_keys_from_c()
    ) == []
    assert detector.check_metrics(
        inventory, detector.extract_metric_names_from_c()
    ) == []
    assert detector.check_ffi_exports(
        inventory, detector.extract_ffi_exports_from_rust()
    ) == []
