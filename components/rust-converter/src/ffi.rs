//! FFI (Foreign Function Interface) layer for C integration
//!
//! This module provides C-compatible data structures and functions for
//! integrating the Rust conversion engine with the NGINX C module.
//!
//! # FFI Boundary Contract
//!
//! ## CRITICAL: String Representation
//!
//! **All strings use UTF-8 bytes + length representation (NOT NUL-terminated C strings)**
//!
//! This is a non-standard but intentional design choice that provides several benefits:
//! 1. **Binary Safety**: Supports embedded NUL bytes in content
//! 2. **Performance**: Avoids strlen() overhead in C code
//! 3. **Explicit Length**: Provides clear boundaries for memory operations
//! 4. **UTF-8 Correctness**: Length represents byte count, not character count
//!
//! ### String Field Pattern
//!
//! Every string field follows this pattern:
//! - Pointer field: `*mut u8` (points to UTF-8 bytes)
//! - Length field: `usize` with `_len` suffix (byte count)
//!
//! Example:
//! ```c
//! // CORRECT: Use length field
//! for (size_t i = 0; i < result.markdown_len; i++) {
//!     process_byte(result.markdown[i]);
//! }
//!
//! // WRONG: Do NOT use strlen()
//! size_t len = strlen((char*)result.markdown);  // INCORRECT!
//! ```
//!
//! ### Length Field Semantics
//!
//! - Length fields contain the **exact byte count** of the string
//! - Length does **NOT** include any NUL terminator
//! - Rust may or may not add a NUL terminator (C must not rely on it)
//! - C code must use the length field for all operations
//!
//! ## Memory Management
//!
//! **Ownership Model:**
//! - Rust allocates all output memory using `Box<[u8]>`
//! - C receives raw pointers but does NOT own the memory
//! - C must call `markdown_result_free()` exactly once to deallocate
//! - After calling free, all pointers become invalid
//! - No shared ownership across the FFI boundary
//!
//! **Allocation Strategy (runnable Rust example):**
//! ```rust
//! use nginx_markdown_converter::ffi::{markdown_result_free, MarkdownResult};
//! use std::ptr;
//!
//! // Rust side: Allocate and transfer ownership into FFI result fields
//! let markdown = String::from("# Hello from Rust\n");
//! let markdown_len = markdown.len();
//! let markdown_ptr = Box::into_raw(markdown.into_bytes().into_boxed_slice()) as *mut u8;
//!
//! let mut result = MarkdownResult {
//!     markdown: markdown_ptr,
//!     markdown_len,
//!     etag: ptr::null_mut(),
//!     etag_len: 0,
//!     token_estimate: 0,
//!     error_code: 0,
//!     error_message: ptr::null_mut(),
//!     error_len: 0,
//! };
//!
//! // Consumer side: use pointer + explicit length (never strlen)
//! assert!(!result.markdown.is_null());
//! assert_eq!(result.markdown_len, markdown_len);
//!
//! // Free via Rust-provided function (not libc free)
//! unsafe { markdown_result_free(&mut result) };
//! assert!(result.markdown.is_null());
//! assert_eq!(result.markdown_len, 0);
//! ```
//!
//! ## Error Handling Contract
//!
//! **Success Case:**
//! - `error_code = 0`
//! - `error_message = NULL`
//! - `error_len = 0`
//! - Output fields (markdown, etag) contain valid data
//!
//! **Error Case:**
//! - `error_code != 0` (see error code constants below)
//! - `error_message` points to UTF-8 error description
//! - `error_len` contains byte length of error message
//! - All output fields (markdown, etag) are NULL
//!
//! **Panic Safety:**
//! - All FFI functions use `catch_unwind` to prevent panics from crossing the boundary
//! - Panics are converted to error codes and messages
//! - C code will never see Rust unwinding
//!
//! ## Pointer Validation
//!
//! All FFI functions validate pointers before dereferencing:
//! - NULL pointers are rejected with appropriate error codes
//! - Invalid pointers may cause undefined behavior (C caller responsibility)
//! - All error paths ensure consistent error state
//!
//! ## Thread Safety
//!
//! - `MarkdownConverterHandle` is NOT thread-safe
//! - Each NGINX worker should have its own converter instance
//! - Concurrent calls to `markdown_convert()` on the same handle are unsafe
//! - Multiple converter instances can be used concurrently

use std::panic;
use std::ptr;
use std::slice;
use std::time::Duration;

