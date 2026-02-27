//! Tests for YAML front matter generation
//!
//! This module contains comprehensive tests for YAML front matter functionality,
//! validating requirements FR-15.3, FR-15.4, FR-15.5, FR-15.6, FR-15.7, FR-15.8

    use nginx_markdown_converter::converter::{ConversionOptions, MarkdownConverter};
    use nginx_markdown_converter::parser::parse_html;
    use proptest::prelude::*;

    fn escape_html_text(value: &str) -> String {
        value
            .replace('&', "&amp;")
            .replace('<', "&lt;")
            .replace('>', "&gt;")
    }

    fn escape_html_attr(value: &str) -> String {
        escape_html_text(value)
            .replace('"', "&quot;")
            .replace('\'', "&#39;")
    }

    /// Test basic YAML front matter generation with title and URL
    /// Validates: FR-15.3, FR-15.4
    #[test]
    fn test_yaml_front_matter_basic() {
        let html = b"<html><head><title>Test Page</title><link rel=\"canonical\" href=\"https://example.com/page\"></head><body><p>Content</p></body></html>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            include_front_matter: true,
            extract_metadata: true,
            base_url: Some("https://example.com/page".to_string()),
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should start with YAML front matter delimiters
        assert!(result.starts_with("---\n"));
        assert!(result.contains("---\n\n"));

        // Should include title
        assert!(result.contains("title: \"Test Page\""));

        // Should include URL
        assert!(result.contains("url: \"https://example.com/page\""));

        // Content should come after front matter
        assert!(result.contains("Content"));
    }

    /// Test YAML front matter with all metadata fields
    /// Validates: FR-15.3, FR-15.4, FR-15.5
    #[test]
    fn test_yaml_front_matter_complete() {
        let html = b"<html><head>
            <title>Complete Page</title>
            <meta name=\"description\" content=\"A test description\">
            <meta property=\"og:image\" content=\"https://example.com/image.png\">
            <meta name=\"author\" content=\"John Doe\">
            <meta property=\"article:published_time\" content=\"2024-01-15\">
            <link rel=\"canonical\" href=\"https://example.com/complete\">
        </head><body><p>Content</p></body></html>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            include_front_matter: true,
            extract_metadata: true,
            base_url: Some("https://example.com/complete".to_string()),
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should include all metadata fields
        assert!(result.contains("title: \"Complete Page\""));
        assert!(result.contains("url: \"https://example.com/complete\""));
        assert!(result.contains("description: \"A test description\""));
        assert!(result.contains("image: \"https://example.com/image.png\""));
        assert!(result.contains("author: \"John Doe\""));
        assert!(result.contains("published: \"2024-01-15\""));
    }

    /// Test YAML front matter with special characters requiring escaping
    /// Validates: FR-15.4 (YAML escaping)
    #[test]
    fn test_yaml_front_matter_escaping() {
        let html = b"<html><head>
            <title>Title with \"quotes\" and backslash\\</title>
            <meta name=\"description\" content=\"Description: with colon\">
            <link rel=\"canonical\" href=\"https://example.com/page\">
        </head><body><p>Content</p></body></html>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            include_front_matter: true,
            extract_metadata: true,
            base_url: Some("https://example.com/page".to_string()),
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Quotes should be escaped
        assert!(result.contains("Title with \\\"quotes\\\" and backslash\\\\"));

        // Colons should be safe within quoted strings
        assert!(result.contains("Description: with colon"));
    }

    /// Test YAML front matter with newlines and tabs
    /// Validates: FR-15.4 (YAML escaping)
    #[test]
    fn test_yaml_front_matter_whitespace_escaping() {
        let html = b"<html><head>
            <title>Title\nwith\nnewlines</title>
            <meta name=\"description\" content=\"Description\twith\ttabs\">
            <link rel=\"canonical\" href=\"https://example.com/page\">
        </head><body><p>Content</p></body></html>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            include_front_matter: true,
            extract_metadata: true,
            base_url: Some("https://example.com/page".to_string()),
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Newlines should be escaped
        assert!(result.contains("Title\\nwith\\nnewlines"));

        // Tabs should be escaped
        assert!(result.contains("Description\\twith\\ttabs"));
    }

    /// Test YAML front matter with resolved absolute URLs for images
    /// Validates: FR-15.5
    #[test]
    fn test_yaml_front_matter_image_url_resolution() {
        let html = b"<html><head>
            <title>Image Test</title>
            <meta property=\"og:image\" content=\"/images/photo.jpg\">
            <link rel=\"canonical\" href=\"https://example.com/page\">
        </head><body><p>Content</p></body></html>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            include_front_matter: true,
            extract_metadata: true,
            base_url: Some("https://example.com/page".to_string()),
            resolve_relative_urls: true,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Image URL should be resolved to absolute URL
        assert!(result.contains("image: \"https://example.com/images/photo.jpg\""));
    }

    /// Test YAML front matter disabled by default
    /// Validates: FR-15.7, FR-15.8
    #[test]
    fn test_yaml_front_matter_disabled_by_default() {
        let html = b"<html><head><title>Test</title></head><body><p>Content</p></body></html>";
        let dom = parse_html(html).expect("Parse failed");

        // Default options should not include front matter
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should not start with YAML delimiters
        assert!(!result.starts_with("---\n"));
        assert!(!result.contains("title:"));
    }

    /// Test YAML front matter requires both flags enabled
    /// Validates: FR-15.6, FR-15.7, FR-15.8
    #[test]
    fn test_yaml_front_matter_requires_both_flags() {
        let html = b"<html><head><title>Test</title></head><body><p>Content</p></body></html>";
        let dom = parse_html(html).expect("Parse failed");

        // Only include_front_matter enabled (no metadata extraction)
        let options1 = ConversionOptions {
            include_front_matter: true,
            extract_metadata: false,
            ..Default::default()
        };
        let converter1 = MarkdownConverter::with_options(options1);
        let result1 = converter1.convert(&dom).expect("Conversion failed");
        assert!(!result1.starts_with("---\n"));

        // Only extract_metadata enabled (no front matter)
        let options2 = ConversionOptions {
            include_front_matter: false,
            extract_metadata: true,
            ..Default::default()
        };
        let converter2 = MarkdownConverter::with_options(options2);
        let result2 = converter2.convert(&dom).expect("Conversion failed");
        assert!(!result2.starts_with("---\n"));
    }

    /// Test YAML front matter with minimal metadata (only title)
    /// Validates: FR-15.4 (only non-empty fields included)
    #[test]
    fn test_yaml_front_matter_minimal() {
        let html = b"<html><head><title>Minimal</title></head><body><p>Content</p></body></html>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            include_front_matter: true,
            extract_metadata: true,
            base_url: Some("https://example.com/page".to_string()),
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should include title
        assert!(result.contains("title: \"Minimal\""));

        // Should include URL from base_url
        assert!(result.contains("url: \"https://example.com/page\""));

        // Should not include empty fields
        assert!(!result.contains("description:"));
        assert!(!result.contains("author:"));
    }

    /// Test YAML front matter with empty metadata
    /// Validates: FR-15.4 (only non-empty fields included)
    #[test]
    fn test_yaml_front_matter_empty_metadata() {
        let html = b"<html><head></head><body><p>Content</p></body></html>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            include_front_matter: true,
            extract_metadata: true,
            base_url: None,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should not include front matter if no metadata available
        // (or should include empty front matter - implementation choice)
        // Current implementation: no front matter if extraction fails
        assert!(!result.starts_with("---\n") || result.starts_with("---\n---\n\n"));
    }

    /// Test YAML front matter format structure
    /// Validates: FR-15.4
    #[test]
    fn test_yaml_front_matter_format() {
        let html = b"<html><head>
            <title>Format Test</title>
            <link rel=\"canonical\" href=\"https://example.com/page\">
        </head><body><p>Content</p></body></html>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            include_front_matter: true,
            extract_metadata: true,
            base_url: Some("https://example.com/page".to_string()),
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should have proper YAML structure
        let lines: Vec<&str> = result.lines().collect();
        assert_eq!(lines[0], "---");

        // Find closing delimiter (skip first line)
        let closing_idx = lines
            .iter()
            .skip(1)
            .position(|&line| line == "---")
            .unwrap()
            + 1;
        assert!(closing_idx > 0);

        // Should have blank line after closing delimiter
        if closing_idx + 1 < lines.len() {
            assert_eq!(lines[closing_idx + 1], "");
        }
    }

    /// Test YAML front matter with Unicode characters
    /// Validates: FR-03.5, FR-15.4
    #[test]
    fn test_yaml_front_matter_unicode() {
        let html = b"<html><head>
            <title>\xE4\xB8\xAD\xE6\x96\x87\xE6\xA0\x87\xE9\xA2\x98</title>
            <meta name=\"description\" content=\"\xE6\x8F\x8F\xE8\xBF\xB0\">
            <link rel=\"canonical\" href=\"https://example.com/page\">
        </head><body><p>Content</p></body></html>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            include_front_matter: true,
            extract_metadata: true,
            base_url: Some("https://example.com/page".to_string()),
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should preserve Unicode characters
        assert!(result.contains("中文标题"));
        assert!(result.contains("描述"));
    }

    proptest! {
        /// Property 26: YAML Front Matter Structure
        /// Validates: FR-15.3, FR-15.4
        #[test]
        fn prop_yaml_front_matter_has_valid_block_structure(
            title in "[A-Za-z0-9 \"\\\\]{1,32}",
            description in "[A-Za-z0-9 :\"\\\\]{0,48}",
            body_text in "[A-Za-z0-9 ]{1,40}",
            use_canonical in any::<bool>(),
        ) {
            let mut html = String::from("<html><head>");
            html.push_str(&format!("<title>{}</title>", escape_html_text(&title)));
            if !description.is_empty() {
                html.push_str(&format!(
                    "<meta name=\"description\" content=\"{}\">",
                    escape_html_attr(&description)
                ));
            }
            if use_canonical {
                html.push_str("<link rel=\"canonical\" href=\"https://example.com/canonical\">");
            }
            html.push_str("</head><body>");
            html.push_str(&format!("<p>{}</p>", escape_html_text(&body_text)));
            html.push_str("</body></html>");

            let dom = parse_html(html.as_bytes()).expect("Parse failed");
            let options = ConversionOptions {
                include_front_matter: true,
                extract_metadata: true,
                base_url: Some("https://example.com/base/page".to_string()),
                ..Default::default()
            };
            let converter = MarkdownConverter::with_options(options);
            let result = converter.convert(&dom).expect("Conversion failed");

            prop_assert!(result.starts_with("---\n"), "Front matter must start with opening delimiter");

            let lines: Vec<&str> = result.lines().collect();
            let closing_idx = lines
                .iter()
                .enumerate()
                .skip(1)
                .find_map(|(idx, line)| (*line == "---").then_some(idx))
                .expect("closing YAML delimiter should exist");

            prop_assert!(closing_idx >= 2, "Front matter should contain at least title/url lines");

            let expected_body = body_text.split_whitespace().collect::<Vec<_>>().join(" ");
            if !expected_body.is_empty() {
                prop_assert!(
                    closing_idx + 1 < lines.len(),
                    "Front matter with non-empty body must include a separator line"
                );
                prop_assert_eq!(
                    lines[closing_idx + 1],
                    "",
                    "Front matter must be followed by a blank line"
                );
            }

            for line in &lines[1..closing_idx] {
                prop_assert!(
                    line.starts_with("title: ")
                        || line.starts_with("url: ")
                        || line.starts_with("description: ")
                        || line.starts_with("image: ")
                        || line.starts_with("author: ")
                        || line.starts_with("published: "),
                    "Unexpected front matter key line: {line}"
                );
                prop_assert!(line.contains(": \""), "Front matter values must use quoted YAML strings: {line}");
                prop_assert!(line.ends_with('"'), "Front matter values must end with a quote: {line}");
            }

            if !expected_body.is_empty() {
                prop_assert!(
                    result.contains(&expected_body),
                    "Markdown body should be preserved after front matter"
                );
            }
        }
    }
