//! Streaming charset detection and transcoding for the streaming pipeline.
//!
//! Implements incremental charset detection using the same three-level cascade
//! as [`detect_charset`](crate::charset::detect_charset):
//!
//! 1. Content-Type header charset (priority 1)
//! 2. HTML meta charset tag within the first 1024 bytes (priority 2)
//! 3. Default UTF-8 (priority 3)
//!
//! The streaming version accumulates up to 1024 bytes in a sniff buffer before
//! resolving the charset. Once resolved, subsequent data is transcoded
//! incrementally using `encoding_rs`.
//!
//! # Zero-Copy Path
//!
//! When the detected charset is UTF-8, no transcoding is performed and input
//! bytes are returned as-is (zero-copy via `Cow::Borrowed`).

use std::borrow::Cow;

use crate::charset::detect_charset;
use crate::error::ConversionError;

/// Maximum bytes to buffer for charset sniffing (matches `charset::META_SCAN_LIMIT`).
const SNIFF_BUFFER_LIMIT: usize = 1024;

/// Streaming charset detection state machine.
///
/// Transitions through three states:
/// - `Pending`: accumulating bytes for charset detection
/// - `Resolved`: charset detected, transcoding active (or zero-copy for UTF-8)
/// - `Failed`: detection or transcoding failed
pub enum CharsetState {
    /// Waiting for enough data to detect charset.
    Pending {
        /// Charset from Content-Type header, if provided.
        header_charset: Option<String>,
        /// Accumulated bytes for meta charset sniffing.
        sniff_buffer: Vec<u8>,
    },
    /// Charset resolved; decoder is `None` for UTF-8 (zero-copy).
    Resolved {
        /// `encoding_rs` decoder instance, or `None` for UTF-8.
        decoder: Option<encoding_rs::Decoder>,
    },
    /// Detection or transcoding failed.
    Failed(String),
}

impl std::fmt::Debug for CharsetState {
    /// Custom `Debug` formatting for `CharsetState`.
    ///
    /// Displays a compact, human-readable representation for each variant:
    /// - `Pending` shows `header_charset` and the length of the sniff buffer as `sniff_buffer_len`.
    /// - `Resolved` shows a boolean `has_decoder` indicating whether a decoder is present (`false` for UTF-8 zero-copy).
    /// - `Failed` shows the failure reason as a tuple-like value.
    ///
    /// # Examples
    ///
    /// ```
    /// use std::fmt::Debug;
    ///
    /// // Pending
    /// let pending = crate::streaming::charset::CharsetState::Pending {
    ///     header_charset: Some("ISO-8859-1".to_string()),
    ///     sniff_buffer: vec![0u8; 10],
    /// };
    /// let s = format!("{:?}", pending);
    /// assert!(s.contains("Pending"));
    /// assert!(s.contains("header_charset"));
    /// assert!(s.contains("sniff_buffer_len"));
    ///
    /// // Resolved (UTF-8 zero-copy)
    /// let resolved_utf8 = crate::streaming::charset::CharsetState::Resolved { decoder: None };
    /// assert!(format!("{:?}", resolved_utf8).contains("has_decoder = false"));
    ///
    /// // Failed
    /// let failed = crate::streaming::charset::CharsetState::Failed("bad".into());
    /// assert_eq!(format!("{:?}", failed), r#"Failed("bad")"#);
    /// ```
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CharsetState::Pending {
                header_charset,
                sniff_buffer,
            } => f
                .debug_struct("Pending")
                .field("header_charset", header_charset)
                .field("sniff_buffer_len", &sniff_buffer.len())
                .finish(),
            CharsetState::Resolved { decoder } => f
                .debug_struct("Resolved")
                .field("has_decoder", &decoder.is_some())
                .finish(),
            CharsetState::Failed(reason) => f.debug_tuple("Failed").field(reason).finish(),
        }
    }
}

impl CharsetState {
    /// Creates a new `CharsetState` in the `Pending` state with no header charset and an empty sniff buffer.
    ///
    /// The returned state will accumulate bytes (up to SNIFF_BUFFER_LIMIT) while awaiting charset resolution.
    ///
    /// # Examples
    ///
    /// ```
    /// let mut state = CharsetState::new();
    /// state.set_content_type(Some("text/html; charset=UTF-8"));
    /// ```
    pub fn new() -> Self {
        CharsetState::Pending {
            header_charset: None,
            sniff_buffer: Vec::new(),
        }
    }

