use crate::parser::parse_html;

use super::{MetadataExtractor, PageMetadata};

#[test]
fn test_extract_title_from_title_tag() {
    let html = b"<html><head><title>Test Title</title></head></html>";
    let dom = parse_html(html).unwrap();
    let extractor = MetadataExtractor::new(None, false);
    let metadata = extractor.extract(&dom).unwrap();

    assert_eq!(metadata.title, Some("Test Title".to_string()));
}

#[test]
fn test_extract_title_from_og_title() {
    let html = b"<html><head>
        <title>Fallback Title</title>
        <meta property=\"og:title\" content=\"OG Title\" />
    </head></html>";
    let dom = parse_html(html).unwrap();
    let extractor = MetadataExtractor::new(None, false);
    let metadata = extractor.extract(&dom).unwrap();

    assert_eq!(metadata.title, Some("OG Title".to_string()));
}

#[test]
fn test_extract_description() {
    let html = b"<html><head>
        <meta name=\"description\" content=\"Test description\" />
    </head></html>";
    let dom = parse_html(html).unwrap();
    let extractor = MetadataExtractor::new(None, false);
    let metadata = extractor.extract(&dom).unwrap();

    assert_eq!(metadata.description, Some("Test description".to_string()));
}

#[test]
fn test_extract_og_description() {
    let html = b"<html><head>
        <meta name=\"description\" content=\"Regular description\" />
        <meta property=\"og:description\" content=\"OG description\" />
    </head></html>";
    let dom = parse_html(html).unwrap();
    let extractor = MetadataExtractor::new(None, false);
    let metadata = extractor.extract(&dom).unwrap();

    assert_eq!(metadata.description, Some("OG description".to_string()));
}

#[test]
fn test_extract_image() {
    let html = b"<html><head>
        <meta property=\"og:image\" content=\"/images/test.jpg\" />
    </head></html>";
    let dom = parse_html(html).unwrap();
    let extractor = MetadataExtractor::new(Some("https://example.com".to_string()), true);
    let metadata = extractor.extract(&dom).unwrap();

    assert_eq!(
        metadata.image,
        Some("https://example.com/images/test.jpg".to_string())
    );
}

#[test]
fn test_extract_author() {
    let html = b"<html><head>
        <meta name=\"author\" content=\"John Doe\" />
    </head></html>";
    let dom = parse_html(html).unwrap();
    let extractor = MetadataExtractor::new(None, false);
    let metadata = extractor.extract(&dom).unwrap();

    assert_eq!(metadata.author, Some("John Doe".to_string()));
}

#[test]
fn test_extract_published_date() {
    let html = b"<html><head>
        <meta property=\"article:published_time\" content=\"2024-01-15T10:30:00Z\" />
    </head></html>";
    let dom = parse_html(html).unwrap();
    let extractor = MetadataExtractor::new(None, false);
    let metadata = extractor.extract(&dom).unwrap();

    assert_eq!(metadata.published, Some("2024-01-15T10:30:00Z".to_string()));
}

#[test]
fn test_extract_canonical_url() {
    let html = b"<html><head>
        <link rel=\"canonical\" href=\"https://example.com/canonical\" />
    </head></html>";
    let dom = parse_html(html).unwrap();
    let extractor = MetadataExtractor::new(None, false);
    let metadata = extractor.extract(&dom).unwrap();

    assert_eq!(
        metadata.url,
        Some("https://example.com/canonical".to_string())
    );
}

#[test]
fn test_url_fallback_to_base_url() {
    let html = b"<html><head><title>Test</title></head></html>";
    let dom = parse_html(html).unwrap();
    let extractor = MetadataExtractor::new(Some("https://example.com/page".to_string()), true);
    let metadata = extractor.extract(&dom).unwrap();

    assert_eq!(metadata.url, Some("https://example.com/page".to_string()));
}

#[test]
fn test_resolve_absolute_url() {
    let extractor = MetadataExtractor::new(Some("https://example.com/page".to_string()), true);

    assert_eq!(
        extractor.resolve_url("https://other.com/image.jpg"),
        "https://other.com/image.jpg"
    );
}

