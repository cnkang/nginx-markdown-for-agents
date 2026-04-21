//! Three-way parity tests comparing full-buffer, incremental, and streaming conversion.
//!
//! Validates that all three conversion paths (full-buffer, incremental, and
//! streaming) produce consistent Markdown output for the same HTML input.
//! Uses both fixture-based and property-based testing to ensure the incremental
//! and streaming converters remain faithful to the reference full-buffer
//! implementation. Divergences are classified as whitespace-only drift,
//! ordered-list numbering drift, or unexpected mismatches, with known
//! differences consulted before flagging a regression. Requires both
//! `incremental` and `streaming` features to be enabled.

#![cfg(all(feature = "incremental", feature = "streaming"))]

#[path = "known_differences.rs"]
mod known_differences;
#[path = "streaming_test_support.rs"]
mod streaming_test_support;

use known_differences::{KnownDifferences, OutputDifference};
use proptest::prelude::*;
use streaming_test_support::{
    convert_full_buffer, convert_incremental, convert_streaming_chunked, convert_streaming_single,
    default_streaming_budget, default_streaming_options, fixture_relative_name,
    known_differences_path, normalize_whitespace_tokens,
};

use nginx_markdown_converter::error::ConversionError;

#[derive(Debug)]
struct ThreeWayResult {
    full_buffer: String,
    incremental: String,
    streaming: String,
    fb_eq_incr: bool,
}

fn normalize_whitespace_for_diff(input: &str) -> String {
    normalize_whitespace_tokens(input)
}

fn normalize_ordered_list_numbering(input: &str) -> String {
    let mut normalized = String::new();

    for line in input.lines() {
        let bytes = line.as_bytes();
        let mut prefix_end = 0usize;
        while prefix_end < bytes.len() && bytes[prefix_end].is_ascii_whitespace() {
            prefix_end += 1;
        }

        let mut digits_end = prefix_end;
        while digits_end < bytes.len() && bytes[digits_end].is_ascii_digit() {
            digits_end += 1;
        }

        if digits_end > prefix_end && digits_end < bytes.len() && bytes[digits_end] == b'.' {
            normalized.push_str(&line[..prefix_end]);
            normalized.push_str("1.");
            normalized.push_str(&line[digits_end + 1..]);
        } else {
            normalized.push_str(line);
        }
        normalized.push('\n');
    }

    normalize_whitespace_for_diff(&normalized)
}

fn assert_streaming_matches_or_known(
    fixture_name: &str,
    full: &str,
    streaming: &str,
    known: &KnownDifferences,
) {
    if full == streaming {
        return;
    }

    let whitespace_only_drift =
        normalize_whitespace_for_diff(full) == normalize_whitespace_for_diff(streaming);
    let ordered_list_numbering_drift =
        normalize_ordered_list_numbering(full) == normalize_ordered_list_numbering(streaming);
    let diff_marker = if whitespace_only_drift {
        "whitespace-only-parity-drift"
    } else if ordered_list_numbering_drift {
        "ordered-list-numbering-drift"
    } else {
        "three-way-streaming-mismatch"
    };

    let diff_text = format!("{diff_marker}\n--- full ---\n{full}\n--- streaming ---\n{streaming}");
    let diff = OutputDifference {
        full_buffer: full,
        streaming,
        diff: &diff_text,
    };

    if let Some(entry) = known.matches(fixture_name, &diff) {
        assert!(
            entry.acceptable,
            "{fixture_name}: matched known diff {} but acceptable=false",
            entry.id
        );
        return;
    }

    if whitespace_only_drift || ordered_list_numbering_drift {
        panic!(
            "{fixture_name}: known drift is not registered\n{}",
            diff_text
        );
    }

    panic!(
        "{fixture_name}: unregistered streaming mismatch\n{}",
        diff_text
    );
}

fn arb_text() -> impl Strategy<Value = String> {
    "[A-Za-z0-9][A-Za-z0-9 ]{0,39}"
}

