//! ETag generation using BLAKE3 hashing
//!
//! This module provides ETag generation for HTTP caching using the BLAKE3 hash algorithm.
//! ETags are used to identify specific versions of resources and enable efficient caching
//! and conditional requests.
//!
//! # Algorithm
//!
//! 1. Hash the Markdown output bytes using BLAKE3
//! 2. Take the first 128 bits (16 bytes) of the hash
//! 3. Encode as hexadecimal string
//! 4. Wrap in double quotes per HTTP specification (RFC 9110)
//!
//! # Why BLAKE3?
//!
//! - **Fast**: Significantly faster than MD5, SHA-1, SHA-2
//! - **Secure**: Cryptographically secure (though not required for ETags)
//! - **Deterministic**: Same input always produces same output
//! - **Collision-resistant**: Extremely unlikely to produce same hash for different content
//!
//! # HTTP Specification Compliance
//!
//! ETags are formatted according to RFC 9110 (HTTP Semantics):
//! - Strong ETags: `"<hex-string>"` (quoted)
//! - Weak ETags: `W/"<hex-string>"` (prefixed with W/)
//!
//! # Requirements
//!
//! - **FR-04.5**: Generate ETag for Markdown variant
//! - **FR-06.4**: ETag must be consistent for identical Markdown output
//!
//! # Example
//!
//! ```
//! use nginx_markdown_converter::etag_generator::ETagGenerator;
//!
//! let generator = ETagGenerator::new();
//! let markdown = b"# Hello World\n\nThis is a test.";
//! let etag = generator.generate(markdown);
//!
//! // ETag format: "0123456789abcdef0123456789abcdef"
//! assert!(etag.starts_with('"'));
//! assert!(etag.ends_with('"'));
//! assert_eq!(etag.len(), 34); // 32 hex chars + 2 quotes
//!
//! // Same content produces same ETag
//! let etag2 = generator.generate(markdown);
//! assert_eq!(etag, etag2);
//! ```

use blake3;

/// ETag generator using BLAKE3 hash
pub struct ETagGenerator;

impl ETagGenerator {
    /// Create a new ETag generator
    pub fn new() -> Self {
        Self
    }

    /// Generate ETag from Markdown bytes
    ///
    /// Uses BLAKE3 hash (first 128 bits) formatted as quoted hex string
    /// per HTTP specification.
    ///
    /// # Arguments
    ///
    /// * `markdown` - Markdown bytes to hash
    ///
    /// # Returns
    ///
    /// ETag string in format: "hexhexhex..."
    ///
    /// # Example
    ///
    /// ```
    /// use nginx_markdown_converter::etag_generator::ETagGenerator;
    ///
    /// let generator = ETagGenerator::new();
    /// let etag = generator.generate(b"# Hello World");
    /// assert!(etag.starts_with('"'));
    /// assert!(etag.ends_with('"'));
    /// ```
    pub fn generate(&self, markdown: &[u8]) -> String {
        let hash = blake3::hash(markdown);
        let hash_bytes = hash.as_bytes();

        // Use first 16 bytes (128 bits) for ETag
        // Format as quoted hex string per HTTP spec
        format!("\"{}\"", hex::encode(&hash_bytes[..16]))
    }

    /// Generate weak ETag (W/"...")
    ///
    /// Weak ETags indicate semantic equivalence rather than byte-for-byte
    /// identity. Currently not used but provided for future extensibility.
    pub fn generate_weak(&self, markdown: &[u8]) -> String {
        format!("W/{}", self.generate(markdown))
    }
}

impl Default for ETagGenerator {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn test_etag_format() {
        let generator = ETagGenerator::new();
        let etag = generator.generate(b"test content");

        // Should be quoted
        assert!(etag.starts_with('"'));
        assert!(etag.ends_with('"'));

        // Should be hex (32 chars + 2 quotes = 34 total)
        assert_eq!(etag.len(), 34);
    }

