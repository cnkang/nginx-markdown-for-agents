//! JSON parser with budget constraints and duplicate-key detection.
//!
//! Implements RFC 8259 JSON parsing with:
//! - Token budget enforcement (max 10,000 tokens)
//! - Nesting depth enforcement (max 8 levels)
//! - Duplicate-key detection within each object

use std::collections::HashSet;
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
    /// Configured token budget (retained for diagnostics).
    token_budget: usize,
}

impl ParseState {
    fn new(input: Vec<u8>, max_depth: usize, token_budget: usize) -> Self {
        Self {
            tokens_remaining: token_budget,
            max_depth,
            pos: 0,
            input,
            token_budget,
        }
    }

    fn consume_token(&mut self) -> Result<(), DynconfParseError> {
        if self.tokens_remaining == 0 {
            return Err(DynconfParseError::new(
                DynconfParseErrorKind::TokenBudgetExceeded,
                format!("parse token budget ({}) exceeded", self.token_budget),
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

    let bounded_depth = max_depth.min(super::MAX_NESTING_DEPTH);
    let mut state = ParseState::new(raw_bytes.to_vec(), bounded_depth, token_budget);

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
    let mut seen_keys: HashSet<String> = HashSet::new();

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
        if seen_keys.contains(&key) {
            return Err(DynconfParseError::new(
                DynconfParseErrorKind::DuplicateKey,
                format!("duplicate key '{}' in object", key),
            ));
        }
        seen_keys.insert(key.clone());

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
    let q = state.advance();
    debug_assert_eq!(q, Some(b'"'));

    let mut result = String::new();

    loop {
        match state.advance() {
            None => return invalid_json("unterminated string"),
            Some(b'"') => return Ok(result),
            Some(b'\\') => parse_escape(state, &mut result)?,
            Some(b) if b < 0x20 => {
                return invalid_json(format!("unescaped control character 0x{:02x} in string", b));
            }
            Some(b) => {
                append_raw_string_byte(state, &mut result, b)?;
            }
        }
    }
}

fn append_raw_string_byte(
    state: &mut ParseState,
    result: &mut String,
    first: u8,
) -> Result<(), DynconfParseError> {
    if first.is_ascii() {
        result.push(first as char);
        return Ok(());
    }

    let start = state.pos.checked_sub(1).ok_or_else(|| {
        DynconfParseError::new(
            DynconfParseErrorKind::InvalidJson,
            "invalid UTF-8 string position".to_string(),
        )
    })?;
    let width = match first {
        0xC2..=0xDF => 2,
        0xE0..=0xEF => 3,
        0xF0..=0xF4 => 4,
        _ => return invalid_json("invalid UTF-8 leading byte in string"),
    };
    let end = start.checked_add(width).ok_or_else(|| {
        DynconfParseError::new(
            DynconfParseErrorKind::InvalidJson,
            "invalid UTF-8 string length".to_string(),
        )
    })?;
    if end > state.input.len()
        || state.input[start + 1..end]
            .iter()
            .any(|byte| !(*byte & 0xC0 == 0x80))
    {
        return invalid_json("invalid UTF-8 continuation byte in string");
    }

    let character = std::str::from_utf8(&state.input[start..end])
        .ok()
        .and_then(|text| text.chars().next())
        .ok_or_else(|| {
            DynconfParseError::new(
                DynconfParseErrorKind::InvalidJson,
                "invalid UTF-8 codepoint in string".to_string(),
            )
        })?;
    state.pos = end;
    result.push(character);
    Ok(())
}

fn invalid_json<T>(message: impl Into<String>) -> Result<T, DynconfParseError> {
    Err(DynconfParseError::new(
        DynconfParseErrorKind::InvalidJson,
        message.into(),
    ))
}

fn parse_escape(state: &mut ParseState, result: &mut String) -> Result<(), DynconfParseError> {
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
            let codepoint = parse_unicode_escape(state)?;
            append_unicode_escape(state, result, codepoint)?;
        }
        Some(c) => return invalid_json(format!("invalid escape sequence '\\{}'", c as char)),
        None => return invalid_json("unterminated escape sequence"),
    }
    Ok(())
}

fn append_unicode_escape(
    state: &mut ParseState,
    result: &mut String,
    codepoint: u32,
) -> Result<(), DynconfParseError> {
    if (0xD800..=0xDBFF).contains(&codepoint) {
        return append_surrogate_pair(state, result, codepoint);
    }
    if (0xDC00..=0xDFFF).contains(&codepoint) {
        return invalid_json("unexpected low surrogate without high surrogate");
    }
    let Some(character) = char::from_u32(codepoint) else {
        return invalid_json("invalid unicode codepoint");
    };
    result.push(character);
    Ok(())
}

fn append_surrogate_pair(
    state: &mut ParseState,
    result: &mut String,
    high: u32,
) -> Result<(), DynconfParseError> {
    if state.advance() != Some(b'\\') || state.advance() != Some(b'u') {
        return invalid_json("expected low surrogate after high surrogate");
    }
    let low = parse_unicode_escape(state)?;
    if !(0xDC00..=0xDFFF).contains(&low) {
        return invalid_json("invalid low surrogate");
    }
    let combined = 0x10000 + ((high - 0xD800) << 10) + (low - 0xDC00);
    let Some(character) = char::from_u32(combined) else {
        return invalid_json("invalid unicode codepoint from surrogate pair");
    };
    result.push(character);
    Ok(())
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

    consume_number_sign(state);
    parse_integer_part(state)?;
    parse_fraction_part(state)?;
    parse_exponent_part(state)?;

    let raw = match std::str::from_utf8(&state.input[start..state.pos]) {
        Ok(raw) => raw.to_string(),
        Err(_) => return invalid_json("invalid UTF-8 in number"),
    };
    let value: f64 = raw.parse().map_err(|_| {
        DynconfParseError::new(
            DynconfParseErrorKind::InvalidJson,
            format!("invalid number '{}'", raw),
        )
    })?;

    Ok(JsonValue::Number(value, raw))
}

fn consume_number_sign(state: &mut ParseState) {
    if state.peek() == Some(b'-') {
        state.advance();
    }
}

fn parse_integer_part(state: &mut ParseState) -> Result<(), DynconfParseError> {
    match state.peek() {
        Some(b'0') => {
            state.advance();
        }
        Some(b'1'..=b'9') => {
            state.advance();
            while let Some(b'0'..=b'9') = state.peek() {
                state.advance();
            }
        }
        _ => {
            return invalid_json(format!("invalid number at position {}", state.pos));
        }
    }
    Ok(())
}

fn parse_fraction_part(state: &mut ParseState) -> Result<(), DynconfParseError> {
    if state.peek() == Some(b'.') {
        state.advance();
        let frac_start = state.pos;
        while let Some(b'0'..=b'9') = state.peek() {
            state.advance();
        }
        if state.pos == frac_start {
            return invalid_json("expected digit after decimal point");
        }
    }
    Ok(())
}

fn parse_exponent_part(state: &mut ParseState) -> Result<(), DynconfParseError> {
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
            return invalid_json("expected digit in exponent");
        }
    }
    Ok(())
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
