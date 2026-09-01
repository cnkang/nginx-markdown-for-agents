# NGINX Markdown for Agents

[![Latest Release](https://img.shields.io/github/v/release/cnkang/nginx-markdown-for-agents?sort=semver)](https://github.com/cnkang/nginx-markdown-for-agents/releases) [![NGINX](https://img.shields.io/badge/NGINX-%3E%3D1.24.0-009639?logo=nginx&logoColor=white)](https://github.com/cnkang/nginx-markdown-for-agents/blob/main/docs/guides/INSTALLATION.md) [![CI](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/ci.yml) [![Security Scanning](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/cnkang/nginx-markdown-for-agents/actions/workflows/codeql.yml) [![License](https://img.shields.io/github/license/cnkang/nginx-markdown-for-agents)](https://github.com/cnkang/nginx-markdown-for-agents/blob/main/LICENSE) [![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=cnkang_nginx-markdown-for-agents&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=cnkang_nginx-markdown-for-agents)

English | [Simplified Chinese](README_zh-CN.md)

> HTML in. Markdown out.
> When the client asks for it, or when you decide to serve it.

> Current line: v0.9.2 is a development candidate and is not published.
> It is the final breaking release before v1.0. Use a published tag for
> installation until the v0.9.2 release is available.

NGINX Markdown for Agents adds a machine-friendly Markdown representation to
HTML pages that you already serve. Clients that send `Accept: text/markdown`
receive Markdown. Browsers and other clients keep receiving the original HTML.

The module performs conversion at the NGINX edge. It does not require an
application rewrite, a second content API, or a separate scraping service.
It can also target selected bots through User-Agent matching.

## What it does

| Request | Result |
|---------|--------|
| `Accept: text/markdown` | Markdown with `Content-Type: text/markdown` |
| `Accept: text/html` | Original HTML |
| Matched User-Agent with `markdown_accept force` | Markdown without changing the upstream `Accept` header |

The module removes browser-oriented noise before an agent consumes a page.
This can reduce token use and make the page structure easier to interpret.
The same URL can continue to serve HTML to browsers.

## Quick Start

### 1. Install the module

Use the [Installation Guide](docs/guides/INSTALLATION.md#2-shortest-success-path)
for the signed release installer and platform-specific packages. The guide
also covers Docker, source builds, Homebrew, and installation troubleshooting.

For macOS, the project Homebrew tap provides a release-tag package:

```bash
brew tap cnkang/nginx-markdown
brew install cnkang/nginx-markdown/nginx-markdown-module
```

### 2. Enable Markdown on a location

```nginx
load_module modules/ngx_http_markdown_filter_module.so;

http {
    upstream backend {
        server 127.0.0.1:8080;
    }

    server {
        listen 80;

        location / {
            markdown_filter on;
            markdown_streaming auto;
            markdown_auto_decompress on;
            proxy_pass http://backend;
        }
    }
}
```

### 3. Verify both representations

```bash
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/
curl -sD - -o /dev/null -H "Accept: text/html" http://localhost/
```

The first request should return `Content-Type: text/markdown`. The second
request should keep the upstream HTML response. Use the
[installation troubleshooting guide](docs/guides/INSTALLATION.md#10-troubleshooting)
when the result differs.

## 0.9.2 configuration essentials

0.9.2 freezes the public configuration at 25 active directives. Configure the
behavior explicitly so `nginx -T` shows the settings that operators selected.

```nginx
http {
    markdown_cache_validation ims_only;
    markdown_streaming auto;
    markdown_limits conversion_memory=64m conversion_timeout=30s
        parser_timeout=10s max_inflight=64;

    server {
        listen 80;

        location /docs/ {
            markdown_filter on;
            markdown_accept strict;
            proxy_pass http://backend;
        }
    }
}
```

- `markdown_streaming off` selects full-buffer conversion. `auto` uses a
  bounded response-shape heuristic. `force` requests streaming after the
  cache and eligibility checks pass.
- `markdown_limits` bounds conversion memory, time, decompression, streaming
  buffers, and concurrent work.
- `markdown_accept strict` is a safe default for staged rollout. Use
  `wildcard` or `force` only when that behavior is intentional.
- `markdown_error_policy` controls whether conversion errors pass through or
  return a configured status.

See the [Configuration Reference](docs/guides/CONFIGURATION.md) for the full
directive table. Use the [0.9.2 migration guide](docs/guides/MIGRATION-0.9.2.md)
before changing an existing 0.9.1 configuration.

## Target selected bots

Many AI crawlers send browser-style `Accept` headers. Use NGINX `map` to select
known User-Agent values, then set `markdown_accept force` for that location.
The module does not rewrite the `Accept` header sent to the upstream.

```nginx
http {
    map $http_user_agent $markdown_for_bot {
        default       off;
        "~*ClaudeBot" on;
        "~*GPTBot"    on;
        "~*Googlebot" on;
    }

    server {
        listen 80;

        location /docs/ {
            markdown_filter $markdown_for_bot;
            markdown_accept force;
            proxy_pass http://backend;
        }
    }
}
```

```bash
curl -sS -D - -o /dev/null \
    -A "ClaudeBot/1.0" -H "Accept: text/html" http://localhost/docs/
```

The [bot-targeted example](examples/nginx-configs/06-bot-targeted-conversion.conf)
contains a larger User-Agent map. Status, content-type, size, and other
eligibility checks still apply.

## What changed in 0.9.2

0.9.2 is a breaking release candidate. Read the
[release notes](docs/releases/0.9.2-release-notes.md) before upgrading.

- 0.9.2 reduces the public surface from 63 directives to 25. Profiles, OTel,
  per-path metrics, shadow mode, and other removed legacy directives are no
  longer accepted. Run `nginx -t` after migration.
- Dynamic configuration now accepts JSON schema v1 with five runtime keys.
  A failed reload keeps the active and last-known-good snapshots unchanged.
  Restore a file by atomically replacing it.
- Diagnostics uses read-only JSON schema v2 and accepts only `GET` and `HEAD`.
  Its built-in access boundary is loopback-only. Prometheus metrics use the
  frozen v1 contract.
- The internal C/Rust FFI ABI advances to v2. Rebuild the module and converter
  together. The FFI is an internal surface and has no cross-version guarantee.

Use the [upgrade guide](docs/guides/UPGRADE-TO-0.9.2.md) for binary replacement,
configuration migration, service restart, and post-upgrade checks. Use the
[rollback guide](docs/guides/VERSION_ROLLBACK-0.9.2.md) when you need to
downgrade.

## Capabilities

| Capability | Summary |
|------------|---------|
| Content negotiation | Return Markdown on request or for selected bots |
| HTML passthrough | Leave browser and non-eligible responses unchanged |
| Compression handling | Process gzip, deflate, and Brotli upstream responses |
| Bounded conversion | Use full-buffer or bounded streaming conversion |
| Cache-aware responses | Support ETags and conditional requests for variants |
| Output controls | Sanitize links, prune noise, and add optional metadata |
| Failure and observability | Configure error policy, diagnostics, and Prometheus metrics |

## Platform support

Release tooling generates the matrix below from the release policy source. It lists the
tested NGINX versions, platforms, artifacts, and support tiers. See the
[package compatibility guide](docs/guides/PACKAGE_COMPATIBILITY.md) for
installation-specific details.

<!-- BEGIN:release-matrix:support-matrix -->

| NGINX | Channel | OS | libc | Arch | Artifact | Tier | Blocking |
|-------|---------|-----|------|------|----------|------|----------|
| 1.31.4 | mainline | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.31.4 | mainline | linux | musl | arm64 | dynamic-module | supported | Yes |
| 1.31.4 | mainline | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.31.4 | mainline | linux | musl | amd64 | dynamic-module | supported | Yes |
| 1.31.4 | mainline | debian12 | glibc | arm64 | deb-package | supported | Yes |
| 1.31.4 | mainline | debian12 | glibc | arm64 | docker-image | supported | Yes |
| 1.31.4 | mainline | debian12 | glibc | amd64 | deb-package | supported | Yes |
| 1.31.4 | mainline | debian12 | glibc | amd64 | docker-image | supported | Yes |
| 1.31.4 | mainline | alpine3.24 | musl | arm64 | docker-image | supported | Yes |
| 1.31.4 | mainline | alpine3.24 | musl | amd64 | docker-image | supported | Yes |
| 1.31.4 | mainline | almalinux9 | glibc | arm64 | rpm-package | supported | Yes |
| 1.31.4 | mainline | almalinux9 | glibc | amd64 | rpm-package | supported | Yes |
| 1.30.4 | stable | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.30.4 | stable | linux | musl | arm64 | dynamic-module | supported | Yes |
| 1.30.4 | stable | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.30.4 | stable | linux | musl | amd64 | dynamic-module | supported | Yes |
| 1.30.4 | stable | debian12 | glibc | arm64 | deb-package | supported | Yes |
| 1.30.4 | stable | debian12 | glibc | amd64 | deb-package | supported | Yes |
| 1.30.4 | stable | almalinux9 | glibc | arm64 | rpm-package | supported | Yes |
| 1.30.4 | stable | almalinux9 | glibc | amd64 | rpm-package | supported | Yes |
| 1.28.3 | legacy | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.28.3 | legacy | linux | musl | arm64 | dynamic-module | supported | Yes |
| 1.28.3 | legacy | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.28.3 | legacy | linux | musl | amd64 | dynamic-module | supported | Yes |
| 1.28.3 | legacy | debian12 | glibc | arm64 | deb-package | supported | Yes |
| 1.28.3 | legacy | debian12 | glibc | amd64 | deb-package | supported | Yes |
| 1.28.3 | legacy | almalinux9 | glibc | arm64 | rpm-package | supported | Yes |
| 1.28.3 | legacy | almalinux9 | glibc | amd64 | rpm-package | supported | Yes |
| 1.26.3 | legacy | macos | darwin | arm64 | homebrew-formula | experimental | No |
| 1.26.3 | legacy | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.26.3 | legacy | linux | musl | arm64 | dynamic-module | supported | Yes |
| 1.26.3 | legacy | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.26.3 | legacy | linux | musl | amd64 | dynamic-module | supported | Yes |
| 1.26.3 | legacy | debian12 | glibc | arm64 | deb-package | supported | Yes |
| 1.26.3 | legacy | debian12 | glibc | arm64 | docker-image | supported | Yes |
| 1.26.3 | legacy | debian12 | glibc | amd64 | deb-package | supported | Yes |
| 1.26.3 | legacy | debian12 | glibc | amd64 | docker-image | supported | Yes |
| 1.26.3 | legacy | any | n/a | any | source | best-effort | No |
| 1.26.3 | legacy | alpine3.20 | musl | arm64 | docker-image | supported | Yes |
| 1.26.3 | legacy | alpine3.20 | musl | amd64 | docker-image | supported | Yes |
| 1.26.3 | legacy | almalinux9 | glibc | arm64 | rpm-package | supported | Yes |
| 1.26.3 | legacy | almalinux9 | glibc | amd64 | rpm-package | supported | Yes |
| 1.24.0 | legacy | linux | glibc | arm64 | dynamic-module | supported | Yes |
| 1.24.0 | legacy | linux | musl | arm64 | dynamic-module | supported | Yes |
| 1.24.0 | legacy | linux | glibc | amd64 | dynamic-module | supported | Yes |
| 1.24.0 | legacy | linux | musl | amd64 | dynamic-module | supported | Yes |
| 1.24.0 | legacy | debian12 | glibc | arm64 | deb-package | supported | Yes |
| 1.24.0 | legacy | debian12 | glibc | amd64 | deb-package | supported | Yes |
| 1.24.0 | legacy | almalinux9 | glibc | arm64 | rpm-package | supported | Yes |
| 1.24.0 | legacy | almalinux9 | glibc | amd64 | rpm-package | supported | Yes |
<!-- END:release-matrix:support-matrix -->

## Documentation

| Need | Canonical documentation |
|------|-------------------------|
| Install or build | [Installation](docs/guides/INSTALLATION.md), [Build Instructions](docs/guides/BUILD_INSTRUCTIONS.md) |
| Configure directives | [Configuration Reference](docs/guides/CONFIGURATION.md) |
| Deploy and operate | [Deployment Examples](docs/guides/DEPLOYMENT_EXAMPLES.md), [Operations](docs/guides/OPERATIONS.md) |
| Upgrade or roll back 0.9.2 | [Migration](docs/guides/MIGRATION-0.9.2.md), [Upgrade](docs/guides/UPGRADE-TO-0.9.2.md), [Rollback](docs/guides/VERSION_ROLLBACK-0.9.2.md) |
| Understand features | [Features index](docs/features/README.md), [Decompression](docs/features/DECOMPRESSION.md), [Streaming](docs/features/STREAMING_COMPATIBILITY.md) |
| Understand architecture | [Architecture index](docs/architecture/README.md), [System Architecture](docs/architecture/SYSTEM_ARCHITECTURE.md) |
| Validate or contribute | [Testing index](docs/testing/README.md), [Harness index](docs/harness/README.md) |

## Development and validation

Run the smallest relevant check for the change:

```bash
make test
make test-rust
make test-nginx-unit
make test-e2e-rust
```

Documentation and repository-contract changes also require:

```bash
make docs-check
make harness-check
```

Runtime integration and native E2E checks require a real NGINX binary. Set
`NGINX_BIN=/absolute/path/to/nginx` when NGINX is not on `PATH`. See the
[testing documentation](docs/testing/README.md) for the full test matrix.

## Earlier releases

The 0.9.1 line is the immediate compatibility baseline for 0.9.2. Use the
[0.9.2 migration guide](docs/guides/MIGRATION-0.9.2.md) for that upgrade.
For 0.9.0 and older releases, use the [CHANGELOG](CHANGELOG.md) and the
versioned migration guides. This README intentionally does not duplicate
historical release logs.

## Roadmap

- Keep the frozen Prometheus and diagnostics contracts compatible with external
  monitoring systems.
- Expand official APT and YUM distribution channels.
- Extend `nginx-markdown-doctor` and runtime monitoring guidance.

## License

BSD 2-Clause "Simplified" License. See [LICENSE](LICENSE).

## Document updates

| Version | Date | Change |
|---------|------|--------|
| 0.9.2 | 2026-09-01 | Reorganized the entry point around the 0.9.2 contract and linked canonical guides. |

Older README changes are available in the Git history.