    #[test]
    fn test_etag_consistency() {
        let generator = ETagGenerator::new();
        let content = b"consistent content";

        let etag1 = generator.generate(content);
        let etag2 = generator.generate(content);

        // Same content should produce same ETag
        assert_eq!(etag1, etag2);
    }

    #[test]
    fn test_etag_uniqueness() {
        let generator = ETagGenerator::new();

        let etag1 = generator.generate(b"content 1");
        let etag2 = generator.generate(b"content 2");

        // Different content should produce different ETags
        assert_ne!(etag1, etag2);
    }

    #[test]
    fn test_etag_empty_content() {
        let generator = ETagGenerator::new();
        let etag = generator.generate(b"");

        // Should still produce valid ETag for empty content
        assert!(etag.starts_with('"'));
        assert!(etag.ends_with('"'));
        assert_eq!(etag.len(), 34);
    }

    #[test]
    fn test_etag_large_content() {
        let generator = ETagGenerator::new();
        let large_content = vec![b'x'; 1_000_000]; // 1MB of 'x'
        let etag = generator.generate(&large_content);

        // Should handle large content efficiently
        assert!(etag.starts_with('"'));
        assert!(etag.ends_with('"'));
        assert_eq!(etag.len(), 34);
    }

    #[test]
    fn test_etag_unicode_content() {
        let generator = ETagGenerator::new();
        let unicode_content = "Hello 世界 🌍".as_bytes();
        let etag = generator.generate(unicode_content);

        // Should handle Unicode correctly
        assert!(etag.starts_with('"'));
        assert!(etag.ends_with('"'));
        assert_eq!(etag.len(), 34);
    }

    #[test]
    fn test_etag_deterministic_across_instances() {
        let generator1 = ETagGenerator::new();
        let generator2 = ETagGenerator::new();
        let content = b"deterministic test";

        let etag1 = generator1.generate(content);
        let etag2 = generator2.generate(content);

        // Different instances should produce same ETag for same content
        assert_eq!(etag1, etag2);
    }

    #[test]
    fn test_etag_hex_characters() {
        let generator = ETagGenerator::new();
        let etag = generator.generate(b"test");

        // Remove quotes and verify all characters are valid hex
        let hex_part = &etag[1..etag.len() - 1];
        assert!(hex_part.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn test_etag_128_bits() {
        let generator = ETagGenerator::new();
        let etag = generator.generate(b"test");

        // 128 bits = 16 bytes = 32 hex characters
        let hex_part = &etag[1..etag.len() - 1];
        assert_eq!(hex_part.len(), 32);
    }

    #[test]
    fn test_weak_etag_format() {
        let generator = ETagGenerator::new();
        let weak_etag = generator.generate_weak(b"test content");

        // Weak ETag should start with W/"
        assert!(weak_etag.starts_with("W/\""));
        assert!(weak_etag.ends_with('"'));
    }

    proptest! {
        /// Property 12: ETag Consistency
        /// Validates: FR-06.4
        #[test]
        fn prop_etag_consistency_for_identical_input(markdown in prop::collection::vec(any::<u8>(), 0..2048)) {
            let generator = ETagGenerator::new();

            let etag1 = generator.generate(&markdown);
            let etag2 = generator.generate(&markdown);

            prop_assert_eq!(&etag1, &etag2, "Identical input must produce identical ETag");
            prop_assert!(etag1.starts_with('"') && etag1.ends_with('"'));
            prop_assert_eq!(etag1.len(), 34);
        }

        /// Property 11: ETag Variant Differentiation
        /// Validates: FR-04.5
        #[test]
        fn prop_etag_differs_for_different_variants(
            variant_a in prop::collection::vec(any::<u8>(), 0..1024),
            variant_b in prop::collection::vec(any::<u8>(), 0..1024),
        ) {
            prop_assume!(variant_a != variant_b);

            let generator = ETagGenerator::new();
            let etag_a = generator.generate(&variant_a);
            let etag_b = generator.generate(&variant_b);

            // Truncated 128-bit BLAKE3 collisions are cryptographically negligible.
            prop_assert_ne!(etag_a, etag_b, "Different variant bytes should produce different ETags");
        }
    }
}
