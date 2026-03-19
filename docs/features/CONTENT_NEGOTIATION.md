# Content Negotiation

## Overview

This module implements HTTP content negotiation to serve Markdown representations of HTML content. Clients request Markdown using the standard `Accept` header, and the module decides whether to convert the response based on eligibility rules and configuration.

## How It Works

### Basic Flow

```
Client Request
  └─> Accept: text/markdown
       └─> NGINX receives request
            └─> Proxy to backend
                 └─> Backend returns HTML
                      └─> Module checks eligibility
                           └─> Convert to Markdown
                                └─> Return with Content-Type: text/markdown
```

### Accept Header Parsing

The module parses the `Accept` header to determine if the client wants Markdown:

**Explicit Request (Default Behavior)**:
```http
Accept: text/markdown
```

**With Quality Values**:
```http
Accept: text/html, text/markdown;q=0.9
```

**Multiple Types**:
```http
Accept: text/html, application/json, text/markdown
```

The module looks for `text/markdown` in the Accept header. Quality values (q-parameters) are currently not evaluated for preference ordering.

### Wildcard Handling

By default, wildcard Accept headers do NOT trigger conversion:

```http
Accept: */*           → No conversion (returns HTML)
Accept: text/*        → No conversion (returns HTML)
```

To enable conversion for wildcards, use the `markdown_on_wildcard` directive:

```nginx
location /docs/ {
    markdown_filter on;
    markdown_on_wildcard on;  # Enable wildcard conversion
}
```

With `markdown_on_wildcard on`:
```http
Accept: */*           → Conversion enabled
Accept: text/*        → Conversion enabled
Accept: text/markdown → Conversion enabled
```

## Configuration

### Basic Enablement

```nginx
location /api/ {
    markdown_filter on;
    proxy_pass http://backend;
}
```

### Variable-Driven Negotiation

For more control, use NGINX variables:

```nginx
# Define Accept header matching
map $http_accept $markdown_accept {
    default 0;
    "~*(^|,)\\s*text/markdown(\\s*;|,|$)" 1;
    "~*(^|,)\\s*text/\\*(\\s*;|,|$)" 1;
}

# Define path matching
map $request_uri $markdown_path {
    default 0;
    "~*\\.html$" 1;
}

# Combine conditions
map "$markdown_accept:$markdown_path" $enable_markdown {
    default 0;
    "1:1" 1;  # Both Accept and path match
}

server {
    location / {
        markdown_filter $enable_markdown;
        proxy_pass http://backend;
    }
}
```

## Response Headers

### Content-Type

The module sets the Content-Type header for converted responses:

```http
Content-Type: text/markdown; charset=utf-8
```

The charset is always UTF-8, as the converter produces UTF-8 output.

### Vary Header

The module automatically adds the `Vary` header to indicate that the response varies based on the Accept header:

```http
Vary: Accept
```

This is critical for correct caching behavior. Without it, caches might serve the wrong variant to clients.

If the upstream response already has a `Vary` header, the module appends `Accept` to it:

```http
Upstream:  Vary: User-Agent
Module:    Vary: User-Agent, Accept
```

## Eligibility Rules

Not all requests are eligible for conversion. The module checks:

### Request Method
- `GET` → Eligible
- `HEAD` → Eligible
- `POST`, `PUT`, `DELETE`, etc. → Not eligible

### Response Status
- `200 OK` → Eligible
- `3xx`, `4xx`, `5xx` → Not eligible

### Content-Type
- `text/html` → Eligible
- `text/html; charset=utf-8` → Eligible
- `application/json` → Not eligible
- `image/png` → Not eligible

### Response Size
- Within `markdown_max_size` → Eligible
- Exceeds limit → Not eligible (passthrough)

### Other Conditions
- Range requests → Not eligible (bypass)
- Chunked responses with streaming types → May be bypassed (see `markdown_stream_types`)

## Testing Content Negotiation

### Test Markdown Conversion

```bash
curl -H "Accept: text/markdown" http://localhost/page.html
```

Expected response:
```http
HTTP/1.1 200 OK
Content-Type: text/markdown; charset=utf-8
Vary: Accept

# Page Title

Content in Markdown format...
```

### Test HTML Passthrough

```bash
curl -H "Accept: text/html" http://localhost/page.html
```

Expected response:
```http
HTTP/1.1 200 OK
Content-Type: text/html

<!DOCTYPE html>
<html>...
```

### Test Wildcard Behavior

```bash
# Without markdown_on_wildcard (default)
curl -H "Accept: */*" http://localhost/page.html
# Returns HTML

# With markdown_on_wildcard on
curl -H "Accept: */*" http://localhost/page.html
# Returns Markdown
```

## Common Patterns

### Bot-Targeted Conversion (User-Agent Based)

Some AI crawlers and agent bots do not send `Accept: text/markdown` in their requests. If you want these bots to receive Markdown automatically when they fetch `text/html` pages, you can use NGINX's `map` directive to rewrite the `Accept` header for matching User-Agent strings before the request reaches the upstream.

