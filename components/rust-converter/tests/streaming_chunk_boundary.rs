#![cfg(feature = "streaming")]

#[path = "known_differences.rs"]
mod known_differences;
#[path = "streaming_test_support.rs"]
mod streaming_test_support;

use known_differences::{KnownDifferences, OutputDifference};
use proptest::prelude::*;
use streaming_test_support::{
    convert_streaming_chunked, convert_streaming_single, default_streaming_budget,
    default_streaming_options, diff_summary, discover_html_fixtures, fixture_relative_name,
    known_differences_path, normalize_whitespace_tokens, single_byte_chunks, tag_boundary_chunks,
    utf8_mid_char_chunks,
};

fn arb_streaming_html() -> impl Strategy<Value = String> {
    prop::collection::vec(
        prop::sample::select(vec![
            "<h1>Heading</h1>".to_string(),
            "<p>Paragraph alpha beta gamma.</p>".to_string(),
            "<p>UTF-8 content: café résumé naïve 你好</p>".to_string(),
            "<blockquote><p>Quote</p></blockquote>".to_string(),
            "<ul><li>A</li><li>B</li></ul>".to_string(),
            "<pre><code>fn main() {}</code></pre>".to_string(),
            "<p><strong>bold</strong> <em>italic</em> <code>inline</code></p>".to_string(),
        ]),
        1..6,
    )
    .prop_map(|parts| {
        format!(
            "<!DOCTYPE html><html><body>{}</body></html>",
            parts.join("")
        )
    })
}

