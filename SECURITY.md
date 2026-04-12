# Security Policy

## Supported Versions

This project is currently maintained on the `0.4.x` line.

| Version | Supported |
|---------|-----------|
| `0.4.x` | Yes |
| `0.3.x` | No |
| `0.2.x` | No |
| `0.1.x` | No |
| `< 0.1.0` | No |

Security fixes are normally released on the latest supported line only.

## Reporting a Vulnerability

Do **not** open a public GitHub issue for a suspected security vulnerability.

Please use one of these private channels instead:

- GitHub Security Advisories for this repository
- A private report shared directly with the maintainers, if a maintainer-provided secure channel is available

When reporting, include:

- affected version or commit
- deployment context (`NGINX` version, platform, module configuration)
- reproduction steps or a minimal proof of concept
- impact assessment
- whether the issue is already publicly known

Response times are best effort for this maintainer-run project and may be slower during holidays or periods of limited availability.

We will try to:

- acknowledge the report within 14 calendar days
- complete initial triage within 45 calendar days when enough detail is available
- coordinate disclosure and release timing with the reporter

## Scope

This policy is intended for vulnerabilities in the code and release artifacts shipped by this repository, including:

- the NGINX C module in `components/nginx-module/`
- the Rust converter and FFI boundary in `components/rust-converter/`
- installation and release tooling in `tools/`
- official container and NGINX examples when used as documented

Examples of in-scope issues:

- memory safety issues, unsafe FFI misuse, or crashes triggered by crafted input
- bypasses of sanitization intended to block script execution, dangerous URLs, XXE-style behavior, or SSRF-style fetch behavior
- authentication or cache-policy flaws that can expose protected content
- denial-of-service conditions that bypass documented resource limits or timeout protections
- supply-chain issues in shipped dependencies or release workflows with a realistic impact on users

Examples that are usually out of scope unless they demonstrate a concrete vulnerability in this project:

- missing TLS, WAF, CSP, or security headers in a user's own deployment
- unsafe local NGINX configuration unrelated to this module
- bugs in unsupported versions
- markdown quality issues, parsing inaccuracies, or content-loss bugs without security impact
- self-XSS or browser-only behavior that does not survive the module's Markdown conversion path

## Disclosure and Fixes

Please give maintainers reasonable time to investigate and ship a fix before public disclosure.

When a report is confirmed:

- critical issues should be fixed in a patch release when feasible
- lower-severity fixes may be batched into the next planned release
- release notes and `CHANGELOG.md` should document the fix, and may include CVE references when appropriate

## Operational Expectations

This project processes **untrusted upstream HTML**. Operators should treat it as a security-sensitive component in the request path.

Recommended operator practices:

- run supported versions of NGINX and Rust toolchains
- keep resource limits and timeouts enabled
- review authenticated-request and cache settings carefully
- expose the metrics endpoint only to trusted networks
- treat metrics as instance-wide shared counters when setting alerts or dashboards
- apply normal least-privilege controls to NGINX workers and deployment environments

## Continuous Security Validation

The repository includes `cargo-fuzz` targets for parser, FFI, and security-validator paths under `components/rust-converter/fuzz/`.

Local smoke example:

```bash
make test-rust-fuzz-smoke
```

Nightly GitHub Actions coverage is defined in `.github/workflows/nightly-fuzz.yml`.

## Related Documentation

- implementation threat model and defenses: `docs/features/security.md`
- configuration guidance: `docs/guides/CONFIGURATION.md`
- operations and monitoring guidance: `docs/guides/OPERATIONS.md`