This approach is useful because:

- Many AI crawlers (ClaudeBot, GPTBot, etc.) request pages with a standard browser-like `Accept` header and have no built-in mechanism to ask for Markdown.
- Injecting `Accept: text/markdown` at the NGINX layer lets the module's existing content negotiation logic handle the rest, without any code changes.
- Operators retain full control over which bots receive Markdown and can add or remove User-Agent patterns through configuration alone.

```nginx
# Rewrite Accept header for known AI bots so the module sees text/markdown
map $http_user_agent $bot_accept_override {
    default         "";
    "~*ClaudeBot"   "text/markdown, text/html;q=0.9";
    "~*GPTBot"      "text/markdown, text/html;q=0.9";
    "~*Googlebot"   "text/markdown, text/html;q=0.9";
}

# Use the override when present, otherwise keep the original Accept header
map $bot_accept_override $final_accept {
    ""      $http_accept;
    default $bot_accept_override;
}

server {
    listen 80;

    location / {
        markdown_filter on;
        proxy_set_header Accept $final_accept;
        proxy_pass http://backend;
    }
}
```

With this configuration:

- Requests from ClaudeBot, GPTBot, or Googlebot have their Accept header replaced with `text/markdown, text/html;q=0.9`, which causes the module to convert eligible `text/html` responses to Markdown.
- Requests from all other clients keep their original Accept header. Browsers and normal tools continue to receive HTML as before.
- The module's standard eligibility checks (status code, content type, size limits, etc.) still apply. Only responses that pass all checks are converted.

Note: If you also want to control the on/off switch per bot (rather than just the Accept header), you can combine this with a variable-driven `markdown_filter`:

```nginx
map $http_user_agent $is_ai_bot {
    default         0;
    "~*ClaudeBot"   1;
    "~*GPTBot"      1;
}

location / {
    markdown_filter $is_ai_bot;
    proxy_set_header Accept $final_accept;
    proxy_pass http://backend;
}
```

This enables the module only for matching bots, so non-bot requests skip the filter entirely.

### API Gateway Pattern

Serve Markdown to agents, HTML to browsers:

```nginx
location /docs/ {
    markdown_filter on;
    markdown_on_wildcard off;  # Explicit opt-in only
    proxy_pass http://docs-backend;
}
```

### Content API Pattern

Serve multiple formats based on Accept:

```nginx
map $http_accept $response_format {
    default "html";
    "~*text/markdown" "markdown";
    "~*application/json" "json";
}

location /content/ {
    markdown_filter on;
    # Additional logic for JSON responses
    proxy_pass http://content-backend;
}
```

### Path-Based Negotiation

Only convert specific paths:

```nginx
map $request_uri $markdown_eligible {
    default 0;
    "~*/articles/" 1;
    "~*/docs/" 1;
}

server {
    location / {
        markdown_filter $markdown_eligible;
        proxy_pass http://backend;
    }
}
```

## Troubleshooting

### Conversion Not Happening

Check:
1. `markdown_filter on` is set
2. Client sends `Accept: text/markdown`
3. Response is `200 OK` with `Content-Type: text/html`
4. Response size is within `markdown_max_size`

Debug with verbose curl:
```bash
curl -v -H "Accept: text/markdown" http://localhost/page.html
```

### Wrong Variant Served from Cache

Check:
1. `Vary: Accept` header is present
2. Cache key includes Accept header
3. Upstream cache respects Vary header

For NGINX proxy cache:
```nginx
proxy_cache_key "$scheme$request_method$host$request_uri$http_accept";
```

### Variable-Driven Filter Not Working

Check:
1. Variable resolves to expected value (use `add_header X-Debug $var;`)
2. Variable is evaluated at the correct phase
3. Map syntax is correct

Debug variable values:
```nginx
add_header X-Markdown-Filter $enable_markdown always;
```

## Performance Considerations

### Accept Header Parsing

Accept header parsing is lightweight and happens early in the request lifecycle. The performance impact is negligible.

### Variable Evaluation

Using variables for `markdown_filter` adds minimal overhead. NGINX evaluates variables efficiently.

### Caching

Proper content negotiation with `Vary: Accept` ensures correct caching:
- HTML and Markdown variants are cached separately
- Clients receive the correct variant
- Cache hit rates remain high

## Related Documentation

- [CONFIGURATION.md](../guides/CONFIGURATION.md) - Configuration directives
- [REQUEST_LIFECYCLE.md](../architecture/REQUEST_LIFECYCLE.md) - Request processing flow
- [CACHE_AWARE_RESPONSES.md](CACHE_AWARE_RESPONSES.md) - Caching and ETags
- [OPERATIONS.md](../guides/OPERATIONS.md) - Troubleshooting guide

## Implementation Details

The content negotiation logic is implemented in:
- `src/ngx_http_markdown_accept.c` - Accept header parsing
- `src/ngx_http_markdown_eligibility.c` - Eligibility checks
- `src/ngx_http_markdown_filter_module.c` - Main filter logic

For implementation details, see the source code and inline comments.
