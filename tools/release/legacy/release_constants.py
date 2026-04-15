"""Shared constants for 0.4.0 release-gate validation and tests.

Centralize these lists to reduce drift between scripts/tests and docs.
"""

from typing import Tuple


# Known 0.4.0 sub-spec directory keyword fragments.
SUBSPECS_KEYWORDS: Tuple[str, ...] = (
    "overall-scope",
    "packaging",
    "benchmark",
    "rollout",
    "prometheus",
    "parser",
)


# Priority split used by go/no-go property checks.
P0_SUBSPECS: Tuple[str, ...] = (
    "overall-scope-release-gates",
    "packaging-and-first-run",
    "benchmark-corpus-and-evidence",
    "rollout-safety-controlled-enablement",
    "prometheus-module-metrics",
)
P1_SUBSPEC = "parser-path-optimization"


# Source of truth: docs/project/release-gates/scope-evaluation-process.md
NON_GOALS: Tuple[str, ...] = (
    "True streaming HTML-to-Markdown conversion",
    "Streaming-aware or chunk-driven FFI contract evolution",
    "New output format negotiation (JSON, text/plain, MDX)",
    "OpenTelemetry tracing",
    "High-cardinality metrics",
    "GUI, console, or dashboard",
    "Cross-web-server ecosystem support (Apache, Caddy, Envoy, Traefik)",
    "Enterprise control plane or policy center",
    "AI post-processing capabilities (summarization, rewriting, extraction)",
    "Complex shadow streaming replacement",
    "Positioning 0.4.0 as a \"1.0.0 pre-release\"",
)
