//! LLM provider abstraction for token estimation.
//!
//! Each provider has a characteristic chars-per-token ratio derived from
//! its tokenizer behavior.  These ratios are heuristic averages suitable
//! for context-window budgeting; they are NOT a replacement for exact
//! tokenization (tiktoken, sentencepiece, etc.).

/// Known LLM providers with provider-specific token estimation constants.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum LlmProvider {
    /// Default heuristic (4.0 chars/token, English-average).
    Default = 0,
    /// OpenAI GPT family (GPT-4, GPT-4o, GPT-5, etc.; cl100k / o200k_base tokenizer).
    OpenAiGpt = 1,
    /// Anthropic Claude family (approximate, no public tokenizer).
    AnthropicClaude = 2,
    /// Google Gemini family (approximate, SentencePiece-derived).
    GoogleGemini = 3,
    /// Meta Llama family (tiktoken-compatible, ~3.8 chars/token).
    MetaLlama = 4,
}

impl LlmProvider {
    /// Return the typical chars-per-token ratio for this provider.
    ///
    /// Values are derived from empirical measurement of each provider's
    /// tokenizer on English + code mixed content.  For non-English text
    /// (especially CJK), the actual ratio is lower; operators should set
    /// `markdown_chars_per_token` explicitly for such workloads.
    pub fn chars_per_token(self) -> f32 {
        match self {
            LlmProvider::Default => 4.0,
            LlmProvider::OpenAiGpt => 3.8,
            LlmProvider::AnthropicClaude => 3.6,
            LlmProvider::GoogleGemini => 4.2,
            LlmProvider::MetaLlama => 3.8,
        }
    }

    /// Return the typical context window size (tokens) for this provider.
    ///
    /// These are the *maximum* context window sizes for the flagship model
    /// in each family as of 2025-Q4.  Used for informational headers only;
    /// the module does NOT enforce context window limits.
    pub fn context_window_tokens(self) -> u32 {
        match self {
            LlmProvider::Default => 0,
            LlmProvider::OpenAiGpt => 128_000,
            LlmProvider::AnthropicClaude => 200_000,
            LlmProvider::GoogleGemini => 1_000_000,
            LlmProvider::MetaLlama => 128_000,
        }
    }

    /// Convert from FFI u8 value to LlmProvider.
    ///
    /// Unknown values map to Default.
    pub fn from_ffi(value: u8) -> Self {
        match value {
            0 => LlmProvider::Default,
            1 => LlmProvider::OpenAiGpt,
            2 => LlmProvider::AnthropicClaude,
            3 => LlmProvider::GoogleGemini,
            4 => LlmProvider::MetaLlama,
            _ => LlmProvider::Default,
        }
    }
}

impl Default for LlmProvider {
    fn default() -> Self {
        LlmProvider::Default
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_provider_chars_per_token() {
        assert_eq!(LlmProvider::Default.chars_per_token(), 4.0);
        assert_eq!(LlmProvider::OpenAiGpt.chars_per_token(), 3.8);
        assert_eq!(LlmProvider::AnthropicClaude.chars_per_token(), 3.6);
        assert_eq!(LlmProvider::GoogleGemini.chars_per_token(), 4.2);
        assert_eq!(LlmProvider::MetaLlama.chars_per_token(), 3.8);
    }

    #[test]
    fn test_provider_context_window() {
        assert_eq!(LlmProvider::Default.context_window_tokens(), 0);
        assert_eq!(LlmProvider::OpenAiGpt.context_window_tokens(), 128_000);
        assert_eq!(LlmProvider::AnthropicClaude.context_window_tokens(), 200_000);
        assert_eq!(LlmProvider::GoogleGemini.context_window_tokens(), 1_000_000);
    }

    #[test]
    fn test_from_ffi_roundtrip() {
        for v in 0u8..=4 {
            assert_eq!(LlmProvider::from_ffi(v) as u8, v);
        }
    }

    #[test]
    fn test_from_ffi_unknown() {
        assert_eq!(LlmProvider::from_ffi(255), LlmProvider::Default);
    }

    #[test]
    fn test_default_is_default() {
        assert_eq!(LlmProvider::default(), LlmProvider::Default);
    }
}
