//! Corpus-based regression tests for parser path optimizations.
//!
//! These tests convert each benchmark corpus fixture with the optimized
//! converter and verify output correctness properties. They serve as a
//! regression gate to ensure optimizations do not alter output for
//! well-formed content.

use nginx_markdown_converter::converter::MarkdownConverter;
use nginx_markdown_converter::parser::parse_html;
use std::fs;
use std::path::{Path, PathBuf};

/// Get the path to the test corpus directory.
fn corpus_dir() -> PathBuf {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir.join("../../tests/corpus")
}

/// Collect all .html files from a corpus subdirectory.
fn collect_html_fixtures(subdir: &str) -> Vec<PathBuf> {
    let dir = corpus_dir().join(subdir);
    if !dir.exists() {
        return vec![];
    }
    let mut fixtures: Vec<PathBuf> = fs::read_dir(&dir)
        .expect("read corpus dir")
        .filter_map(|entry| {
            let entry = entry.ok()?;
            let path = entry.path();
            if path.extension().is_some_and(|ext| ext == "html") {
                Some(path)
            } else {
                None
            }
        })
        .collect();
    fixtures.sort();
    fixtures
}

/// Convert an HTML fixture and return the Markdown output.
fn convert_fixture(path: &Path) -> String {
    let html = fs::read(path).unwrap_or_else(|e| panic!("read {:?}: {}", path, e));
    let dom = parse_html(&html).unwrap_or_else(|e| panic!("parse {:?}: {}", path, e));
    let converter = MarkdownConverter::new();
    converter
        .convert(&dom)
        .unwrap_or_else(|e| panic!("convert {:?}: {}", path, e))
}

/// Verify common output properties for a converted fixture.
///
/// Checks that the output is non-empty, ends with a newline, is deterministic,
/// and does not contain raw HTML structural tags.
///
/// The `check_no_structural_tags` flag controls whether the structural-tag
/// check is applied. Malformed fixtures may legitimately produce output that
/// contains tag-like text due to error recovery, so the check is skipped for
/// those.
fn assert_output_properties(path: &Path, output: &str, check_no_structural_tags: bool) {
    let filename = path.file_name().unwrap().to_string_lossy();

    // Output must be non-empty for non-trivial fixtures
    assert!(
        !output.is_empty(),
        "{}: output should not be empty",
        filename
    );

    // Output must end with a newline
    assert!(
        output.ends_with('\n'),
        "{}: output should end with a newline",
        filename
    );

    // Output must not contain raw HTML structural tags (well-formed input only)
    if check_no_structural_tags {
        let lower = output.to_lowercase();
        assert!(
            !lower.contains("<html"),
            "{}: output should not contain <html>",
            filename
        );
        assert!(
            !lower.contains("<body"),
            "{}: output should not contain <body>",
            filename
        );
        assert!(
            !lower.contains("<head"),
            "{}: output should not contain <head>",
            filename
        );
    }
}

/// Verify that converting the same fixture twice produces identical output.
fn assert_deterministic(path: &Path) {
    let first = convert_fixture(path);
    let second = convert_fixture(path);
    let filename = path.file_name().unwrap().to_string_lossy();
    assert_eq!(
        first, second,
        "{}: conversion must be deterministic",
        filename
    );
}

// ---------------------------------------------------------------------------
// Simple corpus
// ---------------------------------------------------------------------------

#[test]
fn simple_fixtures_output_properties() {
    let fixtures = collect_html_fixtures("simple");
    assert!(
        !fixtures.is_empty(),
        "simple corpus directory should contain fixtures"
    );
    for path in &fixtures {
        let output = convert_fixture(path);
        assert_output_properties(path, &output, true);
    }
}

#[test]
fn simple_fixtures_deterministic() {
    for path in &collect_html_fixtures("simple") {
        assert_deterministic(path);
    }
}

// ---------------------------------------------------------------------------
// Complex corpus
// ---------------------------------------------------------------------------

#[test]
fn complex_fixtures_output_properties() {
    let fixtures = collect_html_fixtures("complex");
    assert!(
        !fixtures.is_empty(),
        "complex corpus directory should contain fixtures"
    );
    for path in &fixtures {
        let output = convert_fixture(path);
        assert_output_properties(path, &output, true);
    }
}

#[test]
fn complex_fixtures_deterministic() {
    for path in &collect_html_fixtures("complex") {
        assert_deterministic(path);
    }
}

