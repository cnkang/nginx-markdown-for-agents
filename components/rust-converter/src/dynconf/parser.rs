//! JSON parser with budget constraints and duplicate-key detection.
//!
//! Implements RFC 8259 JSON parsing with:
//! - Token budget enforcement (max 10,000 tokens)
//! - Nesting depth enforcement (max 8 levels)
//! - Duplicate-key detection within each object

use std::collections::HashMap;
use std::fmt;

/// Error kind for dynconf parsing failures.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DynconfParseErrorKind {
    /// Document exceeds the 1 MiB size limit.
    DocumentTooLarge,
    /// JSON is not valid RFC 8259.
    InvalidJson,
    /// Token budget exceeded during parsing.
    TokenBudgetExceeded,
    /// Nesting depth exceeded during parsing.
    NestingDepthExceeded,
    /// Duplicate key detected in a JSON object.
    DuplicateKey,
    /// schema_version field is missing.
    MissingSchemaVersion,
    /// schema_version is not equal to 1.
    InvalidSchemaVersion,
    /// An unknown key was found in the document.
    UnknownKey,
    /// A value has an incorrect type for its key.
    InvalidType,
    /// A value is outside the allowed range.
    ValueOutOfRange,
    /// Input is not valid UTF-8.
    InvalidUtf8,
}

/// A dynconf parse/validation error with diagnostic context.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DynconfParseError {
    /// The category of error.
    pub kind: DynconfParseErrorKind,
    /// Human-readable diagnostic message.
    pub message: String,
}

impl DynconfParseError {
    /// Create a new parse error.
    pub fn new(kind: DynconfParseErrorKind, message: String) -> Self {
        Self { kind, message }
    }
}

impl fmt::Display for DynconfParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "dynconf parse error ({:?}): {}", self.kind, self.message)
    }
}

impl std::error::Error for DynconfParseError {}

/// A parsed JSON value with position tracking.
#[derive(Debug, Clone, PartialEq)]
pub enum JsonValue {
    /// JSON null
    Null,
    /// JSON boolean
    Bool(bool),
    /// JSON number (stored as raw string for precise validation)
    Number(f64, String),
    /// JSON string
    String(String),
    /// JSON array
    Array(Vec<JsonValue>),
    /// JSON object (preserves insertion order via Vec of key-value pairs)
    Object(Vec<(String, JsonValue)>),
}

type ParseResult = Result<JsonValue, DynconfParseError>;

const JSON_OBJECT_START: u8 = b'{';
const JSON_ARRAY_START: u8 = b'[';
const JSON_STRING_START: u8 = b'"';
const JSON_TRUE_START: u8 = b't';
const JSON_FALSE_START: u8 = b'f';
const JSON_NULL_START: u8 = b'n';
const JSON_NEGATIVE: u8 = b'-';

/// State tracker for budget-constrained JSON parsing.
struct ParseState {
    /// Remaining tokens in the budget.
    tokens_remaining: usize,
    /// Maximum nesting depth allowed.
    max_depth: usize,
    /// Current parsing position in the input.
    pos: usize,
    /// The input bytes.
    input: Vec<u8>,
}

impl ParseState {
    fn new(input: Vec<u8>, max_depth: usize, token_budget: usize) -> Self {
        Self {
            tokens_remaining: token_budget,
            max_depth,
            pos: 0,
            input,
        }
    }

    fn consume_token(&mut self) -> Result<(), DynconfParseError> {
        if self.tokens_remaining == 0 {
            return Err(DynconfParseError::new(
                DynconfParseErrorKind::TokenBudgetExceeded,
                "parse token budget (10000) exceeded".to_string(),
            ));
        }
        self.tokens_remaining -= 1;
        Ok(())
    }

    fn peek(&self) -> Option<u8> {
        self.input.get(self.pos).copied()
    }

    fn advance(&mut self) -> Option<u8> {
        let b = self.input.get(self.pos).copied();
        if b.is_some() {
            self.pos += 1;
        }
        b
    }

    fn skip_whitespace(&mut self) {
        while self.pos < self.input.len() {
            match self.input[self.pos] {
                b' ' | b'\t' | b'\n' | b'\r' => self.pos += 1,
                _ => break,
            }
        }
    }

