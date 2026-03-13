use crate::converter::{ConversionContext, MarkdownConverter};
use crate::error::ConversionError;
use crate::parser::parse_html_with_charset;

use super::abi::{ConversionOutput, MarkdownConverterHandle, MarkdownOptions};
use super::options::decode_options;

pub(crate) fn convert_inner(
    handle_ref: &MarkdownConverterHandle,
    html_slice: &[u8],
    options_ref: &MarkdownOptions,
) -> Result<ConversionOutput, ConversionError> {
    let decoded = decode_options(options_ref)?;

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