use crate::converter::{ConversionContext, ConversionOptions, MarkdownConverter, MarkdownFlavor};
use crate::error::ConversionError;
use crate::etag_generator::ETagGenerator;
use crate::parser::parse_html_with_charset;
use crate::token_estimator::TokenEstimator;

// ============================================================================
// Error Code Constants
// ============================================================================

/// Success - no error occurred
pub const ERROR_SUCCESS: u32 = 0;

/// HTML parsing failed (malformed HTML, invalid structure)
pub const ERROR_PARSE: u32 = 1;

/// Character encoding error (invalid UTF-8, unsupported charset)
pub const ERROR_ENCODING: u32 = 2;

/// Conversion timeout exceeded
pub const ERROR_TIMEOUT: u32 = 3;

/// Memory limit exceeded during conversion
pub const ERROR_MEMORY_LIMIT: u32 = 4;

/// Invalid input data (NULL pointers, invalid parameters)
pub const ERROR_INVALID_INPUT: u32 = 5;

/// Internal error (unexpected condition, panic caught)
pub const ERROR_INTERNAL: u32 = 99;

// ============================================================================
// FFI Data Structures
// ============================================================================

/// Conversion options passed from C to Rust
///
/// This structure controls the behavior of the HTML to Markdown conversion.
/// All fields use C-compatible types and layout.
///
/// # C Compatibility
///
/// - `#[repr(C)]` ensures C-compatible memory layout
/// - All fields use fixed-size types (u32, u8)
/// - No padding or alignment issues across the boundary
///
/// # Field Descriptions
///
/// - `flavor`: Markdown output format
///   - 0 = CommonMark (default, well-specified baseline)
///   - 1 = GitHub Flavored Markdown (GFM, adds tables, task lists, etc.)
///
/// - `timeout_ms`: Maximum conversion time in milliseconds
///   - 0 = no timeout (not recommended)
///   - Typical value: 5000 (5 seconds)
///   - Cooperative timeout (checks periodically, doesn't spawn threads)
///
/// - `generate_etag`: Whether to generate ETag for caching
///   - 0 = no ETag generation (faster)
///   - 1 = generate ETag via BLAKE3 hash of output
///
/// - `estimate_tokens`: Whether to estimate token count for LLMs
///   - 0 = no estimation
///   - 1 = estimate using character count / 4 heuristic
///
/// - `front_matter`: Whether to include YAML front matter with metadata
///   - 0 = no front matter
///   - 1 = include front matter (title, description, etc.)
///
/// - `content_type`: Optional Content-Type header value for charset detection
///   - Pointer to UTF-8 string (e.g., "text/html; charset=UTF-8")
///   - NULL if not available
///   - If pointer is NULL, `content_type_len` must be 0
///   - Used for charset detection cascade (FR-05.1)
///
/// - `content_type_len`: Length of content_type string in bytes
///   - 0 if content_type is NULL
///
/// - `base_url`: Optional base URL for resolving relative URLs in HTML
///   - Pointer to UTF-8 string (e.g., "https://example.com/page")
///   - NULL if not available
///   - If pointer is NULL, `base_url_len` must be 0
///   - Used for metadata extraction and URL resolution
///   - Format: scheme://host/path (e.g., "https://example.com/docs/page.html")
///
/// - `base_url_len`: Length of base_url string in bytes
///   - 0 if base_url is NULL
///
/// # Example Usage (C)
///
/// ```c
/// // Without Content-Type or base_url
/// markdown_options_t options = {
///     .flavor = 0,              // CommonMark
///     .timeout_ms = 5000,       // 5 second timeout
///     .generate_etag = 1,       // Generate ETag
///     .estimate_tokens = 1,     // Estimate tokens
///     .front_matter = 0,        // No front matter
///     .content_type = NULL,     // No Content-Type
///     .content_type_len = 0,
///     .base_url = NULL,         // No base URL
///     .base_url_len = 0
/// };
///
/// // With Content-Type and base_url
/// const char *ct = "text/html; charset=UTF-8";
/// const char *base = "https://example.com/page";
/// markdown_options_t options = {
///     .flavor = 0,
///     .timeout_ms = 5000,
///     .generate_etag = 1,
///     .estimate_tokens = 1,
///     .front_matter = 0,
///     .content_type = (const uint8_t*)ct,
///     .content_type_len = strlen(ct),
///     .base_url = (const uint8_t*)base,
///     .base_url_len = strlen(base)
/// };
/// ```
///
/// # Safety
///
/// This struct is safe to pass across the FFI boundary as it contains only
/// plain data types with no pointers or complex ownership.
#[repr(C)]
pub struct MarkdownOptions {
    /// Markdown flavor: 0=CommonMark, 1=GFM
    pub flavor: u32,
    /// Conversion timeout in milliseconds (0=no timeout)
    pub timeout_ms: u32,
    /// Generate ETag: 0=no, 1=yes
    pub generate_etag: u8,
    /// Estimate tokens: 0=no, 1=yes
    pub estimate_tokens: u8,
    /// Include YAML front matter: 0=no, 1=yes
    pub front_matter: u8,
    /// Content-Type header value for charset detection (UTF-8 bytes, can be NULL)
    pub content_type: *const u8,
    /// Length of content_type in bytes (0 if NULL)
    pub content_type_len: usize,
    /// Base URL for resolving relative URLs (UTF-8 bytes, can be NULL)
    pub base_url: *const u8,
    /// Length of base_url in bytes (0 if NULL)
    pub base_url_len: usize,
}

