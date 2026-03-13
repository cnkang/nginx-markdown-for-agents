use crate::etag_generator::ETagGenerator;
use crate::token_estimator::TokenEstimator;

/// Success - no error occurred.
pub const ERROR_SUCCESS: u32 = 0;
/// HTML parsing failed (malformed HTML, invalid structure).
pub const ERROR_PARSE: u32 = 1;
/// Character encoding error (invalid UTF-8, unsupported charset).
pub const ERROR_ENCODING: u32 = 2;
/// Conversion timeout exceeded.
pub const ERROR_TIMEOUT: u32 = 3;
/// Memory limit exceeded during conversion.
pub const ERROR_MEMORY_LIMIT: u32 = 4;
/// Invalid input data (NULL pointers, invalid parameters).
pub const ERROR_INVALID_INPUT: u32 = 5;
/// Internal error (unexpected condition, panic caught).
pub const ERROR_INTERNAL: u32 = 99;

/// Conversion options passed from C to Rust.
#[repr(C)]
pub struct MarkdownOptions {
    pub flavor: u32,
    pub timeout_ms: u32,
    pub generate_etag: u8,
    pub estimate_tokens: u8,
    pub front_matter: u8,
    pub content_type: *const u8,
    pub content_type_len: usize,
    pub base_url: *const u8,
    pub base_url_len: usize,
}

/// Conversion result returned from Rust to C.
#[repr(C)]
pub struct MarkdownResult {
    pub markdown: *mut u8,
    pub markdown_len: usize,
    pub etag: *mut u8,
    pub etag_len: usize,
    pub token_estimate: u32,
    pub error_code: u32,
    pub error_message: *mut u8,
    pub error_len: usize,
}

/// Opaque handle to Rust converter state shared across conversions.
pub struct MarkdownConverterHandle {
    pub(crate) etag_generator: ETagGenerator,
    pub(crate) token_estimator: TokenEstimator,
}

impl MarkdownConverterHandle {
    /// Build a reusable converter handle for repeated FFI calls.
    pub(crate) fn new() -> Self {
        Self {
            etag_generator: ETagGenerator::new(),
            token_estimator: TokenEstimator::new(),
        }
    }
}

pub(crate) struct ConversionOutput {
    pub(crate) markdown: Box<[u8]>,
    pub(crate) etag: Option<Box<[u8]>>,
    pub(crate) token_estimate: u32,
}
