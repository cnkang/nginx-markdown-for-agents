# NGINX Markdown Filter Module

NGINX C module that integrates the Rust HTML-to-Markdown converter into the NGINX filter chain.

## Overview

This module provides the NGINX integration layer for HTML-to-Markdown conversion:

- HTTP request/response handling
- Content negotiation (Accept header parsing)
- Response buffering and eligibility checks
- FFI calls to Rust converter
- Header manipulation (Content-Type, Vary, ETag, etc.)
- Configuration directive parsing
- Metrics collection

## Architecture

### Module Structure

```
src/
├── ngx_http_markdown_filter_module.c    # Main module and configuration
├── ngx_http_markdown_filter_module.h    # Module header
├── ngx_http_markdown_accept.c           # Accept header parsing
├── ngx_http_markdown_auth.c             # Authentication detection
├── ngx_http_markdown_buffer.c           # Response buffering
├── ngx_http_markdown_conditional.c      # Conditional request handling
├── ngx_http_markdown_decompression.c    # Automatic decompression
├── ngx_http_markdown_eligibility.c      # Eligibility checks
├── ngx_http_markdown_error.c            # Error handling
├── ngx_http_markdown_headers.c          # Header manipulation
└── markdown_converter.h                 # Rust FFI header (generated)
```

### Filter Chain Integration

The module integrates into NGINX's filter chain:

```
NGINX Core
    ↓
Header Filter (markdown_header_filter)
    ↓ (if eligible)
Body Filter (markdown_body_filter)
    ↓ (buffer complete)
Rust Converter (via FFI)
    ↓
Output Markdown
```

## Configuration Directives

See [Configuration Guide](../../docs/guides/CONFIGURATION.md) for complete directive reference.

### Core Directives

- `markdown_filter on|off` - Enable/disable conversion
- `markdown_max_size <size>` - Maximum response size
- `markdown_timeout <time>` - Conversion timeout
- `markdown_on_error pass|reject` - Failure strategy

### Feature Directives

- `markdown_flavor commonmark|gfm` - Markdown flavor
- `markdown_token_estimate on|off` - Token count header
- `markdown_front_matter on|off` - YAML front matter
- `markdown_etag on|off` - ETag generation

### Advanced Directives

- `markdown_auth_policy allow|deny` - Authentication policy
- `markdown_auth_cookies <patterns>` - Cookie patterns
- `markdown_conditional_requests <mode>` - Conditional request mode
- `markdown_metrics` - Metrics endpoint

## Building

### Prerequisites

- NGINX source code (1.18.0+)
- Rust converter library built
- C compiler (GCC or Clang)

### Build as Dynamic Module

```bash
cd /path/to/nginx-source
./configure --add-dynamic-module=/path/to/nginx-markdown-for-agents/components/nginx-module
make modules
```

### Build as Static Module

```bash
cd /path/to/nginx-source
./configure --add-module=/path/to/nginx-markdown-for-agents/components/nginx-module
make
```

### Installation

```bash
# For dynamic module
sudo cp objs/ngx_http_markdown_filter_module.so /usr/local/nginx/modules/

# For static module
sudo make install
```

## Testing

### Unit Tests

Standalone tests that don't require a running NGINX:

```bash
# Run all unit tests
make -C tests unit

# Run specific test
make -C tests unit-eligibility
make -C tests unit-headers
make -C tests unit-conditional_requests
```

### Integration Tests

Tests that require NGINX runtime:

```bash
# C-based integration tests
make -C tests integration-c

# NGINX runtime integration tests
make -C tests integration-nginx
```

### End-to-End Tests

Full system tests with real HTTP requests:

```bash
make -C tests e2e
```

## Development

### Adding a New Directive

1. **Define directive in module structure:**
   ```c
   static ngx_command_t ngx_http_markdown_commands[] = {
       {
           ngx_string("markdown_new_directive"),
           NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
           ngx_conf_set_flag_slot,
           NGX_HTTP_LOC_CONF_OFFSET,
           offsetof(ngx_http_markdown_loc_conf_t, new_directive),
           NULL
       },
       // ...
   };
   ```

2. **Add field to configuration structure:**
   ```c
   typedef struct {
       // ... existing fields
       ngx_flag_t new_directive;
   } ngx_http_markdown_loc_conf_t;
   ```

3. **Initialize in create_loc_conf:**
   ```c
   conf->new_directive = NGX_CONF_UNSET;
   ```