/// Conversion result returned from Rust to C
///
/// This structure contains the output of HTML to Markdown conversion,
/// including the converted content, metadata, and any errors.
///
/// # CRITICAL: String Representation
///
/// **All string fields use UTF-8 bytes + length (NOT NUL-terminated)**
///
/// String fields follow this pattern:
/// - `markdown`: `*mut u8` pointer to UTF-8 bytes
/// - `markdown_len`: `usize` byte count (NOT including NUL)
///
/// **C code MUST:**
/// - Use the `_len` fields for all string operations
/// - NOT use `strlen()` on these pointers
/// - NOT assume NUL termination
///
/// # Memory Ownership
///
/// **Rust owns all allocated memory:**
/// - On success, `markdown` and `etag` point to Rust-allocated memory
/// - C receives pointers but does NOT own the memory
/// - C MUST call `markdown_result_free()` exactly once to deallocate
/// - After calling free, all pointers become invalid
/// - Do NOT call `free()` directly on these pointers
///
/// # Field Descriptions
///
/// ## Output Fields (valid on success)
///
/// - `markdown`: Pointer to UTF-8 Markdown output bytes
/// - `markdown_len`: Byte length of Markdown output
/// - `etag`: Pointer to UTF-8 ETag string (NULL if not generated)
/// - `etag_len`: Byte length of ETag (0 if NULL)
/// - `token_estimate`: Estimated token count for LLM context windows
///
/// ## Error Fields (valid on error)
///
/// - `error_code`: Error code constant (0 = success, see ERROR_* constants)
/// - `error_message`: Pointer to UTF-8 error description (NULL on success)
/// - `error_len`: Byte length of error message (0 if NULL)
///
/// # State Invariants
///
/// **Success State (error_code == 0):**
/// - `markdown` is non-NULL and points to valid UTF-8 bytes
/// - `markdown_len` > 0
/// - `etag` may be NULL or point to valid UTF-8 bytes
/// - `etag_len` matches etag content (0 if etag is NULL)
/// - `token_estimate` contains estimated count (or 0 if not requested)
/// - `error_message` is NULL
/// - `error_len` is 0
///
/// **Error State (error_code != 0):**
/// - `markdown` is NULL
/// - `markdown_len` is 0
/// - `etag` is NULL
/// - `etag_len` is 0
/// - `token_estimate` is 0
/// - `error_message` is non-NULL and points to valid UTF-8 bytes
/// - `error_len` > 0
///
/// # Example Usage (C)
///
/// ```c
/// markdown_result_t result;
/// markdown_convert(converter, html, html_len, &options, &result);
///
/// if (result.error_code == 0) {
///     // Success - process markdown
///     for (size_t i = 0; i < result.markdown_len; i++) {
///         process_byte(result.markdown[i]);
///     }
///     
///     // Check for optional ETag
///     if (result.etag != NULL) {
///         set_etag_header(result.etag, result.etag_len);
///     }
///     
///     // Use token estimate
///     log_tokens(result.token_estimate);
/// } else {
///     // Error - log error message
///     log_error(result.error_code, result.error_message, result.error_len);
/// }
///
/// // Always free result
/// markdown_result_free(&result);
/// ```
///
/// # Safety
///
/// This struct is safe to pass across the FFI boundary, but the pointers
/// it contains require careful handling:
/// - All pointers must be validated before dereferencing
/// - Memory must be freed via `markdown_result_free()`
/// - Pointers become invalid after free
/// - Do not mix Rust and C memory allocators
#[repr(C)]
pub struct MarkdownResult {
    /// Output Markdown (UTF-8 bytes, NOT NUL-terminated)
    /// NULL on error, non-NULL on success
    pub markdown: *mut u8,

