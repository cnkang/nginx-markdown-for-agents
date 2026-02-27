# Frequently Asked Questions (FAQ)

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

- **NGINX**: 1.18.0 or higher
- **Rust**: 1.70.0 or higher (for building)
- **Operating System**: macOS or Linux (x86_64 or aarch64)
- **Memory**: Minimum 512MB RAM per worker (more for large documents)

### Do I need to rebuild NGINX?

Yes, you need to compile the module against your NGINX source code. The module can be built as either:
- **Dynamic module** (recommended): Load with `load_module` directive
- **Static module**: Compiled into NGINX binary

See [Installation Guide](guides/INSTALLATION.md) for details.

### Can I use pre-compiled binaries?

Currently, pre-compiled binaries are not provided due to NGINX version compatibility requirements. You must build the module against your specific NGINX version.

### Does it work with Docker?

Yes! You can use this module in Docker containers. See the `examples/docker/` directory for Dockerfile examples (coming soon).

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

### How do I exclude specific paths from conversion?

Use `markdown_filter off;` in specific locations:

```nginx
location /api/ {
    markdown_filter off;
}
```

### What happens if conversion fails?

By default, the module uses a "fail-open" strategy (`markdown_on_error pass;`), returning the original HTML. You can change this to "fail-closed" (`markdown_on_error reject;`) to return a 502 error instead.

### How do I adjust resource limits?

Use these directives:

```nginx
markdown_max_size 10m;    # Maximum response size
markdown_timeout 5s;      # Maximum conversion time
```

---

## Performance

### What is the performance impact?

Typical overhead:
- **Latency**: 20-50ms for average pages
- **Memory**: 5-10MB per conversion
- **CPU**: Minimal (< 5% increase)

Performance depends on:
- Document size and complexity
- System resources
- Configuration settings

### How can I improve performance?

1. **Enable caching**: Cache converted responses
2. **Reduce limits**: Lower `markdown_max_size` and `markdown_timeout`
3. **Disable optional features**: Turn off token estimation and front matter if not needed
4. **Use CommonMark**: Faster than GFM flavor

See [Performance Tuning](guides/OPERATIONS.md#performance-tuning) for details.

### Does it support streaming?

No, the current version requires full buffering. Streaming support may be added in future versions.

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

### Does it work with CDNs?

Yes, but you may need to:
1. Ensure the CDN passes through the `Accept` header
2. Configure the CDN to cache different variants based on `Accept`
3. The module automatically handles upstream compression from CDNs

### What about compressed responses?

The module automatically detects and decompresses upstream compressed content (gzip, brotli, deflate). No special configuration needed.

---

## Troubleshooting

### Why am I getting HTML instead of Markdown?

Common causes:
1. **Module not enabled**: Check `markdown_filter on;` is set
2. **Missing Accept header**: Ensure client sends `Accept: text/markdown`
3. **Response not eligible**: Must be 200 status with `text/html` content type
4. **Size limit exceeded**: Response larger than `markdown_max_size`

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

### Should I convert authenticated content?

You can, but consider:
- Use `markdown_auth_policy deny;` to skip authenticated requests
- Or use `markdown_auth_policy allow;` with proper cookie detection
- The module automatically adds `Cache-Control: private` for authenticated responses

### How do I secure the metrics endpoint?

Always restrict metrics to localhost:

```nginx
location /markdown-metrics {
    markdown_metrics;
    allow 127.0.0.1;
    allow ::1;
    deny all;
}
```

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
- [Build Instructions](guides/BUILD_INSTRUCTIONS.md) - Building the module
- [Installation Guide](guides/INSTALLATION.md) - Installation steps
- [Configuration Guide](guides/CONFIGURATION.md) - Configuration reference
- [Operations Guide](guides/OPERATIONS.md) - Monitoring and troubleshooting

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

### What features are planned?

See the project roadmap in [README](../README.md#roadmap) for planned features.

### When will feature X be available?

Check the issue tracker for feature requests and their status.

### Can I sponsor development?

Check the repository for sponsorship information (if available).

---

*Last updated: February 27, 2026*
