//! html5ever TokenSink adapter for streaming tokenization.
//!
//! Wraps html5ever's tokenizer to produce [`StreamEvent`] values from
//! raw HTML byte chunks without building a DOM tree.

use std::cell::{Cell, RefCell};
use std::panic;

use html5ever::tendril::StrTendril;
use html5ever::tokenizer::{
    BufferQueue, Tag, TagKind, Token, TokenSink, TokenSinkResult, Tokenizer, TokenizerOpts,
};

use crate::error::ConversionError;
use crate::streaming::types::StreamEvent;

/// Collects [`StreamEvent`]s produced by the html5ever tokenizer.
///
/// This adapter implements [`TokenSink`] so it can be plugged directly
/// into an html5ever [`Tokenizer`].  Tokens are accumulated in an
/// internal buffer that the owning [`StreamingTokenizer`] drains after
/// each feed cycle.
struct TokenSinkAdapter {
    /// Accumulated events since the last drain.
    events: RefCell<Vec<StreamEvent>>,
    /// Coalesce text and count downstream-inert tokens without storing them.
    compact: bool,
    /// Number of non-EOF tokens since the last drain.
    token_count: Cell<u64>,
    /// Number of parse-error tokens since the last drain.
    parse_errors: Cell<u64>,
}

impl TokenSinkAdapter {
    /// Constructs a `TokenSinkAdapter` with an empty internal event buffer.
    fn new() -> Self {
        Self {
            events: RefCell::new(Vec::new()),
            compact: false,
            token_count: Cell::new(0),
            parse_errors: Cell::new(0),
        }
    }

    /// Construct the bounded sink used by the runtime streaming converter.
    fn new_compact() -> Self {
        Self {
            events: RefCell::new(Vec::new()),
            compact: true,
            token_count: Cell::new(0),
            parse_errors: Cell::new(0),
        }
    }

    /// Append text while coalescing adjacent compact-mode events.
    fn push_compact_text(&self, text: &str) {
        let mut events = self.events.borrow_mut();
        if let Some(StreamEvent::Text(previous)) = events.last_mut() {
            previous.push_str(text);
        } else {
            events.push(StreamEvent::Text(text.to_string()));
        }
    }

    /// Move one event batch and its compacted token statistics out of the sink.
    fn drain_batch(&self) -> TokenizerBatch {
        TokenizerBatch {
            events: std::mem::take(&mut *self.events.borrow_mut()),
            token_count: self.token_count.replace(0),
            parse_errors: self.parse_errors.replace(0),
        }
    }
}

impl TokenSink for TokenSinkAdapter {
    type Handle = ();

    /// Converts an html5ever `Token` into a `StreamEvent` and appends it to the adapter's internal buffer.
    ///
    /// The EOF token is ignored and does not produce an event. Other token variants are mapped as:
    /// - `TagToken` → converted via `tag_to_event` (start/end tag with attributes),
    /// - `CharacterTokens` → `StreamEvent::Text` (stringified),
    /// - `CommentToken` → `StreamEvent::Comment` (stringified),
    /// - `DoctypeToken` → `StreamEvent::Doctype`,
    /// - `NullCharacterToken` → `StreamEvent::Text("\u{FFFD}")`,
    /// - `ParseError` → `StreamEvent::ParseError` (stringified).
    ///
    /// # Examples
    ///
    /// ```ignore
    /// use html5ever::tokenizer::Token;
    /// use tendril::StrTendril;
    /// use crate::{TokenSinkAdapter, StreamEvent};
    ///
    /// let adapter = TokenSinkAdapter::new();
    /// adapter.process_token(Token::CharacterTokens(StrTendril::from("hello")), 0);
    /// let events = adapter.drain();
    /// assert_eq!(events, vec![StreamEvent::Text("hello".to_string())]);
    /// ```
    fn process_token(&self, token: Token, _line_number: u64) -> TokenSinkResult<Self::Handle> {
        if matches!(&token, Token::EOFToken) {
            return TokenSinkResult::Continue;
        }
        self.token_count
            .set(self.token_count.get().saturating_add(1));
        if matches!(&token, Token::ParseError(_)) {
            self.parse_errors
                .set(self.parse_errors.get().saturating_add(1));
        }

        if self.compact {
            match token {
                Token::TagToken(tag) => self.events.borrow_mut().push(tag_to_event(tag)),
                Token::CharacterTokens(text) => self.push_compact_text(&text),
                Token::NullCharacterToken => self.push_compact_text("\u{FFFD}"),
                Token::CommentToken(_)
                | Token::DoctypeToken(_)
                | Token::ParseError(_)
                | Token::EOFToken => {}
            }
            return TokenSinkResult::Continue;
        }

        let event = match token {
            Token::TagToken(tag) => tag_to_event(tag),
            Token::CharacterTokens(text) => StreamEvent::Text(text.to_string()),
            Token::CommentToken(text) => StreamEvent::Comment(text.to_string()),
            Token::DoctypeToken(_) => StreamEvent::Doctype,
            Token::NullCharacterToken => StreamEvent::Text("\u{FFFD}".to_string()),
            Token::ParseError(err) => StreamEvent::ParseError(err.to_string()),
            Token::EOFToken => unreachable!("EOF returned before event conversion"),
        };
        self.events.borrow_mut().push(event);
        TokenSinkResult::Continue
    }
}

/// Convert an html5ever `Tag` into a `StreamEvent`.
///
/// The returned event is either a `StartTag` with the tag name, a vector of
/// attribute (name, value) pairs, and the `self_closing` flag, or an `EndTag`
/// containing the tag name.
///
/// # Examples
///
/// ```ignore
/// use html5ever::tokenizer::{Tag, TagKind};
///
/// let tag = Tag {
///     name: "h1".into(),
///     attrs: vec![],
///     self_closing: false,
///     had_duplicate_attributes: false,
///     kind: TagKind::StartTag,
/// };
///
/// let ev = tag_to_event(tag);
/// if let StreamEvent::StartTag { name, .. } = ev {
///     assert_eq!(name, "h1");
/// } else {
///     panic!("expected StartTag");
/// }
/// ```
fn tag_to_event(tag: Tag) -> StreamEvent {
    let name = tag.name.to_string();
    let attrs: Vec<(String, String)> = tag
        .attrs
        .into_iter()
        .map(|a| (a.name.local.to_string(), a.value.to_string()))
        .collect();

    match tag.kind {
        TagKind::StartTag => StreamEvent::StartTag {
            name,
            attrs,
            self_closing: tag.self_closing,
        },
        TagKind::EndTag => StreamEvent::EndTag { name },
    }
}

/// Fixed logical overhead reserved for one persistent html5ever tokenizer.
///
/// This covers the tokenizer object, its default 16-entry `BufferQueue`,
/// empty tendrils, and the event sink before input-dependent allocations.
const TOKENIZER_FIXED_RESERVATION_BYTES: usize = 4 * 1024;