fn arb_element() -> impl Strategy<Value = String> {
    let heading =
        (1u8..=6u8, arb_text()).prop_map(|(level, text)| format!("<h{level}>{text}</h{level}>"));

    let paragraph = arb_text().prop_map(|t| format!("<p>{t}</p>"));

    let unordered_list = prop::collection::vec(arb_text(), 1..=4).prop_map(|items| {
        let lis: String = items.iter().map(|i| format!("<li>{i}</li>")).collect();
        format!("<ul>{lis}</ul>")
    });

    let code_inline = arb_text().prop_map(|t| format!("<code>{t}</code>"));
    let code_block = arb_text().prop_map(|t| format!("<pre><code>{t}</code></pre>"));

    prop_oneof![heading, paragraph, unordered_list, code_inline, code_block,]
}

fn arb_html_document() -> impl Strategy<Value = String> {
    prop::collection::vec(arb_element(), 1..=8).prop_map(|elements| {
        let body: String = elements.join("\n");
        format!("<!DOCTYPE html><html><head><title>Test</title></head><body>{body}</body></html>")
    })
}

fn arb_chunk_splits(n: usize) -> BoxedStrategy<Vec<usize>> {
    if n == 0 {
        return Just(Vec::new()).boxed();
    }

    prop::collection::vec(1usize..=2048usize, 1..=32)
        .prop_map(move |raw| {
            let mut remaining = n;
            let mut out = Vec::new();
            for size in raw {
                if remaining == 0 {
                    break;
                }
                let take = size.min(remaining);
                out.push(take);
                remaining -= take;
            }
            if remaining > 0 {
                out.push(remaining);
            }
            out
        })
        .boxed()
}

fn convert_and_compare_three_way(html: &[u8]) -> Result<ThreeWayResult, String> {
    let options = default_streaming_options();

    let full_buffer = convert_full_buffer(html, None, options.clone())
        .map_err(|err| format!("full-buffer failed: {err}"))?;

    let incremental = convert_incremental(html, None, options.clone())
        .map_err(|err| format!("incremental failed: {err}"))?;

    let streaming = match convert_streaming_single(
        html,
        Some("text/html; charset=UTF-8"),
        options,
        default_streaming_budget(),
        None,
    ) {
        Ok(run) => run.markdown,
        Err(ConversionError::StreamingFallback { .. }) => full_buffer.clone(),
        Err(err) => return Err(format!("streaming failed: {err}")),
    };

    Ok(ThreeWayResult {
        fb_eq_incr: full_buffer == incremental,
        full_buffer,
        incremental,
        streaming,
    })
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(256))]

    #[test]
    fn prop_three_way_equivalence(html in arb_html_document()) {
        let known = KnownDifferences::from_file(&known_differences_path())
            .expect("load known differences");
        let result = convert_and_compare_three_way(html.as_bytes())
            .unwrap_or_else(|err| panic!("three-way conversion failed: {err}\nhtml={html}"));

        prop_assert!(
            result.fb_eq_incr,
            "full/incremental mismatch\nfull={}\nincremental={}",
            result.full_buffer,
            result.incremental,
        );

        assert_streaming_matches_or_known(
            "generated/prop-three-way-equivalence.html",
            &result.full_buffer,
            &result.streaming,
            &known,
        );
    }

    #[test]
    fn prop_three_way_chunked((html, chunk_splits) in arb_html_document().prop_flat_map(|html| {
        let n = html.len();
        (Just(html), arb_chunk_splits(n))
    })) {
        let html_bytes = html.as_bytes();
        let options = default_streaming_options();
        let known = KnownDifferences::from_file(&known_differences_path())
            .expect("load known differences");

        let full = convert_full_buffer(html_bytes, None, options.clone())
            .unwrap_or_else(|err| panic!("full-buffer failed: {err}"));
        let incremental = convert_incremental(html_bytes, None, options.clone())
            .unwrap_or_else(|err| panic!("incremental failed: {err}"));

        let streaming = match convert_streaming_chunked(
            html_bytes,
            &chunk_splits,
            Some("text/html; charset=UTF-8"),
            options,
            default_streaming_budget(),
            None,
        ) {
            Ok(run) => run.markdown,
            Err(ConversionError::StreamingFallback { .. }) => full.clone(),
            Err(err) => panic!("streaming chunked failed: {err}"),
        };

        prop_assert_eq!(&full, &incremental, "full/incremental mismatch");
        assert_streaming_matches_or_known(
            "generated/prop-three-way-chunked.html",
            &full,
            &streaming,
            &known,
        );
    }
}

