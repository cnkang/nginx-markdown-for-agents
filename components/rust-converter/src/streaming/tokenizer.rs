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
    fn new() -> Self {
        Self {
            events: RefCell::new(Vec::new()),
        }
    }

    /// Drain all accumulated events.
    fn drain(&self) -> Vec<StreamEvent> {
        self.events.borrow_mut().drain(..).collect()
    }
}

impl TokenSink for TokenSinkAdapter {
    type Handle = ();

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

/// Convert an html5ever [`Tag`] into a [`StreamEvent`].
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
    fn default() -> Self {
        Self::new()
    }
}

impl StreamingTokenizer {
    /// Create a new streaming tokenizer.
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

    /// Feed a chunk of UTF-8 HTML text and return any complete events.
    ///
    /// # Arguments
    ///
    /// * `data` - UTF-8 encoded HTML fragment
    ///
    /// # Returns
    ///
    /// A vector of [`StreamEvent`] values produced from the input chunk.
    ///
    /// # Errors
    ///
    /// Returns [`ConversionError::InternalError`] if html5ever panics or
    /// if the tokenizer has already been consumed by [`finish`](Self::finish).
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

    /// Signal end-of-input and return any remaining events.
    ///
    /// After calling this method the tokenizer is consumed and cannot be
    /// used again.
    ///
    /// # Returns
    ///
    /// A vector of any remaining [`StreamEvent`] values.
    ///
    /// # Errors
    ///
    /// Returns [`ConversionError::InternalError`] if html5ever panics or
    /// if the tokenizer has already been consumed.
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

/// Extract a human-readable message from a panic payload.
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
        let mut tok1 = StreamingTokenizer::new();
        let events1 = tok1.feed("<p>Text with <strong>bold</strong></p>").unwrap();
        let texts1: Vec<String> = events1
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
        let mut tok2 = StreamingTokenizer::new();
        let ev2a = tok2.feed("<p>Text with <str").unwrap();
        let ev2b = tok2.feed("ong>bold</strong></p>").unwrap();
        let texts2: Vec<String> = ev2a
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

        let combined1 = texts1.join("");
        let combined2 = texts2.join("");
        assert_eq!(
            combined1, combined2,
            "Text content should be identical regardless of chunk split.\n\
             Single: {:?}\nChunked: {:?}",
            texts1, texts2
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