/// Conservative expansion factor from tracked UTF-8 input to simultaneous
/// tokenizer, tendril, attribute, and event-batch storage.
///
/// The worst dense representation is an attribute-heavy tag: html5ever's
/// attribute vector and the converted `(String, String)` vector coexist while
/// the tag is handed to the sink. Tendril capacities round up to a power of
/// two, and Rust vectors may retain spare element capacity. The reservation
/// covers the maximum incomplete token plus one input/event batch without
/// relying on private html5ever allocation counters.
const TOKENIZER_BYTES_PER_INPUT_BYTE: usize = 128;

/// Absolute upper bound for one incomplete syntactic token.
const TOKENIZER_MAX_TOKEN_BYTES: usize = 64 * 1024;

/// Input is handed to html5ever in batches no larger than this target.
const TOKENIZER_BATCH_TARGET_BYTES: usize = 4 * 1024;

/// Tokenizer output produced by one bounded input slice.
pub(crate) struct TokenizerBatch {
    pub(crate) events: Vec<StreamEvent>,
    #[allow(dead_code)]
    pub(crate) token_count: u64,
    #[allow(dead_code)]
    pub(crate) parse_errors: u64,
}

/// Character-reference sub-state tracked by the conservative retention scanner.
#[derive(Debug, Clone, Copy)]
enum CharacterReferenceState {
    Start,
    Named,
    NumericStart,
    Decimal,
    HexStart,
    Hex,
}

/// Conservative lexical state used to bound input-derived tokenizer state.
///
/// The scanner resets its retained-span estimate only after reaching `Data`.
/// It intentionally recognizes fewer completion points than html5ever for
/// malformed comments and declarations. That can reject malformed input
/// conservatively, but cannot undercount a live token retained by html5ever.
#[derive(Debug, Clone, Copy)]
enum FrameScanState {
    Data,
    CarriageReturn,
    CharacterReference(CharacterReferenceState),
    TagOpen,
    EndTagOpen,
    DeclarationProbe {
        index: u8,
        could_be_comment: bool,
        could_be_doctype: bool,
    },
    Tag {
        quote: Option<char>,
    },
    BogusMarkup,
    Comment {
        content_len: usize,
        tail: [char; 3],
    },
}

impl FrameScanState {
    /// Return whether html5ever has no growable token state to carry forward.
    fn is_data(self) -> bool {
        matches!(self, Self::Data)
    }

    /// Advance the conservative state machine by one Unicode scalar value.
    fn advance(self, ch: char) -> Self {
        match self {
            Self::Data => Self::from_data(ch),
            Self::CarriageReturn => {
                if ch == '\n' {
                    Self::Data
                } else {
                    Self::from_data(ch)
                }
            }
            Self::CharacterReference(state) => advance_character_reference(state, ch),
            Self::TagOpen => match ch {
                '!' => Self::DeclarationProbe {
                    index: 0,
                    could_be_comment: true,
                    could_be_doctype: true,
                },
                '/' => Self::EndTagOpen,
                '?' => Self::BogusMarkup,
                c if c.is_ascii_alphabetic() => Self::Tag { quote: None },
                _ => Self::from_data(ch),
            },
            Self::EndTagOpen => match ch {
                '>' => Self::Data,
                c if c.is_ascii_alphabetic() => Self::Tag { quote: None },
                _ => Self::BogusMarkup,
            },
            Self::DeclarationProbe {
                index,
                could_be_comment,
                could_be_doctype,
            } => advance_declaration_probe((index, could_be_comment, could_be_doctype), ch),
            Self::Tag { quote } => Self::advance_tag(quote, ch, self),
            Self::BogusMarkup => {
                if ch == '>' {
                    Self::Data
                } else {
                    self
                }
            }
            Self::Comment { content_len, tail } => {
                if ch == '>' && comment_can_end(content_len, tail) {
                    Self::Data
                } else {
                    Self::Comment {
                        content_len: content_len.saturating_add(1),
                        tail: [tail[1], tail[2], ch],
                    }
                }
            }
        }
    }

    fn advance_tag(quote: Option<char>, ch: char, current: Self) -> Self {
        match quote {
            Some(expected) if ch == expected => Self::Tag { quote: None },
            Some(_) => current,
            None if matches!(ch, '"' | '\'') => Self::Tag { quote: Some(ch) },
            None if ch == '>' => Self::Data,
            None => current,
        }
    }

    /// Enter the correct state from html5ever's Data state.
    fn from_data(ch: char) -> Self {
        match ch {
            '<' => Self::TagOpen,
            '&' => Self::CharacterReference(CharacterReferenceState::Start),
            '\r' => Self::CarriageReturn,
            _ => Self::Data,
        }
    }
}

/// Dispatch one character-reference transition to a small state helper.
fn advance_character_reference(state: CharacterReferenceState, ch: char) -> FrameScanState {
    match state {
        CharacterReferenceState::Start => advance_reference_start(ch),
        CharacterReferenceState::Named => advance_named_reference(ch),
        CharacterReferenceState::NumericStart => advance_numeric_reference_start(ch),
        CharacterReferenceState::Decimal => advance_decimal_reference(ch),
        CharacterReferenceState::HexStart => advance_hex_reference_start(ch),
        CharacterReferenceState::Hex => advance_hex_reference(ch),
    }
}

/// Advance the first character after `&`.
fn advance_reference_start(ch: char) -> FrameScanState {
    if ch == '#' {
        FrameScanState::CharacterReference(CharacterReferenceState::NumericStart)
    } else if ch.is_ascii_alphanumeric() {
        FrameScanState::CharacterReference(CharacterReferenceState::Named)
    } else {
        FrameScanState::from_data(ch)
    }
}

/// Advance a named reference.
fn advance_named_reference(ch: char) -> FrameScanState {
    if ch.is_ascii_alphanumeric() {
        FrameScanState::CharacterReference(CharacterReferenceState::Named)
    } else if ch == ';' {
        FrameScanState::Data
    } else {
        FrameScanState::from_data(ch)
    }
}

/// Advance a decimal numeric reference.
fn advance_decimal_reference(ch: char) -> FrameScanState {
    if ch.is_ascii_digit() {
        FrameScanState::CharacterReference(CharacterReferenceState::Decimal)
    } else if ch == ';' {
        FrameScanState::Data
    } else {
        FrameScanState::from_data(ch)
    }
}

/// Advance the first hexadecimal digit after `&#x`.
fn advance_hex_reference_start(ch: char) -> FrameScanState {
    if ch.is_ascii_hexdigit() {
        FrameScanState::CharacterReference(CharacterReferenceState::Hex)
    } else {
        FrameScanState::from_data(ch)
    }
}

/// Advance a hexadecimal numeric reference.
fn advance_hex_reference(ch: char) -> FrameScanState {
    if ch.is_ascii_hexdigit() {
        FrameScanState::CharacterReference(CharacterReferenceState::Hex)
    } else if ch == ';' {
        FrameScanState::Data
    } else {
        FrameScanState::from_data(ch)
    }
}

/// Normalize one ASCII declaration character for case-insensitive matching.
fn declaration_char(ch: char) -> char {
    ch.to_ascii_lowercase()
}

