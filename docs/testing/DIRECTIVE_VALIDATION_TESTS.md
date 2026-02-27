# Configuration Directive Validation Tests

This document provides validation test cases for all configuration directives exposed by the NGINX Markdown filter module.

## Test Configuration Examples

### 1. markdown_filter (on|off)

**Valid configurations:**
```nginx
markdown_filter on;
markdown_filter off;
```

**Invalid configurations:**
```nginx
markdown_filter yes;        # Error: invalid value, must be "on" or "off"
markdown_filter 1;          # Error: invalid value, must be "on" or "off"
markdown_filter;            # Error: missing value
```

**Expected behavior:**
- Default: off
- Context: http, server, location
- Inheritance: child overrides parent

---

### 2. markdown_max_size (size)

**Valid configurations:**
```nginx
markdown_max_size 10m;      # 10 megabytes
markdown_max_size 5120k;    # 5120 kilobytes
markdown_max_size 1048576;  # 1048576 bytes
```

**Invalid configurations:**
```nginx
markdown_max_size -1;       # Error: negative size
markdown_max_size 0;        # Error: size must be positive
markdown_max_size abc;      # Error: invalid size format
markdown_max_size;          # Error: missing value
```

**Expected behavior:**
- Default: 10m (10 megabytes)
- Context: http, server, location
- Validation: must be positive integer with optional suffix (k, m, g)

---

### 3. markdown_timeout (time)

**Valid configurations:**
```nginx
markdown_timeout 5s;        # 5 seconds
markdown_timeout 5000ms;    # 5000 milliseconds
markdown_timeout 5000;      # 5000 milliseconds (default unit)
```

**Invalid configurations:**
```nginx
markdown_timeout -1;        # Error: negative timeout
markdown_timeout 0;         # Error: timeout must be positive
markdown_timeout abc;       # Error: invalid time format
markdown_timeout;           # Error: missing value
```

**Expected behavior:**
- Default: 5s (5000 milliseconds)
- Context: http, server, location
- Validation: must be positive integer with optional suffix (ms, s, m, h)

---

### 4. markdown_on_error (pass|reject)

**Valid configurations:**
```nginx
markdown_on_error pass;     # Fail-open: return original HTML
markdown_on_error reject;   # Fail-closed: return 502 error
```

**Invalid configurations:**
```nginx
markdown_on_error fail;     # Error: invalid value, must be "pass" or "reject"
markdown_on_error open;     # Error: invalid value, must be "pass" or "reject"
markdown_on_error;          # Error: missing value
markdown_on_error pass reject; # Error: too many arguments
```

**Expected behavior:**
- Default: pass (fail-open)
- Context: http, server, location
- Error message: "invalid value \"%s\" in \"markdown_on_error\" directive, it must be \"pass\" or \"reject\""

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

### 8. markdown_on_wildcard (on|off)

**Valid configurations:**
```nginx
markdown_on_wildcard on;
markdown_on_wildcard off;
```

**Invalid configurations:**
```nginx
markdown_on_wildcard yes;   # Error: invalid value, must be "on" or "off"
markdown_on_wildcard 1;     # Error: invalid value, must be "on" or "off"
markdown_on_wildcard;       # Error: missing value
```

**Expected behavior:**
- Default: off
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

### 11. markdown_etag (on|off)

**Valid configurations:**
```nginx
markdown_etag on;
markdown_etag off;
```

**Invalid configurations:**
```nginx
markdown_etag yes;          # Error: invalid value, must be "on" or "off"
markdown_etag 1;            # Error: invalid value, must be "on" or "off"
markdown_etag;              # Error: missing value
```

**Expected behavior:**
- Default: on
- Context: http, server, location
- When enabled: generates ETag from Markdown output hash

---

### 12. markdown_conditional_requests (mode)

**Valid configurations:**
```nginx
markdown_conditional_requests full_support;
markdown_conditional_requests if_modified_since_only;
markdown_conditional_requests disabled;
```