#[test]
fn test_resolve_protocol_relative_url() {
    let extractor = MetadataExtractor::new(Some("https://example.com/page".to_string()), true);

    assert_eq!(
        extractor.resolve_url("//cdn.example.com/image.jpg"),
        "//cdn.example.com/image.jpg"
    );
}

#[test]
fn test_resolve_absolute_path() {
    let extractor =
        MetadataExtractor::new(Some("https://example.com/page/subpage".to_string()), true);

    assert_eq!(
        extractor.resolve_url("/images/logo.png"),
        "https://example.com/images/logo.png"
    );
}

#[test]
fn test_resolve_relative_path() {
    let extractor =
        MetadataExtractor::new(Some("https://example.com/page/subpage".to_string()), true);

    assert_eq!(
        extractor.resolve_url("image.jpg"),
        "https://example.com/page/image.jpg"
    );
}

#[test]
fn test_resolve_url_disabled() {
    let extractor = MetadataExtractor::new(Some("https://example.com/page".to_string()), false);

    assert_eq!(
        extractor.resolve_url("/images/logo.png"),
        "/images/logo.png"
    );
}

#[test]
fn test_resolve_url_no_base() {
    let extractor = MetadataExtractor::new(None, true);

    assert_eq!(
        extractor.resolve_url("/images/logo.png"),
        "/images/logo.png"
    );
}

#[test]
fn test_resolve_empty_url() {
    let extractor = MetadataExtractor::new(Some("https://example.com".to_string()), true);

    assert_eq!(extractor.resolve_url(""), "");
}

#[test]
fn test_malformed_base_url() {
    let extractor = MetadataExtractor::new(Some("not-a-url".to_string()), true);

    assert_eq!(extractor.resolve_url("/path"), "/path");
}

#[test]
fn test_comprehensive_metadata_extraction() {
    let html = b"<html><head>
        <title>Fallback Title</title>
        <meta property=\"og:title\" content=\"Main Title\" />
        <meta property=\"og:description\" content=\"A great description\" />
        <meta property=\"og:image\" content=\"/images/hero.jpg\" />
        <meta property=\"og:url\" content=\"https://example.com/article\" />
        <meta name=\"author\" content=\"Jane Smith\" />
        <meta property=\"article:published_time\" content=\"2024-01-15\" />
        <link rel=\"canonical\" href=\"https://example.com/canonical-url\" />
    </head></html>";

    let dom = parse_html(html).unwrap();
    let extractor = MetadataExtractor::new(Some("https://example.com/page".to_string()), true);
    let metadata = extractor.extract(&dom).unwrap();

    assert_eq!(metadata.title, Some("Main Title".to_string()));
    assert_eq!(
        metadata.description,
        Some("A great description".to_string())
    );
    assert_eq!(
        metadata.image,
        Some("https://example.com/images/hero.jpg".to_string())
    );
    assert_eq!(
        metadata.url,
        Some("https://example.com/canonical-url".to_string())
    );
    assert_eq!(metadata.author, Some("Jane Smith".to_string()));
    assert_eq!(metadata.published, Some("2024-01-15".to_string()));
}

#[test]
fn test_twitter_card_metadata() {
    let html = b"<html><head>
        <meta name=\"twitter:title\" content=\"Twitter Title\" />
        <meta name=\"description\" content=\"Fallback description\" />
        <meta name=\"twitter:description\" content=\"Twitter Description\" />
        <meta name=\"twitter:image\" content=\"https://cdn.example.com/image.jpg\" />
    </head></html>";

    let dom = parse_html(html).unwrap();
    let extractor = MetadataExtractor::new(None, false);
    let metadata = extractor.extract(&dom).unwrap();

    assert_eq!(metadata.title, Some("Twitter Title".to_string()));
    assert_eq!(
        metadata.description,
        Some("Twitter Description".to_string())
    );
    assert_eq!(
        metadata.image,
        Some("https://cdn.example.com/image.jpg".to_string())
    );
}

#[test]
fn test_page_metadata_default() {
    assert_eq!(PageMetadata::new(), PageMetadata::default());
}
