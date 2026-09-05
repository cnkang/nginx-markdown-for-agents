# Configuration Directive Validation Tests

This document provides validation test cases for all configuration directives exposed by the NGINX Markdown filter module.

## Test Configuration Examples

### 1. markdown_filter (on|off|$variable)

**Valid configurations:**
```nginx
markdown_filter on;
markdown_filter off;
markdown_filter $convert_html;
```

**Invalid configurations:**
```nginx
markdown_filter yes;        # Error: invalid value, must be "on", "off", or a variable
markdown_filter 1;          # Error: invalid value, must be "on", "off", or a variable
markdown_filter;            # Error: missing value
```

**Expected behavior:**
- Default: off
- Context: http, server, location
- Inheritance: child overrides parent
- The module evaluates variables/complex values per request. Resolved values support 1/0, on/off, true/false, yes/no

---

### 2. markdown_limits conversion_memory=<size> (size)

> **0.9.0**: the release retired `markdown_max_size`. Use `markdown_limits conversion_memory=`.

**Valid configurations:**
```nginx
markdown_limits conversion_memory=64m;       # 64 megabytes (frozen default)
markdown_limits conversion_memory=65536k;    # 65536 kilobytes
markdown_limits conversion_memory=67108864;  # 67108864 bytes
```

**Invalid configurations:**
```nginx
markdown_limits conversion_memory=-1;       # Error: negative size
markdown_limits conversion_memory=0;        # Error: size must be positive
markdown_limits conversion_memory=abc;      # Error: invalid size format
markdown_limits conversion_memory;          # Error: missing value
```

**Expected behavior:**
- Default: 64m (64 megabytes)
- Context: http, server, location
- Validation: must be positive integer with optional suffix (k, m, g)

---

### 3. markdown_limits conversion_timeout=<time> (time)

> **0.9.0**: the release retired `markdown_timeout`. Use `markdown_limits conversion_timeout=`.

**Valid configurations:**
```nginx
markdown_limits conversion_timeout=30s;       # 30 seconds (frozen default)
markdown_limits conversion_timeout=30000ms;   # 30000 milliseconds
markdown_limits conversion_timeout=30000;     # 30000 milliseconds (default unit)
```

**Invalid configurations:**
```nginx
markdown_limits conversion_timeout=-1;        # Error: negative timeout
markdown_limits conversion_timeout=0;         # Error: timeout must be positive
markdown_limits conversion_timeout=abc;       # Error: invalid time format
markdown_limits conversion_timeout;           # Error: missing value
```

**Expected behavior:**
- Default: 30s (30000 milliseconds)
- Context: http, server, location
- Validation: must be positive integer with optional suffix (ms, s, m, h)

---

### 4. markdown_error_policy (pass|fail_closed|status <code>)

**Valid configurations:**
```nginx
markdown_error_policy pass;         # Fail-open: return original HTML
markdown_error_policy fail_closed;   # Fail-closed: return 502 error
markdown_error_policy status 503;    # Return custom error status
```

**Invalid configurations:**
```nginx
markdown_error_policy fail;             # Error: invalid value, must be "pass", "fail_closed", or "status <code>"
markdown_error_policy open;             # Error: invalid value, must be "pass", "fail_closed", or "status <code>"
markdown_error_policy;                   # Error: missing value
markdown_error_policy pass fail_closed; # Error: too many arguments
```

**Expected behavior:**
- Default: pass (fail-open)
- Context: http, server, location
- Error message: "invalid value \"%s\" in \"markdown_error_policy\" directive, it must be \"pass\", \"fail_closed\", or \"status <code>\""

---

### 5. markdown_flavor (commonmark|gfm)

**Valid configurations:**
```nginx
markdown_flavor commonmark; # CommonMark specification
markdown_flavor gfm;        # GitHub Flavored Markdown
```

**Invalid configurations:**
```nginx
markdown_flavor markdown;   # Error: invalid value, must be "commonmark" or "gfm"
markdown_flavor github;     # Error: invalid value, must be "commonmark" or "gfm"
markdown_flavor;            # Error: missing value
markdown_flavor commonmark gfm; # Error: too many arguments
```

**Expected behavior:**
- Default: commonmark
- Context: http, server, location
- Error message: "invalid value \"%s\" in \"markdown_flavor\" directive, it must be \"commonmark\" or \"gfm\""

---

### 6. markdown_token_estimate (on|off)

