//! Core FFI conversion logic shared by all export entry points.
//!
//! This module implements [`convert_inner`], the single internal function that
//! executes a full HTML-to-Markdown conversion request. All public FFI exports
//! (`markdown_convert`, `markdown_convert_incremental`, streaming variants)
//! delegate to this function after validating their inputs.
//!
//! # Conversion Pipeline
//!
//! 1. **Decode options** — translate C `MarkdownOptions` into Rust `ConversionOptions`
//! 2. **Empty payload fast path** — skip DOM parsing for zero-length input
//! 3. **Pre-parse budget check** — reject inputs exceeding `parser_memory_budget`
//! 4. **Pre-parse deadline check** — fail early if `parse_timeout` already expired
//! 5. **Parse HTML** — build DOM tree via html5ever with optional charset detection
//! 6. **Post-parse deadline check** — detect if parse exceeded `parse_timeout`
//! 7. **Convert** — traverse DOM with cooperative timeout checks (using `parse_timeout`)
//! 8. **Derive ETag** — compute BLAKE3-based ETag if requested
//! 9. **Estimate tokens** — compute LLM token count if requested
//!
//! Keeping these steps in one place avoids divergent behavior across exports
//! and keeps error propagation deterministic for C callers.

use std::time::Instant;

use crate::converter::{ConversionContext, MarkdownConverter};
use crate::error::ConversionError;
use crate::parser::parse_html_with_charset;
use crate::token_estimator::TokenEstimator;

use super::abi::{ConversionOutput, MarkdownConverterHandle, MarkdownOptions};
use super::options::decode_options;

/* Fixed parser overhead covers tokenizer/tree-builder state and the DOM root.
 * The per-input multiplier includes the source buffer, a worst-case UTF-8
 * transcode expansion, and parser scratch space. Each '<' may begin a DOM node
 * (including malformed markup recovery), so account for RcDom allocation and
 * associated strings separately. This is intentionally conservative because
 * html5ever does not expose allocator-level accounting. */
const PARSER_FIXED_OVERHEAD: u64 = 1024;
const PARSER_BYTES_PER_INPUT_BYTE: u64 = 7;
const PARSER_BYTES_PER_TAG_OPENER: u64 = 512;

/// Estimate the peak parser/transcoding/DOM working set before parsing.
///
/// Returns `u64::MAX` on arithmetic overflow so callers fail closed against
/// every finite parser budget.
pub(crate) fn estimate_parser_working_set(input_len: usize, tag_openers: usize) -> u64 {
    let input_len = u64::try_from(input_len).unwrap_or(u64::MAX);
    let tag_openers = u64::try_from(tag_openers).unwrap_or(u64::MAX);

    input_len
        .checked_mul(PARSER_BYTES_PER_INPUT_BYTE)
        .and_then(|bytes| {
            tag_openers
                .checked_mul(PARSER_BYTES_PER_TAG_OPENER)
                .and_then(|nodes| bytes.checked_add(nodes))
        })
        .and_then(|bytes| bytes.checked_add(PARSER_FIXED_OVERHEAD))
        .unwrap_or(u64::MAX)
}

/// Return the largest raw input that can fit a parser budget before accounting
/// for tag-specific DOM allocations. Incremental callers use this as an early
/// buffer ceiling and apply the full estimate while chunks arrive.
pub(crate) fn max_input_for_parser_budget(parser_budget: u64) -> usize {
    if parser_budget == 0 {
        return 0;
    }

    let bytes = parser_budget.saturating_sub(PARSER_FIXED_OVERHEAD) / PARSER_BYTES_PER_INPUT_BYTE;
    usize::try_from(bytes).unwrap_or(usize::MAX)
}

