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

## Checks (0.9.0 + 0.9.1)

### Core Checks (0.9.0)

| Check | Description |
|-------|-------------|
| `nginx_version` | Detects NGINX version from `nginx -v` output |
| `module_exists` | Verifies that `ngx_http_markdown_filter_module.so` exists at the expected path |
| `config_valid` | Validates basic NGINX configuration syntax via `nginx -t` |

### Extended Checks (0.9.1)

| Check | Description |
|-------|-------------|
| `configure_args` | Checks `nginx -V` for `--with-compat` (required for dynamic modules) |
| `module_signature` | Verifies the module exports `ngx_http_markdown_filter_module` symbol |
| `rust_linkage` | Checks for Rust FFI exports (`markdown_convert`, etc.) |
| `os_arch` | Detects OS, architecture, and libc (glibc/musl with version) |
| `package_type` | Detects how nginx was installed (apt/yum/homebrew/docker/source) |

## Output Formats

### Human-readable (default)

```text
nginx-markdown-doctor v0.9.1
─────────────────────────────────
  ✓ [pass] nginx_version: nginx version 1.28.0 detected
  ✓ [pass] module_exists: module found at /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so
  ✓ [pass] config_valid: nginx config syntax OK (minimal test config)
  ✓ [pass] configure_args: nginx built with --with-compat
  ✓ [pass] module_signature: ngx_http_markdown_filter_module symbol found
  ✓ [pass] rust_linkage: Rust FFI symbols found (4 exports)
  ✓ [pass] os_arch: linux/x86_64 (glibc 2.31)
  ✓ [pass] package_type: nginx installed via apt (1.28.0-1~jammy)

  Recommended artifact: nginx-markdown-module-linux-x86_64-glibc2.31.tar.gz
─────────────────────────────────
Summary: 8 passed, 0 failed, 0 warnings, 0 skipped (8 total)
```

### JSON (`--json`)

```json
{
  "schema_version": 1,
  "tool_version": "0.9.1",
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
    },
    {
      "name": "configure_args",
      "status": "pass",
      "message": "nginx built with --with-compat",
      "details": { "configure_line": "...", "with_compat": true }
    },
    {
      "name": "module_signature",
      "status": "pass",
      "message": "ngx_http_markdown_filter_module symbol found",
      "details": { "symbol": "ngx_http_markdown_filter_module", "found": true }
    },
    {
      "name": "rust_linkage",
      "status": "pass",
      "message": "Rust FFI symbols found (4 exports)",
      "details": { "found_count": 4, "symbols": ["markdown_convert", "markdown_converter_new", "markdown_converter_free", "markdown_result_free"] }
    },
    {
      "name": "os_arch",
      "status": "pass",
      "message": "linux/x86_64 (glibc 2.31)",
      "details": { "os": "linux", "arch": "x86_64", "libc": "glibc", "libc_version": "2.31" }
    },
    {
      "name": "package_type",
      "status": "pass",
      "message": "nginx installed via apt (1.28.0-1~jammy)",
      "details": { "type": "apt", "version": "1.28.0-1~jammy" }
    }
  ],
  "summary": {
    "total": 8,
    "passed": 8,
    "failed": 0,
    "warnings": 0,
    "skipped": 0
  },
  "recommendation": {
    "artifact": "nginx-markdown-module-linux-x86_64-glibc2.31.tar.gz",
    "os": "linux",
    "arch": "x86_64",
    "libc": "glibc",
    "libc_version": "2.31"
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
      "description": "Doctor tool version (e.g. 0.9.1)"
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
          "details": { "type": "object" },
          "hint": { "type": "string", "description": "Remediation suggestion for failed/warn checks" }
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
    },
    "recommendation": {
      "type": "object",
      "description": "Recommended release artifact based on detected environment",
      "properties": {
        "artifact": { "type": "string" },
        "os": { "type": "string" },
        "arch": { "type": "string" },
        "libc": { "type": "string" },
        "libc_version": { "type": "string" }
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

## Remediation Hints (0.9.1)

When a check reports `fail` or `warn` status, it includes a `hint` field
with an actionable remediation suggestion:

| Check | Hint |
|-------|------|
| `module_exists` (warn) | Download the module from GitHub Releases or install via package |
| `config_valid` (fail) | Check nginx error log for details or run nginx -t manually |
| `configure_args` (warn) | Rebuild nginx with --with-compat or use the official nginx.org packages |
| `module_signature` (fail) | The module file may be corrupt or built for a different nginx version |
| `rust_linkage` (warn) | Module may be missing Rust converter linkage or built without FFI exports |

## Artifact Recommendation (0.9.1)

Based on the detected OS, architecture, and libc, the tool recommends the
appropriate release artifact. The recommendation appears in:

- Human-readable output: as a line after the checks
- JSON output: as a `recommendation` object with `artifact`, `os`, `arch`,
  and optional `libc`/`libc_version` fields

## Environment Compatibility (0.9.1)

The tool is designed to work correctly on:

- **macOS** (bash 3.2+): No GNU-only flags, compatible with system bash
- **Linux** (Ubuntu/Debian, CentOS/RHEL): Detects package type and glibc
- **Alpine/musl**: Detects musl libc variant
- **Docker containers**: Detects Docker environment, handles missing tools
- **Minimal images**: Gracefully skips checks when `nm`/`objdump` are unavailable

## Stability Contract

- JSON schema version 1 is stable for the 0.9.x series
- New checks will be additive (new entries in the `checks` array)
- Existing check names and status values will not change without a schema
  version bump
- After 1.0.0: additive-only changes to the schema
