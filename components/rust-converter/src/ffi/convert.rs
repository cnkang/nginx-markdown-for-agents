//! Core FFI conversion logic shared by full-buffer export entry points.
//!
//! This module implements [`convert_inner`], the single internal function that
//! executes a full HTML-to-Markdown conversion request. The full-buffer export
//! `markdown_convert` delegates to this function after validating its inputs.
//! The streaming FFI exports (`markdown_streaming_*`) do NOT delegate to
//! `convert_inner`; they own their streaming state machine in
//! `ffi/streaming.rs`.
//!
//! # Conversion Pipeline
//!
//! 1. **Decode options** — translate C `MarkdownOptions` into Rust `ConversionOptions`
//! 2. **Empty payload fast path** — skip DOM parsing for zero-length input
//! 3. **Pre-parse budget check** — reject inputs exceeding `parser_memory_budget`
//! 4. **Pre-parse deadline check** — fail early if a configured deadline expired
//! 5. **Parse HTML** — build DOM tree via html5ever with optional charset detection
//! 6. **Post-parse deadline check** — detect if parsing exceeded `parse_timeout`
//! 7. **Convert** — traverse DOM with cooperative checks against the overall timeout
//! 8. **Derive ETag** — compute BLAKE3-based ETag if requested
//! 9. **Estimate tokens** — compute LLM token count if requested
//!
//! Keeping these steps in one place avoids divergent behavior across exports
//! and keeps error propagation deterministic for C callers.

use std::time::{Duration, Instant};

use crate::converter::{ConversionContext, MarkdownConverter};
use crate::error::ConversionError;
use crate::parser::parse_html_with_charset;
use crate::token_estimator::TokenEstimator;

use super::abi::{ConversionOutput, MarkdownConverterHandle, MarkdownOptions};
use super::options::{DecodedOptions, decode_options};

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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ConversionDeadlines {
    parser: Duration,
    overall: Duration,
}

/// Keep the parser-specific and overall conversion budgets independent.
fn resolve_conversion_deadlines(parser: Duration, overall: Duration) -> ConversionDeadlines {
    ConversionDeadlines { parser, overall }
}

/// Return `Err(error)` when `deadline` is configured and has elapsed since
/// `start`.  Unconfigured deadlines (`Duration::ZERO`) are always OK.
fn check_deadline(
    deadline: Duration,
    start: Instant,
    error: ConversionError,
) -> Result<(), ConversionError> {
    if !deadline.is_zero() && start.elapsed() > deadline {
        Err(error)
    } else {
        Ok(())
    }
}

/// Reject the request when the estimated parser working set exceeds the
/// configured memory budget.  A budget of zero means unbounded.
fn check_parser_budget(parser_budget: u64, parser_working_set: u64) -> Result<(), ConversionError> {
    if parser_budget > 0 && parser_working_set > parser_budget {
        // `limit` is reported for diagnostics only. Use a saturating
        // conversion so the u64 budget never silently truncates when usize is
        // narrower than u64 (e.g. 32-bit targets); on 64-bit targets this is
        // an identity conversion.
        let limit = usize::try_from(parser_budget).unwrap_or(usize::MAX);
        Err(ConversionError::ParseBudgetExceeded {
            used: usize::try_from(parser_working_set).unwrap_or(usize::MAX),
            limit,
        })
    } else {
        Ok(())
    }
}