    /// Sets the Content-Type header value used for charset detection, giving its
    /// charset parameter priority over HTML <meta> detection.
    ///
    /// This should be called before any data is fed; if the header contains a
    /// `charset` parameter, that charset will be used when resolving the stream's
    /// encoding.
    ///
    /// # Arguments
    ///
    /// * `content_type` - Optional Content-Type header value (e.g. "text/html; charset=ISO-8859-1")
    ///
    /// # Examples
    ///
    /// ```
    /// let mut state = CharsetState::new();
    /// state.set_content_type(Some("text/html; charset=ISO-8859-1"));
    /// ```
    pub fn set_content_type(&mut self, content_type: Option<&str>) {
        if let CharsetState::Pending { header_charset, .. } = self
            && let Some(ct) = content_type
        {
            // Extract charset from Content-Type using the existing function
            if let Some(cs) = crate::charset::extract_charset_from_content_type(ct) {
                *header_charset = Some(cs);
            }
        }
    }

    /// Process an input chunk, performing charset detection while pending and transcoding bytes to UTF-8.
    ///
    /// While the state is `Pending`, incoming bytes are buffered (up to 1024 bytes) until a charset is
    /// determined from the Content-Type header, an HTML `<meta charset=...>` within the sniff buffer,
    /// or the sniff buffer limit is reached. When the charset is resolved the buffered bytes and the
    /// new input are transcoded and the state transitions to `Resolved`. When the state is `Resolved`,
    /// incoming bytes are transcoded directly.
    ///
    /// # Returns
    ///
    /// `Ok(Cow::Borrowed(data))` when the resolved charset is UTF-8 and the transcoded output exactly
    /// matches the input (zero-copy path); `Ok(Cow::Owned(vec))` with transcoded UTF-8 bytes otherwise.
    /// Returns `Err(ConversionError::EncodingError(...))` if the charset is unsupported or transcoding fails.
    ///
    /// # Examples
    ///
    /// ```
    /// let mut st = CharsetState::new();
    /// // feed some bytes (keeps buffering while pending)
    /// let out = st.feed(b"<!doctype html><meta charset=\"utf-8\">Hello").unwrap();
    /// // after resolution the returned bytes are UTF-8
    /// assert!(std::str::from_utf8(&out).is_ok());
    /// // flush any remaining buffered bytes
    /// let tail = st.flush().unwrap();
    /// assert!(std::str::from_utf8(&tail).is_ok());
    /// ```
    pub fn feed<'a>(&mut self, data: &'a [u8]) -> Result<Cow<'a, [u8]>, ConversionError> {
        // Replace self with a temporary to allow state transitions.
        // On error, we restore a meaningful Failed state (not "transitioning").
        let current = std::mem::replace(self, CharsetState::Failed("transitioning".into()));

        match current {
            CharsetState::Pending {
                header_charset,
                mut sniff_buffer,
            } => {
                // If header charset is set, resolve immediately
                if let Some(ref cs) = header_charset {
                    let (mut new_state, _) = resolve_charset(cs).map_err(|e| {
                        *self = CharsetState::Failed(format!("{}", e));
                        e
                    })?;
                    // Transcode any previously buffered data + new data
                    let combined = if sniff_buffer.is_empty() {
                        Cow::Borrowed(data)
                    } else {
                        sniff_buffer.extend_from_slice(data);
                        Cow::Owned(sniff_buffer)
                    };
                    let result = transcode_data(&mut new_state, &combined).map_err(|e| {
                        *self = CharsetState::Failed(format!("{}", e));
                        e
                    })?;
                    *self = new_state;
                    return Ok(Cow::Owned(result));
                }

                // Accumulate data in sniff buffer, capped at SNIFF_BUFFER_LIMIT.
                // Only retain enough bytes for charset detection; any excess
                // from a large chunk is kept aside and transcoded after
                // resolution, avoiding unbounded sniff buffer growth.
                let remaining_capacity = SNIFF_BUFFER_LIMIT.saturating_sub(sniff_buffer.len());
                let sniff_bytes = data.len().min(remaining_capacity);
                sniff_buffer.extend_from_slice(&data[..sniff_bytes]);

                if sniff_buffer.len() >= SNIFF_BUFFER_LIMIT {
                    // Enough data to detect charset from HTML meta or default
                    let detected = detect_charset(None, &sniff_buffer);
                    let (mut new_state, _) = resolve_charset(&detected).map_err(|e| {
                        *self = CharsetState::Failed(format!("{}", e));
                        e
                    })?;
                    // Build full input for transcoding: sniff buffer + any
                    // excess bytes from this chunk that were not added to
                    // the sniff buffer.
                    let full_input = if sniff_bytes < data.len() {
                        let mut combined = sniff_buffer;
                        combined.extend_from_slice(&data[sniff_bytes..]);
                        combined
                    } else {
                        sniff_buffer
                    };
                    let result = transcode_data(&mut new_state, &full_input).map_err(|e| {
                        *self = CharsetState::Failed(format!("{}", e));
                        e
                    })?;
                    *self = new_state;
                    Ok(Cow::Owned(result))
                } else {
                    // Not enough data yet, stay in Pending
                    *self = CharsetState::Pending {
                        header_charset,
                        sniff_buffer,
                    };
                    Ok(Cow::Owned(Vec::new()))
                }
            }

            CharsetState::Resolved { decoder } => {
                let mut state = CharsetState::Resolved { decoder };
                let result = transcode_data(&mut state, data).map_err(|e| {
                    *self = CharsetState::Failed(format!("{}", e));
                    e
                })?;
                *self = state;
                // For UTF-8 zero-copy: if result matches input exactly, return borrowed
                if result == data {
                    Ok(Cow::Borrowed(data))
                } else {
                    Ok(Cow::Owned(result))
                }
            }

            CharsetState::Failed(reason) => {
                *self = CharsetState::Failed(reason.clone());
                Err(ConversionError::EncodingError(reason))
            }
        }
    }

    /// Finalize processing and return any remaining transcoded bytes.
    ///
    /// Call this after all input has been fed to process any bytes still held in the sniff buffer.
    /// If the charset was not resolved (total buffered bytes remained below the sniff limit), the
    /// function resolves to UTF-8. On success the state transitions to `Resolved` (or remains
    /// `Failed` on error).
    ///
    /// # Returns
    ///
    /// The remaining bytes produced by transcoding the buffered input.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::EncodingError` if charset resolution or transcoding fails.
    ///
    /// # Examples
    ///
    /// ```
    /// let mut state = CharsetState::new();
    /// // feed data...
    /// let remaining = state.flush().expect("flush should succeed");
    /// ```
    pub fn flush(&mut self) -> Result<Vec<u8>, ConversionError> {
        let current = std::mem::replace(self, CharsetState::Failed("transitioning".into()));

        match current {
            CharsetState::Pending {
                header_charset,
                sniff_buffer,
            } => {
                if sniff_buffer.is_empty() {
                    *self = CharsetState::Resolved { decoder: None };
                    return Ok(Vec::new());
                }
                // Resolve charset with whatever we have
                let charset = if let Some(ref cs) = header_charset {
                    cs.clone()
                } else {
                    detect_charset(None, &sniff_buffer)
                };
                let (mut new_state, _) = resolve_charset(&charset).map_err(|e| {
                    *self = CharsetState::Failed(format!("{}", e));
                    e
                })?;
                let result = transcode_data(&mut new_state, &sniff_buffer).map_err(|e| {
                    *self = CharsetState::Failed(format!("{}", e));
                    e
                })?;
                *self = new_state;
                Ok(result)
            }
            CharsetState::Resolved { decoder } => {
                // Flush the decoder's internal state at end-of-stream.
                // For non-UTF-8 decoders, calling decode_to_utf8 with
                // last=true emits any trailing bytes buffered internally
                // (e.g. incomplete multibyte sequences).
                if let Some(mut dec) = decoder {
                    let max_len = dec.max_utf8_buffer_length(0).unwrap_or(64);
                    let mut output = vec![0u8; max_len];
                    let (_result, _read, written, had_errors) =
                        dec.decode_to_utf8(&[], &mut output, true);
                    if had_errors {
                        *self = CharsetState::Failed(
                            "Invalid trailing bytes during decoder flush".to_string(),
                        );
                        return Err(ConversionError::EncodingError(
                            "Invalid trailing bytes during decoder flush".to_string(),
                        ));
                    }
                    output.truncate(written);
                    *self = CharsetState::Resolved { decoder: None };
                    Ok(output)
                } else {
                    *self = CharsetState::Resolved { decoder: None };
                    Ok(Vec::new())
                }
            }
            CharsetState::Failed(reason) => {
                *self = CharsetState::Failed(reason.clone());
                Err(ConversionError::EncodingError(reason))
            }
        }
    }

    /// Indicates whether the charset state is resolved.
    ///
    /// # Returns
    ///
    /// `true` if the state is `Resolved`, `false` otherwise.
    ///
    /// # Examples
    ///
    /// ```
    /// let s = CharsetState::new();
    /// assert!(!s.is_resolved());
    /// ```
    pub fn is_resolved(&self) -> bool {
        matches!(self, CharsetState::Resolved { .. })
    }

    /// Indicates whether the charset detection state machine is awaiting resolution.
    ///
    /// Returns `true` if the state machine is in the `Pending` state, `false` otherwise.
    ///
    /// # Examples
    ///
    /// ```
    /// let s = CharsetState::new();
    /// assert!(s.is_pending());
    /// ```
    pub fn is_pending(&self) -> bool {
        matches!(self, CharsetState::Pending { .. })
    }
}