fn arb_chunk_splits(n: usize) -> BoxedStrategy<Vec<usize>> {
    if n == 0 {
        return Just(Vec::new()).boxed();
    }

    prop::collection::vec(1usize..=4096usize, 1..=64)
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

fn assert_equal_or_known_difference(
    fixture_name: &str,
    lhs: &str,
    rhs: &str,
    known: &KnownDifferences,
) -> Result<(), String> {
    if lhs == rhs {
        return Ok(());
    }

    let whitespace_only_drift =
        normalize_whitespace_tokens(lhs) == normalize_whitespace_tokens(rhs);
    let diff = if whitespace_only_drift {
        format!("whitespace-only-parity-drift\n{}", diff_summary(lhs, rhs))
    } else {
        diff_summary(lhs, rhs)
    };

    let output = OutputDifference {
        full_buffer: lhs,
        streaming: rhs,
        diff: &diff,
    };

    if known.matches(fixture_name, &output).is_some() {
        return Ok(());
    }

    if whitespace_only_drift {
        return Err(format!(
            "whitespace-only chunk drift for {} is not registered\n{}\n--- lhs ---\n{}\n--- rhs ---\n{}",
            fixture_name, diff, lhs, rhs
        ));
    }

    Err(format!(
        "streaming chunk split mismatch for {}\n{}\n--- lhs ---\n{}\n--- rhs ---\n{}",
        fixture_name, diff, lhs, rhs
    ))
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    #[test]
    fn prop_chunk_split_invariance((html, split_a, split_b) in arb_streaming_html().prop_flat_map(|html| {
        let n = html.len();
        (Just(html), arb_chunk_splits(n), arb_chunk_splits(n))
    })) {
        let html_bytes = html.as_bytes();

        let run_a = convert_streaming_chunked(
            html_bytes,
            &split_a,
            Some("text/html; charset=UTF-8"),
            default_streaming_options(),
            default_streaming_budget(),
            None,
        );

        let run_b = convert_streaming_chunked(
            html_bytes,
            &split_b,
            Some("text/html; charset=UTF-8"),
            default_streaming_options(),
            default_streaming_budget(),
            None,
        );

        match (run_a, run_b) {
            (Ok(a), Ok(b)) => {
                prop_assert_eq!(
                    a.markdown,
                    b.markdown,
                    "chunk split changed output\nsplit_a={:?}\nsplit_b={:?}\nhtml={}",
                    split_a,
                    split_b,
                    html,
                );
            }
            (Err(err_a), Err(err_b)) => {
                prop_assert_eq!(
                    std::mem::discriminant(&err_a),
                    std::mem::discriminant(&err_b),
                    "chunk split produced different error variants\nsplit_a={:?}\nsplit_b={:?}\nhtml={}\nerr_a={:?}\nerr_b={:?}",
                    split_a,
                    split_b,
                    html,
                    err_a,
                    err_b,
                );
            }
            (Ok(_), Err(err)) => {
                prop_assert!(false, "split_a succeeded but split_b failed: {err}");
            }
            (Err(err), Ok(_)) => {
                prop_assert!(false, "split_a failed but split_b succeeded: {err}");
            }
        }
    }
}

#[test]
fn corpus_single_byte_chunk_invariance() {
    let corpus = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/corpus");
    let known = KnownDifferences::from_file(&known_differences_path())
        .unwrap_or_else(|err| panic!("load known differences: {err}"));
    let mut failures = Vec::new();

    for category in [
        "simple",
        "complex",
        "malformed",
        "encoding",
        "edge-cases",
        "streaming",
    ] {
        for path in discover_html_fixtures(&corpus.join(category)) {
            let html = std::fs::read(&path)
                .unwrap_or_else(|err| panic!("read fixture {}: {err}", path.display()));
            let fixture_name = fixture_relative_name(&path);

            let single = convert_streaming_single(
                &html,
                Some("text/html; charset=UTF-8"),
                default_streaming_options(),
                default_streaming_budget(),
                None,
            );

            let byte_by_byte = convert_streaming_chunked(
                &html,
                &single_byte_chunks(html.len()),
                Some("text/html; charset=UTF-8"),
                default_streaming_options(),
                default_streaming_budget(),
                None,
            );

            match (single, byte_by_byte) {
                (Ok(a), Ok(b)) => {
                    if let Err(err) = assert_equal_or_known_difference(
                        &fixture_name,
                        &a.markdown,
                        &b.markdown,
                        &known,
                    ) {
                        failures.push(err);
                    }
                }
                (Err(_), Err(_)) => {}
                (Ok(_), Err(err)) => panic!(
                    "single chunk succeeded but byte-by-byte failed for {}: {err}",
                    path.display()
                ),
                (Err(err), Ok(_)) => panic!(
                    "single chunk failed but byte-by-byte succeeded for {}: {err}",
                    path.display()
                ),
            }
        }
    }

    assert!(
        failures.is_empty(),
        "single-byte chunk invariance mismatches:\n{}",
        failures.join("\n\n")
    );
}

#[test]
fn utf8_mid_character_split_invariance() {
    let corpus = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/corpus");
    let known = KnownDifferences::from_file(&known_differences_path())
        .unwrap_or_else(|err| panic!("load known differences: {err}"));
    let fixtures = [
        corpus.join("encoding/utf8.html"),
        corpus.join("encoding/utf8-bom.html"),
        corpus.join("streaming/charset-mismatch.html"),
    ];
    let mut failures = Vec::new();

    for fixture in fixtures {
        let html = std::fs::read(&fixture)
            .unwrap_or_else(|err| panic!("read fixture {}: {err}", fixture.display()));
        let splits = utf8_mid_char_chunks(&html);

        let single = convert_streaming_single(
            &html,
            Some("text/html; charset=UTF-8"),
            default_streaming_options(),
            default_streaming_budget(),
            None,
        );

        let chunked = convert_streaming_chunked(
            &html,
            &splits,
            Some("text/html; charset=UTF-8"),
            default_streaming_options(),
            default_streaming_budget(),
            None,
        );

        match (single, chunked) {
            (Ok(a), Ok(b)) => {
                if let Err(err) = assert_equal_or_known_difference(
                    &fixture_relative_name(&fixture),
                    &a.markdown,
                    &b.markdown,
                    &known,
                ) {
                    failures.push(err);
                }
            }
            (Err(_), Err(_)) => {}
            (Ok(_), Err(err)) => panic!(
                "single chunk succeeded but UTF-8 split failed for {}: {err}",
                fixture.display()
            ),
            (Err(err), Ok(_)) => panic!(
                "single chunk failed but UTF-8 split succeeded for {}: {err}",
                fixture.display()
            ),
        }
    }

    assert!(
        failures.is_empty(),
        "UTF-8 mid-character split invariance mismatches:\n{}",
        failures.join("\n\n")
    );
}

#[test]
fn html_tag_boundary_split_invariance() {
    let corpus =
        std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/corpus/streaming");
    let known = KnownDifferences::from_file(&known_differences_path())
        .unwrap_or_else(|err| panic!("load known differences: {err}"));
    let mut failures = Vec::new();

    for fixture in discover_html_fixtures(&corpus) {
        let html = std::fs::read(&fixture)
            .unwrap_or_else(|err| panic!("read fixture {}: {err}", fixture.display()));
        let splits = tag_boundary_chunks(&html);

        let single = convert_streaming_single(
            &html,
            Some("text/html; charset=UTF-8"),
            default_streaming_options(),
            default_streaming_budget(),
            None,
        );

        let chunked = convert_streaming_chunked(
            &html,
            &splits,
            Some("text/html; charset=UTF-8"),
            default_streaming_options(),
            default_streaming_budget(),
            None,
        );

        match (single, chunked) {
            (Ok(a), Ok(b)) => {
                if let Err(err) = assert_equal_or_known_difference(
                    &fixture_relative_name(&fixture),
                    &a.markdown,
                    &b.markdown,
                    &known,
                ) {
                    failures.push(err);
                }
            }
            (Err(_), Err(_)) => {}
            (Ok(_), Err(err)) => panic!(
                "single chunk succeeded but tag-boundary split failed for {}: {err}",
                fixture.display()
            ),
            (Err(err), Ok(_)) => panic!(
                "single chunk failed but tag-boundary split succeeded for {}: {err}",
                fixture.display()
            ),
        }
    }

    assert!(
        failures.is_empty(),
        "tag-boundary split invariance mismatches:\n{}",
        failures.join("\n\n")
    );
}