**Invalid configurations:**
```nginx
markdown_conditional_requests on;       # Error: invalid value
markdown_conditional_requests enabled;  # Error: invalid value
markdown_conditional_requests;          # Error: missing value
markdown_conditional_requests full_support disabled; # Error: too many arguments
```

**Expected behavior:**
- Default: full_support
- Context: http, server, location
- Error message: "invalid value \"%s\" in \"markdown_conditional_requests\" directive, it must be \"full_support\", \"if_modified_since_only\", or \"disabled\""

---

### 13. markdown_buffer_chunked (on|off)

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

### 14. markdown_stream_types (type [type ...])

**Valid configurations:**
```nginx
markdown_stream_types text/event-stream;
markdown_stream_types text/event-stream application/x-ndjson;
markdown_stream_types text/event-stream application/stream+json;
```

**Invalid configurations:**
```nginx
markdown_stream_types;          # Error: missing value (requires at least one type)
markdown_stream_types "";       # Error: empty content type
markdown_stream_types plaintext; # Error: invalid format, must be "type/subtype"
markdown_stream_types text;     # Error: invalid format, must be "type/subtype"
```

**Expected behavior:**
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
    markdown_max_size 5m;
    
    server {
        # Inherits: markdown_filter on, markdown_max_size 5m
        
        location /api {
            markdown_filter off;  # Overrides parent
            # Inherits: markdown_max_size 5m
        }
    }
}
```

**Expected:**
- `/api`: filter off, max_size 5m
- Other locations: filter on, max_size 5m

### Test 2: Multi-level inheritance
```nginx
http {
    markdown_filter on;
    markdown_timeout 10s;
    markdown_on_error pass;
    
    server {
        markdown_timeout 5s;  # Overrides http level
        # Inherits: markdown_filter on, markdown_on_error pass
        
        location /docs {
            markdown_on_error reject;  # Overrides server level
            # Inherits: markdown_filter on, markdown_timeout 5s
        }
    }
}
```

**Expected:**
- `/docs`: filter on, timeout 5s, on_error reject
- Other locations: filter on, timeout 5s, on_error pass

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
   - Occurs when directive is specified multiple times in same context

2. **Invalid value:**
   - Message: "invalid value \"%s\" in \"%s\" directive, it must be ..."
   - Occurs when value doesn't match expected format

3. **Empty value:**
   - Message: "empty [pattern/type] in \"%s\" directive"
   - Occurs for array directives with empty elements

4. **Invalid format:**
   - Message: "invalid [type] \"%s\" in \"%s\" directive, must be in format ..."
   - Occurs when value format is incorrect (e.g., content type without slash)

---

## Configuration Validation at Startup

All configuration parameters are validated at NGINX startup:

1. **Flag directives** (on|off): Validated by ngx_conf_set_flag_slot
2. **Size directives**: Validated by ngx_conf_set_size_slot (must be positive)
3. **Time directives**: Validated by ngx_conf_set_msec_slot (must be positive)
4. **Enum directives**: Validated by custom handlers with explicit value checks
5. **Array directives**: Validated by custom handlers with format checks

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
- [ ] Verify defaults are applied when directive is not specified
- [ ] Test all directives in combination
- [ ] Verify NGINX refuses to start with invalid configuration
- [ ] Verify configuration is logged at startup (info level)

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
- **FR-12.4**: Resource limit configuration (max_size, timeout)
- **FR-12.5**: Failure strategy configuration (on_error)
- **FR-12.6**: Markdown flavor configuration
- **FR-12.7**: Agent-friendly extensions configuration (token_estimate, front_matter)
- **FR-12.8**: Accept header behavior configuration (on_wildcard)
- **FR-12.9**: Authentication policy configuration (auth_policy, auth_cookies)
- **FR-12.10**: ETag configuration (generate_etag)
- **FR-06.6**: Conditional request configuration (conditional_requests)
- **FR-02.9**: Chunked response handling configuration (buffer_chunked, stream_types)