    fn remaining(&self) -> &[u8] {
        &self.input[self.pos..]
    }
}

/// Parse JSON with budget constraints and duplicate-key detection.
///
/// # Arguments
///
/// * `raw_bytes` - Raw input bytes (must be valid UTF-8)
/// * `max_depth` - Maximum allowed nesting depth
/// * `token_budget` - Maximum number of parse tokens
///
/// # Returns
///
/// A `JsonValue` representing the parsed top-level value, which must be an object.
pub fn parse_json_with_budget(
    raw_bytes: &[u8],
    max_depth: usize,
    token_budget: usize,
) -> Result<JsonValue, DynconfParseError> {
    // Validate UTF-8
    let _text = std::str::from_utf8(raw_bytes).map_err(|e| {
        DynconfParseError::new(
            DynconfParseErrorKind::InvalidUtf8,
            format!("input is not valid UTF-8: {}", e),
        )
    })?;

    let mut state = ParseState::new(raw_bytes.to_vec(), max_depth, token_budget);

    state.skip_whitespace();
    let value = parse_value(&mut state, 0)?;
    state.skip_whitespace();

    // Ensure no trailing content
    if state.pos < state.input.len() {
        return Err(DynconfParseError::new(
            DynconfParseErrorKind::InvalidJson,
            "unexpected trailing content after JSON value".to_string(),
        ));
    }

    // Top-level must be an object
    if !matches!(value, JsonValue::Object(_)) {
        return Err(DynconfParseError::new(
            DynconfParseErrorKind::InvalidJson,
            "dynconf document must be a JSON object at top level".to_string(),
        ));
    }

    Ok(value)
}

fn parse_value(state: &mut ParseState, depth: usize) -> Result<JsonValue, DynconfParseError> {
    if depth > state.max_depth {
        return Err(DynconfParseError::new(
            DynconfParseErrorKind::NestingDepthExceeded,
            format!("nesting depth exceeds maximum of {}", state.max_depth),
        ));
    }

    state.skip_whitespace();
    state.consume_token()?;

    if state.peek().is_none() {
        return Err(DynconfParseError::new(
            DynconfParseErrorKind::InvalidJson,
            "unexpected end of input".to_string(),
        ));
    }
    let first = state.peek().unwrap();

    dispatch(state, depth, first)
}

fn dispatch(state: &mut ParseState, depth: usize, first: u8) -> ParseResult {
    if first == JSON_OBJECT_START {
        return parse_object(state, depth);
    }
    if first == JSON_ARRAY_START {
        return parse_array(state, depth);
    }
    if first == JSON_STRING_START {
        return parse_string(state).map(JsonValue::String);
    }
    if first == JSON_TRUE_START || first == JSON_FALSE_START {
        return parse_bool(state);
    }
    if first == JSON_NULL_START {
        return parse_null(state);
    }
    if first == JSON_NEGATIVE || first.is_ascii_digit() {
        return parse_number(state);
    }

    Err(DynconfParseError::new(
        DynconfParseErrorKind::InvalidJson,
        format!(
            "unexpected character '{}' at position {}",
            first as char, state.pos
        ),
    ))
}

fn parse_object(state: &mut ParseState, depth: usize) -> Result<JsonValue, DynconfParseError> {
    // Consume opening '{'
    state.advance();
    state.skip_whitespace();

    let mut entries: Vec<(String, JsonValue)> = Vec::new();
    let mut seen_keys: HashMap<String, ()> = HashMap::new();

    // Empty object
    if state.peek() == Some(b'}') {
        state.advance();
        return Ok(JsonValue::Object(entries));
    }

    loop {
        state.skip_whitespace();
        state.consume_token()?;

        // Parse key
        if state.peek() != Some(b'"') {
            return Err(DynconfParseError::new(
                DynconfParseErrorKind::InvalidJson,
                format!("expected string key at position {}", state.pos),
            ));
        }
        let key = parse_string(state)?;

        // Duplicate-key detection
        if seen_keys.contains_key(&key) {
            return Err(DynconfParseError::new(
                DynconfParseErrorKind::DuplicateKey,
                format!("duplicate key '{}' in object", key),
            ));
        }
        seen_keys.insert(key.clone(), ());

        // Expect colon
        state.skip_whitespace();
        match state.advance() {
            Some(b':') => {}
            _ => {
                return Err(DynconfParseError::new(
                    DynconfParseErrorKind::InvalidJson,
                    format!("expected ':' after key '{}' at position {}", key, state.pos),
                ));
            }
        }

        // Parse value
        let value = parse_value(state, depth + 1)?;
        entries.push((key, value));

        // Expect comma or closing brace
        state.skip_whitespace();
        match state.peek() {
            Some(b',') => {
                state.advance();
            }
            Some(b'}') => {
                state.advance();
                return Ok(JsonValue::Object(entries));
            }
            _ => {
                return Err(DynconfParseError::new(
                    DynconfParseErrorKind::InvalidJson,
                    format!("expected ',' or '}}' at position {}", state.pos),
                ));
            }
        }
    }
}

