# NGINX Markdown for Agents

An NGINX module that converts HTML to Markdown for AI agents through HTTP content negotiation.

Inspired by Cloudflare's [Markdown for Agents](https://blog.cloudflare.com/markdown-for-agents/) announcement, this project explores the same agent-friendly content delivery idea as a self-hostable NGINX filter module.

## Problem This Solves

AI agents usually fetch web pages as HTML, but raw HTML is noisy for LLM consumption:

- Boilerplate markup increases token usage and parsing cost
- Important content is mixed with navigation, scripts, and presentation markup
- Clients often need custom HTML scraping logic before they can use page content

This module lets clients ask NGINX for a Markdown representation of eligible HTML pages via standard content negotiation (`Accept: text/markdown`), so you can:

- Keep existing HTML pages and backends unchanged
- Add an agent-friendly response variant at the edge / reverse proxy layer
- Preserve normal browser behavior (`Accept: text/html` still returns HTML)

## Start Here

If you want the fastest path to a working setup:

1. Build the converter/module prerequisites in [Build & Quick Start](#build--quick-start)
2. Pick a config pattern:
   - small-scope rollout (`markdown_filter on;` only on one route first)
   - global-on with exclusions (recommended steady-state for many sites)
3. Validate with [Quick Verification (curl)](#quick-verification-curl)
4. Check [Common Deployment Notes (gzip / PHP / existing sites)](#common-deployment-notes-gzip--php--existing-sites)

## Overview

This module enables AI agents to retrieve web content in Markdown format by requesting `Accept: text/markdown`. The module consists of:

- **Rust converter**: Memory-safe HTML parsing and Markdown generation
- **NGINX filter module (C)**: Integration layer for the NGINX filter chain
- **FFI Bridge**: Safe boundary between Rust and C components

### Why C + Rust

This project uses C and Rust together for practical reasons:

- **C aligns with NGINX extension points**: request phases, filter-chain hooks,
  and buffer/header handling are all native NGINX module APIs.
- **Rust aligns with transformation logic**: HTML parsing and Markdown generation
  are logic-heavy paths where stronger type checks and ownership rules help reduce
  memory-management mistakes.
- **Separation of concerns is explicit**: C handles request integration and policy
  decisions; Rust handles conversion and normalization.
- **Deployment impact stays small**: operators can keep normal NGINX deployment
  patterns while using the Rust converter through a stable FFI boundary.
- **Failure behavior remains configurable**: conversion errors can still follow the
  module's fail-open or fail-closed strategy (`markdown_on_error`).

## Build & Quick Start

### Pre-compiled Binary Installation (Recommended)

If you are using an official NGINX release (like those from the `nginx` PPA, Alpine `nginx` package, or official Docker images), the easiest way to install the module is using the installation script. This avoids the need to compile Rust or C code.

```bash
curl -sSL https://raw.githubusercontent.com/cnkang/nginx-markdown-for-agents/main/tools/install.sh | sudo bash
```

After the script completes, simply add the `load_module` directive to the top of your `nginx.conf`:

```nginx
load_module /etc/nginx/modules/ngx_http_markdown_filter_module.so;
# Note: The script will specify the exact path to use
```

Then reload NGINX:
```bash
sudo nginx -t && sudo nginx -s reload
```

### Installation from Source

If you want to compile the module from source, the shortest path to confirm the project builds locally is:

```bash
# One-time setup
cargo install cbindgen

# Build Rust converter + generate/copy header + run smoke test
make test
```

If you only want the build artifacts (no smoke test), use:

```bash
# Build Rust library and generate/copy the C header
make

# (Optional) run the root-level smoke test target
make test
```

For a more complete local workflow, see [Build Instructions](docs/guides/BUILD_INSTRUCTIONS.md).

### Quick Start (NGINX Runtime Integration)

Once the Rust library/header are prepared (`make`), the fastest runtime path is:

```bash
# In your NGINX source tree
./configure --add-dynamic-module=/path/to/nginx-markdown-for-agents/components/nginx-module
make modules

# Install the built module (example prefix)
sudo mkdir -p /usr/local/nginx/modules
sudo cp objs/ngx_http_markdown_filter_module.so /usr/local/nginx/modules/
```

Then enable it in your NGINX config.

#### Minimal Enablement (Reverse Proxy / HTTP Upstream)

```nginx
load_module modules/ngx_http_markdown_filter_module.so;

http {
    # Safer default: enable only where you want it first
    markdown_filter off;

    server {
        listen 8080;

        location /docs/ {
            markdown_filter on;
            # Ask upstream for uncompressed HTML to simplify conversion
            proxy_set_header Accept-Encoding "";
            proxy_pass http://backend;
        }
    }
}
```

#### Minimal Enablement (PHP-FPM)

If your site is served by `php-fpm` (common setup), use `fastcgi_pass` instead of `proxy_pass`:

```nginx
load_module modules/ngx_http_markdown_filter_module.so;

http {
    markdown_filter off;
    markdown_on_error pass;

    server {
        listen 8080;
        root /var/www/html;
        index index.php index.html;

        location / {
            try_files $uri $uri/ /index.php?$query_string;
        }

        location ~ \.php$ {
            include fastcgi_params;
            fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
            fastcgi_pass 127.0.0.1:9000;  # or unix:/run/php/php-fpm.sock

            # Enable conversion for HTML generated by PHP
            markdown_filter on;
        }
    }
}
```

The simplest rollout strategy is:

1. Enable the module
2. Turn `markdown_filter on;` only in one known HTML location (for example `/docs/` or PHP location)
3. Verify with `curl`
4. Expand scope after validation

#### Global Enablement + Path Exceptions (Common Production Pattern)

Many existing sites want the opposite behavior: enable Markdown conversion globally, then disable it for API/static/download paths.

Reverse proxy example:

```nginx
load_module modules/ngx_http_markdown_filter_module.so;

http {
    markdown_filter on;
    markdown_on_error pass;

    server {
        listen 8080;

        location / {
            # Safer for conversion: avoid compressed upstream HTML
            proxy_set_header Accept-Encoding "";
            proxy_pass http://backend;
        }

        # Common exclusions
        location /api/ { markdown_filter off; proxy_pass http://backend; }
        location /assets/ { markdown_filter off; proxy_pass http://backend; }
        location /downloads/ { markdown_filter off; proxy_pass http://backend; }
    }
}
```

PHP-FPM example (global on, exceptions by path):

```nginx
load_module modules/ngx_http_markdown_filter_module.so;

http {
    markdown_filter on;
    markdown_on_error pass;

    server {
        listen 8080;
        root /var/www/html;
        index index.php index.html;

        # Disable selected paths before PHP routing
        location ^~ /api/ { markdown_filter off; try_files $uri $uri/ /index.php?$query_string; }
        location ^~ /assets/ { markdown_filter off; }
        location ^~ /uploads/ { markdown_filter off; }

        location / {
            try_files $uri $uri/ /index.php?$query_string;
        }

        location ~ \.php$ {
            include fastcgi_params;
            fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
            fastcgi_pass 127.0.0.1:9000;  # or unix:/run/php/php-fpm.sock

            # Inherits markdown_filter on from http/server unless overridden above
        }
    }
}
```

Notes for the global-on pattern:

- Start with `markdown_on_error pass;` (fail-open) to reduce rollout risk
- Exclude API/static/media/download routes explicitly for clarity
- Existing `.md` files usually do not need conversion (module only converts eligible `text/html` responses)
- Keep using `Accept: text/markdown` for verification; browsers with normal `Accept` headers still get HTML
- In PHP setups, verify the final request lands in the location block you expect

#### Production-Oriented Example (gzip + php-fpm + static cache + API exceptions)

This example reflects a common production PHP-FPM deployment: global enablement, explicit exclusions, static asset caching, and gzip enabled (including Markdown responses).

```nginx
load_module modules/ngx_http_markdown_filter_module.so;

http {
    include       mime.types;
    default_type  application/octet-stream;
    # If your system mime.types does not map .md to text/markdown, add that mapping in your MIME config.

    sendfile on;

    # Compress final responses (including converted Markdown)
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types
        text/plain
        text/css
        text/xml
        application/json
        application/javascript
        application/xml
        image/svg+xml
        text/markdown;

    # Global enablement + safe defaults
    markdown_filter on;
    markdown_on_error pass;
    markdown_max_size 10m;
    markdown_timeout 5s;

    server {
        listen 80;
        server_name example.com;
        root /var/www/example/public;
        index index.php index.html;

        # API: never convert
        location ^~ /api/ {
            markdown_filter off;
            try_files $uri $uri/ /index.php?$query_string;
        }

        # Native Markdown files: already suitable for agents
        location ~* \.md$ {
            markdown_filter off;
            try_files $uri =404;
        }

        # Static assets: cache aggressively, no conversion
        location ~* \.(?:css|js|mjs|map|jpg|jpeg|png|gif|svg|ico|webp|woff2?|ttf)$ {
            markdown_filter off;
            expires 30d;
            add_header Cache-Control "public, max-age=2592000, immutable";
            access_log off;
            try_files $uri =404;
        }

        # User uploads / downloads: no conversion
        location ^~ /uploads/ {
            markdown_filter off;
            expires 7d;
            add_header Cache-Control "public, max-age=604800";
            try_files $uri =404;
        }

        location ^~ /downloads/ {
            markdown_filter off;
            try_files $uri =404;
        }

        # App routes
        location / {
            try_files $uri $uri/ /index.php?$query_string;
        }

        # PHP-FPM
        location ~ \.php$ {
            include fastcgi_params;
            fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
            fastcgi_pass unix:/run/php/php-fpm.sock;  # or 127.0.0.1:9000

            # If PHP compresses responses itself, disable it in PHP/FPM config for converted routes
        }
    }
}
```

If you are using a reverse proxy upstream instead of PHP-FPM, add `proxy_set_header Accept-Encoding "";` on converted locations so the upstream returns uncompressed HTML.

Then validate and reload:

```bash
sudo /usr/local/nginx/sbin/nginx -t
sudo /usr/local/nginx/sbin/nginx -s reload
```

Full install details are in [Installation Guide](docs/guides/INSTALLATION.md).

---

## ⚡ 2-Minute Quick Verification

After deployment, use these commands to quickly verify the module is working:

### 1. Check Module Loading
```bash
# Check NGINX error log for module initialization
sudo tail -20 /usr/local/nginx/logs/error.log | grep markdown
# Expected: "markdown filter: converter initialized"
```

### 2. Verify Markdown Conversion
```bash
# Request Markdown format
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost:8080/

# Expected headers:
# Content-Type: text/markdown; charset=utf-8
# Vary: Accept
```

### 3. Confirm HTML Still Works
```bash
# Request HTML format
curl -sD - -o /dev/null -H "Accept: text/html" http://localhost:8080/

# Expected:
# Content-Type: text/html
```

### 4. Inspect Converted Content
```bash
# View actual Markdown output
curl -s -H "Accept: text/markdown" http://localhost:8080/ | head -20

# Should see Markdown format (# headings, [links](url)), not HTML tags
```

✅ If all checks pass, congratulations! The module is working correctly.

❌ If you encounter issues, see [Common Issues Quick Reference](#common-issues-quick-reference) below.

---

### Building NGINX Module

```bash
# Configure manually against an NGINX source tree
cd /path/to/nginx-source
./configure --add-dynamic-module=/path/to/nginx-markdown-for-agents/components/nginx-module
make modules
```

Notes:

- The top-level `Makefile` in this repository does not currently provide a `make nginx-module` target.
- The generated FFI header should be copied into `components/nginx-module/src/` first (`make` or `make copy-headers`).

### Development Workflow

```bash
# Build Rust release library and regenerate header
make rust-lib
make copy-headers

# Run Rust tests
cd components/rust-converter && cargo test --all

# Run selected standalone/mock NGINX module tests
make -C components/nginx-module/tests unit-eligibility
make -C components/nginx-module/tests unit-headers

# Clean build artifacts
make clean
```

## Configuration

Quick-start configuration examples are already included in [Build & Quick Start](#build--quick-start), including:

- minimal enablement for reverse proxy / HTTP upstream
- minimal enablement for PHP-FPM
- global enablement with path exceptions
- a production-oriented example (`gzip`, `php-fpm`, static cache, API exclusions)

For the full directive reference and more templates, see [Configuration Guide](docs/guides/CONFIGURATION.md).

## Quick Verification (curl)

After enabling the module and reloading NGINX, use `curl` to verify conversion is actually happening.

### 1. Check response headers switch to Markdown

```bash
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost:8080/ | grep -iE 'content-type|vary'
```

Expected:

- `Content-Type` includes `text/markdown` (often with `charset=utf-8`)
- `Vary` includes `Accept` (may also include `Accept-Encoding` when compression is enabled)

### 2. Confirm HTML clients still get HTML

```bash
curl -sD - -o /dev/null -H "Accept: text/html" http://localhost:8080/ | grep -i 'content-type'
```

Expected:

- `Content-Type: text/html` (or your upstream HTML content type)

### 3. Inspect the body (should look like Markdown, not raw HTML)

```bash
curl -s -H "Accept: text/markdown" http://localhost:8080/ | head -20
```

You should see Markdown output (for example headings like `# Title`, links like `[text](url)`), not `<html>`, `<div>`, etc.

### 4. (Recommended) Verify one included path and one excluded path

```bash
# Included route (should convert)
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost:8080/docs/ | grep -i content-type

# Excluded route (should stay original type, often JSON or HTML)
curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost:8080/api/health | grep -i content-type
```

Note:

- Prefer GET-based header checks (`curl -sD - -o /dev/null ...`) over `curl -I` for verification.
- In proxied deployments, `HEAD` requests may not carry an upstream body, which can cause fail-open behavior and make headers look unconverted even when GET conversion works.

### If it does not convert

Common causes:

- `markdown_filter on;` is not enabled in the active `http/server/location`
- Request did not include `Accept: text/markdown`
- Upstream response is not eligible (for example not `200`, not `text/html`, or exceeds size limits)

For more diagnostics, see [Installation Guide](docs/guides/INSTALLATION.md) and [Operations Guide](docs/guides/OPERATIONS.md).

## Common Deployment Notes (gzip / PHP / existing sites)

### 1. `gzip on;` in NGINX is usually fine

- This module converts HTML to Markdown first
- NGINX `gzip`/`brotli` can still compress the final Markdown response afterward
- Add `text/markdown` to `gzip_types` if your NGINX config uses an explicit list
- Continue verifying with `curl -H "Accept: text/markdown"` after enabling compression

### 2. Upstream compression is automatically handled

The module automatically detects and decompresses upstream compressed content (`gzip`, `br`, `deflate`), so conversion works even when upstream/CDN forces compression.

**No special configuration needed** - the module handles this transparently.

**Optional optimization**: You can still conditionally disable upstream compression for Markdown requests to save decompression overhead:

```nginx
map $http_accept $markdown_requested {
    default 0;
    "~*(^|,)\\s*text/markdown(\\s*;|,|$)" 1;
}

map $markdown_requested $upstream_accept_encoding {
    0 $http_accept_encoding;
    1 "";
}

location / {
    proxy_set_header Accept-Encoding $upstream_accept_encoding;
    proxy_pass http://backend;
}
```

The outer NGINX `gzip`/`brotli` module can still compress the final Markdown response to the client.

### 3. PHP (`php-fpm`) deployments: enable on the PHP location or server scope

- PHP pages are commonly served from `location ~ \.php$` via `fastcgi_pass`
- If you only enable `markdown_filter on;` in `location /`, PHP requests may end up in another location block and not be converted
- Prefer enabling in the PHP location (or `server` scope if you understand the impact)

### 4. PHP application-level compression can interfere

If PHP is compressing output itself (for example `zlib.output_compression=On`), disable that for routes you want to convert, and let NGINX handle response compression instead.

### 5. Existing `.md` files usually bypass conversion automatically

This module only converts eligible `text/html` responses. If the origin already returns Markdown (for example `Content-Type: text/markdown`) or plain text (`text/plain`), it should be bypassed automatically.

Practical guidance:

- If a route serves native `.md` files, you usually do not need special handling
- In a global-on deployment, you can still disable the module explicitly for `.md` paths to reduce overhead and make intent clear
- Make sure your server sends a correct MIME type for `.md` (`text/markdown` preferred; `text/plain` is also non-convertible)

Example explicit exclusion for native Markdown files:

```nginx
location ~* \.md$ {
    markdown_filter off;
}
```

### 6. Start narrow, then expand

A practical rollout order:

1. Enable only a docs/help/test route
2. Verify `Content-Type: text/markdown` and body format
3. Check normal browser HTML path still works
4. Expand to more locations
5. Keep API/static/media routes disabled (or rely on eligibility checks, but explicit is clearer)

### 7. Global-on + exceptions is a good steady-state configuration

For many production sites, the most convenient long-term setup is:

- `markdown_filter on;` at `http` or `server` scope
- `markdown_filter off;` in known exception paths (`/api/`, `/assets/`, `/uploads/`, `/downloads/`, optionally `*.md`)
- verify both an included route and an excluded route with `curl`

## Architecture

### Component Interaction

```
Client (Accept: text/markdown)
    ↓
NGINX Core
    ↓
Markdown Filter Module (C)
    ↓ FFI
Conversion Engine (Rust)
    ↓
Markdown Output
```

### Key Design Decisions

- **Rust for Conversion**: Memory safety, strong ecosystem
- **FFI Boundary**: C-compatible API with cbindgen
- **Full Buffering**: Simplifies v1, enables accurate Content-Length
- **BLAKE3 ETags**: Fast, consistent variant identification
- **Cooperative Timeout**: No thread spawning, NGINX-friendly

## Testing

```bash
# Root-level smoke test (current top-level Makefile target)
make test

# Rust tests
cd components/rust-converter
cargo test --all
cargo test --test ffi_test

# NGINX module standalone/mock tests (examples)
make -C components/nginx-module/tests unit-eligibility
make -C components/nginx-module/tests unit-headers
make -C components/nginx-module/tests unit-conditional_requests
make -C components/nginx-module/tests unit-passthrough
make -C components/nginx-module/tests unit-failure_strategies
```

Notes:

- Some `nginx-module` integration/E2E tests require a local `nginx` binary and extra environment setup.
- The root-level `make test` is not a full-suite runner.

### CI Workflows

- `.github/workflows/ci.yml`
  - Path-aware CI entrypoint for PR/push
  - Runs docs checks, Rust quality gate (`fmt`/`clippy`/tests), NGINX C unit + integration-c tests
  - Runs real NGINX large-response E2E regression for relevant changes
- `.github/workflows/real-nginx-ims.yml`
  - Dedicated real-NGINX delegated `If-Modified-Since` validation
  - Runs matrix jobs for latest `stable` and `mainline` NGINX channels in isolated ports/environments
  - Triggered on module/converter/IMS-script/workflow changes and manual dispatch

## Documentation

### Quick Links

- **[FAQ](docs/FAQ.md)** - Frequently asked questions and answers
- **[Contributing Guide](CONTRIBUTING.md)** - How to contribute to this project
- **[Changelog](CHANGELOG.md)** - Version history and release notes
- **[Project Status](docs/project/PROJECT_STATUS.md)** - Current implementation status and validation summary
- **[Build Instructions](docs/guides/BUILD_INSTRUCTIONS.md)** - Build, smoke-test, and troubleshooting guide
- **[Installation Guide](docs/guides/INSTALLATION.md)** - Installation and deployment instructions
- **[Configuration Guide](docs/guides/CONFIGURATION.md)** - All configuration directives and examples
- **[Operations Guide](docs/guides/OPERATIONS.md)** - Monitoring, troubleshooting, and performance tuning

### Configuration Examples

Ready-to-use NGINX configuration templates:

- [Minimal Reverse Proxy](examples/nginx-configs/01-minimal-reverse-proxy.conf)
- [Minimal PHP-FPM](examples/nginx-configs/02-minimal-php-fpm.conf)
- [Global with Exceptions](examples/nginx-configs/03-global-with-exceptions.conf)
- [Production Full](examples/nginx-configs/04-production-full.conf)
- [High Performance](examples/nginx-configs/05-high-performance.conf)

See [examples/nginx-configs/README.md](examples/nginx-configs/README.md) for usage guide.

### Complete Documentation

See [docs/](docs/) for comprehensive documentation:

- [Documentation Guide](docs/README.md) - documentation index and navigation
- [Guides Index](docs/guides/README.md) - build, installation, configuration, and operations guides
- [Project Docs Index](docs/project/README.md) - status and project-level maintenance docs
- [Duplication and Sync Policy](docs/DOCUMENTATION_DUPLICATION_POLICY.md) - canonical vs mirror documentation rules

- **[docs/features/](docs/features/)** - Feature documentation
  - [Security](docs/features/security.md) - XSS, XXE, SSRF prevention
  - [Deterministic Output](docs/features/deterministic-output.md) - Stable ETag generation
  - [Timeout Mechanism](docs/features/COOPERATIVE_TIMEOUT.md) - Cooperative timeout implementation
  - [Token Estimation](docs/features/TOKEN_ESTIMATOR.md) - Token count estimation
  - [YAML Front Matter](docs/features/YAML_FRONT_MATTER.md) - Metadata extraction
  - And more...

- **[docs/testing/](docs/testing/)** - Testing documentation
  - [Integration Tests](docs/testing/INTEGRATION_TESTS.md)
  - [End-to-End Tests](docs/testing/E2E_TESTS.md)
  - [Performance Baselines](docs/testing/PERFORMANCE_BASELINES.md)

- **`docs/archive/`** - Local archive path for process/history documents (typically excluded by `.gitignore`)

### Specification Documents

- **Requirements**: `.kiro/specs/nginx-markdown-for-agents/requirements.md`
- **Design**: `.kiro/specs/nginx-markdown-for-agents/design.md`
- **Tasks**: `.kiro/specs/nginx-markdown-for-agents/tasks.md`
- **Rust API Docs**: `cd components/rust-converter && cargo doc --no-deps`

## Developer & Project Details

### Current Status

Implementation is substantially complete, with remaining work focused on final validation, environment-specific integration checks, and production-readiness hardening.

See:

- [Project Status](docs/project/PROJECT_STATUS.md) for the current code/test/spec-tracker-aligned status summary
- [docs/](docs/) for the maintained documentation index

### Project Structure

```
.
├── components/rust-converter/          # Rust converter
│   ├── src/
│   │   ├── lib.rs          # Library entry point
│   │   ├── ffi.rs          # FFI interface for C integration
│   │   ├── parser.rs       # HTML5 parser (html5ever)
│   │   ├── converter.rs    # Markdown converter
│   │   ├── metadata.rs     # Metadata extraction
│   │   ├── token_estimator.rs  # Token count estimation
│   │   ├── etag_generator.rs   # ETag generation (BLAKE3)
│   │   └── error.rs        # Error types
│   ├── Cargo.toml          # Rust dependencies
│   └── cbindgen.toml       # C header generation config
│
├── components/nginx-module/  # NGINX C module
│   ├── config                # NGINX build configuration
│   ├── src/                  # Module implementation and headers
│   └── tests/                # Unit/integration/E2E tests
│
├── Makefile                # Coordinated build system
├── tests/
│   └── corpus/             # Shared HTML corpus for converter and E2E tests
├── tools/                  # Docs/corpus/e2e/ci helper scripts
├── docs/                   # Maintained documentation set
│   ├── guides/             # Build / install / config / operations guides
│   ├── features/           # Feature design and implementation notes
│   ├── testing/            # Test strategy and validation references
│   ├── project/            # Project status and planning docs
│   └── architecture/       # Repository and design structure docs
└── README.md               # This file
```

### Build Requirements

#### System Dependencies

- **Rust**: 1.70+ (install from [rustup.rs](https://rustup.rs/))
- **cbindgen**: C header generator (`cargo install cbindgen`)
- **NGINX**: 1.18.0+ source code (for module compilation)
- **GCC/Clang**: C compiler
- **Make**: Build automation

#### Rust Dependencies

Managed by Cargo (see `components/rust-converter/Cargo.toml`):
- `html5ever`: HTML5 parser
- `markup5ever_rcdom`: DOM tree representation
- `blake3`: Fast hashing for ETags
- `hex`: Hex encoding utilities

## Contributing

This project follows a specification-first development process:

1. Requirements define WHAT the system must do
2. Design defines HOW it will be implemented
3. Tasks break down implementation into discrete steps

If a `CONTRIBUTING.md` file is added later, it should become the source of truth for contribution workflow details.

## License

This repository uses the BSD 2-Clause "Simplified" License (`BSD-2-Clause`).

## Roadmap

### Implemented in Current Codebase

- Content negotiation and HTML-to-Markdown conversion
- HTTP semantics (ETag generation, conditional requests, cache behavior)
- Configuration system and directive validation
- Resource protection (size/time limits, buffering behavior)
- Error classification and fail-open/fail-closed strategies
- Metrics collection and metrics endpoint support
- Agent-oriented metadata features (token estimation, YAML front matter)

### Next-Stage Validation and Hardening

- Full end-to-end validation in deployment-like environments
- Performance benchmarking under representative workloads
- Security review and deployment hardening review
- Production rollout playbooks and operational validation

### Potential Future Enhancements

- External conversion service support
- Stronger isolation strategies for conversion execution
- Advanced fault tolerance and retry strategies
- Expanded observability integrations

## Common Issues Quick Reference

Encountering problems? Check these most common scenarios first:

| Issue | Likely Cause | Quick Fix |
|-------|--------------|-----------|
| Returns HTML instead of Markdown | Module not enabled or Accept header missing | [See details](docs/guides/OPERATIONS.md#issue-1-conversion-not-occurring) |
| High failure rate | Resource limits or malformed HTML | [See details](docs/guides/OPERATIONS.md#issue-2-high-failure-rate) |
| Slow response times | Conversion timeout or high system load | [See details](docs/guides/OPERATIONS.md#issue-3-slow-conversion-performance) |
| Module won't load | Missing dependencies or version mismatch | [See details](docs/guides/INSTALLATION.md#troubleshooting) |
| Cache returns wrong content | Cache key misconfiguration | [See details](docs/guides/OPERATIONS.md#issue-5-incorrect-cache-behavior) |

Complete troubleshooting guide: [OPERATIONS.md](docs/guides/OPERATIONS.md#troubleshooting)

---

## Contact

Use the repository hosting platform and issue tracker configured for your deployment of this project.