    /// Length of markdown in bytes (NOT including NUL)
    /// 0 on error, >0 on success
    pub markdown_len: usize,

    /// ETag string (UTF-8 bytes, optional, NULL if not generated)
    /// NULL if not requested or on error
    pub etag: *mut u8,

    /// Length of etag in bytes
    /// 0 if etag is NULL
    pub etag_len: usize,

    /// Estimated token count for LLM context windows
    /// 0 if not requested or on error
    pub token_estimate: u32,

    /// Error code: 0=success, non-zero=error (see ERROR_* constants)
    pub error_code: u32,

    /// Error message (UTF-8 bytes, NULL if success)
    /// Non-NULL on error, NULL on success
    pub error_message: *mut u8,

    /// Length of error message in bytes
    /// 0 if error_message is NULL
    pub error_len: usize,
}

/// Opaque handle to Rust converter instance
///
/// This is an opaque type that hides the internal Rust implementation
/// from C code. C code receives a pointer to this type but cannot
/// access its internals.
///
/// # Lifecycle
///
/// 1. Create: `markdown_converter_new()` returns a handle
/// 2. Use: Pass handle to `markdown_convert()` for conversions
/// 3. Destroy: `markdown_converter_free()` deallocates the handle
///
/// # Thread Safety
///
/// **NOT thread-safe** - Each NGINX worker should have its own instance.
/// Do not share handles across threads or concurrent requests.
///
/// # Example Usage (C)
///
/// ```c
/// // Create converter
/// markdown_converter_t *converter = markdown_converter_new();
/// if (converter == NULL) {
///     // Handle allocation failure
///     return;
/// }
///
/// // Use converter for multiple conversions
/// markdown_result_t result1, result2;
/// markdown_convert(converter, html1, len1, &options, &result1);
/// markdown_convert(converter, html2, len2, &options, &result2);
///
/// // Clean up results
/// markdown_result_free(&result1);
/// markdown_result_free(&result2);
///
/// // Destroy converter
/// markdown_converter_free(converter);
/// ```
pub struct MarkdownConverterHandle {
    etag_generator: ETagGenerator,
    token_estimator: TokenEstimator,
}

struct ConversionOutput {
    markdown: Box<[u8]>,
    etag: Option<Box<[u8]>>,
    token_estimate: u32,
}

fn reset_result(result: &mut MarkdownResult) {
    result.markdown = ptr::null_mut();
    result.markdown_len = 0;
    result.etag = ptr::null_mut();
    result.etag_len = 0;
    result.token_estimate = 0;
    result.error_code = ERROR_SUCCESS;
    result.error_message = ptr::null_mut();
    result.error_len = 0;
}

fn set_error_result(result: &mut MarkdownResult, error_code: u32, error_message: String) {
    let error_bytes = error_message.into_bytes().into_boxed_slice();
    result.error_code = error_code;
    result.error_len = error_bytes.len();
    result.error_message = Box::into_raw(error_bytes) as *mut u8;
}

fn set_success_result(result: &mut MarkdownResult, output: ConversionOutput) {
    result.markdown_len = output.markdown.len();
    result.markdown = Box::into_raw(output.markdown) as *mut u8;
    result.token_estimate = output.token_estimate;
    result.error_code = ERROR_SUCCESS;
    result.error_message = ptr::null_mut();
    result.error_len = 0;

    if let Some(etag_bytes) = output.etag {
        result.etag_len = etag_bytes.len();
        result.etag = Box::into_raw(etag_bytes) as *mut u8;
    } else {
        result.etag = ptr::null_mut();
        result.etag_len = 0;
    }
}

fn required_ref<'a, T>(ptr: *const T, name: &str) -> Result<&'a T, ConversionError> {
    if ptr.is_null() {
        return Err(ConversionError::InvalidInput(format!(
            "{name} pointer is NULL"
        )));
    }

    // SAFETY: Caller provided a non-NULL pointer and accepts FFI contract
    // that this points to a valid, properly aligned value.
    Ok(unsafe { &*ptr })
}

fn required_bytes<'a>(ptr: *const u8, len: usize, name: &str) -> Result<&'a [u8], ConversionError> {
    if len == 0 {
        return Ok(&[]);
    }

    if ptr.is_null() {
        return Err(ConversionError::InvalidInput(format!(
            "{name} pointer is NULL"
        )));
    }

    // SAFETY: Pointer was validated as non-NULL above; caller guarantees `len`
    // bytes are valid and readable for the duration of this call.
    Ok(unsafe { slice::from_raw_parts(ptr, len) })
}

