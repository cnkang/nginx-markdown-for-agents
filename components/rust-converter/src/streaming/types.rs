//! Shared types for the streaming conversion pipeline.

/// Token event produced by the html5ever TokenSink adapter.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StreamEvent {
    /// Opening HTML tag with attributes.
    StartTag {
        name: String,
        attrs: Vec<(String, String)>,
        self_closing: bool,
    },
    /// Closing HTML tag.
    EndTag { name: String },
    /// Text content.
    Text(String),
    /// HTML comment (typically ignored).
    Comment(String),
    /// DOCTYPE declaration (ignored).
    Doctype,
    /// Parse error from html5ever (logged, not fatal).
    ParseError(String),
}

/// Tracks whether the converter has emitted any Markdown output.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommitState {
    /// No Markdown output has been delivered to the caller yet.
    PreCommit,
    /// At least one Markdown chunk has been delivered.
    PostCommit,
}

/// Reason for falling back from streaming to full-buffer conversion.
#[derive(Debug, Clone)]
pub enum FallbackReason {
    /// A `<table>` element was detected.
    TableDetected,
    /// The lookahead buffer exceeded its budget.
    LookaheadExceeded,
    /// Front matter extraction requires data beyond the lookahead budget.
    FrontMatterOverflow,
    /// An unsupported HTML structure/capability was encountered.
    UnsupportedStructure(String),
}

impl std::fmt::Display for FallbackReason {
    /// Format a `FallbackReason` into a concise, human-readable message.
    ///
    /// This implementation converts each `FallbackReason` variant into a short
    /// descriptive string (for example, `TableDetected` → `"table element detected"`).
    ///
    /// # Examples
    ///
    /// ```
    /// use components::rust_converter::streaming::types::FallbackReason;
    /// let r = FallbackReason::UnsupportedStructure("nested table".into());
    /// assert_eq!(format!("{}", r), "unsupported structure: nested table");
    /// ```
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::TableDetected => write!(f, "table element detected"),
            Self::LookaheadExceeded => write!(f, "lookahead buffer exceeded budget"),
            Self::FrontMatterOverflow => write!(f, "front matter data exceeds lookahead budget"),
            Self::UnsupportedStructure(s) => write!(f, "unsupported structure: {}", s),
        }
    }
}

/// Output from a single `feed_chunk` call.
#[derive(Debug, Clone)]
pub struct ChunkOutput {
    /// Ready Markdown bytes (may be empty if no flush point was reached).
    pub markdown: Vec<u8>,
    /// Number of flush points produced during this chunk.
    pub flush_count: u32,
}

/// Final result returned by `finalize`.
#[derive(Debug, Clone)]
pub struct StreamingResult {
    /// Remaining Markdown bytes flushed during finalization.
    pub final_markdown: Vec<u8>,
    /// Total estimated token count (if token estimation is enabled).
    pub token_estimate: Option<u32>,
    /// ETag string (if ETag generation is enabled).
    pub etag: Option<String>,
    /// Conversion statistics.
    pub stats: StreamingStats,
}

/// Conversion statistics collected during streaming.
#[derive(Debug, Clone, Default)]
pub struct StreamingStats {
    /// Total number of HTML tokens processed.
    pub tokens_processed: u64,
    /// Total number of flush points emitted.
    pub flush_count: u32,
    /// Peak estimated memory usage in bytes.
    pub peak_memory_estimate: usize,
    /// Number of input chunks processed.
    pub chunks_processed: u32,
}
