# Observability Metrics Pack

Use this as the primary pack when metrics, reason-code visibility, text/JSON or
Prometheus output, or release-gate monitoring semantics change.

## Triggers

- touched files in metrics renderers or reason-code classification paths
- touched files under `tools/release/**`
- touched files under `docs/guides/prometheus-metrics.md`
- keywords like `prometheus`, `reason code`, `ttfb`, `snapshot`, `metrics`

## Common Supporting Packs

- `docs-tooling-drift` when dashboards, docs, or release gates need to stay in sync

## Sync Points

- struct fields vs renderer output vs docs
- metric name semantics vs actual measurement point
- reason-code additions vs log emission and severity classification
- release-gate docs vs validator key paths

## Minimum Verification

```bash
make harness-check
make docs-check
make release-gates-check
```

## Canonical References

- [../../guides/prometheus-metrics.md](../../guides/prometheus-metrics.md)
- [../../project/release-gates/README.md](../../project/release-gates/README.md)
- [../../../AGENTS.md](../../../AGENTS.md)
