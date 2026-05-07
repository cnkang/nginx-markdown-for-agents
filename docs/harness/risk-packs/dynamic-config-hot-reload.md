# Dynamic Config Hot-Reload Pack

Use this pack when runtime configuration parsing, reload scheduling, reload
retry state, or worker lifecycle integration changes.

## Triggers

- touched `ngx_http_markdown_dynconf_impl.h`, reload timer/lifecycle code,
  request-time dynamic config apply code, or config directive handlers
- touched operator examples or configuration docs for runtime reload behavior
- keywords like `dynconf`, `dynamic config`, `hot reload`, `reload_pending`, or
  `config reload`

## Risks

- Failed reload clears retry state and silently leaves workers stale
- Blocking file I/O runs in the worker request path
- Parser misses the final config line when the file has no trailing newline
- Size/budget directives use raw integer parsers instead of NGINX size parsers
- Dynamic path buffers are not NUL-terminated before file-system calls
- Runtime-applied values diverge from normal directive parsing semantics

## Common Supporting Packs

- `nginx-protocol-safety` when runtime config changes request eligibility or
  response behavior
- `observability-metrics` when reload success/failure is logged or counted
- `docs-tooling-drift` when configuration docs or examples change

## Sync Points

- Normal directive parsing and dynamic config parsing must share equivalent
  bounds, units, accepted values, and error behavior.
- `reload_pending` or equivalent retry latches must remain set after failed
  reloads and clear only after a successful apply.
- Worker timer setup and cleanup must match NGINX lifecycle ownership rules.
- File paths used by reload code must be bounded, sanitized, and
  NUL-terminated before file-system APIs receive them.
- Runtime reload tests must include final-line-without-newline, parse failure,
  retry, and successful apply cases.
- **effective_conf**: request-path code must read dynconf-mutable fields
  (`enabled`, `enabled_source`, `prune_noise`, `log_verbosity`,
  `memory_budget`, `streaming_budget`) through
  `ngx_http_markdown_effective_*()` helpers via `ctx->effective_conf`,
  not directly from live `conf->`.  Detection script:
  `tools/harness/detect_live_conf_reads.sh`.
- **CWE-190**: size-value parsing via `ngx_parse_size()` must go through
  `ngx_http_markdown_dynconf_parse_size_safe()` (parse→validate→safe-cast).
  Direct `(size_t)` casts of `ssize_t` results without non-negative guards
  are forbidden in new request-path code.  Detection script:
  `tools/harness/detect_cwe190_casts.sh`.
- **CWE-22**: Python tooling scripts that accept file paths from CLI
  arguments must pass them through `validate_read_path()` before `open()`.
  Detection script: `tools/harness/detect_cwe22_paths.py`.

## Minimum Verification

```bash
make harness-check
make harness-security-checks
make test-nginx-unit
make docs-check
```

For changes that affect request-time reload behavior, also run the relevant
integration or E2E target before broader release-quality checks.

## Canonical References

- [../../guides/CONFIGURATION.md](../../guides/CONFIGURATION.md)
- [../../../AGENTS.md](../../../AGENTS.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.6.0 | 2026-05-03 | Codex | Initial pack from two-week branch scan |
| 0.6.2 | 2026-05-07 | Kang | Added effective_conf, CWE-190, CWE-22 sync points and harness-security-checks |
