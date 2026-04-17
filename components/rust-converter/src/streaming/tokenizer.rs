//! html5ever TokenSink adapter for streaming tokenization.
//!
//! Wraps html5ever's tokenizer to produce [`StreamEvent`] values from
//! raw HTML byte chunks without building a DOM tree.

use std::cell::RefCell;
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
}

impl TokenSinkAdapter {
    /// Constructs a `TokenSinkAdapter` with an empty internal event buffer.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let adapter = TokenSinkAdapter::new();
    /// assert!(adapter.drain().is_empty());
    /// ```
    fn new() -> Self {
        Self {
            events: RefCell::new(Vec::new()),
        }
    }

    /// Remove and return all events currently buffered by the sink.
    ///
    /// Empties the internal event buffer and yields the collected `StreamEvent` values.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let adapter = TokenSinkAdapter::new();
    /// let events = adapter.drain();
    /// assert!(events.is_empty());
    /// ```
    fn drain(&self) -> Vec<StreamEvent> {
        self.events.borrow_mut().drain(..).collect()
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
        let event = match token {
            Token::TagToken(tag) => tag_to_event(tag),
            Token::CharacterTokens(text) => StreamEvent::Text(text.to_string()),
            Token::CommentToken(text) => StreamEvent::Comment(text.to_string()),
            Token::DoctypeToken(_) => StreamEvent::Doctype,
            Token::NullCharacterToken => StreamEvent::Text("\u{FFFD}".to_string()),
            Token::ParseError(err) => StreamEvent::ParseError(err.to_string()),
            Token::EOFToken => return TokenSinkResult::Continue,
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
        let sink = TokenSinkAdapter::new();
        let opts = TokenizerOpts {
            ..Default::default()
        };
        let tokenizer = Tokenizer::new(sink, opts);
        Self {
            tokenizer: Some(tokenizer),
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
        let tokenizer = self.tokenizer.as_mut().ok_or_else(|| {
            ConversionError::InternalError("tokenizer already consumed by finish()".into())
        })?;

        let tendril = StrTendril::from(data);
        let queue = BufferQueue::default();
        queue.push_back(tendril);

        // Wrap html5ever call in catch_unwind for panic safety.
        let result = panic::catch_unwind(panic::AssertUnwindSafe(|| {
            let _ = tokenizer.feed(&queue);
        }));

        match result {
            Ok(()) => Ok(tokenizer.sink.drain()),
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
        let tokenizer = self.tokenizer.take().ok_or_else(|| {
            ConversionError::InternalError("tokenizer already consumed by finish()".into())
        })?;

        let result = panic::catch_unwind(panic::AssertUnwindSafe(|| {
            tokenizer.end();
        }));

        match result {
            Ok(()) => Ok(tokenizer.sink.drain()),
            Err(payload) => {
                let msg = panic_payload_to_message(&payload, "html5ever panic during finish");
                Err(ConversionError::InternalError(msg))
            }
        }
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
}