**Valid configurations:**
```nginx
markdown_token_estimate on;
markdown_token_estimate off;
```

**Invalid configurations:**
```nginx
markdown_token_estimate yes;    # Error: invalid value, must be "on" or "off"
markdown_token_estimate 1;      # Error: invalid value, must be "on" or "off"
markdown_token_estimate;        # Error: missing value
```

**Expected behavior:**
- Default: off
- Context: http, server, location
- When enabled: adds X-Markdown-Tokens header to response

---

### 7. markdown_front_matter (on|off)

**Valid configurations:**
```nginx
markdown_front_matter on;
markdown_front_matter off;
```

**Invalid configurations:**
```nginx
markdown_front_matter yes;  # Error: invalid value, must be "on" or "off"
markdown_front_matter 1;    # Error: invalid value, must be "on" or "off"
markdown_front_matter;      # Error: missing value
```

**Expected behavior:**
- Default: off
- Context: http, server, location
- When enabled: includes YAML front matter with metadata

---

### 8. markdown_accept (strict|wildcard|force)

**Valid configurations:**
```nginx
markdown_accept wildcard;
markdown_accept strict;
markdown_accept force;
```

**Invalid configurations:**
```nginx
markdown_accept yes;       # Error: invalid value, must be "strict", "wildcard", or "force"
markdown_accept 1;         # Error: invalid value, must be "strict", "wildcard", or "force"
markdown_accept;           # Error: missing value
```

