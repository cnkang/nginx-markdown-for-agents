# TokenEstimator Implementation

## Overview

The `TokenEstimator` provides a fast, character-based heuristic for estimating token counts in Markdown text. This is useful for LLM context window planning when converting HTML to Markdown.

## Implementation Details

### Algorithm

The estimator uses a simple formula:
```
estimated_tokens = ceil(character_count / chars_per_token)
```

- **Default**: 4.0 characters per token (reasonable for English text)
- **Configurable**: Can be adjusted for different languages or use cases
- **Fast**: No tokenizer dependency, just character counting
- **Approximate**: Not a replacement for actual tokenization

### API

```rust
pub struct TokenEstimator {
    chars_per_token: f32,
}

impl TokenEstimator {
    // Create with default settings (4.0 chars/token)
    pub fn new() -> Self
    
    // Create with custom chars_per_token
    pub fn with_chars_per_token(chars_per_token: f32) -> Self
    
    // Estimate token count for given Markdown text
    pub fn estimate(&self, markdown: &str) -> u32
}
```

### Usage Examples

#### Basic Usage

```rust
use nginx_markdown_converter::token_estimator::TokenEstimator;

let estimator = TokenEstimator::new();
let markdown = "# Hello World\n\nThis is a test.";
let tokens = estimator.estimate(markdown);
println!("Estimated tokens: {}", tokens);
```

#### Custom Configuration

```rust
// Conservative estimate (assumes more tokens)
let estimator = TokenEstimator::with_chars_per_token(3.0);

// Optimistic estimate (assumes fewer tokens)
let estimator = TokenEstimator::with_chars_per_token(5.0);
```

#### With Conversion

```rust
use nginx_markdown_converter::converter::MarkdownConverter;
use nginx_markdown_converter::parser::parse_html;
use nginx_markdown_converter::token_estimator::TokenEstimator;

let html = b"<h1>Title</h1><p>Content</p>";
let dom = parse_html(html)?;
let converter = MarkdownConverter::new();
let markdown = converter.convert(&dom)?;

let estimator = TokenEstimator::new();
let tokens = estimator.estimate(&markdown);
println!("Markdown will use approximately {} tokens", tokens);
```

## Test Coverage

The implementation includes comprehensive unit tests covering:

1. **Basic estimation** - Simple text with default settings
2. **Default chars_per_token** - Verify default is 4.0
3. **Custom chars_per_token** - Test with different divisors (3.0, 5.0)
4. **Ceiling behavior** - Verify always rounds up
5. **Unicode characters** - Emoji, CJK characters, mixed content
6. **Markdown content** - Formatted text, code blocks
7. **Large text** - 1000+ character documents
8. **Whitespace handling** - Spaces, newlines count as characters
9. **Default trait** - Verify Default implementation
10. **Return type** - Verify returns u32

All tests pass successfully.

## Limitations

### Known Limitations

1. **CJK Languages**: Character-based estimation is less accurate for Chinese, Japanese, Korean
   - Fewer spaces, different tokenization patterns
   - May need adjusted chars_per_token value

2. **Emoji and Unicode**: May skew estimates
   - Single emoji = 1 character but may be multiple tokens
   - Consider this when estimating emoji-heavy content

3. **Code Blocks**: Different tokenization patterns
   - Code may tokenize differently than prose
   - Estimate is still reasonable for mixed content

4. **Model-Specific**: Token counts vary by model
   - GPT-4 vs Claude vs Llama have different tokenizers
   - This is a general approximation

### Recommendations

- Use default 4.0 for English prose
- Consider 3.0 for conservative estimates (more tokens)
- Consider 5.0 for optimistic estimates (fewer tokens)
- For CJK content, consider 2.5-3.0
- For code-heavy content, consider 3.5

## Requirements Satisfied

- ✅ FR-15.1: Estimate token count using character-based algorithm
- ✅ Default: 4 characters per token
- ✅ Configurable chars_per_token
- ✅ Returns u32 for HTTP header compatibility
- ✅ Fast computation (no tokenizer dependency)
- ✅ Comprehensive test coverage

## Future Enhancements (Out of Scope for v1)

- Language detection for automatic adjustment
- Token breakdown by section (headers, content, code)
- Model-specific estimation profiles
- Actual tokenizer integration for precise counts