fn optional_utf8<'a>(
    ptr: *const u8,
    len: usize,
    field_name: &str,
) -> Result<Option<&'a str>, ConversionError> {
    if len == 0 {
        return Ok(None);
    }

    if ptr.is_null() {
        return Err(ConversionError::InvalidInput(format!(
            "{field_name}_len > 0 with NULL {field_name} pointer"
        )));
    }

    // SAFETY: Pointer is non-NULL and caller guarantees `len` readable bytes.
    let bytes = unsafe { slice::from_raw_parts(ptr, len) };

    Ok(std::str::from_utf8(bytes).ok())
}

fn convert_inner(
    handle_ref: &MarkdownConverterHandle,
    html_slice: &[u8],
    options_ref: &MarkdownOptions,
) -> Result<ConversionOutput, ConversionError> {
    let content_type_str = optional_utf8(
        options_ref.content_type,
        options_ref.content_type_len,
        "content_type",
    )?;
    let base_url_str = optional_utf8(options_ref.base_url, options_ref.base_url_len, "base_url")?
        .map(ToOwned::to_owned);

    if html_slice.is_empty() {
        let markdown_bytes = Box::<[u8]>::default();
        let token_estimate = if options_ref.estimate_tokens != 0 {
            handle_ref.token_estimator.estimate("")
        } else {
            0
        };

        let etag_bytes = if options_ref.generate_etag != 0 {
            Some(
                handle_ref
                    .etag_generator
                    .generate(markdown_bytes.as_ref())
                    .into_bytes()
                    .into_boxed_slice(),
            )
        } else {
            None
        };

        return Ok(ConversionOutput {
            markdown: markdown_bytes,
            etag: etag_bytes,
            token_estimate,
        });
    }

    // Parse HTML with charset detection cascade (FR-05.1, FR-05.2, FR-05.3)
    let dom = parse_html_with_charset(html_slice, content_type_str)?;

    // Create conversion context with timeout
    let timeout_ms = options_ref.timeout_ms;
    let timeout_duration = if timeout_ms > 0 {
        Duration::from_millis(timeout_ms as u64)
    } else {
        Duration::ZERO // No timeout
    };
    let mut ctx = ConversionContext::new(timeout_duration);

    // Check timeout after parsing
    ctx.check_timeout()?;

    // Build conversion options
    let flavor = match options_ref.flavor {
        1 => MarkdownFlavor::GitHubFlavoredMarkdown,
        _ => MarkdownFlavor::CommonMark,
    };

    let resolve_relative_urls = base_url_str.is_some();
    let conv_options = ConversionOptions {
        flavor,
        include_front_matter: options_ref.front_matter != 0,
        extract_metadata: options_ref.front_matter != 0,
        simplify_navigation: true,
        preserve_tables: true,
        base_url: base_url_str,
        resolve_relative_urls,
    };

    // Create converter and perform conversion with timeout support
    let converter = MarkdownConverter::with_options(conv_options);
    let markdown = converter.convert_with_context(&dom, &mut ctx)?;

    // Estimate tokens while Markdown is still in String form (avoids reconstructing
    // a UTF-8 view from raw bytes after allocation).
    let token_estimate = if options_ref.estimate_tokens != 0 {
        handle_ref.token_estimator.estimate(&markdown)
    } else {
        0
    };

    let markdown_bytes = markdown.into_bytes().into_boxed_slice();
    let etag_bytes = if options_ref.generate_etag != 0 {
        Some(
            handle_ref
                .etag_generator
                .generate(markdown_bytes.as_ref())
                .into_bytes()
                .into_boxed_slice(),
        )
    } else {
        None
    };

    Ok(ConversionOutput {
        markdown: markdown_bytes,
        etag: etag_bytes,
        token_estimate,
    })
}

fn free_buffer(ptr_field: &mut *mut u8, len_field: &mut usize) {
    if (*ptr_field).is_null() {
        return;
    }

    let raw = ptr::slice_from_raw_parts_mut(*ptr_field, *len_field);
    // SAFETY: `raw` was allocated by `Box<[u8]>` via `Box::into_raw`.
    let _ = unsafe { Box::from_raw(raw) };
    *ptr_field = ptr::null_mut();
    *len_field = 0;
}

// ============================================================================
// FFI Functions
// ============================================================================

