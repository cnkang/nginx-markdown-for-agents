# Verification Map

Source of truth:
`docs/harness/routing-manifest.json -> verification_families`.

Use this map as a fast lookup, then trust the manifest for final values.

## Families

- `harness-sync` (`cheap-blocker`)
  - `make harness-check`
- `docs-tooling` (`cheap-blocker`)
  - `make docs-check`
- `rust-streaming` (`focused-semantic`)
  - `make test-rust-streaming`
- `nginx-streaming` (`focused-semantic`)
  - `make test-nginx-unit-streaming`
- `ffi-boundary` (`focused-semantic`)
  - `make build`
  - `make test-rust`
- `observability-metrics` (`focused-semantic`)
  - `make docs-check`
  - `make release-gates-check`
- `runtime-e2e` (`umbrella`)
  - `make verify-chunked-native-e2e-smoke`
  - `make verify-streaming-failure-cache-e2e`
- `release-quality` (`umbrella`)
  - `make harness-check-full`

## Recommended Order

1. `cheap-blocker`
2. `focused-semantic`
3. `umbrella` (only when required by touched surfaces)