4. **Merge in merge_loc_conf:**
   ```c
   ngx_conf_merge_value(conf->new_directive, prev->new_directive, 0);
   ```

5. **Use in filter functions:**
   ```c
   if (conf->new_directive) {
       // Implementation
   }
   ```

6. **Add tests:**
   - Unit test in `tests/unit/`
   - Integration test if needed

7. **Update documentation:**
   - Add to `docs/guides/CONFIGURATION.md`
   - Update examples if relevant

### Code Style

Follow NGINX coding conventions:

- Use NGINX types (`ngx_int_t`, `ngx_str_t`, etc.)
- Use NGINX memory management (`ngx_palloc`, `ngx_pcalloc`)
- Use NGINX string functions (`ngx_strcmp`, `ngx_strncmp`)
- Handle errors properly (return `NGX_ERROR`, `NGX_DECLINED`)
- Add comprehensive comments
- Use 4-space indentation

### Memory Management

- Always use pool-based allocation (`ngx_palloc`, `ngx_pcalloc`)
- Never use `malloc`/`free` directly
- Memory is automatically freed when request completes
- For persistent data, use `ngx_http_markdown_create_main_conf` pool

### Error Handling

```c
// Check for errors
if (result == NULL) {
    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                  "markdown filter: operation failed");
    return NGX_ERROR;
}

// Decline processing (not an error)
if (!eligible) {
    return NGX_DECLINED;
}

// Success
return NGX_OK;
```

## Debugging

### Enable Debug Logging

```nginx
error_log /var/log/nginx/error.log debug;
```

### Common Debug Points

- Configuration parsing: Check `create_loc_conf` and `merge_loc_conf`
- Eligibility: Check `ngx_http_markdown_eligibility.c`
- Accept parsing: Check `ngx_http_markdown_accept.c`
- Buffering: Check `ngx_http_markdown_buffer.c`
- Conversion: Check FFI call in main filter

### Using GDB

```bash
# Start NGINX with single worker
nginx -g "daemon off; master_process off;"

# Attach GDB
gdb -p $(pgrep nginx)

# Set breakpoints
break ngx_http_markdown_header_filter
break ngx_http_markdown_body_filter

# Continue execution
continue
```

## Performance Considerations

### Optimization Tips

1. **Minimize buffering overhead**: Use appropriate `markdown_max_size`
2. **Fast-path checks**: Eligibility checks happen before buffering
3. **Avoid unnecessary work**: Skip conversion for non-eligible responses
4. **Use efficient data structures**: Leverage NGINX's pool allocation
5. **Profile hot paths**: Use profiling tools to identify bottlenecks

### Memory Usage

- Per-request overhead: ~5-10MB (depends on response size)
- Configuration overhead: Minimal (~1KB per location)
- Metrics overhead: ~1KB per worker

## FFI Interface

### Calling Rust Functions

```c
#include "markdown_converter.h"

// Create converter
MarkdownConverter *converter = markdown_converter_new();
if (converter == NULL) {
    // Handle error
}

// Convert HTML
const char *html = "<h1>Title</h1>";
size_t html_len = strlen(html);
char *markdown = NULL;
size_t markdown_len = 0;

int result = markdown_convert(converter, html, html_len, &markdown, &markdown_len);
if (result == 0) {
    // Use markdown
    // ...
    
    // Free markdown
    markdown_free_string(markdown);
}

// Destroy converter
markdown_converter_free(converter);
```

### Error Handling

```c
if (result != 0) {
    const char *error = markdown_get_last_error(converter);
    ngx_log_error(NGX_LOG_ERR, log, 0,
                  "markdown filter: conversion failed: %s", error);
}
```

## Contributing

See [CONTRIBUTING.md](../../CONTRIBUTING.md) for general contribution guidelines.

### Module-Specific Guidelines

- Follow NGINX coding style
- Add unit tests for new functionality
- Update configuration documentation
- Test with multiple NGINX versions
- Ensure thread safety (NGINX is multi-process, not multi-threaded)

## Resources

- [NGINX Module Development Guide](https://nginx.org/en/docs/dev/development_guide.html)
- [NGINX Source Code](https://github.com/nginx/nginx)
- [Configuration Guide](../../docs/guides/CONFIGURATION.md)
- [Operations Guide](../../docs/guides/OPERATIONS.md)

## License

Licensed under the BSD 2-Clause "Simplified" License (`BSD-2-Clause`).