fn parse_array(state: &mut ParseState, depth: usize) -> Result<JsonValue, DynconfParseError> {
    // Consume opening '['
    state.advance();
    state.skip_whitespace();

    let mut items: Vec<JsonValue> = Vec::new();

    // Empty array
    if state.peek() == Some(b']') {
        state.advance();
        return Ok(JsonValue::Array(items));
    }

    loop {
        let item = parse_value(state, depth + 1)?;
        items.push(item);

        state.skip_whitespace();
        match state.peek() {
            Some(b',') => {
                state.advance();
            }
            Some(b']') => {
                state.advance();
                return Ok(JsonValue::Array(items));
            }
            _ => {
                return Err(DynconfParseError::new(
                    DynconfParseErrorKind::InvalidJson,
                    format!("expected ',' or ']' at position {}", state.pos),
                ));
            }
        }
    }
}

fn parse_string(state: &mut ParseState) -> Result<String, DynconfParseError> {
    // Consume opening quote
    let q = state.advance();
    debug_assert_eq!(q, Some(b'"'));

    let mut result = String::new();

    loop {
        match state.advance() {
            None => {
                return Err(DynconfParseError::new(
                    DynconfParseErrorKind::InvalidJson,
                    "unterminated string".to_string(),
                ));
            }
            Some(b'"') => return Ok(result),
            Some(b'\\') => {
                match state.advance() {
                    Some(b'"') => result.push('"'),
                    Some(b'\\') => result.push('\\'),
                    Some(b'/') => result.push('/'),
                    Some(b'b') => result.push('\u{0008}'),
                    Some(b'f') => result.push('\u{000C}'),
                    Some(b'n') => result.push('\n'),
                    Some(b'r') => result.push('\r'),
                    Some(b't') => result.push('\t'),
                    Some(b'u') => {
                        let cp = parse_unicode_escape(state)?;
                        // Handle surrogate pairs
                        if (0xD800..=0xDBFF).contains(&cp) {
                            // High surrogate, expect \uXXXX low surrogate
                            if state.advance() != Some(b'\\') || state.advance() != Some(b'u') {
                                return Err(DynconfParseError::new(
                                    DynconfParseErrorKind::InvalidJson,
                                    "expected low surrogate after high surrogate".to_string(),
                                ));
                            }
                            let low = parse_unicode_escape(state)?;
                            if !(0xDC00..=0xDFFF).contains(&low) {
                                return Err(DynconfParseError::new(
                                    DynconfParseErrorKind::InvalidJson,
                                    "invalid low surrogate".to_string(),
                                ));
                            }
                            let combined = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
                            if let Some(c) = char::from_u32(combined) {
                                result.push(c);
                            } else {
                                return Err(DynconfParseError::new(
                                    DynconfParseErrorKind::InvalidJson,
                                    "invalid unicode codepoint from surrogate pair".to_string(),
                                ));
                            }
                        } else if (0xDC00..=0xDFFF).contains(&cp) {
                            return Err(DynconfParseError::new(
                                DynconfParseErrorKind::InvalidJson,
                                "unexpected low surrogate without high surrogate".to_string(),
                            ));
                        } else if let Some(c) = char::from_u32(cp) {
                            result.push(c);
                        } else {
                            return Err(DynconfParseError::new(
                                DynconfParseErrorKind::InvalidJson,
                                "invalid unicode codepoint".to_string(),
                            ));
                        }
                    }
                    Some(c) => {
                        return Err(DynconfParseError::new(
                            DynconfParseErrorKind::InvalidJson,
                            format!("invalid escape sequence '\\{}'", c as char),
                        ));
                    }
                    None => {
                        return Err(DynconfParseError::new(
                            DynconfParseErrorKind::InvalidJson,
                            "unterminated escape sequence".to_string(),
                        ));
                    }
                }
            }
            Some(b) if b < 0x20 => {
                return Err(DynconfParseError::new(
                    DynconfParseErrorKind::InvalidJson,
                    format!("unescaped control character 0x{:02x} in string", b),
                ));
            }
            Some(b) => {
                result.push(b as char);
            }
        }
    }
}

