//! Token count estimation for LLM context windows

/// Token estimator using character-based heuristic
pub struct TokenEstimator {
    /// Characters per token (default: 4.0 for English)
    chars_per_token: f32,
}

impl TokenEstimator {
    /// Create a new estimator with default settings
    pub fn new() -> Self {
        Self {
            chars_per_token: 4.0,
        }
    }

    /// Create a new estimator with custom chars_per_token
    pub fn with_chars_per_token(chars_per_token: f32) -> Self {
        Self { chars_per_token }
    }

    /// Estimate token count for given Markdown text
    ///
    /// Uses simple character count / chars_per_token heuristic.
    /// Fast but approximate - not a replacement for actual tokenization.
    ///
    /// # Arguments
    ///
    /// * `markdown` - Markdown text to estimate
    ///
    /// # Returns
    ///
    /// Estimated token count
    pub fn estimate(&self, markdown: &str) -> u32 {
        let char_count = markdown.chars().count();
        (char_count as f32 / self.chars_per_token).ceil() as u32
    }
}

impl Default for TokenEstimator {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn test_basic_estimation() {
        let estimator = TokenEstimator::new();

        // Empty string
        assert_eq!(estimator.estimate(""), 0);

        // Simple text (15 chars / 4 = 3.75 -> ceil = 4 tokens)
        assert_eq!(estimator.estimate("This is a test."), 4);

        // Longer text (40 chars / 4 = 10 tokens)
        assert_eq!(
            estimator.estimate("The quick brown fox jumps over the lazy"),
            10
        );
    }

    #[test]
    fn test_default_chars_per_token() {
        let estimator = TokenEstimator::new();
        // Verify default is 4.0
        assert_eq!(estimator.estimate("1234"), 1); // 4 chars / 4 = 1 token
        assert_eq!(estimator.estimate("12345"), 2); // 5 chars / 4 = 1.25 -> ceil = 2 tokens
    }

    #[test]
    fn test_custom_chars_per_token() {
        // Test with custom divisor of 3.0
        let estimator = TokenEstimator::with_chars_per_token(3.0);
        assert_eq!(estimator.estimate("123"), 1); // 3 chars / 3 = 1 token
        assert_eq!(estimator.estimate("1234"), 2); // 4 chars / 3 = 1.33 -> ceil = 2 tokens

        // Test with custom divisor of 5.0
        let estimator = TokenEstimator::with_chars_per_token(5.0);
        assert_eq!(estimator.estimate("12345"), 1); // 5 chars / 5 = 1 token
        assert_eq!(estimator.estimate("123456"), 2); // 6 chars / 5 = 1.2 -> ceil = 2 tokens
    }

    #[test]
    fn test_ceiling_behavior() {
        let estimator = TokenEstimator::new();

        // Test that we always round up
        assert_eq!(estimator.estimate("1"), 1); // 1 char / 4 = 0.25 -> ceil = 1
        assert_eq!(estimator.estimate("12"), 1); // 2 chars / 4 = 0.5 -> ceil = 1
        assert_eq!(estimator.estimate("123"), 1); // 3 chars / 4 = 0.75 -> ceil = 1
        assert_eq!(estimator.estimate("1234"), 1); // 4 chars / 4 = 1.0 -> ceil = 1
        assert_eq!(estimator.estimate("12345"), 2); // 5 chars / 4 = 1.25 -> ceil = 2
    }

    #[test]
    fn test_unicode_characters() {
        let estimator = TokenEstimator::new();

        // Unicode characters count as single chars
        assert_eq!(estimator.estimate("café"), 1); // 4 chars / 4 = 1 token
        assert_eq!(estimator.estimate("🎉🎊🎈🎁"), 1); // 4 emoji chars / 4 = 1 token

        // Mixed ASCII and Unicode
        assert_eq!(estimator.estimate("Hello 世界"), 2); // 8 chars / 4 = 2 tokens
    }

    #[test]
    fn test_markdown_content() {
        let estimator = TokenEstimator::new();

        // Markdown with formatting
        let markdown = "# Heading\n\nThis is **bold** and *italic* text.";
        let char_count = markdown.chars().count(); // 47 chars
        let expected = (char_count as f32 / 4.0).ceil() as u32; // 47 / 4 = 11.75 -> 12
        assert_eq!(estimator.estimate(markdown), expected);

        // Code block
        let code = "```rust\nfn main() {}\n```";
        let code_chars = code.chars().count(); // 24 chars
        let expected_code = (code_chars as f32 / 4.0).ceil() as u32; // 24 / 4 = 6
        assert_eq!(estimator.estimate(code), expected_code);
    }

    #[test]
    fn test_large_text() {
        let estimator = TokenEstimator::new();

        // Generate large text (1000 characters)
        let large_text = "a".repeat(1000);
        assert_eq!(estimator.estimate(&large_text), 250); // 1000 / 4 = 250 tokens

        // Generate text with 1001 characters (should round up)
        let large_text_plus = "a".repeat(1001);
        assert_eq!(estimator.estimate(&large_text_plus), 251); // 1001 / 4 = 250.25 -> 251
    }

    #[test]
    fn test_whitespace_handling() {
        let estimator = TokenEstimator::new();

        // Whitespace counts as characters
        assert_eq!(estimator.estimate("    "), 1); // 4 spaces / 4 = 1 token
        assert_eq!(estimator.estimate("\n\n\n\n"), 1); // 4 newlines / 4 = 1 token
        assert_eq!(estimator.estimate("a b c d"), 2); // 7 chars / 4 = 1.75 -> 2 tokens
    }

    #[test]
    fn test_default_trait() {
        let estimator = TokenEstimator::default();
        assert_eq!(estimator.estimate("test"), 1); // 4 chars / 4 = 1 token
    }

    #[test]
    fn test_return_type_is_u32() {
        let estimator = TokenEstimator::new();
        let result: u32 = estimator.estimate("test");
        assert_eq!(result, 1);
    }

    proptest! {
        #[test]
        fn prop_estimate_matches_default_formula(chars in prop::collection::vec(any::<char>(), 0..256)) {
            let text: String = chars.into_iter().collect();
            let estimator = TokenEstimator::new();

            let expected = (text.chars().count() as f32 / 4.0).ceil() as u32;
            prop_assert_eq!(estimator.estimate(&text), expected);
        }

        #[test]
        fn prop_estimate_is_monotonic_under_appending(
            lhs in prop::collection::vec(any::<char>(), 0..128),
            rhs in prop::collection::vec(any::<char>(), 0..128),
        ) {
            let lhs: String = lhs.into_iter().collect();
            let rhs: String = rhs.into_iter().collect();
            let combined = format!("{lhs}{rhs}");

            let estimator = TokenEstimator::new();
            let lhs_tokens = estimator.estimate(&lhs);
            let combined_tokens = estimator.estimate(&combined);

            prop_assert!(
                combined_tokens >= lhs_tokens,
                "Appending content must not reduce estimated token count"
            );
        }
    }
}