/// Create a new converter instance
///
/// Allocates and initializes a new Markdown converter that can be used
/// for multiple conversion operations.
///
/// # Returns
///
/// - Non-NULL pointer to `MarkdownConverterHandle` on success
/// - NULL on allocation failure
///
/// # Memory Management
///
/// The returned handle is owned by the caller and must be freed by calling
/// `markdown_converter_free()` exactly once when no longer needed.
///
/// # Thread Safety
///
/// This function is thread-safe. Multiple threads can create their own
/// converter instances concurrently. However, the returned handle is NOT
/// thread-safe and should not be shared across threads.
///
/// # Example (C)
///
/// ```c
/// markdown_converter_t *converter = markdown_converter_new();
/// if (converter == NULL) {
///     fprintf(stderr, "Failed to create converter\n");
///     return -1;
/// }
/// // Use converter...
/// markdown_converter_free(converter);
/// ```
///
/// # Safety
///
/// This function is safe to call from C code. It performs no pointer
/// dereferencing and handles all allocation failures gracefully.
#[unsafe(no_mangle)]
pub extern "C" fn markdown_converter_new() -> *mut MarkdownConverterHandle {
    // Catch any panics to prevent unwinding into C code
    let result = panic::catch_unwind(|| {
        let handle = MarkdownConverterHandle {
            etag_generator: ETagGenerator::new(),
            token_estimator: TokenEstimator::new(),
        };

        // Allocate on heap and return raw pointer
        Box::into_raw(Box::new(handle))
    });

    match result {
        Ok(ptr) => ptr,
        Err(_) => {
            // Panic occurred during initialization
            // Return NULL to indicate failure
            ptr::null_mut()
        }
    }
}

/// Perform HTML to Markdown conversion
///
/// Converts HTML content to Markdown format according to the provided options.
/// This is the main conversion function that C code calls to transform HTML.
///
/// # Parameters
///
/// - `handle`: Pointer to converter instance from `markdown_converter_new()`
///   - Must be non-NULL
///   - Must be a valid handle (not freed)
///   - Must not be used concurrently from multiple threads
///
/// - `html`: Pointer to HTML input bytes
///   - Must be non-NULL when `html_len > 0`
///   - Must point to valid memory of at least `html_len` bytes
///   - May be NULL when `html_len == 0`
///   - Should be valid UTF-8 (invalid UTF-8 will cause encoding error)
///   - Content is not modified (read-only)
///
/// - `html_len`: Length of HTML input in bytes
///   - Must accurately reflect the size of the html buffer
///   - Can be 0 (will result in empty output)
///
/// - `options`: Pointer to conversion options
///   - Must be non-NULL
///   - Must point to valid `MarkdownOptions` struct
///   - Content is not modified (read-only)
///
/// - `result`: Pointer to result structure to populate
///   - Must be non-NULL
///   - Must point to valid `MarkdownResult` struct
///   - Previous contents are overwritten
///   - Caller must call `markdown_result_free()` after use
///
/// # Behavior
///
/// **On Success:**
/// - `result->error_code` is set to 0
/// - `result->markdown` points to allocated Markdown bytes
/// - `result->markdown_len` contains byte length
/// - `result->etag` may be set if requested
/// - `result->token_estimate` may be set if requested
/// - `result->error_message` is NULL
///
/// **On Error:**
/// - `result->error_code` is set to non-zero error code
/// - `result->error_message` points to error description
/// - `result->error_len` contains error message length
/// - All output fields (markdown, etag) are NULL
///
/// # Error Codes
///
/// - `ERROR_INVALID_INPUT` (5): NULL pointer or invalid parameter
/// - `ERROR_PARSE` (1): HTML parsing failed
/// - `ERROR_ENCODING` (2): Character encoding error
/// - `ERROR_TIMEOUT` (3): Conversion exceeded timeout
/// - `ERROR_MEMORY_LIMIT` (4): Memory limit exceeded
/// - `ERROR_INTERNAL` (99): Internal error or panic caught
///
/// # Memory Management
///
/// **Input Memory:**
/// - Caller owns `html` and `options` memory
/// - Function does not modify or free input memory
/// - Input memory can be freed after function returns
///
/// **Output Memory:**
/// - Function allocates memory for output fields
/// - Caller must call `markdown_result_free()` to deallocate
/// - Do NOT call `free()` directly on result pointers
///
/// # Panic Safety
///
/// This function uses `catch_unwind` to prevent Rust panics from unwinding
/// into C code. Any panic is caught and converted to `ERROR_INTERNAL`.
///
/// # Thread Safety
///
/// This function is NOT thread-safe with respect to the same `handle`.
/// Do not call this function concurrently on the same handle from multiple
/// threads. Each thread should have its own converter instance.
///
/// # Example (C)
///
/// ```c
/// markdown_converter_t *converter = markdown_converter_new();
/// markdown_options_t options = {
///     .flavor = 0,
///     .timeout_ms = 5000,
///     .generate_etag = 1,
///     .estimate_tokens = 1,
///     .front_matter = 0
/// };
///
/// markdown_result_t result;
/// markdown_convert(converter, html, html_len, &options, &result);
///
/// if (result.error_code == 0) {
///     // Success - use result.markdown
///     send_response(result.markdown, result.markdown_len);
/// } else {
///     // Error - log error message
///     log_error(result.error_code, result.error_message, result.error_len);
/// }
///
/// markdown_result_free(&result);
/// markdown_converter_free(converter);
/// ```
///
/// # Safety
///
/// **Pointer Validation:**
/// - All pointers are validated for NULL before dereferencing
/// - NULL pointers result in `ERROR_INVALID_INPUT`
/// - Invalid (non-NULL but bad) pointers cause undefined behavior
///
/// **Memory Safety:**
/// - No buffer overflows (uses Rust's bounds checking)
/// - No use-after-free (ownership model prevents it)
/// - No double-free (memory freed exactly once via result_free)
///
/// **Undefined Behavior:**
/// - Passing invalid (non-NULL but bad) pointers is undefined behavior
/// - Using a freed handle is undefined behavior
/// - Concurrent calls on same handle is undefined behavior
/// - Not calling `markdown_result_free()` causes memory leak (not UB)
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_convert(
    handle: *mut MarkdownConverterHandle,
    html: *const u8,
    html_len: usize,
    options: *const MarkdownOptions,
    result: *mut MarkdownResult,
) {
    // Validate result pointer first so we can report errors.
    if result.is_null() {
        // Cannot report error if result pointer is NULL.
        return;
    }

    // SAFETY: `result` was validated as non-NULL above.
    let result_ref = unsafe { &mut *result };
    reset_result(result_ref);

    // Catch any panics to prevent unwinding into C code.
    let panic_result = panic::catch_unwind(|| -> Result<ConversionOutput, ConversionError> {
        let handle_ref = required_ref(handle.cast_const(), "Converter handle")?;
        let options_ref = required_ref(options, "Options")?;
        let html_slice = required_bytes(html, html_len, "HTML")?;
        convert_inner(handle_ref, html_slice, options_ref)
    });

    // Handle panic or error.
    match panic_result {
        Ok(Ok(output)) => set_success_result(result_ref, output),
        Ok(Err(e)) => {
            set_error_result(result_ref, e.code(), e.to_string());
        }
        Err(_) => {
            set_error_result(
                result_ref,
                ERROR_INTERNAL,
                "Internal panic during conversion".to_string(),
            );
        }
    }
}