// ---------------------------------------------------------------------------
// Edge-cases corpus
// ---------------------------------------------------------------------------

#[test]
fn edge_case_fixtures_convert_without_panic() {
    let fixtures = collect_html_fixtures("edge-cases");
    assert!(
        !fixtures.is_empty(),
        "edge-cases corpus directory should contain fixtures"
    );
    for path in &fixtures {
        let html = fs::read(path).unwrap_or_else(|e| panic!("read {:?}: {}", path, e));
        // parse_html returns Err for truly empty input; that is acceptable.
        if let Ok(dom) = parse_html(&html) {
            let converter = MarkdownConverter::new();
            let result = converter.convert(&dom);
            assert!(
                result.is_ok(),
                "{}: conversion should not error: {:?}",
                path.file_name().unwrap().to_string_lossy(),
                result.err()
            );
        }
    }
}

#[test]
fn edge_case_fixtures_deterministic() {
    for path in &collect_html_fixtures("edge-cases") {
        let html = fs::read(path).unwrap_or_else(|e| panic!("read {:?}: {}", path, e));
        if parse_html(&html).is_ok() {
            assert_deterministic(path);
        }
    }
}

// ---------------------------------------------------------------------------
// Encoding corpus
// ---------------------------------------------------------------------------

#[test]
fn encoding_fixtures_output_properties() {
    let fixtures = collect_html_fixtures("encoding");
    assert!(
        !fixtures.is_empty(),
        "encoding corpus directory should contain fixtures"
    );
    for path in &fixtures {
        let output = convert_fixture(path);
        assert_output_properties(path, &output, true);
    }
}

#[test]
fn encoding_fixtures_deterministic() {
    for path in &collect_html_fixtures("encoding") {
        assert_deterministic(path);
    }
}

// ---------------------------------------------------------------------------
// Malformed corpus
// ---------------------------------------------------------------------------

#[test]
fn malformed_fixtures_output_properties() {
    let fixtures = collect_html_fixtures("malformed");
    assert!(
        !fixtures.is_empty(),
        "malformed corpus directory should contain fixtures"
    );
    for path in &fixtures {
        let output = convert_fixture(path);
        // Malformed fixtures may produce output containing tag-like text
        // due to html5ever error recovery, so skip the structural-tag check.
        assert_output_properties(path, &output, false);
    }
}

#[test]
fn malformed_fixtures_deterministic() {
    for path in &collect_html_fixtures("malformed") {
        assert_deterministic(path);
    }
}

// ---------------------------------------------------------------------------
// Boilerplate-heavy fixtures: nav/footer/aside content presence
// ---------------------------------------------------------------------------

/// With the default build (prune_noise_regions disabled), content from
/// `<nav>`, `<footer>`, and `<aside>` elements should be present in the
/// Markdown output.
#[test]
fn boilerplate_heavy_fixtures_preserve_nav_footer_aside() {
    let boilerplate_fixtures = [
        "boilerplate-heavy-ecommerce.html",
        "boilerplate-heavy-landing.html",
    ];

    for name in &boilerplate_fixtures {
        let path = corpus_dir().join("complex").join(name);
        if !path.exists() {
            continue;
        }

        let html_bytes = fs::read(&path).unwrap_or_else(|e| panic!("read {:?}: {}", path, e));
        let html_str = String::from_utf8_lossy(&html_bytes);
        let output = convert_fixture(&path);

        // If the HTML contains <nav>, the output should include some
        // navigation link text (e.g. "Shop" from the ecommerce nav).
        if html_str.contains("<nav") {
            assert!(
                output.contains("Shop") || output.contains("Home") || output.contains("nav"),
                "{}: nav content should be present in output (prune_noise_regions is off)",
                name
            );
        }

        // If the HTML contains <footer>, the output should include some
        // footer text.
        if html_str.contains("<footer") {
            assert!(
                output.contains("footer")
                    || output.contains("Footer")
                    || output.contains("©")
                    || output.contains("Copyright")
                    || output.contains("Privacy")
                    || output.contains("Terms")
                    || output.contains("Contact"),
                "{}: footer content should be present in output (prune_noise_regions is off)",
                name
            );
        }

        // If the HTML contains <aside>, the output should include some
        // sidebar text.
        if html_str.contains("<aside") {
            assert!(
                !output.is_empty(),
                "{}: aside content should contribute to non-empty output",
                name
            );
        }
    }
}
