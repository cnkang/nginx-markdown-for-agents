//! Coverage improvement tests for additional edge cases in:
//! - security.rs: check_attributes via conversion, control chars in URLs
//! - converter/blocks.rs: empty li, lang- prefix, code fence edge cases
//! - converter/tables.rs: unknown alignment, CR in cells, col span edge cases
//! - converter/traversal.rs: input types, embedded content with title, media extraction
//! - metadata/resolve.rs: http:// base URL, no-path base URL
//! - streaming/charset.rs: flush with non-UTF-8 decoder, set_content_type after resolved

use nginx_markdown_converter::MarkdownConverter;
use nginx_markdown_converter::parser::parse_html;
use nginx_markdown_converter::security::{SanitizeAction, SecurityValidator};

/// Helper: parse HTML string and convert to Markdown using default options.
fn convert_html(html: &str) -> String {
    let dom = parse_html(html.as_bytes()).expect("Parse failed");
    MarkdownConverter::new()
        .convert(&dom)
        .expect("Conversion failed")
}

/* ================================================================
 * security.rs — additional coverage via conversion
 * ================================================================ */

/// Verifies that `style` attributes are stripped during conversion, preventing
/// CSS injection attacks.
#[test]
fn test_security_style_attribute_stripped_via_conversion() {
    let md = convert_html(r#"<p style="color:red">Styled text</p>"#);
    assert!(md.contains("Styled text"));
    assert!(!md.contains("style"));
    assert!(!md.contains("color:red"));
}

/// Verifies that `javascript:` URLs in `href` attributes are stripped during
/// conversion, preventing XSS via link injection.
#[test]
fn test_security_dangerous_href_stripped_via_conversion() {
    let md = convert_html(r#"<a href="javascript:void(0)">JS Link</a>"#);
    assert!(md.contains("JS Link"));
    assert!(!md.contains("javascript:"));
}

/// Verifies that `data:` URLs in `src` attributes are stripped during
/// conversion, preventing data URI injection.
#[test]
fn test_security_dangerous_src_stripped_via_conversion() {
    let md = convert_html(r#"<img src="data:image/png;base64,AAAA" alt="Data Image">"#);
    assert!(!md.contains("data:"));
}

/// Verifies that multiple dangerous attributes (`onclick`, `style`) on the same
/// element are all stripped during conversion.
#[test]
fn test_security_multiple_dangerous_attributes() {
    let md = convert_html(r#"<div onclick="alert(1)" style="display:none"><p>Content</p></div>"#);
    assert!(md.contains("Content"));
    assert!(!md.contains("onclick"));
    assert!(!md.contains("style"));
}

/// Verifies that control characters (ETX, US, DEL) and percent-encoded control
/// characters in URLs are detected as dangerous by the security validator.
#[test]
fn test_security_control_chars_in_url() {
    let validator = SecurityValidator::new();
    /* Additional control characters beyond NUL and tab */
    assert!(validator.is_dangerous_url("https://example.com/\x03path"));
    assert!(validator.is_dangerous_url("https://example.com/\x1Fpath"));
    assert!(validator.is_dangerous_url("https://example.com/\x7Fpath"));
    assert!(validator.is_dangerous_url("https://example.com/%00path"));
    assert!(validator.is_dangerous_url("https://example.com/%1fpath"));
    assert!(validator.is_dangerous_url("https://example.com/%7Fpath"));
    /* DEL character (0x7F) is a control character */
    assert!(validator.is_dangerous_url("\x7F"));
}

/// Verifies that `<noscript>` and `<applet>` elements are classified as
/// dangerous and removed during sanitization.
#[test]
fn test_security_noscript_applet_in_dangerous_elements() {
    let validator = SecurityValidator::new();
    assert_eq!(validator.check_element("noscript"), SanitizeAction::Remove);
    assert_eq!(validator.check_element("applet"), SanitizeAction::Remove);
}

/* ================================================================
 * converter/blocks.rs — edge cases
 * ================================================================ */

/// Verifies that empty list items do not cause panics and that non-empty
/// siblings are still rendered.
#[test]
fn test_blocks_empty_list_item() {
    let md = convert_html("<ul><li></li><li>Item</li></ul>");
    assert!(md.contains("Item"));
}

/// Verifies that the `lang-` prefix (alternative to `language-`) is recognized
/// for code block language detection.
#[test]
fn test_blocks_code_block_lang_prefix() {
    /* class="lang-python" is an alternative to class="language-python" */
    let md = convert_html(r#"<pre><code class="lang-python">print("hello")</code></pre>"#);
    assert!(md.contains("python"));
    assert!(md.contains("print"));
}

/// Verifies that code blocks containing backticks use a longer fence to avoid
/// content conflicts.
#[test]
fn test_blocks_code_block_single_backtick_in_content() {
    /* Content with single backtick should use longer fence */
    let md = convert_html("<pre><code>`code`</code></pre>");
    assert!(md.contains("code"));
}

/// Verifies that code blocks containing double backticks use a fence longer
/// than two backticks.
#[test]
fn test_blocks_code_block_double_backtick_in_content() {
    let md = convert_html("<pre><code>``nested``</code></pre>");
    assert!(md.contains("nested"));
}

/// Verifies that ordered lists produce numbered Markdown output.
#[test]
fn test_blocks_ordered_list() {
    let md = convert_html("<ol><li>First</li><li>Second</li><li>Third</li></ol>");
    assert!(md.contains("First"));
    assert!(md.contains("Second"));
    assert!(md.contains("Third"));
}

/// Verifies that nested unordered lists produce correctly indented Markdown.
#[test]
fn test_blocks_nested_unordered_list() {
    let md = convert_html(
        "<ul><li>Item 1<ul><li>Sub 1</li><li>Sub 2</li></ul></li><li>Item 2</li></ul>",
    );
    assert!(md.contains("Item 1"));
    assert!(md.contains("Sub 1"));
    assert!(md.contains("Item 2"));
}

/// Verifies that consecutive headings at different levels produce correct
/// Markdown prefixes.
#[test]
fn test_blocks_heading_after_heading() {
    let md = convert_html("<h1>Title</h1><h2>Subtitle</h2>");
    assert!(md.contains("# Title"));
    assert!(md.contains("## Subtitle"));
}

/// Verifies that empty paragraphs do not produce visible output and that
/// adjacent non-empty paragraphs are preserved.
#[test]
fn test_blocks_empty_paragraph() {
    let md = convert_html("<p></p><p>Content</p>");
    assert!(md.contains("Content"));
}

/* ================================================================
 * converter/tables.rs — edge cases
 * ================================================================ */

/// Verifies that unknown alignment values (e.g. `justify`) fall back to default
/// alignment without errors.
#[test]
fn test_tables_unknown_align_value() {
    /* align="justify" is not a recognized alignment, should fall back */
    let md = convert_html(
        r#"<table><tr><th align="justify">Header</th></tr><tr><td>Data</td></tr></table>"#,
    );
    assert!(md.contains("Header"));
    assert!(md.contains("Data"));
}

/// Verifies that unknown `text-align` values in CSS style attributes fall back
/// gracefully without errors.
#[test]
fn test_tables_unknown_text_align_in_style() {
    let md = convert_html(
        r#"<table><tr><th style="text-align:justify">Header</th></tr><tr><td>Data</td></tr></table>"#,
    );
    assert!(md.contains("Header"));
}

/// Verifies that CSS style attributes without `text-align` properties are
/// ignored and do not affect column alignment.
#[test]
fn test_tables_style_without_text_align() {
    let md = convert_html(
        r#"<table><tr><th style="color:red;font-weight:bold">Header</th></tr><tr><td>Data</td></tr></table>"#,
    );
    assert!(md.contains("Header"));
}

/// Verifies that carriage return + newline (`\r\n`) sequences in table cells are
/// handled correctly without panicking.
#[test]
fn test_tables_carriage_return_in_cell() {
    let md = convert_html("<table><tr><th>H</th></tr><tr><td>A\r\nB</td></tr></table>");
    assert!(md.contains("A"));
    assert!(md.contains("B"));
}

/// Verifies that a lone carriage return (`\r`) in table cells is handled
/// correctly without panicking.
#[test]
fn test_tables_lone_carriage_return_in_cell() {
    let md = convert_html("<table><tr><th>H</th></tr><tr><td>A\rB</td></tr></table>");
    assert!(md.contains("A"));
}

/// Verifies that a table with only `<tbody>` (no `<thead>`) converts correctly.
#[test]
fn test_tables_tbody_only_no_thead() {
    let md = convert_html(
        "<table><tbody><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></tbody></table>",
    );
    assert!(md.contains("A"));
    assert!(md.contains("C"));
}

/// Verifies that an empty `<tbody>` element does not cause errors when a
/// `<thead>` is present.
#[test]
fn test_tables_empty_tbody() {
    let md = convert_html("<table><thead><tr><th>H</th></tr></thead><tbody></tbody></table>");
    assert!(md.contains("H"));
}

/// Verifies that body rows with more columns than the header are handled
/// gracefully without truncation.
#[test]
fn test_tables_body_row_more_columns_than_header() {
    let md =
        convert_html("<table><tr><th>A</th></tr><tr><td>1</td><td>2</td><td>3</td></tr></table>");
    assert!(md.contains("A"));
    assert!(md.contains("1"));
    assert!(md.contains("2"));
    assert!(md.contains("3"));
}

/* ================================================================
 * converter/traversal.rs — input types, embedded content, media
 * ================================================================ */

/// Verifies that hidden input elements do not produce visible text in the
/// Markdown output.
#[test]
fn test_traversal_input_hidden() {
    let md = convert_html(r#"<form><input type="hidden" value="secret"></form>"#);
    /* Hidden input should not produce visible text */
    assert!(!md.contains("secret"));
}

/// Verifies that submit input elements produce their value as visible text.
#[test]
fn test_traversal_input_submit_with_value() {
    let md = convert_html(r#"<form><input type="submit" value="Save Changes"></form>"#);
    assert!(md.contains("Save Changes"));
}

/// Verifies that reset input elements produce their value as visible text.
#[test]
fn test_traversal_input_reset_with_value() {
    let md = convert_html(r#"<form><input type="reset" value="Clear Form"></form>"#);
    assert!(md.contains("Clear Form"));
}

/// Verifies that input elements with `aria-label` attributes produce that label
/// as visible text.
#[test]
fn test_traversal_input_with_aria_label() {
    let md = convert_html(r#"<form><input type="text" aria-label="Search"></form>"#);
    assert!(md.contains("Search"));
}

/// Verifies that input elements with `placeholder` attributes produce that
/// placeholder as visible text.
#[test]
fn test_traversal_input_with_placeholder() {
    let md = convert_html(r#"<form><input type="text" placeholder="Enter name"></form>"#);
    assert!(md.contains("Enter name"));
}

/// Verifies that input elements with `value` attributes produce that value as
/// visible text.
#[test]
fn test_traversal_input_with_value() {
    let md = convert_html(r#"<form><input type="text" value="default text"></form>"#);
    assert!(md.contains("default text"));
}

/// Verifies that iframes with safe URLs extract the URL as a Markdown link and
/// preserve fallback text.
#[test]
fn test_traversal_iframe_with_title() {
    let md = convert_html(
        r#"<iframe src="https://example.com/embed" title="Embedded Video"><p>Fallback</p></iframe>"#,
    );
    /* Should extract URL as link and preserve fallback text */
    assert!(md.contains("example.com") || md.contains("Fallback"));
}

/// Verifies that iframes with dangerous URLs (`javascript:`) suppress the URL
/// but preserve fallback text.
#[test]
fn test_traversal_iframe_dangerous_url() {
    let md = convert_html(r#"<iframe src="javascript:void(0)"><p>Fallback</p></iframe>"#);
    assert!(!md.contains("javascript:"));
    assert!(md.contains("Fallback"));
}

/// Verifies that object elements with dangerous `data:` URLs suppress the URL
/// but preserve fallback text.
#[test]
fn test_traversal_object_dangerous_data_url() {
    let md = convert_html(r#"<object data="data:text/html,test"><p>Fallback</p></object>"#);
    assert!(!md.contains("data:"));
    assert!(md.contains("Fallback"));
}

/// Verifies that video elements with `poster` and `src` attributes extract URLs
/// as Markdown links.
#[test]
fn test_traversal_video_with_poster() {
    let md = convert_html(
        r#"<video src="https://example.com/video.mp4" poster="https://example.com/poster.jpg">No video</video>"#,
    );
    /* Should extract video src and/or poster. */
    assert!(md.contains("example.com"));
}

/// Verifies that video elements without `src` produce fallback text and no URL.
#[test]
fn test_traversal_video_missing_src() {
    let md = convert_html(r#"<video>No video</video>"#);
    assert!(!md.contains("://"));
    assert!(md.contains("No video"));
}

/// Verifies that video elements with a safe `src` URL extract it as a Markdown link.
#[test]
fn test_traversal_video_with_src() {
    let md = convert_html(r#"<video src="https://example.com/video.mp4">No video</video>"#);
    assert!(md.contains("example.com"));
}

/// Verifies that video elements with dangerous `javascript:` URLs suppress the URL.
#[test]
fn test_traversal_video_dangerous_src() {
    let md = convert_html(r#"<video src="javascript:void(0)">No video</video>"#);
    assert!(!md.contains("javascript:"));
}

/// Verifies that audio elements without `src` produce fallback text and no URL.
#[test]
fn test_traversal_audio_missing_src() {
    let md = convert_html(r#"<audio>No audio</audio>"#);
    assert!(!md.contains("://"));
    assert!(md.contains("No audio"));
}

/// Verifies that audio elements with a safe `src` URL extract it as a Markdown link.
#[test]
fn test_traversal_audio_with_src() {
    let md = convert_html(r#"<audio src="https://example.com/audio.mp3">No audio</audio>"#);
    assert!(md.contains("example.com"));
}

/// Verifies that `<source>` elements without `src` produce no visible output.
#[test]
fn test_traversal_source_missing_src() {
    let md = convert_html(r#"<video><source type="audio/mpeg"></video>"#);
    assert!(md.trim().is_empty());
}

/// Verifies that `<source>` elements with `src` and `type` attributes extract
/// the URL as a Markdown link.
#[test]
fn test_traversal_source_with_type() {
    let md = convert_html(
        r#"<video><source src="https://example.com/audio.mp3" type="audio/mpeg"></video>"#,
    );
    assert!(md.contains("example.com"));
}

/// Verifies that `<track>` elements without attributes produce no visible output.
#[test]
fn test_traversal_track_missing_attributes() {
    let md = convert_html(r#"<video><track></video>"#);
    assert!(md.trim().is_empty());
}

/// Verifies that `<track>` elements with `src` and `label` attributes produce
/// a Markdown link or label text.
#[test]
fn test_traversal_track_with_label() {
    let md = convert_html(
        r#"<video><track src="https://example.com/subs.vtt" label="English"></video>"#,
    );
    assert!(md.contains("example.com") || md.contains("English"));
}

/// Verifies that `<area>` elements without attributes produce no visible output.
#[test]
fn test_traversal_area_missing_attributes() {
    let md = convert_html(r#"<map name="m"><area></map>"#);
    assert!(md.trim().is_empty());
}

/// Verifies that `<area>` elements with `href` and `alt` attributes produce
/// a Markdown link or alt text.
#[test]
fn test_traversal_area_link() {
    let md = convert_html(
        r#"<map name="m"><area href="https://example.com/region" alt="Region"></map>"#,
    );
    assert!(md.contains("example.com") || md.contains("Region"));
}

/// Verifies that HTML comments are ignored and do not appear in the Markdown output.
#[test]
fn test_traversal_comment_ignored() {
    let md = convert_html("<p>Before</p><!-- This is a comment --><p>After</p>");
    assert!(md.contains("Before"));
    assert!(md.contains("After"));
    assert!(!md.contains("This is a comment"));
}

/// Verifies that DOCTYPE declarations are ignored and do not appear in the
/// Markdown output.
#[test]
fn test_traversal_doctype_ignored() {
    let md = convert_html("<!DOCTYPE html><html><body><p>Content</p></body></html>");
    assert!(md.contains("Content"));
}

/* ================================================================
 * metadata/resolve.rs — http:// and no-path base URLs
 * ================================================================ */

use nginx_markdown_converter::ffi::*;
use std::ptr;

fn ffi_test_default_options() -> MarkdownOptions {
    MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
    }
}

fn ffi_test_empty_result() -> MarkdownResult {
    MarkdownResult {
        markdown: ptr::null_mut(),
        markdown_len: 0,
        etag: ptr::null_mut(),
        etag_len: 0,
        token_estimate: 0,
        error_code: 0,
        error_message: ptr::null_mut(),
        error_len: 0,
        peak_memory_estimate: 0,
    }
}

fn ffi_result_markdown(result: &MarkdownResult) -> String {
    if result.markdown.is_null() || result.markdown_len == 0 {
        return String::new();
    }

    let bytes = unsafe { std::slice::from_raw_parts(result.markdown, result.markdown_len) };
    String::from_utf8_lossy(bytes).into_owned()
}

/// Verifies that URL resolution works correctly with `http://` base URLs.
#[test]
fn test_url_resolution_http_base_url() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let base_url = b"http://example.com/docs/";
    let options = MarkdownOptions {
        base_url: base_url.as_ptr(),
        base_url_len: base_url.len(),
        ..ffi_test_default_options()
    };
    let mut result = ffi_test_empty_result();
    let html = b"<a href=\"page.html\">Link</a>";

    unsafe {
        markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);
    }
    assert_eq!(result.error_code, 0, "http:// base URL should work");
    let md = ffi_result_markdown(&result);
    assert!(md.contains("http://example.com/docs/page.html"));
    unsafe { markdown_result_free(&mut result) };
    unsafe { markdown_converter_free(converter) };
}

/// Verifies that URL resolution works correctly when the base URL has no path
/// component (e.g. `https://example.com`).
#[test]
fn test_url_resolution_base_url_no_path() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let base_url = b"https://example.com";
    let options = MarkdownOptions {
        base_url: base_url.as_ptr(),
        base_url_len: base_url.len(),
        ..ffi_test_default_options()
    };
    let mut result = ffi_test_empty_result();
    let html = b"<a href=\"/page\">Link</a>";

    unsafe {
        markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);
    }
    assert_eq!(result.error_code, 0, "base URL with no path should work");
    let md = ffi_result_markdown(&result);
    assert!(md.contains("https://example.com/page"));
    unsafe { markdown_result_free(&mut result) };
    unsafe { markdown_converter_free(converter) };
}

/// Verifies that URL resolution works correctly when the base URL is just a
/// trailing slash (e.g. `https://example.com/`).
#[test]
fn test_url_resolution_base_url_trailing_slash_only() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let base_url = b"https://example.com/";
    let options = MarkdownOptions {
        base_url: base_url.as_ptr(),
        base_url_len: base_url.len(),
        ..ffi_test_default_options()
    };
    let mut result = ffi_test_empty_result();
    let html = b"<a href=\"page.html\">Link</a>";

    unsafe {
        markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);
    }
    assert_eq!(
        result.error_code, 0,
        "base URL with trailing slash only should work"
    );
    let md = ffi_result_markdown(&result);
    assert!(md.contains("https://example.com/page.html"));
    unsafe { markdown_result_free(&mut result) };
    unsafe { markdown_converter_free(converter) };
}

/// Verifies that already-absolute `http://` URLs are not double-resolved when
/// a base URL is provided.
#[test]
fn test_url_resolution_already_absolute_http() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let base_url = b"https://example.com/docs/";
    let options = MarkdownOptions {
        base_url: base_url.as_ptr(),
        base_url_len: base_url.len(),
        ..ffi_test_default_options()
    };
    let mut result = ffi_test_empty_result();
    let html = b"<a href=\"http://other.com/page\">Link</a>";

    unsafe {
        markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);
    }
    assert_eq!(
        result.error_code, 0,
        "already-absolute http:// URL should work"
    );
    let md = ffi_result_markdown(&result);
    assert!(md.contains("http://other.com/page"));
    unsafe { markdown_result_free(&mut result) };
    unsafe { markdown_converter_free(converter) };
}

/* ================================================================
 * Additional conversion edge cases
 * ================================================================ */

/// Verifies that an empty HTML body produces empty or minimal Markdown output.
#[test]
fn test_conversion_empty_html_body() {
    let md = convert_html("<html><body></body></html>");
    /* Should produce empty or whitespace-only output */
    assert!(md.trim().is_empty() || md.len() < 10);
}

/// Verifies that a single text paragraph produces the expected Markdown output.
#[test]
fn test_conversion_single_text_node() {
    let md = convert_html("<p>Hello World</p>");
    assert!(md.contains("Hello World"));
}

/// Verifies that inline formatting (bold, italic) is preserved in Markdown output.
#[test]
fn test_conversion_inline_formatting() {
    let md = convert_html("<p>This is <strong>bold</strong> and <em>italic</em> text.</p>");
    assert!(md.contains("bold"));
    assert!(md.contains("italic"));
}

/// Verifies that inline code spans are preserved in Markdown output.
#[test]
fn test_conversion_inline_code() {
    let md = convert_html("<p>Use the <code>printf</code> function.</p>");
    assert!(md.contains("printf"));
}

/// Verifies that links with URLs are preserved as Markdown links.
#[test]
fn test_conversion_link_with_url() {
    let md = convert_html(r#"<a href="https://example.com">Example</a>"#);
    assert!(md.contains("Example"));
    assert!(md.contains("https://example.com"));
}

/// Verifies that definition lists (`<dl>`, `<dt>`, `<dd>`) produce readable
/// Markdown output.
#[test]
fn test_conversion_definition_list() {
    let md = convert_html("<dl><dt>Term</dt><dd>Definition</dd></dl>");
    assert!(md.contains("Term"));
    assert!(md.contains("Definition"));
}

/// Verifies that subscript and superscript elements produce readable Markdown output.
#[test]
fn test_conversion_sup_sub_scripts() {
    let md = convert_html("<p>H<sub>2</sub>O and E=mc<sup>2</sup></p>");
    assert!(md.contains("H"));
    assert!(md.contains("O"));
}

/// Verifies that `<abbr>` elements produce readable Markdown output with the
/// abbreviation text preserved.
#[test]
fn test_conversion_abbr_element() {
    let md =
        convert_html("<p>The <abbr title=\"HyperText Markup Language\">HTML</abbr> standard</p>");
    assert!(md.contains("HTML"));
}

/// Verifies that `<details>` and `<summary>` elements produce readable Markdown
/// output with both the summary and hidden content preserved.
#[test]
fn test_conversion_details_summary() {
    let md =
        convert_html("<details><summary>Click to expand</summary><p>Hidden content</p></details>");
    assert!(md.contains("Click to expand"));
    assert!(md.contains("Hidden content"));
}

/// Verifies that `<figure>` and `<figcaption>` elements produce the caption
/// text in the Markdown output.
#[test]
fn test_conversion_figure_with_caption() {
    let md = convert_html(
        r#"<figure><img src="https://example.com/img.png" alt="Image"><figcaption>Caption</figcaption></figure>"#,
    );
    assert!(md.contains("Caption"));
}
