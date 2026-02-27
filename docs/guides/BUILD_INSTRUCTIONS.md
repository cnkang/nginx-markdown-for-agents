# Build Instructions

## Scope

This document covers building and smoke-testing the project on macOS, including:

- the Rust conversion library (`components/rust-converter`)
- the generated C header (`markdown_converter.h`)
- the standalone NGINX module test binaries used during development

For runtime deployment and NGINX integration, see `INSTALLATION.md` and `CONFIGURATION.md`.

## Supported Build Environment

The top-level `Makefile` currently targets macOS on:

- Apple Silicon (`arm64` -> `aarch64-apple-darwin`)
- Intel (`x86_64` -> `x86_64-apple-darwin`)

The build target is selected automatically from `uname -m`.

## Prerequisites

**1. Rust Toolchain (rustup and cargo)**

Install Rust and confirm your host toolchain:

```bash
rustup show
cargo --version
```

If needed, add the macOS target explicitly:

```bash
# Apple Silicon
rustup target add aarch64-apple-darwin

# Intel Mac
rustup target add x86_64-apple-darwin
```

**2. cbindgen**

`cbindgen` is required to generate the C header used by the NGINX module.

```bash
cargo install cbindgen
cbindgen --version
```

**3. Xcode Command Line Tools (macOS)**

```bash
xcode-select --install
```

## Quick Start

Build the Rust library and copy the generated header into `components/nginx-module/src/`:

```bash
make
```

Run the default smoke test target from the root `Makefile`:

```bash
make test
```

## Common Build Targets (Top-Level `Makefile`)

```bash
make help
```

Available targets include:

- `make` / `make all` - Build release Rust library and copy headers
- `make rust-lib` - Build release Rust library and generate `markdown_converter.h`
- `make rust-lib-debug` - Build debug Rust library
- `make copy-headers` - Copy generated header to `components/nginx-module/src/`
- `make check-headers` - Verify the generated/copy headers are identical
- `make test` - Build and run the default smoke test (`components/nginx-module/tests` smoke targets)
- `make clean` - Clean Rust and selected NGINX module test artifacts

## Detailed Build Steps

### 1. Build the Rust Conversion Library (Release)

```bash
make rust-lib
```

This does the following:

- builds `components/rust-converter` in release mode for the detected architecture
- generates `components/rust-converter/include/markdown_converter.h` using `cbindgen`

### 2. Copy the Generated Header into the NGINX Module Directory

```bash
make copy-headers
```

This copies the generated header to:

- `components/nginx-module/src/markdown_converter.h`

Source-of-truth note:

- `components/rust-converter/include/markdown_converter.h` is the generated source of truth
- `components/nginx-module/src/markdown_converter.h` is a synchronized copy used by C module builds/tests
- run `make check-headers` to detect drift between the two files

### 3. (Optional) Build a Debug Rust Library

```bash
make rust-lib-debug
```

Use this when debugging Rust-side behavior.

## Running Tests

### Root-Level Smoke Test

The top-level `make test` runs a limited smoke test via `components/nginx-module/tests`. It is useful for quick validation but does not run the full project test suite.

```bash
make test
```

### Rust Test Suite (Recommended)

```bash
cd components/rust-converter
cargo test --all
```

Additional useful commands:

```bash
cargo test --lib
cargo test --test ffi_test
```

### NGINX Module Standalone Test Targets

Many NGINX module tests are mock-based and can run without a system NGINX installation.

Examples:

```bash
make -C components/nginx-module/tests unit
```

Note:

- Some integration-style tests require a local `nginx` binary and additional environment setup.
- Check `components/nginx-module/tests/Makefile` for target-specific prerequisites.

## Output Locations

### Rust Library Output

The release static library is written to:

- `components/rust-converter/target/aarch64-apple-darwin/release/libnginx_markdown_converter.a` (Apple Silicon)
- `components/rust-converter/target/x86_64-apple-darwin/release/libnginx_markdown_converter.a` (Intel)

### Generated Header

The generated header is written to:

- `components/rust-converter/include/markdown_converter.h`

and copied to:

- `components/nginx-module/src/markdown_converter.h`

`components/rust-converter/include/markdown_converter.h` is the canonical generated file. Treat the copy
in `components/nginx-module/src/` as a build/test convenience mirror.

## Troubleshooting

### `markdown_converter.h` Not Found

Symptom:

```text
fatal error: 'markdown_converter.h' file not found
```

Fix:

```bash
make rust-lib
make copy-headers
```

### Missing `cbindgen`

Symptom:

```text
cbindgen: command not found
```

Fix:

```bash
cargo install cbindgen
```

### Architecture Mismatch at Link Time

Symptom (example):

```text
ld: warning: ignoring file ..., building for macOS-arm64 but attempting to link with file built for macOS-x86_64
```

Fix:

```bash
make clean
make rust-lib
```

Also verify your active Rust host/target configuration with `rustup show`.

## Next Steps

After building successfully:

1. Review runtime setup in `INSTALLATION.md`.
2. Review module directives in `CONFIGURATION.md`.
3. Review operational guidance in `OPERATIONS.md`.
