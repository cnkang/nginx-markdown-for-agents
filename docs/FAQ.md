# Frequently Asked Questions (FAQ)

This document is for quick answers and orientation.

For canonical directive syntax and defaults, use [Configuration Guide](guides/CONFIGURATION.md). For production diagnostics and monitoring, use [Operations Guide](guides/OPERATIONS.md). For runtime design details, use [Architecture Documentation](architecture/README.md).

## General Questions

### What is NGINX Markdown for Agents?

NGINX Markdown for Agents is an NGINX filter module that converts HTML responses to Markdown format when clients request `Accept: text/markdown`. This makes web content more accessible to AI agents and LLMs by reducing token usage and improving content clarity.

### Why would I use this?

- **Reduce token usage**: Markdown is more concise than HTML, reducing LLM API costs
- **Improve content quality**: Remove boilerplate markup and focus on actual content
- **Standard content negotiation**: Uses HTTP Accept headers, no custom scraping needed
- **Preserve existing infrastructure**: Works with existing HTML backends unchanged

### How does it compare to Cloudflare's Markdown for Agents?

This project is inspired by Cloudflare's announcement but provides a self-hostable solution:
- **Self-hosted**: Run on your own infrastructure
- **Open source**: Full control and customization
- **NGINX integration**: Works with existing NGINX deployments
- **Flexible configuration**: Fine-grained control over conversion behavior

---

## Installation and Setup

### What are the system requirements?

- **NGINX**: 1.24.0 or higher
- **Rust**: 1.85.0 or higher (for building)
- **Operating System**: macOS or Linux (x86_64 or aarch64)
- **Memory**: Minimum 512MB RAM per worker (more for large documents)

If your distro ships an older NGINX (for example 1.22.x or earlier), upgrade NGINX first or maintain a custom build for that version.

### Do I need to rebuild NGINX?

Not always.

- If you use one of the supported official NGINX builds and a matching release artifact exists, you can install the published dynamic module without rebuilding NGINX.
- If you use a custom NGINX build, or there is no exact version match for your runtime, compile the module against your NGINX source tree.

The module can be integrated as either:
- **Dynamic module** (recommended): Load with `load_module`
- **Static module**: Compile it into the NGINX binary

See [Installation Guide](guides/INSTALLATION.md) for details.

### Can I use pre-compiled binaries?

Yes, for selected Linux targets, pre-compiled dynamic module binaries are published and can be installed with `tools/install.sh`.

However, NGINX dynamic modules are version-specific:
- The binary must match your `nginx -v` version exactly
- Release assets may be grouped by major.minor for readability, but you must still use the exact patch version
- If your version is not published yet, build from source or switch to one of the available pre-built versions

### Does it work with Docker?

Yes. The usual pattern is to add the published dynamic module or a source build step to your image, then load it from `nginx.conf`.

This repository now ships an official-image source-build template:
- `examples/docker/Dockerfile.official-nginx-source-build`

It is designed for the common official tags:
- `nginx:mainline`
- `nginx:mainline-alpine`
- `nginx:stable`
- `nginx:stable-alpine`

The Dockerfile clones this repository in the build stage, compiles the module there, and then copies the built module into a clean official `nginx` runtime image.

See [Installation Guide](guides/INSTALLATION.md) for build and verification commands.

---

## Configuration

### How do I enable conversion for my entire site?

Set `markdown_filter on;` at the `http` or `server` level:

```nginx
http {
    markdown_filter on;
    # ... other settings
}
```

For rollout patterns and exceptions by path, see [Deployment Examples](guides/DEPLOYMENT_EXAMPLES.md).

### How do I exclude specific paths from conversion?

Use `markdown_filter off;` in specific locations:

```nginx
location /api/ {
    markdown_filter off;
}
```

### What happens if conversion fails?

By default, the module uses a fail-open strategy (`markdown_on_error pass;`), so the original eligible HTML response is returned. If you want strict behavior instead, `markdown_on_error reject;` makes failures fail-closed.

For the canonical directive behavior, see [Configuration Guide](guides/CONFIGURATION.md). For the exact runtime branches, see [Request Lifecycle](architecture/REQUEST_LIFECYCLE.md).

### How do I adjust resource limits?

Use these directives:

```nginx
markdown_max_size 10m;    # Maximum response size
markdown_timeout 5s;      # Maximum conversion time
```

For defaults, examples, and tradeoffs, see [Configuration Guide](guides/CONFIGURATION.md) and [Configuration to Behavior Map](architecture/CONFIG_BEHAVIOR_MAP.md).

---

## Performance

### What is the performance impact?

There is no single fixed overhead number that applies to every deployment. Performance depends on:

