use crate::converter::{ConversionContext, MarkdownConverter};
use crate::error::ConversionError;
use crate::parser::parse_html_with_charset;

use super::abi::{ConversionOutput, MarkdownConverterHandle, MarkdownOptions};
use super::options::decode_options;

/// Execute one FFI conversion request end-to-end.
///
/// This function is intentionally linear: decode options, parse HTML, run
/// conversion with cooperative timeout checks, then optionally derive token
/// estimate and ETag from the produced Markdown bytes.
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
            handle_ref.token_estimator.estimate("")
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

    let dom = parse_html_with_charset(html_slice, decoded.content_type)?;

    let mut ctx = ConversionContext::new(decoded.timeout);
    ctx.set_input_size_hint(html_slice.len());
    // Check once before conversion so a near-expired deadline can fail early
    // without spending cycles traversing a large DOM.
    ctx.check_timeout()?;

    let converter = MarkdownConverter::with_options(decoded.conversion);
    let markdown = converter.convert_with_context(&dom, &mut ctx)?;

    let token_estimate = if decoded.estimate_tokens {
        handle_ref.token_estimator.estimate(&markdown)
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