/// Free memory allocated by conversion result
///
/// Deallocates all memory associated with a `MarkdownResult` structure.
/// This function must be called exactly once for each successful or failed
/// conversion to prevent memory leaks.
///
/// # Parameters
///
/// - `result`: Pointer to result structure to free
///   - Must be non-NULL
///   - Must point to valid `MarkdownResult` struct
///   - Must have been populated by `markdown_convert()`
///   - Must not have been freed previously
///
/// # Behavior
///
/// This function:
/// 1. Frees `markdown` memory if non-NULL
/// 2. Frees `etag` memory if non-NULL
/// 3. Frees `error_message` memory if non-NULL
/// 4. Sets all pointers to NULL
/// 5. Sets all lengths to 0
///
/// After calling this function, all pointers in the result become invalid
/// and must not be dereferenced.
///
/// # Idempotency
///
/// This function is safe to call multiple times on the same result
/// (though not recommended). After the first call, all pointers are NULL,
/// so subsequent calls are no-ops.
///
/// # Memory Management
///
/// **CRITICAL:** This function uses Rust's memory allocator to free memory.
/// Do NOT call C's `free()` on these pointers - it will cause undefined
/// behavior due to allocator mismatch.
///
/// **Memory Deallocation Strategy:**
/// - `markdown` and `etag`: Allocated as `Box<[u8]>`, freed using `Box::from_raw(slice::from_raw_parts_mut())`
/// - `error_message`: Allocated as `Box<[u8]>`, freed using `Box::from_raw(slice::from_raw_parts_mut())`
/// - **NEVER use CString::from_raw() for markdown/etag fields** - they are NOT NUL-terminated C strings
///
/// # Example (C)
///
/// ```c
/// markdown_result_t result;
/// markdown_convert(converter, html, html_len, &options, &result);
///
/// // Use result...
/// if (result.error_code == 0) {
///     process_markdown(result.markdown, result.markdown_len);
/// }
///
/// // Always free, even on error
/// markdown_result_free(&result);
///
/// // After free, pointers are invalid
/// // result.markdown is now NULL and must not be used
/// ```
///
/// # Safety
///
/// **Pointer Validation:**
/// - NULL `result` pointer is handled gracefully (no-op)
/// - NULL pointers within result are handled gracefully (no-op)
///
/// **Memory Safety:**
/// - Uses Rust's `Box::from_raw()` to reconstruct and drop allocations
/// - Prevents double-free by setting pointers to NULL after freeing
/// - Safe to call multiple times (idempotent)
///
/// **Undefined Behavior:**
/// - Passing invalid (non-NULL but bad) pointer is undefined behavior
/// - Freeing a result that wasn't populated by `markdown_convert()` is UB
/// - Mixing Rust and C allocators (calling C `free()`) is UB
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_result_free(result: *mut MarkdownResult) {
    if result.is_null() {
        return;
    }

    // SAFETY: `result` was validated as non-NULL above.
    let result_ref = unsafe { &mut *result };
    free_buffer(&mut result_ref.markdown, &mut result_ref.markdown_len);
    free_buffer(&mut result_ref.etag, &mut result_ref.etag_len);
    // NOTE: We do NOT use CString::from_raw() because error_message
    // is NOT a NUL-terminated C string - it's UTF-8 bytes with length.
    free_buffer(&mut result_ref.error_message, &mut result_ref.error_len);
    result_ref.token_estimate = 0;
    result_ref.error_code = 0;
}

