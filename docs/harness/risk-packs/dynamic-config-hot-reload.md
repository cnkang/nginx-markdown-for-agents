# Dynamic Config Hot-Reload Pack

> **ARCHIVED.** The 0.9.2 convergence removed the dynamic-configuration
> (runtime hot-reload) subsystem: the external runtime file, the reload timer,
> the observed-versus-applied modification-time retry state, and the runtime
> snapshot that a reload could swap. This pack stays as a historical record of
> the pre-0.9.2 risk surface. It has no active triggers or source paths.
>
> This pack does not cover the retained static configuration model (the
> per-level merge, the `effective_conf` view that the module binds once at
> header-filter entry, and the static block-mask). Static configuration
> changes route through `nginx-protocol-safety` and `docs-tooling-drift`.
> Rules 45 and 71 in
> [../rules/dynconf-snapshot.md](../rules/dynconf-snapshot.md) document the
> retained static binding.

## Historical scope

There are no active triggers or source paths for this pack. The entries below
describe the risks behind the removed runtime-reload behavior. They are not
requirements of the 0.9.2 production contract. A future runtime-reload feature
must define its own routing entry and rules as part of its own review.

## Historical risks (pre-0.9.2, no longer enforced)

- A failed reload cleared retry state and silently left workers on stale
  configuration.
- The global runtime view leaked into locations that had the feature off.
- An unknown key was silently ignored instead of failing the whole file
  atomically.
- Blocking file I/O ran in the worker request path.
- The parser missed the final line of a file that had no trailing newline.
- Size and budget values used raw integer parsers instead of the NGINX size
  parsers, diverging from normal directive semantics.
- Runtime path buffers reached file-system calls without NUL termination.

## Historical sync points (pre-0.9.2, no longer enforced)

- Normal directive parsing and runtime parsing once had to share equivalent
  bounds, units, accepted values, and error behavior.
- A retry latch once had to stay set after a failed reload and clear only after
  a successful apply.
- The confirmed-applied modification time once updated only after a successful
  reload, and the timer retried while the observed and confirmed values
  diverged.
- Unknown keys once triggered atomic rejection of the whole file.
- Worker timer setup and cleanup once had to follow NGINX lifecycle ownership.
- Runtime path buffers once had to stay bounded and sanitized and to carry a
  NUL terminator before file-system APIs received them.
- Runtime tests once had to cover the final-line-without-newline, parse
  failure, retry, and successful-apply cases.

## Historical minimum verification (pre-0.9.2)

The commands below were the minimum verification set when the runtime-reload
behavior was live. They document what the archived pack used to require, not
the current release requirements.

```bash
make harness-check
make harness-security-checks
make test-nginx-unit
make docs-check
```

## Canonical References

- [../rules/dynconf-snapshot.md](../rules/dynconf-snapshot.md) — Rules 45 and 71
  document the retained static configuration binding and block-mask.
- [../../../AGENTS.md](../../../AGENTS.md)

## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-19 | Kang | Marked pack archived (dynamic-config runtime hot-reload subsystem removed in the 0.9.2 convergence); rewrote entries as historical record; pointed static config to nginx-protocol-safety, docs-tooling-drift, and Rules 45/71 |
| 0.6.2 | 2026-05-07 | Kang | Added effective_conf, CWE-190, CWE-22 sync points and harness-security-checks |
| 0.6.2 | 2026-05-07 | Kang | Added dynconf_enabled isolation, applied_mtime retry contract, unknown-key atomic rejection risks and sync points; startup apply of existing dynconf file |
| 0.6.0 | 2026-05-03 | Codex | Initial pack from two-week branch scan |
