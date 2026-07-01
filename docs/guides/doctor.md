# nginx-markdown-doctor

Installation diagnostic tool for nginx-markdown-for-agents.

## Overview

`nginx-markdown-doctor` performs basic health checks to verify that the
nginx-markdown-for-agents module is correctly installed and configured.
It helps diagnose common installation issues such as version mismatches,
missing module files, and configuration errors.

## Usage

```bash
bash tools/doctor/nginx-markdown-doctor.sh [OPTIONS]
```

### Options

| Option | Description |
|--------|-------------|
| `--json` | Output JSON instead of human-readable text |
| `--module-path PATH` | Explicit path to the directory containing the module `.so` |
| `--nginx-bin PATH` | Explicit path to the nginx binary (empty string to skip nginx checks) |
| `-h`, `--help` | Show help message |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | At least one check failed |
| 2 | Usage error |

## Checks (0.9.0)

| Check | Description |
|-------|-------------|
| `nginx_version` | Detects NGINX version from `nginx -v` output |
| `module_exists` | Verifies that `ngx_http_markdown_filter_module.so` exists at the expected path |
| `config_valid` | Validates basic NGINX configuration syntax via `nginx -t` |

## Output Formats

### Human-readable (default)

```text
nginx-markdown-doctor v0.9.0
─────────────────────────────────
  ✓ [pass] nginx_version: nginx version 1.28.0 detected
  ✓ [pass] module_exists: module found at /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so
  ✓ [pass] config_valid: nginx config syntax OK (minimal test config)
─────────────────────────────────
Summary: 3 passed, 0 failed, 0 warnings, 0 skipped (3 total)
```

### JSON (`--json`)

```json
{
  "schema_version": 1,
  "tool_version": "0.9.0",
  "timestamp": "2026-07-01T12:00:00Z",
  "checks": [
    {
      "name": "nginx_version",
      "status": "pass",
      "message": "nginx version 1.28.0 detected",
      "details": { "version": "1.28.0" }
    },
    {
      "name": "module_exists",
      "status": "pass",
      "message": "module found at /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so",
      "details": { "path": "/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so" }
    },
    {
      "name": "config_valid",
      "status": "pass",
      "message": "nginx config syntax OK (minimal test config)"
    }
  ],
  "summary": {
    "total": 3,
    "passed": 3,
    "failed": 0,
    "warnings": 0,
    "skipped": 0
  }
}
```

## JSON Schema (v1)

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["schema_version", "tool_version", "timestamp", "checks", "summary"],
  "properties": {
    "schema_version": {
      "type": "integer",
      "description": "Schema version number (currently 1)"
    },
    "tool_version": {
      "type": "string",
      "description": "Doctor tool version (e.g. 0.9.0)"
    },
    "timestamp": {
      "type": "string",
      "format": "date-time",
      "description": "ISO 8601 UTC timestamp"
    },
    "checks": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "status", "message"],
        "properties": {
          "name": { "type": "string" },
          "status": { "type": "string", "enum": ["pass", "fail", "warn", "skip"] },
          "message": { "type": "string" },
          "details": { "type": "object" }
        }
      }
    },
    "summary": {
      "type": "object",
      "required": ["total", "passed", "failed", "warnings", "skipped"],
      "properties": {
        "total": { "type": "integer" },
        "passed": { "type": "integer" },
        "failed": { "type": "integer" },
        "warnings": { "type": "integer" },
        "skipped": { "type": "integer" }
      }
    }
  }
}
```

## Check Status Values

| Status | Meaning |
|--------|---------|
| `pass` | Check completed successfully |
| `fail` | Check detected a problem |
| `warn` | Check detected a potential issue (non-blocking) |
| `skip` | Check could not run (e.g., nginx not available) |

## Examples

### Basic usage

```bash
bash tools/doctor/nginx-markdown-doctor.sh
```

### JSON output for scripting

```bash
bash tools/doctor/nginx-markdown-doctor.sh --json 2>/dev/null | python3 -m json.tool
```

### Custom module path

```bash
bash tools/doctor/nginx-markdown-doctor.sh --module-path /opt/nginx/modules
```

### Skip nginx checks (CI environments without nginx)

```bash
bash tools/doctor/nginx-markdown-doctor.sh --nginx-bin ""
```

## Module Search Paths

When `--module-path` is not specified, the tool searches these default
locations for `ngx_http_markdown_filter_module.so`:

- `/usr/lib/nginx/modules`
- `/usr/lib64/nginx/modules`
- `/usr/local/lib/nginx/modules`
- `/opt/homebrew/lib/nginx/modules`
- `/usr/local/libexec/nginx`

## CI Integration

The doctor tool runs as a smoke test in CI via
`.github/workflows/doctor-smoke.yml`. The smoke test verifies:

1. The tool runs without crashing
2. JSON output is valid
3. At least 3 checks are reported
4. JSON schema structure is correct

## Future (0.9.1)

Additional checks planned for 0.9.1:

- `configure_arguments`: Verify `--with-compat` and build options
- `module_signature`: Binary compatibility signature check
- `rust_linkage`: Rust converter library linkage verification
- `os_arch_libc`: OS, architecture, and libc detection
- `package_type`: Package format detection and artifact recommendation
- Remediation suggestions for each failed check

## Stability Contract

- JSON schema version 1 is stable for the 0.9.x series
- New checks will be additive (new entries in the `checks` array)
- Existing check names and status values will not change without a schema
  version bump
- After 1.0.0: additive-only changes to the schema