/// Execute one FFI conversion request end-to-end.
///
/// This function is intentionally linear: decode options, parse HTML, run
/// conversion with cooperative timeout checks, then optionally derive token
/// estimate and ETag from the produced Markdown bytes.
///
/// # Parser constraints
///
/// Since html5ever cannot be interrupted mid-parse, this function enforces
/// `parse_timeout` and `parser_memory_budget` via pre/post checks:
///
/// - **Pre-check (budget):** If `parser_memory_budget > 0`, a conservative,
///   overflow-safe estimate covers fixed parser state, raw input, worst-case
///   UTF-8 transcoding, parser scratch space, and DOM-node amplification.
/// - **Pre-check (deadline):** If the parse deadline has already expired before
///   parsing begins, the request is rejected with `ParseTimeout`.
/// - **Post-check (deadline):** If parsing completes but the deadline has
///   elapsed, the request is rejected with `ParseTimeout`.
/// - **DOM traversal:** The `ConversionContext` uses `parse_timeout` for its
///   cooperative checkpoint deadline, ensuring the full pipeline (parse + DOM
///   traversal) stays within the parse budget.
///
/// Keeping these steps in one place avoids divergent behavior across exports
/// and keeps error propagation deterministic for C callers.
pub(crate) fn convert_inner(
    handle_ref: &MarkdownConverterHandle,
    html_slice: &[u8],
    options_ref: &MarkdownOptions,
) -> Result<ConversionOutput, ConversionError> {
    let decoded = decode_options(options_ref)?;

    // Fast path for empty payloads: skip DOM/parser setup, but still preserve
    // optional metadata behavior (token estimate and deterministic ETag).
    if html_slice.is_empty() {
        let markdown = Box::<[u8]>::default();
        let token_estimate = if decoded.estimate_tokens {
            TokenEstimator::with_chars_per_token(decoded.effective_chars_per_token).estimate("")
        } else {
            0
        };
        let etag = decoded.generate_etag.then(|| {
            handle_ref
                .etag_generator
                .generate(markdown.as_ref())
                .into_bytes()
                .into_boxed_slice()
        });

        return Ok(ConversionOutput {
            markdown,
            etag,
            token_estimate,
        });
    }

    // --- Parser memory budget pre-check ---
    // html5ever does not expose allocator accounting. Estimate the complete
    // parser/transcoding/DOM working set and fail closed on arithmetic overflow.
    let input_size = html_slice.len();
    let parser_budget = decoded.parser_memory_budget;
    let tag_openers = html_slice.iter().filter(|byte| **byte == b'<').count();
    let parser_working_set = estimate_parser_working_set(input_size, tag_openers);
    if parser_budget > 0 && parser_working_set > parser_budget {
        // `limit` is reported for diagnostics only. Use a saturating
        // conversion so the u64 budget never silently truncates when usize is
        // narrower than u64 (e.g. 32-bit targets); on 64-bit targets this is
        // an identity conversion.
        let limit = usize::try_from(parser_budget).unwrap_or(usize::MAX);
        return Err(ConversionError::ParseBudgetExceeded {
            used: usize::try_from(parser_working_set).unwrap_or(usize::MAX),
            limit,
        });
    }

    // Resolve the effective parse deadline: parse_timeout constrains both the
    // html5ever parse phase and the subsequent DOM traversal.
    let parse_timeout = decoded.parse_timeout;
    let parse_start = Instant::now();

    // --- Pre-parse deadline check ---
    // If the deadline is already expired (e.g., upstream processing consumed
    // the budget), fail immediately without invoking the parser.
    if !parse_timeout.is_zero() && parse_start.elapsed() > parse_timeout {
        return Err(ConversionError::ParseTimeout);
    }

    let dom = parse_html_with_charset(html_slice, decoded.content_type)?;

    // --- Post-parse deadline check ---
    // html5ever cannot be interrupted, but we detect overruns after it returns.
    if !parse_timeout.is_zero() && parse_start.elapsed() > parse_timeout {
        return Err(ConversionError::ParseTimeout);
    }

    // Compute remaining time budget for DOM traversal. The ConversionContext
    // uses this as its cooperative checkpoint deadline so the full pipeline
    // (parse + traversal) stays within parse_timeout.
    let traversal_budget = if parse_timeout.is_zero() {
        parse_timeout
    } else {
        parse_timeout.saturating_sub(parse_start.elapsed())
    };

    let mut ctx = ConversionContext::new(traversal_budget);
    ctx.set_input_size_hint(input_size);
    // Check once before conversion so a near-expired deadline can fail early
    // without spending cycles traversing a large DOM.
    ctx.check_timeout()?;

    let converter = MarkdownConverter::with_options(decoded.conversion);
    let markdown = converter.convert_with_context(&dom, &mut ctx)?;

    let token_estimate = if decoded.estimate_tokens {
        TokenEstimator::with_chars_per_token(decoded.effective_chars_per_token).estimate(&markdown)
    } else {
        0
    };

    let markdown = markdown.into_bytes().into_boxed_slice();
    let etag = decoded.generate_etag.then(|| {
        handle_ref
            .etag_generator
            .generate(markdown.as_ref())
            .into_bytes()
            .into_boxed_slice()
    });

    Ok(ConversionOutput {
        markdown,
        etag,
        token_estimate,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ffi::exports::markdown_options_init;

    #[test]
    fn parser_working_set_accounts_for_transcoding_and_dom_nodes() {
        let plain = estimate_parser_working_set(128, 0);
        let markup = estimate_parser_working_set(128, 16);

        assert!(plain > 128, "estimate must exceed raw input bytes");
        assert!(markup > plain, "DOM node candidates must increase estimate");
    }

    #[test]
    fn parser_working_set_overflow_fails_closed() {
        assert_eq!(
            estimate_parser_working_set(usize::MAX, usize::MAX),
            u64::MAX
        );
    }

    #[test]
    fn parser_budget_raw_ceiling_reserves_fixed_and_transcode_costs() {
        let budget = PARSER_FIXED_OVERHEAD + (PARSER_BYTES_PER_INPUT_BYTE * 100);
        assert_eq!(max_input_for_parser_budget(budget), 100);
        assert_eq!(max_input_for_parser_budget(PARSER_FIXED_OVERHEAD - 1), 0);
    }

    #[test]
    fn full_conversion_rejects_dom_amplification_before_parsing() {
        let html = b"<i></i>".repeat(32);
        let raw_size = u64::try_from(html.len()).unwrap();
        let mut options: MarkdownOptions = unsafe { std::mem::zeroed() };
        unsafe { markdown_options_init(&mut options) };
        options.parser_memory_budget = raw_size + 1;

        let error = match convert_inner(&MarkdownConverterHandle::new(), &html, &options) {
            Ok(_) => panic!("raw-input-only accounting would incorrectly accept this request"),
            Err(error) => error,
        };
        assert!(matches!(error, ConversionError::ParseBudgetExceeded { .. }));
    }
}