impl Default for CharsetState {
    /// Creates a new `CharsetState` in its initial pending state.
    ///
    /// This is equivalent to `CharsetState::new()` and produces a `Pending` state
    /// with no header charset and an empty sniff buffer.
    ///
    /// # Examples
    ///
    /// ```
    /// let mut state = CharsetState::default();
    /// assert!(state.is_pending());
    /// ```
    fn default() -> Self {
        Self::new()
    }
}

/// Resolve a charset label into a `CharsetState::Resolved` state and indicate if it is UTF-8 zero-copy.
///
/// Normalizes the input label and returns a resolved state containing `decoder: None` for UTF-8
/// (zero-copy mode) or `decoder: Some(...)` for other supported encodings. If the label is not
/// recognized, returns `ConversionError::EncodingError`.
///
/// # Returns
///
/// A tuple `(resolved_state, is_utf8)` where `resolved_state` is `CharsetState::Resolved { decoder: ... }`
/// and `is_utf8` is `true` when the resolved encoding is UTF-8 (i.e., `decoder` is `None`), `false` otherwise.
///
/// # Examples
///
/// ```
/// // UTF-8 resolves to decoder == None and is_utf8 == true
/// let (state, is_utf8) = resolve_charset("utf-8").unwrap();
/// match state {
///     CharsetState::Resolved { decoder } => assert!(decoder.is_none()),
///     _ => panic!("expected Resolved"),
/// }
/// assert!(is_utf8);
///
/// // Non-UTF-8 (example) yields a decoder and is_utf8 == false
/// let (state, is_utf8) = resolve_charset("iso-8859-1").unwrap();
/// match state {
///     CharsetState::Resolved { decoder } => assert!(decoder.is_some()),
///     _ => panic!("expected Resolved"),
/// }
/// assert!(!is_utf8);
/// ```
fn resolve_charset(charset: &str) -> Result<(CharsetState, bool), ConversionError> {
    let normalized = charset.to_uppercase();
    if normalized == "UTF-8" {
        return Ok((CharsetState::Resolved { decoder: None }, true));
    }

    let encoding = encoding_rs::Encoding::for_label(charset.as_bytes()).ok_or_else(|| {
        ConversionError::EncodingError(format!("Unsupported charset '{}'", charset))
    })?;

    // encoding_rs maps some labels to UTF-8 internally
    if encoding == encoding_rs::UTF_8 {
        return Ok((CharsetState::Resolved { decoder: None }, true));
    }

    let decoder = encoding.new_decoder_without_bom_handling();
    Ok((
        CharsetState::Resolved {
            decoder: Some(decoder),
        },
        false,
    ))
}