/// Fast path for empty payloads: skip DOM/parser setup, but still preserve
/// optional metadata behavior (token estimate and deterministic ETag).
fn convert_empty_payload<'a>(
    decoded: &DecodedOptions<'a>,
    handle_ref: &MarkdownConverterHandle,
) -> ConversionOutput {
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
    ConversionOutput {
        markdown,
        etag,
        token_estimate,
    }
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
/// - **DOM traversal:** The `ConversionContext` uses the remaining general
///   conversion timeout for its cooperative checkpoint deadline. A configured
///   `parse_timeout` limits only the parser phase.
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
        return Ok(convert_empty_payload(&decoded, handle_ref));
    }

    let deadlines = resolve_conversion_deadlines(decoded.parse_timeout, decoded.timeout);
    let conversion_start = Instant::now();

    // --- Parser memory budget pre-check ---
    // html5ever does not expose allocator accounting. Estimate the complete
    // parser/transcoding/DOM working set and fail closed on arithmetic overflow.
    let input_size = html_slice.len();
    let tag_openers = html_slice.iter().filter(|byte| **byte == b'<').count();
    let parser_working_set = estimate_parser_working_set(input_size, tag_openers);
    check_parser_budget(decoded.parser_memory_budget, parser_working_set)?;

    // The parser deadline starts immediately before parsing. The general
    // conversion deadline started before the parser-budget estimate so it
    // bounds the complete full-buffer pipeline.
    let parse_start = Instant::now();

    // --- Pre-parse deadline check ---
    // If a deadline is already expired (e.g., upstream processing consumed
    // the budget), fail immediately without invoking the parser.  The
    // parser sub-deadline is measured from the pipeline entry
    // (conversion_start), not from parse_start below: parse_start is
    // created immediately before this check, so its elapsed time is ~0
    // and the check could never fire — the intent is to bound
    // pre-parse work (budget estimation, upstream delay) within the
    // parser sub-limit too.
    check_deadline(
        deadlines.parser,
        conversion_start,
        ConversionError::ParseTimeout,
    )?;
    check_deadline(
        deadlines.overall,
        conversion_start,
        ConversionError::Timeout,
    )?;

    let dom = parse_html_with_charset(html_slice, decoded.content_type)?;

    // --- Post-parse deadline check ---
    // html5ever cannot be interrupted, but we detect overruns after it returns.
    check_deadline(deadlines.parser, parse_start, ConversionError::ParseTimeout)?;
    check_deadline(
        deadlines.overall,
        conversion_start,
        ConversionError::Timeout,
    )?;

    // Compute remaining time budget for DOM traversal. The ConversionContext
    // uses this as its cooperative checkpoint deadline so the full pipeline
    // (parse + traversal) stays within the overall deadline.  An overall
    // deadline that pre-parse work already exhausted is a genuine timeout:
    // fail with Timeout instead of handing ConversionContext a ZERO budget,
    // which it interprets as "no deadline" and would run unbounded.
    // (ZERO from an explicitly unconfigured overall deadline never reaches
    // here — check_deadline treats it as always-OK above.)
    let elapsed_overall = conversion_start.elapsed();
    if !deadlines.overall.is_zero() && elapsed_overall >= deadlines.overall {
        return Err(ConversionError::Timeout);
    }
    let traversal_budget = deadlines.overall.saturating_sub(elapsed_overall);

    let mut ctx = ConversionContext::new(traversal_budget);
    ctx.set_input_size_hint(input_size);
    ctx.set_output_budget(decoded.memory_budget);
    // Check once before conversion so a near-expired deadline can fail early
    // without spending cycles traversing a large DOM.
    ctx.check_timeout()?;

    let converter = MarkdownConverter::with_options(decoded.conversion);
    let markdown = converter.convert_with_context(&dom, &mut ctx)?;

    // --- Post-traversal deadline check ---
    // The DOM traversal is complete; the remaining post-processing steps
    // (token estimation, ETag generation) are bounded but still count
    // against the overall conversion deadline.
    check_deadline(
        deadlines.overall,
        conversion_start,
        ConversionError::Timeout,
    )?;

    let token_estimate = if decoded.estimate_tokens {
        TokenEstimator::with_chars_per_token(decoded.effective_chars_per_token).estimate(&markdown)
    } else {
        0
    };

    // --- Post-token-estimation deadline check ---
    check_deadline(
        deadlines.overall,
        conversion_start,
        ConversionError::Timeout,
    )?;

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
    fn parser_timeout_does_not_shorten_overall_conversion_timeout() {
        let deadlines =
            resolve_conversion_deadlines(Duration::from_secs(5), Duration::from_secs(30));

        assert_eq!(deadlines.parser, Duration::from_secs(5));
        assert_eq!(deadlines.overall, Duration::from_secs(30));
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