fn parse_unicode_escape(state: &mut ParseState) -> Result<u32, DynconfParseError> {
    let mut hex_str = String::with_capacity(4);
    for _ in 0..4 {
        match state.advance() {
            Some(b) if b.is_ascii_hexdigit() => hex_str.push(b as char),
            _ => {
                return Err(DynconfParseError::new(
                    DynconfParseErrorKind::InvalidJson,
                    "invalid unicode escape (expected 4 hex digits)".to_string(),
                ));
            }
        }
    }
    u32::from_str_radix(&hex_str, 16).map_err(|_| {
        DynconfParseError::new(
            DynconfParseErrorKind::InvalidJson,
            "invalid unicode escape value".to_string(),
        )
    })
}

fn parse_number(state: &mut ParseState) -> Result<JsonValue, DynconfParseError> {
    let start = state.pos;

    // Optional leading minus
    if state.peek() == Some(b'-') {
        state.advance();
    }

    // Integer part
    match state.peek() {
        Some(b'0') => {
            state.advance();
            // After leading zero, only '.', 'e', 'E', or end of number
            // No leading zeros like 01, 02, etc.
        }
        Some(b'1'..=b'9') => {
            state.advance();
            while let Some(b'0'..=b'9') = state.peek() {
                state.advance();
            }
        }
        _ => {
            return Err(DynconfParseError::new(
                DynconfParseErrorKind::InvalidJson,
                format!("invalid number at position {}", state.pos),
            ));
        }
    }

    // Fractional part
    if state.peek() == Some(b'.') {
        state.advance();
        let frac_start = state.pos;
        while let Some(b'0'..=b'9') = state.peek() {
            state.advance();
        }
        if state.pos == frac_start {
            return Err(DynconfParseError::new(
                DynconfParseErrorKind::InvalidJson,
                "expected digit after decimal point".to_string(),
            ));
        }
    }

    // Exponent
    if matches!(state.peek(), Some(b'e') | Some(b'E')) {
        state.advance();
        if matches!(state.peek(), Some(b'+') | Some(b'-')) {
            state.advance();
        }
        let exp_start = state.pos;
        while let Some(b'0'..=b'9') = state.peek() {
            state.advance();
        }
        if state.pos == exp_start {
            return Err(DynconfParseError::new(
                DynconfParseErrorKind::InvalidJson,
                "expected digit in exponent".to_string(),
            ));
        }
    }

    let raw = std::str::from_utf8(&state.input[start..state.pos])
        .unwrap()
        .to_string();
    let value: f64 = raw.parse().map_err(|_| {
        DynconfParseError::new(
            DynconfParseErrorKind::InvalidJson,
            format!("invalid number '{}'", raw),
        )
    })?;

    Ok(JsonValue::Number(value, raw))
}

fn parse_bool(state: &mut ParseState) -> Result<JsonValue, DynconfParseError> {
    if state.remaining().starts_with(b"true") {
        state.pos += 4;
        Ok(JsonValue::Bool(true))
    } else if state.remaining().starts_with(b"false") {
        state.pos += 5;
        Ok(JsonValue::Bool(false))
    } else {
        Err(DynconfParseError::new(
            DynconfParseErrorKind::InvalidJson,
            format!("invalid literal at position {}", state.pos),
        ))
    }
}

fn parse_null(state: &mut ParseState) -> Result<JsonValue, DynconfParseError> {
    if state.remaining().starts_with(b"null") {
        state.pos += 4;
        Ok(JsonValue::Null)
    } else {
        Err(DynconfParseError::new(
            DynconfParseErrorKind::InvalidJson,
            format!("invalid literal at position {}", state.pos),
        ))
    }
}