**Expected behavior:**
- Default: strict
- Context: http, server, location
- When enabled: converts on Accept: */* or Accept: text/*

---

### 9. markdown_auth_policy (allow|deny)

**Valid configurations:**
```nginx
markdown_auth_policy allow; # Convert authenticated requests
markdown_auth_policy deny;  # Skip authenticated requests
```

**Invalid configurations:**
```nginx
markdown_auth_policy yes;   # Error: invalid value, must be "allow" or "deny"
markdown_auth_policy block; # Error: invalid value, must be "allow" or "deny"
markdown_auth_policy;       # Error: missing value
markdown_auth_policy allow deny; # Error: too many arguments
```

**Expected behavior:**
- Default: allow
- Context: http, server, location
- Error message: "invalid value \"%s\" in \"markdown_auth_policy\" directive, it must be \"allow\" or \"deny\""

---

### 10. markdown_auth_cookies (pattern [pattern ...])

**Valid configurations:**
```nginx
markdown_auth_cookies session*;
markdown_auth_cookies session* auth_token;
markdown_auth_cookies PHPSESSID wordpress_logged_in_*;
markdown_auth_cookies session* auth* JSESSIONID;
```

**Invalid configurations:**
```nginx
markdown_auth_cookies;      # Error: missing value (requires at least one pattern)
markdown_auth_cookies "";   # Error: empty cookie pattern
```

**Expected behavior:**
- Default: NULL (no patterns, only Authorization header detection)
- Context: http, server, location
- Accepts multiple patterns (space-separated)
- Patterns support exact match and prefix match with *
- Error message: "empty cookie pattern in \"markdown_auth_cookies\" directive"

---

### 11. markdown_cache_validation (off|ims_only|full)

> **0.9.0**: the release retired `markdown_etag` and `markdown_conditional_requests`. Use `markdown_cache_validation`.

**Valid configurations:**
```nginx
markdown_cache_validation off;
markdown_cache_validation ims_only;
markdown_cache_validation full;
```

**Invalid configurations:**
```nginx
markdown_cache_validation on;          # Error: invalid value
markdown_cache_validation enabled;     # Error: invalid value
markdown_cache_validation;             # Error: missing value
markdown_cache_validation full off;    # Error: too many arguments
```

**Expected behavior:**
- Default: ims_only
- Context: http, server, location
- `full`: generates transformed ETag + If-None-Match + If-Modified-Since
- `ims_only`: If-Modified-Since via upstream Last-Modified (no ETag)
- `off`: no conditional request support

---

### 13. markdown_buffer_chunked (on|off, removed in 0.9.2)

**Removed:** The 0.9.2 release removed this directive. It rejects
configurations using it with the standard unknown-directive error at
`nginx -t` time. See
[MIGRATION-0.9.2.md](../guides/MIGRATION-0.9.2.md). The sections below
document the pre-removal behavior only.

**Valid configurations:**
```nginx
markdown_buffer_chunked on;
markdown_buffer_chunked off;
```

**Invalid configurations:**
```nginx
markdown_buffer_chunked yes;    # Error: invalid value, must be "on" or "off"
markdown_buffer_chunked 1;      # Error: invalid value, must be "on" or "off"
markdown_buffer_chunked;        # Error: missing value
```

**Expected behavior:**
- Default: on
- Context: http, server, location
- When on: buffers chunked responses for conversion
- When off: passes through chunked responses without conversion

---

### 14. markdown_stream_types (removed in 0.9.2)

**Removed:** The 0.9.2 release removed this directive. It rejects configurations
using it with the standard unknown-directive error at `nginx -t` time. See
[MIGRATION-0.9.2.md](../guides/MIGRATION-0.9.2.md) for the replacement
surface. Historical validation examples stay below only as an
archived record of the pre-0.9.2 surface.

**Historical (pre-0.9.2) examples:**
```nginx
markdown_stream_types text/event-stream;
markdown_stream_types text/event-stream application/x-ndjson;
markdown_stream_types text/event-stream application/stream+json;
```

**Historical invalid examples:**
```nginx
markdown_stream_types;          # Error: missing value (requires at least one type)
markdown_stream_types "";       # Error: empty content type
markdown_stream_types plaintext; # Error: invalid format, must be "type/subtype"
markdown_stream_types text;     # Error: invalid format, must be "type/subtype"
```

**Historical expected behavior:**
- Default: NULL (no exclusions)
- Context: http, server, location
- Accepts multiple content types (space-separated)
- Validation: each type must contain a slash (type/subtype format)
- Error message: "invalid content type \"%s\" in \"markdown_stream_types\" directive, must be in format \"type/subtype\""

---

## Configuration Inheritance Tests

### Test 1: Simple inheritance
```nginx
http {
    markdown_filter on;
    markdown_limits conversion_memory=5m;

    server {
        # Inherits: markdown_filter on, markdown_limits conversion_memory=5m

        location /api {
            markdown_filter off;  # Overrides parent
            # Inherits: markdown_limits conversion_memory=5m
        }
    }
}
```

**Expected:**
- `/api`: filter off, limits conversion_memory=5m
- Other locations: filter on, limits conversion_memory=5m

### Test 2: Multi-level inheritance
```nginx
http {
    markdown_filter on;
    markdown_limits conversion_timeout=10s;
    markdown_error_policy pass;

    server {
        markdown_limits conversion_timeout=5s;  # Overrides http level
        # Inherits: markdown_filter on, markdown_error_policy pass

        location /docs {
            markdown_error_policy fail_closed;  # Overrides server level
            # Inherits: markdown_filter on, markdown_limits conversion_timeout=5s
        }
    }
}
```

**Expected:**
- `/docs`: filter on, conversion_timeout 5s, error_policy fail_closed
- Other locations: filter on, conversion_timeout 5s, error_policy pass

### Test 3: Array directive inheritance
```nginx
http {
    markdown_auth_cookies session* auth*;

    server {
        # Inherits: markdown_auth_cookies session* auth*

        location /admin {
            markdown_auth_cookies admin_session*;  # Overrides parent completely
        }
    }
}
```

**Expected:**
- `/admin`: auth_cookies = ["admin_session*"]
- Other locations: auth_cookies = ["session*", "auth*"]

---

## Validation Error Messages

All directive handlers provide clear error messages:

1. **Duplicate directive:**
   - Message: "is duplicate"
   - Occurs when the directive appears multiple times in the same context

2. **Invalid value:**
   - Message: "invalid value \"%s\" in \"%s\" directive, it must be ..."
   - Occurs when value does not match expected format

3. **Empty value:**
   - Message: "empty [pattern/type] in \"%s\" directive"
   - Occurs for array directives with empty elements

4. **Invalid format:**
   - Message: "invalid [type] \"%s\" in \"%s\" directive, must be in format ..."
   - Occurs when value format is incorrect (for example content type without slash)

---

## Configuration Validation at Startup

NGINX validates static directive syntax and fixed configuration values at
startup. Runtime variables cannot be fully validated until a request resolves
them:

1. **Flag directives** (on|off): Validated by ngx_conf_set_flag_slot
2. **Size directives**: Validated by ngx_conf_set_size_slot (must be positive)
3. **Time directives**: Validated by ngx_conf_set_msec_slot (must be positive)
4. **Enum directives**: Validated by custom handlers with explicit value checks
5. **Array directives**: Validated by custom handlers with format checks

For example, configuration loading checks `markdown_filter $convert_html` as a
valid complex value. NGINX resolves `$convert_html` and evaluates its resulting
value for each request. Startup validation does not
claim that every runtime value is valid.

If validation fails, NGINX will refuse to start and log the specific error.

---

## Standalone Parsing Harnesses

For fast local validation without a full NGINX source build, the project keeps
standalone C harnesses under `components/nginx-module/tests/unit/`:

- `config_parsing_test.c` - directive value and constant validation
- `config_merge_test.c` - inheritance and merge behavior validation

Representative runs:

```bash
make -C components/nginx-module/tests unit-config_parsing
```

```bash
make -C components/nginx-module/tests unit-config_merge
```

Use these harnesses for quick feedback on parser logic, then verify final config
acceptance with real NGINX startup checks (`nginx -t`) in an integration setup.

---

## Testing Checklist

- [ ] Test each directive with valid values
- [ ] Test each directive with invalid values
- [ ] Verify error messages are clear and helpful
- [ ] Test configuration inheritance (http > server > location)
- [ ] Test configuration override (child overrides parent)
- [ ] Test duplicate directive detection
- [ ] Test array directives with multiple values
- [ ] Test array directives with empty values
- [ ] Verify defaults apply when the directive is not specified
- [ ] Test all directives in combination
- [ ] Verify NGINX refuses to start with invalid configuration
- [ ] Verify the configuration logs at startup (info level)

---

## Implementation Notes

### Directive Handler Patterns

1. **Simple flag directives** (on|off):
   - Use `ngx_conf_set_flag_slot` built-in handler
   - No custom validation needed

2. **Size directives**:
   - Use `ngx_conf_set_size_slot` built-in handler
   - Automatically parses k, m, g suffixes
   - Validates positive values

3. **Time directives**:
   - Use `ngx_conf_set_msec_slot` built-in handler
   - Automatically parses ms, s, m, h suffixes
   - Validates positive values

4. **Enum directives** (custom values):
   - Use custom handler function
   - Check for duplicate with `NGX_CONF_UNSET_UINT`
   - Parse and validate value with `ngx_strcmp`
   - Provide clear error message with all valid options

5. **Array directives** (multiple values):
   - Use custom handler function
   - Check for duplicate with `NGX_CONF_UNSET_PTR`
   - Create `ngx_array_t` to store values
   - Validate each value individually
   - Provide clear error messages

### Memory Management

- Configuration structures allocated from `cf->pool`
- Arrays created with `ngx_array_create(cf->pool, ...)`
- String values copied directly (no allocation needed, NGINX manages)
- No manual cleanup needed (NGINX pool cleanup handles it)

### Error Handling

- Return `NGX_CONF_ERROR` for validation failures
- Return `"is duplicate"` for duplicate directives
- Use `ngx_conf_log_error(NGX_LOG_EMERG, ...)` for error messages
- Use `ngx_conf_log_error(NGX_LOG_DEBUG, ...)` for debug messages

---

## Requirements Mapping

This implementation satisfies the following requirements:

- **FR-12.1**: Configuration directives for all features
- **FR-12.2**: Configuration validation at startup
- **FR-12.3**: Clear error messages for invalid configurations
- **FR-12.4**: Resource limit configuration (conversion_memory, conversion_timeout)
- **FR-12.5**: Failure strategy configuration (markdown_error_policy)
- **FR-12.6**: Markdown flavor configuration
- **FR-12.7**: Agent-friendly extensions configuration (token_estimate, front_matter)
- **FR-12.8**: Accept header behavior configuration (markdown_accept)
- **FR-12.9**: Authentication policy configuration (auth_policy, auth_cookies)
- **FR-12.10**: ETag configuration (markdown_etag — historical, non-applicable; retired in 0.9.0, see FR-06.6 markdown_cache_validation)
- **FR-06.6**: Conditional request configuration (markdown_cache_validation)
- **FR-02.9**: Chunked response handling configuration (historical
  markdown_buffer_chunked and markdown_stream_types behavior)


## Document Updates

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.9.2 | 2026-08-08 | Kang | Fixed markdown_limits key names (conversion_memory, conversion_timeout); marked markdown_buffer_chunked removed in 0.9.2 |
| 0.6.2 | 2026-05-08 | Kang | Unified version narrative to 0.6.2 current release line |
| 0.5.0 | 2026-04-21 | docs-standardization | Standardized formatting, added mermaid diagrams where applicable, verified directive accuracy against code, added update tracking section |