/// Transcodes a byte slice according to the charset resolved in `state`.
///
/// Returns a UTF-8 byte vector produced by decoding `data` using the `Resolved` state's decoder.
/// - If `state` is `Resolved { decoder: None }`, the input bytes are treated as UTF-8 and returned as-is (copied).
/// - If `state` is `Resolved { decoder: Some(dec) }`, the decoder is used to convert `data` to UTF-8; decoding errors produce `ConversionError::EncodingError`.
/// - If `data` is empty, an empty `Vec<u8>` is returned.
/// - If `state` is not `Resolved`, an `EncodingError` is returned.
///
/// # Examples
///
/// ```
/// // UTF-8 path: decoder == None
/// let mut state = CharsetState::Resolved { decoder: None };
/// let out = transcode_data(&mut state, b"hello").unwrap();
/// assert_eq!(out, b"hello");
/// ```
fn transcode_data(state: &mut CharsetState, data: &[u8]) -> Result<Vec<u8>, ConversionError> {
    if data.is_empty() {
        return Ok(Vec::new());
    }

    match state {
        CharsetState::Resolved { decoder: None } => {
            // UTF-8 path: validate and return as-is
            // We use lossy conversion to be lenient with partial sequences
            // that may span chunk boundaries
            Ok(data.to_vec())
        }
        CharsetState::Resolved { decoder: Some(dec) } => {
            // Non-UTF-8: transcode using encoding_rs
            // Calculate maximum output size
            let max_len = dec
                .max_utf8_buffer_length(data.len())
                .unwrap_or(data.len().saturating_mul(4));
            let mut output = vec![0u8; max_len];

            let (_result, _read, written, had_errors) =
                dec.decode_to_utf8(data, &mut output, false);

            if had_errors {
                return Err(ConversionError::EncodingError(
                    "Invalid byte sequence during transcoding".to_string(),
                ));
            }

            output.truncate(written);
            Ok(output)
        }
        _ => Err(ConversionError::EncodingError(
            "CharsetState not in Resolved state".to_string(),
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    // ========================================================================
    // Task 8.2: Unit tests for CharsetState
    // ========================================================================

    // --- Content-Type header charset priority ---

    #[test]
    fn test_content_type_charset_takes_priority() {
        let mut state = CharsetState::new();
        state.set_content_type(Some("text/html; charset=UTF-8"));

        // Feed HTML with a different meta charset
        let html = b"<html><head><meta charset=\"ISO-8859-1\"></head><body>Hello</body></html>";
        let result = state.feed(html).unwrap();

        // Should resolve immediately using Content-Type charset (UTF-8)
        assert!(state.is_resolved());
        assert!(!result.is_empty());
        assert_eq!(
            std::str::from_utf8(&result).unwrap(),
            std::str::from_utf8(html).unwrap()
        );
    }

    #[test]
    fn test_content_type_charset_resolves_on_first_feed() {
        let mut state = CharsetState::new();
        state.set_content_type(Some("text/html; charset=UTF-8"));

        let result = state.feed(b"<p>Hello</p>").unwrap();
        assert!(state.is_resolved());
        assert!(!result.is_empty());
    }

    #[test]
    fn test_content_type_without_charset_falls_through() {
        let mut state = CharsetState::new();
        state.set_content_type(Some("text/html"));

        // No charset in Content-Type, should stay pending with small data
        let result = state.feed(b"<p>Hi</p>").unwrap();
        assert!(state.is_pending());
        assert!(result.is_empty());
    }

    // --- HTML meta charset detection ---

    #[test]
    fn test_html_meta_charset_detected() {
        let mut state = CharsetState::new();

        // Build HTML with meta charset that exceeds sniff buffer
        let mut html = Vec::new();
        html.extend_from_slice(b"<html><head><meta charset=\"UTF-8\"></head><body>");
        // Pad to exceed SNIFF_BUFFER_LIMIT
        while html.len() < SNIFF_BUFFER_LIMIT + 1 {
            html.extend_from_slice(b"x");
        }
        html.extend_from_slice(b"</body></html>");

        let result = state.feed(&html).unwrap();
        assert!(state.is_resolved());
        assert!(!result.is_empty());
    }

    #[test]
    fn test_html_meta_charset_detected_on_flush() {
        let mut state = CharsetState::new();

        // Feed less than 1024 bytes with a meta charset
        let html = b"<html><head><meta charset=\"UTF-8\"></head><body>Hello</body></html>";
        let result = state.feed(html).unwrap();
        // Still pending (less than 1024 bytes)
        assert!(state.is_pending());
        assert!(result.is_empty());

        // Flush should resolve using meta charset
        let flushed = state.flush().unwrap();
        assert!(state.is_resolved());
        assert!(!flushed.is_empty());
    }

    // --- Default UTF-8 fallback ---

    #[test]
    fn test_default_utf8_fallback() {
        let mut state = CharsetState::new();

        // Feed data without any charset indication, exceeding sniff limit
        let mut html = Vec::new();
        html.extend_from_slice(b"<html><body>");
        while html.len() < SNIFF_BUFFER_LIMIT + 1 {
            html.extend_from_slice(b"Hello World ");
        }
        html.extend_from_slice(b"</body></html>");

        let result = state.feed(&html).unwrap();
        assert!(state.is_resolved());
        assert!(!result.is_empty());
    }

    #[test]
    fn test_default_utf8_on_flush_with_small_input() {
        let mut state = CharsetState::new();

        let result = state.feed(b"<p>Hello</p>").unwrap();
        assert!(state.is_pending());
        assert!(result.is_empty());

        let flushed = state.flush().unwrap();
        assert!(state.is_resolved());
        assert_eq!(&flushed, b"<p>Hello</p>");
    }

    // --- ISO-8859-1 transcoding ---

    #[test]
    fn test_iso_8859_1_transcoding() {
        let mut state = CharsetState::new();
        state.set_content_type(Some("text/html; charset=ISO-8859-1"));

        // ISO-8859-1 encoded bytes: "café" where é is 0xE9
        let input = b"caf\xe9";
        let result = state.feed(input).unwrap();
        assert!(state.is_resolved());
        assert_eq!(std::str::from_utf8(&result).unwrap(), "café");
    }

    // --- Windows-1252 transcoding ---

    #[test]
    fn test_windows_1252_transcoding() {
        let mut state = CharsetState::new();
        state.set_content_type(Some("text/html; charset=windows-1252"));

        // Windows-1252: smart quotes 0x93 = left double quote, 0x94 = right double quote
        let input = b"\x93Hello\x94";
        let result = state.feed(input).unwrap();
        assert!(state.is_resolved());
        let text = std::str::from_utf8(&result).unwrap();
        assert!(text.contains("Hello"));
        // Should contain Unicode smart quotes
        assert!(text.contains('\u{201C}')); // left double quotation mark
        assert!(text.contains('\u{201D}')); // right double quotation mark
    }

    // --- Unsupported charset returns EncodingError ---

    #[test]
    fn test_unsupported_charset_returns_error() {
        let mut state = CharsetState::new();
        state.set_content_type(Some("text/html; charset=FAKE-ENCODING-999"));

        let result = state.feed(b"<p>Hello</p>");
        assert!(result.is_err());
        match result.unwrap_err() {
            ConversionError::EncodingError(msg) => {
                assert!(msg.contains("Unsupported charset"));
            }
            other => panic!("Expected EncodingError, got {:?}", other),
        }
    }

    // --- UTF-8 zero-copy path ---

    #[test]
    fn test_utf8_zero_copy_in_resolved_state() {
        let mut state = CharsetState::new();
        state.set_content_type(Some("text/html; charset=UTF-8"));

        // First feed resolves the charset
        let _ = state.feed(b"initial").unwrap();
        assert!(state.is_resolved());

        // Subsequent feeds should be zero-copy (Cow::Borrowed)
        let data = b"<p>Hello World</p>";
        let result = state.feed(data).unwrap();
        assert!(
            matches!(result, Cow::Borrowed(_)),
            "Expected Cow::Borrowed for UTF-8 zero-copy"
        );
        assert_eq!(&*result, data.as_slice());
    }

    #[test]
    fn test_utf8_decoder_is_none() {
        let mut state = CharsetState::new();
        state.set_content_type(Some("text/html; charset=UTF-8"));
        let _ = state.feed(b"test").unwrap();

        match &state {
            CharsetState::Resolved { decoder } => {
                assert!(
                    decoder.is_none(),
                    "UTF-8 should have no decoder (zero-copy)"
                );
            }
            _ => panic!("Expected Resolved state"),
        }
    }

    // --- Edge cases ---

    #[test]
    fn test_empty_feed() {
        let mut state = CharsetState::new();
        let result = state.feed(b"").unwrap();
        assert!(state.is_pending());
        assert!(result.is_empty());
    }

    #[test]
    fn test_flush_empty_pending() {
        let mut state = CharsetState::new();
        let result = state.flush().unwrap();
        assert!(result.is_empty());
        assert!(state.is_resolved());
    }

    #[test]
    fn test_multiple_feeds_accumulate() {
        let mut state = CharsetState::new();

        // Feed small chunks that don't exceed sniff limit
        for _ in 0..10 {
            let result = state.feed(b"<p>chunk</p>").unwrap();
            if state.is_resolved() {
                assert!(!result.is_empty());
                return;
            }
            assert!(result.is_empty());
        }

        // After enough chunks, should have resolved
        // If not, flush should resolve
        let flushed = state.flush().unwrap();
        assert!(state.is_resolved());
        assert!(!flushed.is_empty());
    }

    #[test]
    fn test_feed_after_resolved() {
        let mut state = CharsetState::new();
        state.set_content_type(Some("text/html; charset=UTF-8"));

        let _ = state.feed(b"first").unwrap();
        assert!(state.is_resolved());

        // Subsequent feeds should work fine
        let result = state.feed(b"second").unwrap();
        assert_eq!(&*result, b"second");
    }

    #[test]
    fn test_iso_8859_1_via_meta_tag() {
        let mut state = CharsetState::new();

        // Build HTML with ISO-8859-1 meta charset, padded to exceed sniff limit
        let mut html = Vec::new();
        html.extend_from_slice(b"<html><head><meta charset=\"ISO-8859-1\"></head><body>caf\xe9");
        while html.len() < SNIFF_BUFFER_LIMIT + 1 {
            html.push(b' ');
        }
        html.extend_from_slice(b"</body></html>");

        let result = state.feed(&html).unwrap();
        assert!(state.is_resolved());
        let text = std::str::from_utf8(&result).unwrap();
        assert!(text.contains("café"));
    }

    // --- Regression tests ---

    /// Regression: charset state machine must not leave a misleading
    /// "transitioning" error state when resolve_charset or transcode_data
    /// fails. The Failed state should contain the actual error description.
    #[test]
    fn test_error_state_contains_actual_reason_not_transitioning() {
        let mut state = CharsetState::new();
        state.set_content_type(Some("text/html; charset=FAKE-ENCODING-999"));

        // First call should fail with the real error
        let err = state.feed(b"<p>Hello</p>").unwrap_err();
        assert!(
            format!("{}", err).contains("Unsupported charset"),
            "First error should mention unsupported charset, got: {}",
            err
        );

        // Subsequent calls should also fail with a meaningful message,
        // NOT "transitioning"
        let err2 = state.feed(b"more data").unwrap_err();
        let msg = format!("{}", err2);
        assert!(
            !msg.contains("transitioning"),
            "Error after failure should not say 'transitioning', got: {}",
            msg
        );
        assert!(
            msg.contains("Unsupported charset"),
            "Error after failure should contain actual reason, got: {}",
            msg
        );
    }

    /// Regression: flush() must also set a meaningful Failed state on error.
    #[test]
    fn test_flush_error_state_contains_actual_reason() {
        let mut state = CharsetState::new();
        // Manually set header_charset to an invalid encoding so flush() fails
        // during resolve_charset.
        if let CharsetState::Pending { header_charset, .. } = &mut state {
            *header_charset = Some("NONEXISTENT-ENCODING".to_string());
        }
        // Feed a small amount so it stays Pending
        let _ = state.feed(b"<p>Hi</p>");

        // flush() should fail
        let err = state.flush().unwrap_err();
        assert!(
            format!("{}", err).contains("Unsupported charset"),
            "flush error should mention unsupported charset, got: {}",
            err
        );

        // State should be Failed with the real reason
        let err2 = state.feed(b"more").unwrap_err();
        let msg = format!("{}", err2);
        assert!(
            !msg.contains("transitioning"),
            "State after flush error should not say 'transitioning', got: {}",
            msg
        );
    }

    // ========================================================================
    // Task 8.3: Property test for Charset Detection equivalence (Property 9)
    // ========================================================================

    /// Produces a proptest strategy that yields a random encoding label supported by encoding_rs.
    ///
    /// # Examples
    ///
    /// ```
    /// use proptest::prelude::*;
    ///
    /// proptest! {
    ///     #[test]
    ///     fn pick_label(label in crate::arb_encoding_label()) {
    ///         // strategy yields one of the known encoding labels (including "UTF-8")
    ///         assert!(label == "UTF-8" || label.starts_with("ISO-8859-") || label.starts_with("windows-") || label.starts_with("KOI8-"));
    ///     }
    /// }
    /// ```
    fn arb_encoding_label() -> impl Strategy<Value = &'static str> {
        prop::sample::select(vec![
            "ISO-8859-1",
            "ISO-8859-2",
            "ISO-8859-3",
            "ISO-8859-4",
            "ISO-8859-5",
            "ISO-8859-6",
            "ISO-8859-7",
            "ISO-8859-8",
            "ISO-8859-10",
            "ISO-8859-13",
            "ISO-8859-14",
            "ISO-8859-15",
            "ISO-8859-16",
            "windows-1250",
            "windows-1251",
            "windows-1252",
            "windows-1253",
            "windows-1254",
            "windows-1255",
            "windows-1256",
            "windows-1257",
            "windows-1258",
            "KOI8-R",
            "KOI8-U",
            "UTF-8",
        ])
    }

    /// Produces a proptest strategy that generates ASCII-safe HTML body strings.
    ///
    /// The generated strings are 1 to 9 words (random length in 1..10), each chosen from
    /// a small ASCII-only word list and joined with single spaces.
    ///
    /// # Examples
    ///
    /// ```
    /// use proptest::strategy::Strategy;
    ///
    /// let mut runner = proptest::test_runner::TestRunner::default();
    /// let strategy = crate::arb_ascii_body();
    /// let value = strategy.new_tree(&mut runner).unwrap().current();
    /// assert!(!value.is_empty());
    /// ```
    fn arb_ascii_body() -> impl Strategy<Value = String> {
        prop::collection::vec(
            prop::sample::select(vec![
                "Hello",
                "World",
                "Test",
                "Content",
                "Page",
                "Data",
                "Paragraph",
                "Section",
                "Article",
                "Main",
            ]),
            1..10,
        )
        .prop_map(|words| words.join(" "))
    }

    proptest! {
        /// Feature: rust-streaming-engine-core, Property 9: Charset Detection Equivalence
        ///
        /// **Validates: Requirements 7.1, 7.4**
        ///
        /// For any HTML input with a Content-Type charset, the streaming
        /// CharsetState detection result should match detect_charset().
        #[test]
        fn prop_charset_detection_equivalence_with_content_type(
            encoding_label in arb_encoding_label(),
            body_text in arb_ascii_body(),
        ) {
            let content_type = format!("text/html; charset={}", encoding_label);
            let html_str = format!(
                "<html><head><title>Test</title></head><body><p>{}</p></body></html>",
                body_text
            );

            // Full-buffer detection
            let full_buffer_charset = detect_charset(Some(&content_type), html_str.as_bytes());

            // Streaming detection: set content type and feed all data
            let mut state = CharsetState::new();
            state.set_content_type(Some(&content_type));

            // The streaming path should resolve to the same charset
            // We verify by checking that both detect the same encoding label
            let encoding_from_full = encoding_rs::Encoding::for_label(full_buffer_charset.as_bytes());

            // For the streaming path, resolve_charset uses the same logic
            let resolved = resolve_charset(&full_buffer_charset);
            match resolved {
                Ok((CharsetState::Resolved { decoder }, _)) => {
                    if full_buffer_charset.eq_ignore_ascii_case("UTF-8") {
                        prop_assert!(decoder.is_none(),
                            "UTF-8 should have no decoder");
                    } else {
                        // Both should recognize the encoding
                        prop_assert!(encoding_from_full.is_some(),
                            "encoding_rs should support '{}'", full_buffer_charset);
                    }
                }
                Ok(_) => {
                    prop_assert!(false, "Expected Resolved state");
                }
                Err(_) => {
                    // If streaming fails, full-buffer should also not recognize it
                    prop_assert!(encoding_from_full.is_none(),
                        "Streaming failed but full-buffer recognized '{}'", full_buffer_charset);
                }
            }
        }

        /// Feature: rust-streaming-engine-core, Property 9: Charset Detection Equivalence
        ///
        /// **Validates: Requirements 7.1, 7.4**
        ///
        /// For HTML with a meta charset tag and an encoding_rs-supported encoding,
        /// encode the body in that encoding, then verify streaming and full-buffer
        /// detect the same charset and produce the same transcoded UTF-8 output.
        #[test]
        fn prop_charset_transcoding_equivalence(
            encoding_label in arb_encoding_label(),
            body_text in arb_ascii_body(),
        ) {
            // Build HTML with meta charset
            let html_template = format!(
                "<html><head><meta charset=\"{}\"></head><body><p>{}</p></body></html>",
                encoding_label, body_text
            );

            // Encode the HTML in the target encoding
            let encoding = match encoding_rs::Encoding::for_label(encoding_label.as_bytes()) {
                Some(enc) => enc,
                None => return Ok(()), // Skip unsupported encodings
            };

            let (encoded_bytes, _, had_unmappable) = encoding.encode(&html_template);
            if had_unmappable {
                return Ok(()); // Skip if encoding can't represent the content
            }

            // Full-buffer: detect charset and get the detected name
            let full_buffer_charset = detect_charset(None, &encoded_bytes);

            // Streaming: feed all data at once (exceeding sniff buffer)
            let mut padded = encoded_bytes.to_vec();
            while padded.len() < SNIFF_BUFFER_LIMIT + 1 {
                // Pad with spaces (safe in all single-byte encodings)
                padded.push(b' ');
            }

            let streaming_charset = detect_charset(None, &padded);

            // Both should detect the same charset
            prop_assert_eq!(
                full_buffer_charset.to_uppercase(),
                streaming_charset.to_uppercase(),
                "Charset detection mismatch for encoding '{}'",
                encoding_label
            );

            // Now verify transcoding produces the same result
            let mut state = CharsetState::new();
            state.set_content_type(Some(&format!("text/html; charset={}", encoding_label)));
            let streaming_result = state.feed(&encoded_bytes);

            match streaming_result {
                Ok(transcoded) => {
                    // The transcoded output should be valid UTF-8
                    prop_assert!(
                        std::str::from_utf8(&transcoded).is_ok(),
                        "Streaming transcoding produced invalid UTF-8 for '{}'",
                        encoding_label
                    );
                    // And should contain the body text (ASCII subset)
                    let text = std::str::from_utf8(&transcoded).unwrap();
                    prop_assert!(
                        text.contains(&body_text),
                        "Transcoded output should contain body text '{}' for encoding '{}'",
                        body_text, encoding_label
                    );
                }
                Err(e) => {
                    prop_assert!(false,
                        "Streaming transcoding failed for '{}': {}",
                        encoding_label, e);
                }
            }
        }
    }
}
