"""Pin the module listener port across the chart surfaces.

The module HTTP listener port is duplicated across chart templates (the
Deployment containerPort, the ConfigMap ``listen``, the sidecar
``proxy_pass``) and reserved in the values schema.  Nothing regenerates
these from one source, so this test pins them to a single value: an edit
that changes only one copy fails here instead of shipping a chart whose
pieces disagree.
"""

import re
from pathlib import Path

CHART = Path(__file__).resolve().parents[3] / "charts" / "nginx-markdown"


def _port(pattern: str, text: str) -> int:
    match = re.search(pattern, text, flags=re.MULTILINE)
    assert match, pattern
    return int(match.group(1))


def test_module_listener_port_is_consistent_across_chart_surfaces() -> None:
    deployment = (CHART / "templates" / "deployment.yaml").read_text(
        encoding="utf-8"
    )
    configmap = (CHART / "templates" / "configmap.yaml").read_text(
        encoding="utf-8"
    )
    schema = (CHART / "values.schema.json").read_text(encoding="utf-8")

    ports = {
        "deployment containerPort": _port(r"containerPort:\s*(\d+)", deployment),
        "configmap listen": _port(r"^\s*listen\s+(\d+);", configmap),
        "configmap proxy_pass": _port(r"127\.0\.0\.1:(\d+)", configmap),
        "schema reserved const": _port(r'"not":\s*\{\s*"const":\s*(\d+)', schema),
    }

    assert len(set(ports.values())) == 1, ports