- document size and complexity
- whether upstream responses need decompression
- enabled features such as token estimation or YAML front matter
- system resources, cache behavior, and concurrency

The current design buffers the full eligible response before conversion, so large HTML bodies are the main driver of latency and memory use. Use your own workload to establish baselines, then tune limits and rollout scope accordingly.

### How can I improve performance?

1. **Enable caching**: Cache converted responses
2. **Reduce limits**: Lower `markdown_max_size` and `markdown_timeout`
3. **Disable optional features**: Turn off token estimation and front matter if not needed
4. **Use CommonMark**: Faster than GFM flavor

See [Performance Tuning](guides/OPERATIONS.md#performance-tuning) for details.

### Does it support streaming?

The current design requires full buffering for eligible responses before conversion. Chunked transfer responses can still be buffered and converted when `markdown_buffer_chunked on;` is enabled, but the module does not yet provide true streaming Markdown generation.

See [Request Lifecycle](architecture/REQUEST_LIFECYCLE.md) and [ADR-0002](architecture/ADR/0002-full-buffering-approach.md) for the reasoning behind this design.

---

## Compatibility

### Does it work with PHP applications?

Yes! It works great with PHP-FPM. Enable conversion in the PHP location block:

```nginx
location ~ \.php$ {
    markdown_filter on;
    fastcgi_pass 127.0.0.1:9000;
    # ... other fastcgi settings
}
```

See [example configuration](../examples/nginx-configs/02-minimal-php-fpm.conf).

### Does it work with WordPress?

Yes, WordPress is a PHP application, so the PHP-FPM configuration applies.

### Does it work with reverse proxies?

Yes, it works with any HTTP backend. Just enable conversion in the proxy location:

```nginx
location / {
    markdown_filter on;
    proxy_pass http://backend;
}
```

If you want the simplest first rollout, you can temporarily disable upstream compression with `proxy_set_header Accept-Encoding "";`. Once the path is verified, the module can also handle upstream `gzip`, `br`, and `deflate` responses directly.

### Does it work with CDNs?

Yes, but you may need to:
1. Ensure the CDN passes through the `Accept` header
2. Configure the CDN to cache different variants based on `Accept`
3. The module automatically handles upstream compression from CDNs

### What about compressed responses?

The module automatically detects and decompresses supported upstream compressed content (`gzip`, `br`, `deflate`) as part of the conversion path.

For operational guidance, see [Operations Guide](guides/OPERATIONS.md). For implementation details, see [Automatic Decompression](features/AUTOMATIC_DECOMPRESSION.md).

---

## Troubleshooting

### Why am I getting HTML instead of Markdown?

Common causes:
1. **Module not enabled**: Check `markdown_filter on;` is set
2. **Missing Accept header**: Ensure client sends `Accept: text/markdown`
3. **Variable map mismatch** (when using `markdown_filter $var`):
   - Exact `map $http_accept` strings often miss real-world multi-value `Accept` headers
   - Use regex map rules for `Accept` matching
   - Prefer `$uri` over `$request_uri` for extension checks (query strings can break matches)
   - If map includes `text/*`, enable `markdown_on_wildcard on;`
4. **Response not eligible**: Must be 200 status with `text/html` content type
5. **Size limit exceeded**: Response larger than `markdown_max_size`

If you need to trace the decision path rather than just the checklist, use [Request Lifecycle](architecture/REQUEST_LIFECYCLE.md) and [Configuration to Behavior Map](architecture/CONFIG_BEHAVIOR_MAP.md).

### Why is conversion failing?

Check the error log for specific error messages:

```bash
tail -f /var/log/nginx/error.log | grep markdown
```

Common issues:
- **Malformed HTML**: Invalid HTML structure
- **Timeout**: Conversion taking too long
- **Memory**: Insufficient memory for large documents

### How do I debug conversion issues?

1. **Enable debug logging**:
   ```nginx
   error_log /var/log/nginx/error.log debug;
   ```

2. **Check metrics**:
   ```bash
   curl http://localhost/markdown-metrics
   ```

3. **Test with curl**:
   ```bash
   curl -v -H "Accept: text/markdown" http://localhost/test
   ```

### Why is caching not working correctly?

Ensure your cache key includes the `Accept` header:

```nginx
proxy_cache_key "$scheme$request_method$host$request_uri$http_accept";
```

Also verify the `Vary: Accept` header is present in responses.

---

## Security

### Is it safe to use in production?

Yes, the module includes multiple security protections:
- Memory-safe Rust implementation
- XSS prevention through sanitization
- XXE attack prevention
- SSRF protection for URLs
- Resource limits to prevent DoS

### Does it expose sensitive information?

No, the module only converts HTML to Markdown. It doesn't add or expose information not already in the HTML response.

### Should I convert authenticated requests?

You can, but consider:
- Use `markdown_auth_policy deny;` to skip authenticated requests
- Or use `markdown_auth_policy allow;` with proper cookie detection
- The module automatically adds `Cache-Control: private` for Markdown responses generated from authenticated requests

See [Configuration Guide](guides/CONFIGURATION.md) for the canonical directive semantics.

### How do I secure the metrics endpoint?

The handler already enforces localhost-only access. In practice, you should still keep explicit NGINX `allow`/`deny` rules on the metrics location:

```nginx
location /markdown-metrics {
    markdown_metrics;
    allow 127.0.0.1;
    allow ::1;
    deny all;
}
```

For response formats, metric fields, and monitoring usage, see [Configuration Guide](guides/CONFIGURATION.md) and [Operations Guide](guides/OPERATIONS.md).

---

## Features

### What Markdown flavor is supported?

Two flavors are supported:
- **CommonMark** (default): Standard Markdown specification
- **GFM** (GitHub Flavored Markdown): Includes tables, strikethrough, task lists

### Does it support tables?

Yes, when using GFM flavor:

```nginx
markdown_flavor gfm;
```

### Can it extract metadata?

Yes, enable YAML front matter:

```nginx
markdown_front_matter on;
```

This adds metadata (title, description, URL) at the beginning of the Markdown output.

### Does it estimate token counts?

Yes, enable token estimation:

```nginx
markdown_token_estimate on;
```

This adds an `X-Markdown-Tokens` header with the estimated token count.

### What about images and links?

- **Images**: Converted to Markdown image syntax `![alt](url)`
- **Links**: Converted to Markdown link syntax `[text](url)`
- **Relative URLs**: Preserved as-is
- **Absolute URLs**: Preserved as-is

---

## Development

### How do I contribute?

See [CONTRIBUTING.md](../CONTRIBUTING.md) for contribution guidelines.

### How do I report bugs?

Use the repository's issue tracker. Include:
- NGINX version
- Module version
- Configuration
- Error logs
- Steps to reproduce

### How do I request features?

Open a feature request in the issue tracker with:
- Use case description
- Expected behavior
- Why it's valuable

### Where is the source code?

The project is organized as:
- `components/rust-converter/` - Rust conversion library
- `components/nginx-module/` - NGINX C module
- `docs/` - Documentation
- `tests/` - Test suites

---

## Comparison with Alternatives

### vs. Custom HTML scraping

**Advantages:**
- Standard HTTP content negotiation
- No custom scraping logic needed
- Consistent output format
- Maintained and tested

**Disadvantages:**
- Requires NGINX module installation
- Some setup complexity

### vs. Client-side conversion

**Advantages:**
- Centralized conversion logic
- Consistent across all clients
- Reduced client complexity
- Better caching

**Disadvantages:**
- Server-side resource usage
- Requires module installation

### vs. Application-level conversion

**Advantages:**
- Works with existing applications
- No application changes needed
- Centralized at edge/proxy layer

**Disadvantages:**
- Additional NGINX module
- Some performance overhead

---

## Getting Help

### Where can I find more documentation?

- [README](../README.md) - Project overview and quick start
- [Deployment Examples](guides/DEPLOYMENT_EXAMPLES.md) - NGINX config examples and verification
- [Build Instructions](guides/BUILD_INSTRUCTIONS.md) - Building the module
- [Installation Guide](guides/INSTALLATION.md) - Installation steps
- [Configuration Guide](guides/CONFIGURATION.md) - Configuration reference
- [Operations Guide](guides/OPERATIONS.md) - Monitoring and troubleshooting
- [Architecture Documentation](architecture/README.md) - Structure, lifecycle, and design rationale

### How do I get support?

1. Check this FAQ
2. Review the documentation
3. Search closed issues
4. Open a new issue if needed

### Is there a community?

Check the repository for:
- Issue tracker
- Discussions (if enabled)
- Contributing guidelines

---

## License and Legal

### What is the license?

Licensed under the BSD 2-Clause "Simplified" License (`BSD-2-Clause`).

### Can I use this commercially?

Yes. BSD-2-Clause allows commercial use.

### Do I need to attribute?

Yes. Keep the copyright notice and license text in source and binary redistributions.

---

## Roadmap

### Where should I look for future direction?

Use the roadmap section in [README](../README.md#roadmap) for broad project direction, and check the issue tracker for current feature requests and discussion.

### Can I sponsor development?

Check the repository for sponsorship information (if available).

---

*Last updated: March 11, 2026*