#[test]
fn deterministic_boundary_cases() {
    let known =
        KnownDifferences::from_file(&known_differences_path()).expect("load known differences");
    let nested_fixture = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/corpus/streaming/nested-lists-deep.html");
    let nested_html = std::fs::read_to_string(&nested_fixture)
        .unwrap_or_else(|err| panic!("read {}: {err}", nested_fixture.display()));

    let cases = vec![
        (
            "single-heading",
            "generated/single-heading.html".to_string(),
            "<!DOCTYPE html><html><body><h1>Hello</h1></body></html>".to_string(),
        ),
        (
            "single-paragraph",
            "generated/single-paragraph.html".to_string(),
            "<!DOCTYPE html><html><body><p>Paragraph</p></body></html>".to_string(),
        ),
        (
            "large-nested",
            fixture_relative_name(&nested_fixture),
            nested_html,
        ),
        (
            "unicode",
            "generated/unicode.html".to_string(),
            "<!DOCTYPE html><html><body><p>Unicode: café 你好 Привет مرحبا</p></body></html>"
                .to_string(),
        ),
    ];

    for (name, fixture_name, html) in cases {
        let result = convert_and_compare_three_way(html.as_bytes())
            .unwrap_or_else(|err| panic!("{name}: conversion failed: {err}"));
        assert!(result.fb_eq_incr, "{name}: full != incremental");
        assert_streaming_matches_or_known(
            &fixture_name,
            &result.full_buffer,
            &result.streaming,
            &known,
        );
    }
}

#[test]
fn empty_input_boundary_case() {
    let options = default_streaming_options();
    let html: &[u8] = b"";

    let full = convert_full_buffer(html, None, options.clone());
    let incremental = convert_incremental(html, None, options.clone());
    let streaming = convert_streaming_single(
        html,
        Some("text/html; charset=UTF-8"),
        options,
        default_streaming_budget(),
        None,
    );

    assert!(full.is_err(), "empty input should fail on full-buffer path");
    assert!(
        incremental.is_err(),
        "empty input should fail on incremental path"
    );
    assert!(
        streaming.is_err() || streaming.as_ref().is_ok_and(|run| run.markdown.is_empty()),
        "streaming should fail or return empty markdown on empty input"
    );
}

#[test]
fn table_fallback_preserves_full_buffer_equivalence() {
    let html =
        b"<!DOCTYPE html><html><body><table><tr><td>A</td><td>B</td></tr></table></body></html>";
    let options = default_streaming_options();

    let full = convert_full_buffer(html, None, options.clone())
        .expect("full-buffer conversion should succeed for table fixture");
    let incremental = convert_incremental(html, None, options.clone())
        .expect("incremental conversion should succeed for table fixture");

    assert_eq!(full, incremental, "full and incremental output mismatch");

    let streaming = convert_streaming_single(
        html,
        Some("text/html; charset=UTF-8"),
        options,
        default_streaming_budget(),
        None,
    );

    match streaming {
        Err(ConversionError::StreamingFallback { .. }) => {}
        Ok(run) => assert_eq!(
            full, run.markdown,
            "if streaming does not fallback, output must still equal full-buffer"
        ),
        Err(err) => panic!("unexpected streaming error: {err}"),
    }
}