/// Return whether `>` terminates the current HTML comment state.
fn comment_can_end(content_len: usize, tail: [char; 3]) -> bool {
    content_len == 0
        || (content_len == 1 && tail[2] == '-')
        || (content_len >= 2 && tail[1] == '-' && tail[2] == '-')
        || (content_len >= 3 && tail == ['-', '-', '!'])
}

/// Result of consuming one bounded tokenizer input slice.
pub(crate) struct TokenizerStep {
    pub(crate) consumed: usize,
    pub(crate) batch: Option<TokenizerBatch>,
}

/// Total-budget-aware tokenizer used by [`StreamingConverter`].
///
/// html5ever does not expose the capacity of its private tendrils and token
/// scratch fields. This wrapper therefore:
///
/// 1. reserves a conservative fixed envelope from `MemoryBudget.total`;
/// 2. retains the normal persistent tokenizer across every input chunk;
/// 3. scans each small slice before feeding it to bound incomplete token state;
/// 4. rejects a slice before html5ever sees bytes beyond that bound; and
/// 5. drains the event sink after every bounded slice.
///
/// The envelope covers persistent parser state, one input tendril, and one
/// event batch. Large documents remain constant-resident because completed
/// events are processed before the next slice is fed.
pub(crate) struct BudgetedStreamingTokenizer {
    tokenizer: StreamingTokenizer,
    scan_state: FrameScanState,
    token_span: usize,
    batch_target: usize,
    max_token_span: usize,
    reserved_bytes: usize,
}

impl BudgetedStreamingTokenizer {
    /// Construct a tokenizer whose conservative reservation fits in `total`.
    pub fn new(total: usize) -> Self {
        let maximum_tracked_input =
            TOKENIZER_MAX_TOKEN_BYTES.saturating_add(TOKENIZER_BATCH_TARGET_BYTES);
        let maximum_reservation = TOKENIZER_FIXED_RESERVATION_BYTES
            .saturating_add(maximum_tracked_input.saturating_mul(TOKENIZER_BYTES_PER_INPUT_BYTE));
        let reservation_share = total.saturating_sub(total / 4).min(maximum_reservation);
        let dynamic_bytes = reservation_share.saturating_sub(TOKENIZER_FIXED_RESERVATION_BYTES);
        let tracked_input =
            (dynamic_bytes / TOKENIZER_BYTES_PER_INPUT_BYTE).min(maximum_tracked_input);
        let batch_target = TOKENIZER_BATCH_TARGET_BYTES.min(tracked_input / 4);
        let max_token_span = tracked_input
            .saturating_sub(batch_target)
            .min(TOKENIZER_MAX_TOKEN_BYTES);
        let accounted_input = batch_target.saturating_add(max_token_span);
        let reserved_bytes = if accounted_input == 0 {
            reservation_share
        } else {
            TOKENIZER_FIXED_RESERVATION_BYTES
                .saturating_add(accounted_input.saturating_mul(TOKENIZER_BYTES_PER_INPUT_BYTE))
        };

        Self {
            tokenizer: StreamingTokenizer::new_compact(),
            scan_state: FrameScanState::Data,
            token_span: 0,
            batch_target,
            max_token_span,
            reserved_bytes,
        }
    }

    /// Return the conservative resident-memory reservation.
    pub fn reserved_bytes(&self) -> usize {
        self.reserved_bytes
    }

    /// Return the maximum accepted byte length of one incomplete token.
    #[cfg(test)]
    fn max_token_span(&self) -> usize {
        self.max_token_span
    }

    /// Pre-scan and feed one bounded UTF-8 slice into the persistent tokenizer.
    pub fn feed_next(&mut self, data: &str) -> Result<TokenizerStep, ConversionError> {
        if data.is_empty() {
            return Ok(TokenizerStep {
                consumed: 0,
                batch: None,
            });
        }
        if self.batch_target == 0 || self.max_token_span == 0 {
            return Err(ConversionError::BudgetExceeded {
                stage: "tokenizer_reservation".to_string(),
                used: TOKENIZER_FIXED_RESERVATION_BYTES
                    .saturating_add(TOKENIZER_BYTES_PER_INPUT_BYTE),
                limit: self.reserved_bytes,
            });
        }

        let consumed = bounded_char_boundary(data, self.batch_target);
        if consumed == 0 {
            return Err(ConversionError::BudgetExceeded {
                stage: "tokenizer_input_slice".to_string(),
                used: data.chars().next().map_or(0, char::len_utf8),
                limit: self.batch_target,
            });
        }
        let slice = &data[..consumed];
        let (scan_state, token_span) =
            scan_slice(self.scan_state, self.token_span, slice, self.max_token_span)?;
        let batch = self.tokenizer.feed_batch(slice)?;
        self.scan_state = scan_state;
        self.token_span = token_span;

        Ok(TokenizerStep {
            consumed,
            batch: Some(batch),
        })
    }

    /// Finish the persistent tokenizer and drain its final bounded events.
    pub fn finish(&mut self) -> Result<TokenizerBatch, ConversionError> {
        self.tokenizer.finish_batch()
    }
}

/// Return the largest prefix at or below `limit` that ends on a UTF-8 boundary.
fn bounded_char_boundary(data: &str, limit: usize) -> usize {
    let mut end = data.len().min(limit);
    while end > 0 && !data.is_char_boundary(end) {
        end -= 1;
    }
    end
}

/// Preview scanner state for a slice before passing any of it to html5ever.
fn scan_slice(
    mut state: FrameScanState,
    mut token_span: usize,
    data: &str,
    max_token_span: usize,
) -> Result<(FrameScanState, usize), ConversionError> {
    for ch in data.chars() {
        let next_state = state.advance(ch);
        token_span = next_token_span(state, next_state, token_span, ch.len_utf8());
        if token_span > max_token_span {
            return Err(ConversionError::BudgetExceeded {
                stage: "tokenizer_incomplete_token".to_string(),
                used: token_span,
                limit: max_token_span,
            });
        }
        state = next_state;
    }
    Ok((state, token_span))
}

/// Update the byte span of tokenizer state that may retain input-derived data.
fn next_token_span(
    state: FrameScanState,
    next_state: FrameScanState,
    current: usize,
    char_len: usize,
) -> usize {
    if next_state.is_data() {
        0
    } else if state.is_data() {
        char_len
    } else {
        current.saturating_add(char_len)
    }
}

/// Streaming HTML tokenizer backed by html5ever.
///
/// Accepts arbitrary byte chunks via [`feed`](StreamingTokenizer::feed) and
/// produces a sequence of [`StreamEvent`] values.  The html5ever tokenizer
/// maintains its own internal state across calls, so tokens that span chunk
/// boundaries are handled correctly.
///
/// # Panic Safety
///
/// All calls into html5ever are wrapped in [`std::panic::catch_unwind`]
/// so that an unexpected panic inside the parser is surfaced as a
/// [`ConversionError::InternalError`] rather than unwinding through the
/// caller.
pub struct StreamingTokenizer {
    /// html5ever tokenizer instance.
    /// Wrapped in `Option` so we can take ownership in `finish`.
    tokenizer: Option<Tokenizer<TokenSinkAdapter>>,
    /// Persistent input queue so partial tags/tokens survive chunk boundaries.
    queue: BufferQueue,
}