/// Destroy converter instance
///
/// Deallocates a converter instance created by `markdown_converter_new()`.
/// This function must be called exactly once when the converter is no longer
/// needed to prevent memory leaks.
///
/// # Parameters
///
/// - `handle`: Pointer to converter instance
///   - Must be non-NULL
///   - Must be a valid handle from `markdown_converter_new()`
///   - Must not have been freed previously
///   - Must not be in use by concurrent operations
///
/// # Behavior
///
/// This function:
/// 1. Deallocates the converter instance
/// 2. Invalidates the handle pointer
///
/// After calling this function, the handle pointer becomes invalid and
/// must not be used for any further operations.
///
/// # Lifecycle
///
/// This is the final step in the converter lifecycle:
/// 1. Create: `markdown_converter_new()`
/// 2. Use: `markdown_convert()` (can be called multiple times)
/// 3. Destroy: `markdown_converter_free()` (call once)
///
/// # Memory Management
///
/// **CRITICAL:** This function uses Rust's memory allocator to free memory.
/// Do NOT call C's `free()` on the handle - it will cause undefined
/// behavior due to allocator mismatch.
///
/// # Example (C)
///
/// ```c
/// markdown_converter_t *converter = markdown_converter_new();
///
/// // Use converter for multiple conversions
/// markdown_result_t result1, result2;
/// markdown_convert(converter, html1, len1, &options, &result1);
/// markdown_convert(converter, html2, len2, &options, &result2);
///
/// // Free results
/// markdown_result_free(&result1);
/// markdown_result_free(&result2);
///
/// // Destroy converter
/// markdown_converter_free(converter);
///
/// // After free, converter pointer is invalid
/// // Do not use converter for any further operations
/// ```
///
/// # Safety
///
/// **Pointer Validation:**
/// - NULL handle is handled gracefully (no-op)
///
/// **Memory Safety:**
/// - Uses Rust's `Box::from_raw()` to reconstruct and drop allocation
/// - Prevents double-free (caller responsibility to call only once)
///
/// **Undefined Behavior:**
/// - Passing invalid (non-NULL but bad) pointer is undefined behavior
/// - Freeing a handle that wasn't created by `markdown_converter_new()` is UB
/// - Using handle after free is undefined behavior
/// - Freeing handle while conversion is in progress is undefined behavior
/// - Mixing Rust and C allocators (calling C `free()`) is UB
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_converter_free(handle: *mut MarkdownConverterHandle) {
    if handle.is_null() {
        return;
    }

    // SAFETY: `handle` was validated as non-NULL above and was originally
    // created by `Box::into_raw` in `markdown_converter_new`.
    unsafe { drop(Box::from_raw(handle)) };
}
