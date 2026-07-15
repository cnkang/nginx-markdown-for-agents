# Rust/C FFI ABI Compatibility

## Supported contract

The Rust converter C ABI is a **bundled internal boundary** between the Rust
static library and the NGINX module built from the same source tree. The
project does not publish a standalone converter shared library, header SDK, or
third-party ABI compatibility promise. Release artifacts contain the linked
NGINX module; source builds generate the header and link the matching Rust
archive as one coordinated build.

The generated `markdown_converter.h` is public source only so the bundled C
module can compile. Its presence in the repository does not make arbitrary
third-party C consumers a supported product surface.

## v0.9.1 baseline reset

v0.9.1 is the final coordinated pre-v1 reset. ABI version **1** is the new
baseline. This reset:

- removes the unimplemented MDX and Org-mode flavor discriminants;
- removes the unused Rust streaming-decision FFI model;
- removes `FFIConditionalResult`, its reserved `matched_etag_len` field, and
  the superseded `markdown_check_conditional` API;
- removes the superseded `markdown_build_base_url` helper; and
- removes 15 zero-production-consumer exports: the accept/decision init
  helpers, Rust decision/error-policy wrappers, standalone URL checks,
  diagnostics-schema accessors, convenience constructors, and redundant
  streaming finish/free/reason helpers; and
- retains `markdown_decide_conditional` and `markdown_decide_base_url` as the
  complete production decision interfaces.

These changes are intentionally breaking at the internal FFI boundary. Both
bundled sides are updated atomically, so operators do not migrate C calls.
Operators must install or build the complete v0.9.1 module rather than mixing
an older Rust archive or header with the new C module.

## Explicit ABI alignment

Rust owns:

```text
MARKDOWN_ABI_VERSION = 1
markdown_abi_version() -> 1
```

`cbindgen` emits both declarations into the generated header. During NGINX
preconfiguration, the C module calls `markdown_abi_version()` and compares the
result with its generated-header `MARKDOWN_ABI_VERSION`. A mismatch logs a
critical configuration error and makes `nginx -t`/startup fail. This check runs
before the module installs its header and body filters.

`nginx-markdown-doctor` also checks that the module contains the
`markdown_abi_version` symbol. The doctor symbol check is diagnostic; the
NGINX startup comparison is the authoritative value enforcement.

Increment `MARKDOWN_ABI_VERSION` whenever a shared struct changes size or
layout (including a tail-field append), a field is removed/reordered or changes
type, an enum representation changes, an export is removed/changed, or any
other change makes a C object built against the previous header unsafe or
semantically invalid.

## Compatibility policy

### Before v1.0

v0.9.1 is the last planned coordinated reset. Any further pre-v1 incompatible
change must be release-noted, increment `MARKDOWN_ABI_VERSION`, and update all
Rust definitions, C consumers, headers, layout assertions, tests, and operator
diagnostics in one change.

### v1.0 and later

The bundled ABI becomes a frozen internal contract:

- every shared-struct size/layout change, including a tail-field append,
  requires an ABI version increment;
- new structs and exports are preferred to changing existing layouts;
- field removal, reordering, type changes, or signature changes require an ABI
  version increment and a release whose compatibility policy explicitly
  permits that break;
- Cargo package version is not the ABI identifier; `MARKDOWN_ABI_VERSION` is;
  and
- no external third-party ABI support is implied without a separate published
  SDK policy, artifact, support matrix, and conformance suite.

An append-only exception is possible only after the boundary adopts and
validates an explicit size-tagged/versioned struct protocol. The current bare
pointer ABI has no caller-size field, so init helpers cannot make old/new
object sizes interoperable.

## Layout and platform rules

All shared structs use `#[repr(C)]`. All FFI enums use an explicit integer
representation. Raw strings use pointer-plus-length pairs, not implicit
NUL-termination. Opaque handles are owned by Rust and never dereferenced by C.

The production module and checked layout assertions target LP64 platforms:

| Platform | Pointer | `size_t` | `unsigned long` |
|----------|---------|----------|-----------------|
| Linux x86_64 | 8 | 8 | 8 |
| Linux aarch64 | 8 | 8 | 8 |
| macOS x86_64 | 8 | 8 | 8 |
| macOS arm64 | 8 | 8 | 8 |

The layout header fails compilation on non-LP64 data models. Rust layout tests
and C `_Static_assert` checks cover size, alignment, and field offsets. Feature
combinations must not change the layout of a type that exists in more than one
combination.

## Ownership rules

- C borrows input pointers only for the documented call duration.
- Rust-owned result buffers are released only through their matching Rust free
  function.
- A finalize, abort, free, or safe-finish operation that consumes a handle
  makes that handle invalid immediately.
- Empty Rust output buffers cross the boundary as `NULL` plus length zero.
- Slice ownership transfer uses a thin data pointer plus explicit length; it
  never exposes a Rust fat pointer.
- Output structs are initialized to a safe state before fallible work and are
  written only after `catch_unwind` succeeds.

## Header synchronization

The two checked-in copies are:

1. `components/rust-converter/include/markdown_converter.h` (generated), and
2. `components/nginx-module/src/markdown_converter.h` (bundled consumer copy).

They must be byte-identical. Regenerate with pinned `cbindgen 0.29.2`, copy the
header, and run:

```bash
make check-headers
make test-rust
make test-nginx-unit
```

## Atomic change checklist

For every ABI change:

1. update Rust definitions, exports, defaults, cleanup, and panic fallbacks;
2. update all C call sites and init sites;
3. regenerate and copy both headers;
4. update Rust layout tests and C static assertions;
5. update x86_64/aarch64 and feature-combination coverage;
6. update doctor/alignment checks when the ABI version changes;
7. update this document, the migration contract, changelog, and release notes;
8. run header, Rust, C, harness, and documentation gates.

Never suppress an ABI mismatch or bypass the startup comparison. Rebuild both
bundled halves from the same source revision.