impl Default for StreamingTokenizer {
    /// Creates a new streaming HTML tokenizer with default tokenizer options.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let _ = StreamingTokenizer::default();
    /// ```
    fn default() -> Self {
        Self::new()
    }
}

impl StreamingTokenizer {
    /// Constructs a new StreamingTokenizer with a fresh html5ever tokenizer and an empty internal sink.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let _tokenizer = StreamingTokenizer::new();
    /// ```
    pub fn new() -> Self {
        Self::with_sink(TokenSinkAdapter::new())
    }

    /// Construct the persistent tokenizer with a compact runtime sink.
    fn new_compact() -> Self {
        Self::with_sink(TokenSinkAdapter::new_compact())
    }

    /// Construct a tokenizer around the selected sink mode.
    fn with_sink(sink: TokenSinkAdapter) -> Self {
        let opts = TokenizerOpts {
            // BOM (U+FEFF) at the start of a feed() call is stripped by
            // html5ever when discard_bom is true (the default).  In streaming
            // mode, a BOM whose lead byte (0xEF) was split into utf8_tail by
            // the previous chunk gets reassembled at the start of the next
            // feed(), causing html5ever to strip it — diverging from
            // single-chunk conversion where the same BOM is mid-stream and
            // preserved.  Disabling discard_bom ensures consistent BOM
            // handling: the StreamingConverter strips a leading BOM once at
            // stream start, so mid-stream BOMs reach the tokenizer intact.
            discard_bom: false,
            ..Default::default()
        };
        let tokenizer = Tokenizer::new(sink, opts);
        Self {
            tokenizer: Some(tokenizer),
            queue: BufferQueue::default(),
        }
    }

    /// Feed a chunk of UTF-8 HTML and return any complete stream events.
    ///
    /// Takes an HTML fragment, feeds it to the internal html5ever tokenizer, and
    /// returns any `StreamEvent` values produced from that chunk.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::InternalError` if the tokenizer has already been
    /// consumed by `finish()` or if html5ever panics while processing the input.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let mut tok = StreamingTokenizer::new();
    /// let events = tok.feed("<h1>Hello</h1>").unwrap();
    /// assert!(matches!(events.as_slice(), [StreamEvent::StartTag{..}, StreamEvent::Text(_), StreamEvent::EndTag{..}]));
    /// ```
    pub fn feed(&mut self, data: &str) -> Result<Vec<StreamEvent>, ConversionError> {
        self.feed_batch(data).map(|batch| batch.events)
    }

    /// Feed one slice and return events plus compacted token statistics.
    fn feed_batch(&mut self, data: &str) -> Result<TokenizerBatch, ConversionError> {
        let tokenizer = self.tokenizer.as_mut().ok_or_else(|| {
            ConversionError::InternalError("tokenizer already consumed by finish()".into())
        })?;

        let tendril = StrTendril::from(data);
        self.queue.push_back(tendril);

        // Wrap html5ever call in catch_unwind for panic safety.
        let result = panic::catch_unwind(panic::AssertUnwindSafe(|| {
            let _ = tokenizer.feed(&self.queue);
        }));

        match result {
            Ok(()) => Ok(tokenizer.sink.drain_batch()),
            Err(payload) => {
                // After a panic, the tokenizer's internal state may be
                // corrupted. Invalidate it so subsequent calls fail cleanly
                // with "tokenizer already consumed" instead of reusing
                // potentially broken state.
                self.tokenizer = None;
                let msg = panic_payload_to_message(&payload, "html5ever panic");
                Err(ConversionError::InternalError(msg))
            }
        }
    }

    /// Signals end-of-input to the tokenizer and returns any remaining stream events.
    ///
    /// After calling this method the tokenizer is consumed and cannot be used again.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::InternalError` if the tokenizer has already been consumed
    /// or if html5ever panics while finishing tokenization.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let mut t = StreamingTokenizer::new();
    /// let events = t.feed("<h1>Hi</h1>").unwrap();
    /// // process `events`...
    /// let remaining = t.finish().unwrap();
    /// // `t` cannot be used after `finish()`
    /// ```
    pub fn finish(&mut self) -> Result<Vec<StreamEvent>, ConversionError> {
        self.finish_batch().map(|batch| batch.events)
    }

    /// End tokenization and return events plus compacted token statistics.
    fn finish_batch(&mut self) -> Result<TokenizerBatch, ConversionError> {
        let tokenizer = self.tokenizer.take().ok_or_else(|| {
            ConversionError::InternalError("tokenizer already consumed by finish()".into())
        })?;

        let result = panic::catch_unwind(panic::AssertUnwindSafe(|| {
            tokenizer.end();
        }));

        match result {
            Ok(()) => Ok(tokenizer.sink.drain_batch()),
            Err(payload) => {
                let msg = panic_payload_to_message(&payload, "html5ever panic during finish");
                Err(ConversionError::InternalError(msg))
            }
        }
    }
}

/// Probe the two declarations html5ever recognizes in the HTML namespace.
fn advance_declaration_probe(probe: (u8, bool, bool), ch: char) -> FrameScanState {
    const COMMENT_PREFIX: [char; 2] = ['-', '-'];
    const DOCTYPE_PREFIX: [char; 7] = ['d', 'o', 'c', 't', 'y', 'p', 'e'];

    let (index, could_be_comment, could_be_doctype) = probe;
    let idx = usize::from(index);
    let comment_matches = could_be_comment
        && COMMENT_PREFIX
            .get(idx)
            .is_some_and(|expected| ch == *expected);
    let doctype_matches = could_be_doctype
        && DOCTYPE_PREFIX
            .get(idx)
            .is_some_and(|expected| declaration_char(ch) == *expected);
    let next_index = index.saturating_add(1);

    if comment_matches && usize::from(next_index) == COMMENT_PREFIX.len() {
        return FrameScanState::Comment {
            content_len: 0,
            tail: ['\0'; 3],
        };
    }
    if doctype_matches && usize::from(next_index) == DOCTYPE_PREFIX.len() {
        return FrameScanState::Tag { quote: None };
    }
    if !comment_matches && !doctype_matches {
        return FrameScanState::BogusMarkup;
    }

    FrameScanState::DeclarationProbe {
        index: next_index,
        could_be_comment: comment_matches,
        could_be_doctype: doctype_matches,
    }
}

/// Advance the first character after `&#`.
fn advance_numeric_reference_start(ch: char) -> FrameScanState {
    if ch == 'x' || ch == 'X' {
        FrameScanState::CharacterReference(CharacterReferenceState::HexStart)
    } else if ch.is_ascii_digit() {
        FrameScanState::CharacterReference(CharacterReferenceState::Decimal)
    } else {
        FrameScanState::from_data(ch)
    }
}

/// Format a panic payload into a human-readable message with a given prefix.
///
/// The function attempts to downcast the panic payload to `&str` or `String` and
/// returns `"{prefix}: {message}"` when successful; otherwise returns
/// `"{prefix}: unknown"`.
///
/// # Examples
///
/// ```ignore
/// use std::any::Any;
///
/// let payload: Box<dyn Any + Send> = Box::new("something went wrong");
/// let msg = panic_payload_to_message(&payload, "panic occurred");
/// assert_eq!(msg, "panic occurred: something went wrong");
/// ```
fn panic_payload_to_message(payload: &Box<dyn std::any::Any + Send>, prefix: &str) -> String {
    if let Some(s) = payload.downcast_ref::<&str>() {
        format!("{}: {}", prefix, s)
    } else if let Some(s) = payload.downcast_ref::<String>() {
        format!("{}: {}", prefix, s)
    } else {
        format!("{}: unknown", prefix)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_tag_tokens() {
        let mut tok = StreamingTokenizer::new();
        let events = tok.feed("<h1>Hello</h1>").unwrap();
        let has_h1_start = events
            .iter()
            .any(|e| matches!(e, StreamEvent::StartTag { name, .. } if name == "h1"));
        let has_h1_end = events
            .iter()
            .any(|e| matches!(e, StreamEvent::EndTag { name } if name == "h1"));
        let has_text = events
            .iter()
            .any(|e| matches!(e, StreamEvent::Text(t) if t == "Hello"));
        assert!(has_h1_start, "Expected h1 start tag in {:?}", events);
        assert!(has_h1_end, "Expected h1 end tag in {:?}", events);
        assert!(has_text, "Expected 'Hello' text in {:?}", events);
    }

    #[test]
    fn test_self_closing_tag() {
        let mut tok = StreamingTokenizer::new();
        let events = tok.feed("<br/>").unwrap();
        // html5ever may not set self_closing for void elements in HTML mode.
        // Just verify we get a br tag.
        let has_br_tag = events
            .iter()
            .any(|e| matches!(e, StreamEvent::StartTag { name, .. } if name == "br"));
        assert!(has_br_tag, "Expected br tag in {:?}", events);
    }

    #[test]
    fn test_text_before_tag_preserves_trailing_space() {
        // Single chunk: "Text with <strong>bold</strong>"
        let mut tok_first_chunk = StreamingTokenizer::new();
        let events_first_chunk = tok_first_chunk
            .feed("<p>Text with <strong>bold</strong></p>")
            .unwrap();
        let texts_first_chunk: Vec<String> = events_first_chunk
            .iter()
            .filter_map(|e| {
                if let StreamEvent::Text(t) = e {
                    Some(t.clone())
                } else {
                    None
                }
            })
            .collect();

        // Chunked: split inside the <strong> tag
        let mut tok_second_chunk = StreamingTokenizer::new();
        let ev2a = tok_second_chunk.feed("<p>Text with <str").unwrap();
        let ev2b = tok_second_chunk.feed("ong>bold</strong></p>").unwrap();
        let texts_second_chunk: Vec<String> = ev2a
            .iter()
            .chain(ev2b.iter())
            .filter_map(|e| {
                if let StreamEvent::Text(t) = e {
                    Some(t.clone())
                } else {
                    None
                }
            })
            .collect();

        let combined_first = texts_first_chunk.join("");
        let combined_second = texts_second_chunk.join("");
        assert_eq!(
            combined_first, combined_second,
            "Text content should be identical regardless of chunk split.\n\
             Single: {:?}\nChunked: {:?}",
            texts_first_chunk, texts_second_chunk
        );
    }

    #[test]
    fn test_cross_chunk_boundary() {
        let mut tok = StreamingTokenizer::new();
        let events1 = tok.feed("<h1>Hel").unwrap();
        let events2 = tok.feed("lo</h1>").unwrap();
        let all_events: Vec<_> = events1.into_iter().chain(events2).collect();
        let texts: Vec<String> = all_events
            .iter()
            .filter_map(|e| {
                if let StreamEvent::Text(t) = e {
                    Some(t.clone())
                } else {
                    None
                }
            })
            .collect();
        let combined = texts.join("");
        assert!(
            combined.contains("Hello"),
            "Expected 'Hello' across chunks, got: {}",
            combined
        );
    }

    #[test]
    fn test_malformed_html_no_panic() {
        let mut tok = StreamingTokenizer::new();
        // Unclosed tags, broken attributes, missing quotes
        assert!(
            tok.feed("<div><p>unclosed<span attr=no\"quote>broken")
                .is_ok()
        );
        assert!(tok.finish().is_ok());
    }

    #[test]
    fn test_empty_input() {
        let mut tok = StreamingTokenizer::new();
        let events = tok.feed("").unwrap();
        // Empty input should produce no events (or only implicit ones).
        // Just verify no panic.
        let _ = events;
        let _ = tok.finish().unwrap();
    }

    #[test]
    fn test_attributes_preserved() {
        let mut tok = StreamingTokenizer::new();
        let events = tok
            .feed(r#"<a href="https://example.com" class="link">text</a>"#)
            .unwrap();
        let link = events
            .iter()
            .find(|e| matches!(e, StreamEvent::StartTag { name, .. } if name == "a"));
        if let Some(StreamEvent::StartTag { attrs, .. }) = link {
            let href = attrs.iter().find(|(k, _)| k == "href");
            assert!(href.is_some(), "Expected href attribute");
            assert_eq!(href.unwrap().1, "https://example.com");
        } else {
            panic!("Expected <a> start tag in {:?}", events);
        }
    }

    #[test]
    fn test_comment_token() {
        let mut tok = StreamingTokenizer::new();
        let events = tok.feed("<!-- a comment -->").unwrap();
        let has_comment = events
            .iter()
            .any(|e| matches!(e, StreamEvent::Comment(c) if c.contains("comment")));
        assert!(has_comment, "Expected comment in {:?}", events);
    }

    #[test]
    fn test_doctype_token() {
        let mut tok = StreamingTokenizer::new();
        let events = tok
            .feed("<!DOCTYPE html><html><body>x</body></html>")
            .unwrap();
        let has_doctype = events.iter().any(|e| matches!(e, StreamEvent::Doctype));
        assert!(has_doctype, "Expected doctype in {:?}", events);
    }

    #[test]
    fn test_finish_after_finish_returns_error() {
        let mut tok = StreamingTokenizer::new();
        tok.feed("<p>test</p>").unwrap();
        tok.finish().unwrap();
        let err = tok.finish().unwrap_err();
        assert_eq!(err.code(), 99); // InternalError
    }

    #[test]
    fn test_feed_after_finish_returns_error() {
        let mut tok = StreamingTokenizer::new();
        tok.finish().unwrap();
        let err = tok.feed("<p>test</p>").unwrap_err();
        assert_eq!(err.code(), 99); // InternalError
    }

    // --- Entity recognition tests ---

    /// Named HTML entities (&amp;, &lt;, &gt;, &eacute;) are decoded to
    /// their Unicode characters in emitted Text events.
    #[test]
    fn test_named_entity_decoded() {
        let mut tok = StreamingTokenizer::new();
        let events = tok.feed("<p>&amp; &lt; &gt; &eacute;</p>").unwrap();
        let text: String = events
            .iter()
            .filter_map(|e| {
                if let StreamEvent::Text(t) = e {
                    Some(t.as_str())
                } else {
                    None
                }
            })
            .collect();
        assert!(text.contains('&'), "Expected decoded '&' in {:?}", text);
        assert!(text.contains('<'), "Expected decoded '<' in {:?}", text);
        assert!(text.contains('>'), "Expected decoded '>' in {:?}", text);
        assert!(text.contains('é'), "Expected decoded 'é' in {:?}", text);
    }

    /// Decimal numeric entities (&#169;, &#123;) are decoded correctly.
    #[test]
    fn test_decimal_numeric_entity_decoded() {
        let mut tok = StreamingTokenizer::new();
        let events = tok.feed("<p>&#169; &#123;</p>").unwrap();
        let text: String = events
            .iter()
            .filter_map(|e| {
                if let StreamEvent::Text(t) = e {
                    Some(t.as_str())
                } else {
                    None
                }
            })
            .collect();
        // &#169; = © (copyright), &#123; = { (left curly brace)
        assert!(text.contains('©'), "Expected decoded '©' in {:?}", text);
        assert!(text.contains('{'), "Expected decoded '{{' in {:?}", text);
    }

    /// Hex numeric entities (&#x00A9;, &#x1F4A9;) are decoded correctly.
    #[test]
    fn test_hex_numeric_entity_decoded() {
        let mut tok = StreamingTokenizer::new();
        let events = tok.feed("<p>&#x00A9; &#x1F4A9;</p>").unwrap();
        let text: String = events
            .iter()
            .filter_map(|e| {
                if let StreamEvent::Text(t) = e {
                    Some(t.as_str())
                } else {
                    None
                }
            })
            .collect();
        // &#x00A9; = © (copyright), &#x1F4A9; = 💩 (pile of poo)
        assert!(text.contains('©'), "Expected decoded '©' in {:?}", text);
        assert!(text.contains('💩'), "Expected decoded '💩' in {:?}", text);
    }

    /// Named entity split across chunk boundary: "&am" | "p;" is correctly
    /// merged and decoded to '&'.
    #[test]
    fn test_named_entity_split_across_chunks() {
        let mut tok = StreamingTokenizer::new();
        let ev1 = tok.feed("<p>A &am").unwrap();
        let ev2 = tok.feed("p; B</p>").unwrap();
        let text: String = ev1
            .iter()
            .chain(ev2.iter())
            .filter_map(|e| {
                if let StreamEvent::Text(t) = e {
                    Some(t.as_str())
                } else {
                    None
                }
            })
            .collect();
        // Combined text should contain the decoded ampersand
        assert!(
            text.contains('&'),
            "Expected '&' character in combined text: {:?}",
            text
        );
    }

    /// Decimal numeric entity split across chunks: "&#16" | "9;" is
    /// correctly merged and decoded to '©'.
    #[test]
    fn test_decimal_entity_split_across_chunks() {
        let mut tok = StreamingTokenizer::new();
        let ev1 = tok.feed("<p>Copyright &#16").unwrap();
        let ev2 = tok.feed("9; symbol</p>").unwrap();
        let text: String = ev1
            .iter()
            .chain(ev2.iter())
            .filter_map(|e| {
                if let StreamEvent::Text(t) = e {
                    Some(t.as_str())
                } else {
                    None
                }
            })
            .collect();
        assert!(
            text.contains('©'),
            "Expected decoded '©' from split numeric entity, got: {:?}",
            text
        );
    }

    /// Hex numeric entity split across chunks: "&#x1F4" | "A9;" is
    /// correctly merged and decoded to '💩'.
    #[test]
    fn test_hex_entity_split_across_chunks() {
        let mut tok = StreamingTokenizer::new();
        let ev1 = tok.feed("<p>Emoji: &#x1F4").unwrap();
        let ev2 = tok.feed("A9;</p>").unwrap();
        let text: String = ev1
            .iter()
            .chain(ev2.iter())
            .filter_map(|e| {
                if let StreamEvent::Text(t) = e {
                    Some(t.as_str())
                } else {
                    None
                }
            })
            .collect();
        assert!(
            text.contains('💩'),
            "Expected decoded '💩' from split hex entity, got: {:?}",
            text
        );
    }

    // --- Attribute cross-chunk tests ---

    /// Attribute name split across chunks: `<a hr` | `ef="url">` still
    /// produces the correct href attribute.
    #[test]
    fn test_attribute_name_split_across_chunks() {
        let mut tok = StreamingTokenizer::new();
        let ev1 = tok.feed(r#"<a hr"#).unwrap();
        let ev2 = tok.feed(r#"ef="https://example.com">link</a>"#).unwrap();
        let all_events: Vec<_> = ev1.into_iter().chain(ev2).collect();
        let link = all_events
            .iter()
            .find(|e| matches!(e, StreamEvent::StartTag { name, .. } if name == "a"));
        if let Some(StreamEvent::StartTag { attrs, .. }) = link {
            let href = attrs.iter().find(|(k, _)| k == "href");
            assert!(href.is_some(), "Expected href attribute in {:?}", attrs);
            assert_eq!(href.unwrap().1, "https://example.com");
        } else {
            panic!("Expected <a> start tag in {:?}", all_events);
        }
    }

    /// Attribute value split across chunks: `<a href="https://exa` | `mple.com">`
    /// produces the correct full URL.
    #[test]
    fn test_attribute_value_split_across_chunks() {
        let mut tok = StreamingTokenizer::new();
        let ev1 = tok.feed(r#"<a href="https://exa"#).unwrap();
        let ev2 = tok.feed(r#"mple.com">link</a>"#).unwrap();
        let all_events: Vec<_> = ev1.into_iter().chain(ev2).collect();
        let link = all_events
            .iter()
            .find(|e| matches!(e, StreamEvent::StartTag { name, .. } if name == "a"));
        if let Some(StreamEvent::StartTag { attrs, .. }) = link {
            let href = attrs.iter().find(|(k, _)| k == "href");
            assert!(href.is_some(), "Expected href attribute in {:?}", attrs);
            assert_eq!(href.unwrap().1, "https://example.com");
        } else {
            panic!("Expected <a> start tag in {:?}", all_events);
        }
    }

    /// Multiple attributes preserved when tag is split across chunks.
    #[test]
    fn test_multiple_attributes_split_across_chunks() {
        let mut tok = StreamingTokenizer::new();
        let ev1 = tok.feed(r#"<a href="https://example.com" cl"#).unwrap();
        let ev2 = tok.feed(r#"ass="external" id="link1">text</a>"#).unwrap();
        let all_events: Vec<_> = ev1.into_iter().chain(ev2).collect();
        let link = all_events
            .iter()
            .find(|e| matches!(e, StreamEvent::StartTag { name, .. } if name == "a"));
        if let Some(StreamEvent::StartTag { attrs, .. }) = link {
            let href = attrs.iter().find(|(k, _)| k == "href");
            let class = attrs.iter().find(|(k, _)| k == "class");
            let id = attrs.iter().find(|(k, _)| k == "id");
            assert_eq!(
                href.map(|(_, v)| v.as_str()),
                Some("https://example.com"),
                "href mismatch"
            );
            assert_eq!(
                class.map(|(_, v)| v.as_str()),
                Some("external"),
                "class mismatch"
            );
            assert_eq!(id.map(|(_, v)| v.as_str()), Some("link1"), "id mismatch");
        } else {
            panic!("Expected <a> start tag in {:?}", all_events);
        }
    }

    // --- Tag recognition cross-chunk tests ---

    /// Tag name split across chunks: `<str` | `ong>` is recognized as
    /// a <strong> start tag.
    #[test]
    fn test_tag_name_split_across_chunks() {
        let mut tok = StreamingTokenizer::new();
        let ev1 = tok.feed("<str").unwrap();
        let ev2 = tok.feed("ong>bold</strong>").unwrap();
        let all_events: Vec<_> = ev1.into_iter().chain(ev2).collect();
        let has_strong_start = all_events
            .iter()
            .any(|e| matches!(e, StreamEvent::StartTag { name, .. } if name == "strong"));
        let has_strong_end = all_events
            .iter()
            .any(|e| matches!(e, StreamEvent::EndTag { name } if name == "strong"));
        assert!(
            has_strong_start,
            "Expected <strong> start tag in {:?}",
            all_events
        );
        assert!(
            has_strong_end,
            "Expected </strong> end tag in {:?}",
            all_events
        );
    }

    /// Closing tag split across chunks: `</str` | `ong>` is recognized.
    #[test]
    fn test_closing_tag_split_across_chunks() {
        let mut tok = StreamingTokenizer::new();
        let ev1 = tok.feed("<strong>bold</str").unwrap();
        let ev2 = tok.feed("ong>").unwrap();
        let all_events: Vec<_> = ev1.into_iter().chain(ev2).collect();
        let has_strong_end = all_events
            .iter()
            .any(|e| matches!(e, StreamEvent::EndTag { name } if name == "strong"));
        assert!(
            has_strong_end,
            "Expected </strong> end tag in {:?}",
            all_events
        );
    }

    /// Self-closing tag split across chunks: `<br/` | `>` or `<br` | `/>`
    #[test]
    fn test_self_closing_split_across_chunks() {
        let mut tok = StreamingTokenizer::new();
        let ev1 = tok.feed("<br/").unwrap();
        let ev2 = tok.feed(">").unwrap();
        let all_events: Vec<_> = ev1.into_iter().chain(ev2).collect();
        let has_br = all_events
            .iter()
            .any(|e| matches!(e, StreamEvent::StartTag { name, .. } if name == "br"));
        assert!(has_br, "Expected <br> tag in {:?}", all_events);
    }

    /// Entity in attribute value is decoded correctly.
    #[test]
    fn test_entity_in_attribute_value() {
        let mut tok = StreamingTokenizer::new();
        let events = tok.feed(r#"<a href="page?a=1&amp;b=2">link</a>"#).unwrap();
        let link = events
            .iter()
            .find(|e| matches!(e, StreamEvent::StartTag { name, .. } if name == "a"));
        if let Some(StreamEvent::StartTag { attrs, .. }) = link {
            let href = attrs.iter().find(|(k, _)| k == "href");
            assert!(href.is_some(), "Expected href attribute");
            // html5ever decodes entities in attribute values
            assert_eq!(href.unwrap().1, "page?a=1&b=2");
        } else {
            panic!("Expected <a> start tag in {:?}", events);
        }
    }

    // --- Edge case tests ---

    /// Malformed/unclosed entity is passed through without panic.
    #[test]
    fn test_malformed_entity_no_panic() {
        let mut tok = StreamingTokenizer::new();
        let events = tok.feed("<p>&invalid; &amp &; &#; &#x;</p>").unwrap();
        // Should not panic; text content may vary based on html5ever's
        // error recovery, but events should be produced.
        assert!(
            !events.is_empty(),
            "Expected some events for malformed entities"
        );
        let _ = tok.finish().unwrap();
    }

    /// Tag split at the opening angle bracket: everything before `<` is text,
    /// the tag completes in the next chunk.
    #[test]
    fn test_split_at_angle_bracket() {
        let mut tok = StreamingTokenizer::new();
        let ev1 = tok.feed("Hello <").unwrap();
        let ev2 = tok.feed("em>world</em>").unwrap();
        let all_events: Vec<_> = ev1.into_iter().chain(ev2).collect();
        let texts: String = all_events
            .iter()
            .filter_map(|e| {
                if let StreamEvent::Text(t) = e {
                    Some(t.as_str())
                } else {
                    None
                }
            })
            .collect();
        assert!(
            texts.contains("Hello"),
            "Expected 'Hello' in text: {:?}",
            texts
        );
        assert!(
            texts.contains("world"),
            "Expected 'world' in text: {:?}",
            texts
        );
        let has_em_start = all_events
            .iter()
            .any(|e| matches!(e, StreamEvent::StartTag { name, .. } if name == "em"));
        assert!(has_em_start, "Expected <em> start tag in {:?}", all_events);
    }

    // --- Regression tests ---

    /// Regression: after html5ever panics inside feed(), the tokenizer must
    /// be invalidated so subsequent calls return a clean error instead of
    /// reusing potentially corrupted internal state.
    ///
    /// Note: html5ever is extremely unlikely to panic on any input, so we
    /// cannot easily trigger a real panic in this test. Instead we verify
    /// the invariant indirectly: after finish() consumes the tokenizer
    /// (setting it to None — the same mechanism used after a panic),
    /// subsequent feed() calls return InternalError.
    #[test]
    fn test_tokenizer_invalidated_after_consumption_returns_clean_error() {
        let mut tok = StreamingTokenizer::new();
        // Feed some data to establish state
        tok.feed("<div>").unwrap();
        // Consume the tokenizer (simulates the None state set after a panic)
        tok.finish().unwrap();
        // Subsequent feed should fail cleanly
        let err = tok.feed("<p>more</p>").unwrap_err();
        assert_eq!(err.code(), 99);
        let msg = format!("{}", err);
        assert!(
            msg.contains("already consumed"),
            "Error should mention tokenizer was consumed, got: {}",
            msg
        );
        // finish() should also fail cleanly
        let err2 = tok.finish().unwrap_err();
        assert_eq!(err2.code(), 99);
    }

    fn feed_all_budgeted(
        tokenizer: &mut BudgetedStreamingTokenizer,
        data: &str,
        events: &mut Vec<StreamEvent>,
    ) -> Result<(), ConversionError> {
        let mut offset = 0usize;
        while offset < data.len() {
            let step = tokenizer.feed_next(&data[offset..])?;
            assert!(step.consumed > 0, "non-empty input must make progress");
            offset += step.consumed;
            if let Some(batch) = step.batch {
                events.extend(batch.events);
            }
        }
        Ok(())
    }

    fn coalesce_text(events: Vec<StreamEvent>) -> Vec<StreamEvent> {
        let mut normalized: Vec<StreamEvent> = Vec::new();
        for event in events {
            if let StreamEvent::Text(text) = event {
                if let Some(StreamEvent::Text(previous)) = normalized.last_mut() {
                    previous.push_str(&text);
                } else {
                    normalized.push(StreamEvent::Text(text));
                }
            } else {
                normalized.push(event);
            }
        }
        normalized
    }

    #[test]
    fn budgeted_scanner_retains_cross_slice_lexical_state() {
        let cases: &[(&[&str], &str)] = &[
            (&["<!-- com", "ment --", ">"], "comment"),
            (&["<!DOC", "TYPE html", ">"], "doctype"),
            (&[r#"<a title="1>"#, r#"2">"#], "quoted tag"),
            (&["&am", "p;"], "entity"),
            (&["\r", "\n"], "CRLF"),
        ];

        for (chunks, label) in cases {
            let mut state = FrameScanState::Data;
            let mut span = 0usize;
            for (index, chunk) in chunks.iter().enumerate() {
                (state, span) = scan_slice(state, span, chunk, usize::MAX).unwrap();
                if index + 1 < chunks.len() {
                    assert!(
                        !state.is_data() && span > 0,
                        "{label} state cleared before its final slice"
                    );
                }
            }
            assert!(
                state.is_data() && span == 0,
                "{label} did not return to Data after completion"
            );
        }
    }

    #[test]
    fn budgeted_tokenizer_matches_persistent_tokenizer_across_slices() {
        let chunks = [
            "<!DOC",
            "TYPE html><p title=\"a>",
            "b\">A &am",
            "p;\r",
            "\nB</p><!-- com",
            "ment -->",
        ];
        let complete = chunks.concat();

        let mut expected_tokenizer = StreamingTokenizer::new();
        let mut expected = expected_tokenizer.feed(&complete).unwrap();
        expected.extend(expected_tokenizer.finish().unwrap());

        let mut actual_tokenizer =
            BudgetedStreamingTokenizer::new(crate::streaming::MemoryBudget::default().total);
        let mut actual = Vec::new();
        for chunk in chunks {
            feed_all_budgeted(&mut actual_tokenizer, chunk, &mut actual).unwrap();
        }
        actual.extend(actual_tokenizer.finish().unwrap().events);

        fn filter_for_comparison(events: Vec<StreamEvent>) -> Vec<StreamEvent> {
            events
                .into_iter()
                .filter(|e| !matches!(e, StreamEvent::Doctype | StreamEvent::Comment(_)))
                .collect()
        }

        assert_eq!(
            coalesce_text(filter_for_comparison(actual)),
            coalesce_text(filter_for_comparison(expected))
        );
    }

    #[test]
    fn oversized_incomplete_token_is_rejected_before_feed() {
        let mut tokenizer =
            BudgetedStreamingTokenizer::new(crate::streaming::MemoryBudget::default().total);
        let max_span = tokenizer.max_token_span();
        let prefix = r#"<a title=""#;
        assert!(max_span > prefix.len() + 1);

        let accepted = format!("{prefix}{}", "x".repeat(max_span - prefix.len() - 1));
        let mut events = Vec::new();
        feed_all_budgeted(&mut tokenizer, &accepted, &mut events).unwrap();
        assert!(events.is_empty(), "the tag must remain incomplete");

        let error = match tokenizer.feed_next("yy\">") {
            Ok(_) => panic!("oversized incomplete token must be rejected"),
            Err(error) => error,
        };
        assert!(matches!(
            error,
            ConversionError::BudgetExceeded {
                ref stage,
                used,
                limit,
            } if stage == "tokenizer_incomplete_token"
                && used == max_span + 1
                && limit == max_span
        ));

        feed_all_budgeted(&mut tokenizer, "\">ok</a>", &mut events).unwrap();
        let href = events.iter().find_map(|event| {
            if let StreamEvent::StartTag { attrs, .. } = event {
                attrs
                    .iter()
                    .find(|(name, _)| name == "title")
                    .map(|(_, value)| value)
            } else {
                None
            }
        });
        assert_eq!(
            href.map(String::as_str),
            Some(&accepted[prefix.len()..]),
            "rejected slice must not reach the persistent tokenizer"
        );
    }

    #[test]
    fn large_text_keeps_tokenizer_reservation_constant() {
        let total = crate::streaming::MemoryBudget::default().total;
        let mut tokenizer = BudgetedStreamingTokenizer::new(total);
        let reservation = tokenizer.reserved_bytes();
        let text = "plain text ".repeat(100_000);
        let mut events = Vec::new();

        feed_all_budgeted(&mut tokenizer, &text, &mut events).unwrap();
        events.extend(tokenizer.finish().unwrap().events);

        assert_eq!(tokenizer.reserved_bytes(), reservation);
        assert!(reservation <= total);
        assert_eq!(
            events
                .iter()
                .filter_map(|event| match event {
                    StreamEvent::Text(text) => Some(text.len()),
                    _ => None,
                })
                .sum::<usize>(),
            text.len()
        );
    }

    #[test]
    fn tiny_total_rejects_before_accepting_input() {
        let total = TOKENIZER_FIXED_RESERVATION_BYTES;
        let mut tokenizer = BudgetedStreamingTokenizer::new(total);
        assert!(tokenizer.reserved_bytes() <= total);

        let error = match tokenizer.feed_next("x") {
            Ok(_) => panic!("a total below the tokenizer envelope must reject input"),
            Err(error) => error,
        };
        assert!(matches!(
            error,
            ConversionError::BudgetExceeded {
                ref stage,
                limit,
                ..
            } if stage == "tokenizer_reservation"
                && limit == tokenizer.reserved_bytes()
        ));
    }

    #[test]
    fn finish_batch_stays_inside_reserved_envelope() {
        let total = crate::streaming::MemoryBudget::default().total;
        let mut tokenizer = BudgetedStreamingTokenizer::new(total);
        let max_span = tokenizer.max_token_span();
        // Use a complete tag (emitted in compact mode) instead of comment (filtered in compact)
        let prefix = "<a title=\"";
        let attr_value = "x".repeat(max_span - prefix.len() - 3);
        let complete_tag = format!("{prefix}{}\">", attr_value);
        let mut prior_events = Vec::new();
        feed_all_budgeted(&mut tokenizer, &complete_tag, &mut prior_events).unwrap();

        let batch = tokenizer.finish().unwrap();
        let event_storage = batch
            .events
            .capacity()
            .saturating_mul(std::mem::size_of::<StreamEvent>());
        let string_storage = batch
            .events
            .iter()
            .map(|event| match event {
                StreamEvent::Comment(text)
                | StreamEvent::Text(text)
                | StreamEvent::ParseError(text) => text.capacity(),
                StreamEvent::StartTag { name, attrs, .. } => name.capacity().saturating_add(
                    attrs
                        .iter()
                        .map(|(key, value)| key.capacity().saturating_add(value.capacity()))
                        .sum(),
                ),
                StreamEvent::EndTag { name } => name.capacity(),
                StreamEvent::Doctype => 0,
            })
            .sum::<usize>();

        assert!(event_storage.saturating_add(string_storage) <= tokenizer.reserved_bytes());
        // In compact mode, some events may be filtered/coalesced; just verify memory reservation
        // bounds hold regardless of which specific events are emitted.
        let _ = batch.events;
    }
}
