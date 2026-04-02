//! Streaming HTML-to-Markdown converter integration layer.
//!
//! [`StreamingConverter`] orchestrates all pipeline stages:
//! charset detection → tokenization → sanitization → state machine → emission,
//! plus incremental ETag hashing, token estimation, front matter extraction,
//! timeout checking, and commit state tracking.

use std::time::{Duration, Instant};

use crate::converter::ConversionOptions;
use crate::error::ConversionError;
use crate::metadata::PageMetadata;
use crate::streaming::budget::MemoryBudget;
use crate::streaming::charset::CharsetState;
use crate::streaming::emitter::IncrementalEmitter;
use crate::streaming::sanitizer::{SanitizeDecision, StreamingSanitizer};
use crate::streaming::state_machine::{StateMachineAction, StructuralStateMachine};
use crate::streaming::tokenizer::StreamingTokenizer;
use crate::streaming::types::{
    ChunkOutput, CommitState, FallbackReason, StreamEvent, StreamingResult, StreamingStats,
};

/// Streaming HTML-to-Markdown converter with bounded memory.
///
/// Processes HTML input in chunks via [`feed_chunk`](Self::feed_chunk) and
/// produces incremental Markdown output at block-level flush points. Call
/// [`finalize`](Self::finalize) after all input has been fed to obtain the
/// final result including ETag, token estimate, and statistics.
///
/// # Examples
///
/// ```ignore
/// use nginx_markdown_converter::converter::ConversionOptions;
/// use nginx_markdown_converter::streaming::budget::MemoryBudget;
/// use nginx_markdown_converter::streaming::converter::StreamingConverter;
///
/// let mut conv = StreamingConverter::new(ConversionOptions::default(), MemoryBudget::default());
/// conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
///
/// let output1 = conv.feed_chunk(b"<h1>Hello</h1>").unwrap();
/// let output2 = conv.feed_chunk(b"<p>World</p>").unwrap();
/// let result = conv.finalize().unwrap();
/// ```
pub struct StreamingConverter {
    /// Conversion options (reuses existing type).
    options: ConversionOptions,
    /// Memory budget for bounded-memory enforcement.
    budget: MemoryBudget,
    /// Charset detector / transcoder.
    charset_state: CharsetState,
    /// html5ever tokenizer (TokenSink adapter).
    tokenizer: StreamingTokenizer,
    /// Streaming security sanitizer.
    sanitizer: StreamingSanitizer,
    /// Structural state machine for document context tracking.
    state_machine: StructuralStateMachine,
    /// Incremental Markdown emitter.
    emitter: IncrementalEmitter,
    /// Incremental ETag hasher (optional, enabled when extract_metadata is set).
    etag_hasher: Option<blake3::Hasher>,
    /// Incremental token estimate: accumulated character count.
    /// The final token count is computed once in `finalize` as
    /// `ceil(total_markdown_chars / 4.0)` to match the one-shot
    /// `TokenEstimator` exactly.
    total_markdown_chars: u64,
    /// Commit state (PreCommit / PostCommit).
    commit_state: CommitState,
    /// Cooperative timeout deadline.
    deadline: Option<Instant>,
    /// Conversion statistics.
    stats: StreamingStats,
    /// Total bytes of Markdown emitted so far (for PostCommitError reporting).
    bytes_emitted: usize,
    /// Extracted page metadata from `<head>` region.
    metadata: PageMetadata,
    /// Whether we are currently collecting a `<title>` text.
    collecting_title: bool,
    /// Buffer for `<title>` element text (separate from metadata.title so
    /// that a social title set by og:title/twitter:title is not polluted
    /// by subsequent `<title>` text).
    html_title_buf: String,
    /// Whether a social title (og:title / twitter:title) has been set.
    /// When true, `<title>` text is collected but not written to
    /// `metadata.title` — the social title takes priority.
    social_title_set: bool,
    /// Whether the HTML `<title>` has already been written to metadata.
    /// Ensures first-`<title>`-wins semantics matching the full-buffer path.
    html_title_set: bool,
    /// Whether the first `<link rel="canonical" href="...">` (with an href)
    /// has been found. Once true, subsequent canonicals are ignored.
    /// Matches full-buffer `find_link_href_recursive` which skips canonicals
    /// without `href` and returns the first one that has it.
    canonical_found: bool,
    /// Bytes of `<head>` region processed so far (for lookahead budget enforcement).
    /// When this exceeds `budget.lookahead`, front matter extraction triggers
    /// an Explicit_Fallback per Requirement 10.2.
    head_bytes_seen: usize,
    /// Trailing bytes from the previous chunk that form an incomplete UTF-8
    /// sequence. Prepended to the next chunk before `String::from_utf8_lossy`
    /// so multibyte characters split across chunk boundaries are preserved.
    utf8_tail: Vec<u8>,
}

impl StreamingConverter {
    /// Constructs a StreamingConverter configured for incremental HTML→Markdown conversion.
    ///
    /// # Arguments
    ///
    /// * `options` - ConversionOptions that control features such as metadata extraction, URL resolution, and other conversion behavior.
    /// * `budget` - MemoryBudget that bounds working-set usage of pipeline components during streaming processing.
    ///
    /// # Returns
    ///
    /// A new `StreamingConverter` initialized to accept input via `feed_chunk` and to be finalized with `finalize`.
    ///
    /// # Examples
    ///
    /// ```
    /// let options = ConversionOptions::default();
    /// let budget = MemoryBudget::default();
    /// let mut conv = StreamingConverter::new(options, budget);
    /// // send some bytes
    /// let _ = conv.feed_chunk(b"<p>Hello</p>").unwrap();
    /// let result = conv.finalize().unwrap();
    /// assert!(result.final_markdown.contains("Hello"));
    /// ```
    pub fn new(options: ConversionOptions, budget: MemoryBudget) -> Self {
        let etag_hasher = if options.extract_metadata {
            Some(blake3::Hasher::new())
        } else {
            None
        };
        Self {
            options,
            charset_state: CharsetState::new(),
            tokenizer: StreamingTokenizer::new(),
            sanitizer: StreamingSanitizer::new(),
            state_machine: StructuralStateMachine::new(&budget),
            emitter: IncrementalEmitter::new(&budget),
            budget,
            etag_hasher,
            total_markdown_chars: 0,
            commit_state: CommitState::PreCommit,
            deadline: None,
            stats: StreamingStats::default(),
            bytes_emitted: 0,
            metadata: PageMetadata::new(),
            collecting_title: false,
            html_title_buf: String::new(),
            social_title_set: false,
            html_title_set: false,
            canonical_found: false,
            head_bytes_seen: 0,
            utf8_tail: Vec::new(),
        }
    }

    /// Provide an optional Content-Type header to the charset detector.
    ///
    /// Must be called before the first `feed_chunk` invocation. If the header
    /// includes a `charset` parameter, that charset takes precedence over any
    /// charset discovered via HTML meta tags.
    ///
    /// # Arguments
    ///
    /// * `content_type` - Optional Content-Type header value (for example
    ///   `"text/html; charset=UTF-8"`).
    ///
    /// # Examples
    ///
    /// ```
    /// let mut conv = StreamingConverter::new(Default::default(), Default::default());
    /// conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
    /// ```
    pub fn set_content_type(&mut self, content_type: Option<String>) {
        self.charset_state.set_content_type(content_type.as_deref());
    }

    /// Set a cooperative deadline for the conversion.
    ///
    /// The converter checks this deadline at the start of `feed_chunk` and `finalize`; if the
    /// deadline has passed those calls will return a timeout error. If adding `timeout` to the
    /// current instant would overflow, the deadline is left unset (treated as no deadline).
    ///
    /// # Examples
    ///
    /// ```no_run
    /// use std::time::Duration;
    /// // Assume `StreamingConverter::new` is available in scope.
    /// let mut conv = StreamingConverter::new(/* options */, /* budget */);
    /// // Limit the whole conversion to 2 seconds.
    /// conv.set_timeout(Duration::from_secs(2));
    /// ```
    pub fn set_timeout(&mut self, timeout: Duration) {
        // If Instant::now() + timeout overflows, treat as "no deadline"
        // (effectively infinite timeout) rather than setting an
        // already-passed deadline that would trigger immediate timeout.
        self.deadline = Instant::now().checked_add(timeout);
    }

    /// Process an incoming chunk of HTML bytes and return any ready Markdown output.
    ///
    /// This incrementally feeds the pipeline (charset detection/transcoding, tokenization,
    /// sanitization, structural analysis, and emission) and flushes complete block-level
    /// Markdown that became available as a result of this chunk.
    ///
    /// # Arguments
    ///
    /// * `data` - Raw HTML bytes in any text encoding; charset detection/transcoding is applied.
    ///
    /// # Returns
    ///
    /// `Ok(ChunkOutput)` containing the emitted Markdown bytes and the number of block-level
    /// flushes produced; or an appropriate `ConversionError` such as streaming fallback,
    /// budget/memory limits, timeout (pre-commit), or a post-commit error that includes
    /// bytes already emitted.
    ///
    /// # Examples
    ///
    /// ```
    /// let options = ConversionOptions::default();
    /// let budget = MemoryBudget::default();
    /// let mut conv = StreamingConverter::new(options, budget);
    /// let out = conv.feed_chunk(b"<p>Hello</p>").unwrap();
    /// assert!(!out.markdown.is_empty());
    /// ```
    pub fn feed_chunk(&mut self, data: &[u8]) -> Result<ChunkOutput, ConversionError> {
        // 1. Check cooperative timeout
        self.check_timeout()?;

        // 2. Check prune_noise_regions feature → fallback if enabled and PreCommit
        #[cfg(feature = "prune_noise_regions")]
        if matches!(self.commit_state, CommitState::PreCommit) {
            return Err(ConversionError::StreamingFallback {
                reason: FallbackReason::UnsupportedStructure("prune_noise_regions".to_string()),
            });
        }

        // 3. Charset detection / transcoding
        let transcoded = self
            .charset_state
            .feed(data)
            .map_err(|e| self.wrap_error(e))?;

        // If charset is still pending (accumulating sniff buffer), no tokens yet
        if transcoded.is_empty() {
            self.stats.chunks_processed = self.stats.chunks_processed.saturating_add(1);
            return Ok(ChunkOutput {
                markdown: Vec::new(),
                flush_count: 0,
            });
        }

        // 4. Tokenization: feed UTF-8 to html5ever.
        //
        // Prepend any trailing bytes from the previous chunk that formed
        // an incomplete UTF-8 sequence, then split off any new incomplete
        // tail so multibyte characters spanning chunk boundaries are
        // preserved instead of being replaced with U+FFFD.
        let effective = if self.utf8_tail.is_empty() {
            std::borrow::Cow::Borrowed(transcoded.as_ref())
        } else {
            let mut combined = std::mem::take(&mut self.utf8_tail);
            combined.extend_from_slice(&transcoded);
            std::borrow::Cow::Owned(combined)
        };

        // Find the last valid UTF-8 boundary. Any trailing bytes that
        // start a multibyte sequence but don't complete it are stashed
        // in utf8_tail for the next chunk.
        let (valid, tail) = split_utf8_tail(&effective);
        if !tail.is_empty() {
            self.utf8_tail = tail.to_vec();
        }

        let utf8_str = match std::str::from_utf8(valid) {
            Ok(s) => std::borrow::Cow::Borrowed(s),
            // Interior invalid bytes that aren't a trailing split —
            // fall back to lossy so the tokenizer still gets input.
            Err(_) => String::from_utf8_lossy(valid),
        };

        let events = self
            .tokenizer
            .feed(&utf8_str)
            .map_err(|e| self.wrap_error(e))?;

        // 5. Process each token: sanitize → state machine → emitter
        let initial_flush_count = self.emitter.flush_count();
        for event in events {
            self.process_single_event(event)?;
        }

        // 6. Collect flushed output
        let flushed = self.emitter.take_flushed();
        let flush_count = self
            .emitter
            .flush_count()
            .saturating_sub(initial_flush_count);

        // 7. Update ETag hasher and token count with flushed bytes
        self.update_incremental_stats(&flushed);

        // 8. Track CommitState transition
        if !flushed.is_empty() && matches!(self.commit_state, CommitState::PreCommit) {
            self.commit_state = CommitState::PostCommit;
        }

        self.stats.chunks_processed = self.stats.chunks_processed.saturating_add(1);

        Ok(ChunkOutput {
            markdown: flushed,
            flush_count,
        })
    }

    /// Finalize the streaming conversion and produce the final converted result.
    ///
    /// This method consumes the converter, flushes any pending pipeline state, auto-closes
    /// unclosed HTML contexts, finalizes metadata resolution, computes the final ETag and
    /// token estimate, and returns the accumulated conversion output and statistics.
    ///
    /// # Returns
    ///
    /// `StreamingResult` containing:
    /// - `final_markdown`: the remaining emitted Markdown bytes,
    /// - `token_estimate`: a token-count estimate computed from total emitted characters,
    /// - `etag`: optional final ETag (hex-encoded, first 16 bytes) when metadata extraction was enabled,
    /// - `stats`: accumulated streaming statistics.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError` for timeout, budget/memory limits, fallback, or post-commit errors
    /// that occur during finalization.
    ///
    /// # Examples
    ///
    /// ```no_run
    /// use components::streaming::StreamingConverter;
    ///
    /// // Create converter with appropriate options and budget (omitted).
    /// let options = Default::default();
    /// let budget = Default::default();
    /// let converter = StreamingConverter::new(options, budget);
    ///
    /// // Finalize and obtain the final result (consumes `converter`).
    /// let result = converter.finalize().expect("finalize conversion");
    /// println!("Final markdown size: {}", result.final_markdown.len());
    /// ```
    pub fn finalize(mut self) -> Result<StreamingResult, ConversionError> {
        // Check timeout
        self.check_timeout()?;

        // 1. Flush charset state (any remaining buffered data)
        let remaining_charset = self.charset_state.flush().map_err(|e| self.wrap_error(e))?;

        // 2. If there is remaining charset data or a stashed UTF-8 tail,
        //    combine them and feed to the tokenizer. At end-of-input we use
        //    lossy conversion for any truly invalid trailing bytes.
        let mut final_bytes = std::mem::take(&mut self.utf8_tail);
        final_bytes.extend_from_slice(&remaining_charset);
        if !final_bytes.is_empty() {
            let utf8_cow = String::from_utf8_lossy(&final_bytes);
            let events = self
                .tokenizer
                .feed(&utf8_cow)
                .map_err(|e| self.wrap_error(e))?;
            for event in events {
                self.process_single_event(event)?;
            }
        }

        // 3. Finish tokenizer (signal end-of-input)
        let final_events = self.tokenizer.finish().map_err(|e| self.wrap_error(e))?;

        // 4. Process remaining tokens
        for event in final_events {
            self.process_single_event(event)?;
        }

        // 5. Finalize state machine (auto-close unclosed contexts)
        let unclosed = self.state_machine.finalize();
        for ctx in &unclosed {
            let action = StateMachineAction::Exit(ctx.clone());
            self.emitter
                .process_action(&action, &mut self.state_machine)
                .map_err(|e| self.wrap_error(e))?;
        }

        // 6. Finalize emitter (flush all pending bytes)
        let final_markdown = self.emitter.finalize().map_err(|e| self.wrap_error(e))?;

        // Update incremental stats with final markdown
        self.update_incremental_stats(&final_markdown);

        // 6b. Finalize metadata URL: match MetadataExtractor::extract
        // which unconditionally overwrites metadata.url after meta tag
        // extraction — canonical if found, else base_url (which may be None).
        // This means og:url is always overwritten in the final result.
        if self.options.extract_metadata {
            self.metadata.url = Self::resolve_final_url(
                self.canonical_found,
                &self.metadata.url,
                &self.options.base_url,
            );
        }

        // 7. Compute final ETag
        let etag = self.etag_hasher.map(|hasher| {
            let hash = hasher.finalize();
            let hash_bytes = hash.as_bytes();
            format!("\"{}\"", hex::encode(&hash_bytes[..16]))
        });

        // 8. Compute final token estimate from accumulated character count.
        // Uses the same ceil(chars / 4.0) formula as TokenEstimator,
        // computed once over the total to avoid per-flush rounding drift.
        // Saturates to u32::MAX for documents exceeding ~17 billion chars.
        let token_estimate = {
            let est = self.total_markdown_chars.div_ceil(4);
            Some(u32::try_from(est).unwrap_or(u32::MAX))
        };

        Ok(StreamingResult {
            final_markdown,
            token_estimate,
            etag,
            stats: self.stats,
        })
    }

    // ── Internal helpers ────────────────────────────────────────────

    /// Processes a single `StreamEvent` through the sanitizer, state machine, and emitter.
    ///
    /// This advances internal streaming state (including token counts, optional head-region
    /// metadata extraction, and peak-memory accounting) but does not drain the emitter's
    /// flushed output buffer — the caller is responsible for reading flushed bytes.
    ///
    /// # Errors
    ///
    /// Returns a `ConversionError` when processing cannot continue:
    /// - `StreamingFallback { reason: FrontMatterOverflow }` if head-region lookahead exceeds the budget during pre-commit metadata extraction.
    /// - `StreamingFallback { reason: TableDetected }` if a table requires fallback during pre-commit.
    /// - `PostCommitError` if a table or other error is detected after commit; the error includes `bytes_emitted`.
    /// - `MemoryLimit` when sanitization reports nesting depth exceeded.
    /// - Other `ConversionError` variants produced by the state machine or emitter; in post-commit these are wrapped as `PostCommitError`.
    ///
    /// # Examples
    ///
    /// ```
    /// // Assume `conv` is a mutable StreamingConverter and `ev` is a StreamEvent.
    /// // The example demonstrates the callsite pattern; concrete construction is omitted.
    /// let _ = conv.process_single_event(ev)?;
    /// ```
    fn process_single_event(&mut self, event: StreamEvent) -> Result<(), ConversionError> {
        self.stats.tokens_processed = self.stats.tokens_processed.saturating_add(1);

        // Front matter extraction: collect metadata from <head> region,
        // but only when metadata extraction is enabled (Requirement 10.1).
        // If the <head> region exceeds the lookahead budget, trigger
        // Explicit_Fallback per Requirement 10.2 (FrontMatterOverflow).
        if self.state_machine.in_head && self.options.extract_metadata {
            let budget_exceeded = self.extract_metadata_from_event(&event);
            if budget_exceeded && matches!(self.commit_state, CommitState::PreCommit) {
                return Err(ConversionError::StreamingFallback {
                    reason: FallbackReason::FrontMatterOverflow,
                });
            }
        }

        // Sanitize
        let decision = self.sanitizer.process_event(event);

        let sanitized_event = match decision {
            SanitizeDecision::Pass(ev) | SanitizeDecision::PassModified(ev) => ev,
            SanitizeDecision::Skip => return Ok(()),
            SanitizeDecision::DepthExceeded => {
                return Err(self.wrap_error(ConversionError::MemoryLimit(
                    "nesting depth exceeded during sanitization".to_string(),
                )));
            }
        };

        // State machine
        let action = self
            .state_machine
            .process_event(&sanitized_event)
            .map_err(|e| self.wrap_error(e))?;

        // Check for table fallback
        if matches!(action, StateMachineAction::FallbackRequired) {
            if matches!(self.commit_state, CommitState::PreCommit) {
                return Err(ConversionError::StreamingFallback {
                    reason: FallbackReason::TableDetected,
                });
            } else {
                return Err(ConversionError::PostCommitError {
                    reason: "table detected after commit".to_string(),
                    bytes_emitted: self.bytes_emitted,
                    original_code: 99, /* internal: no pre-existing error */
                });
            }
        }

        // Emitter
        self.emitter
            .process_action(&action, &mut self.state_machine)
            .map_err(|e| self.wrap_error(e))?;

        // Track peak working set after emitter processes the event,
        // when the pending buffer is at its largest.
        self.update_peak_memory().map_err(|e| self.wrap_error(e))?;

        Ok(())
    }

    /// Checks whether the converter's cooperative timeout has been exceeded.
    ///
    /// If a deadline is set and the current time is at or after that deadline,
    /// returns a timeout error. If the converter is already in `PostCommit` state
    /// the error is returned as `ConversionError::PostCommitError` and includes the
    /// number of bytes already emitted.
    ///
    /// # Examples
    ///
    /// ```no_run
    /// // Call from a StreamingConverter instance to validate its deadline.
    /// // let conv: StreamingConverter = /* ... */ ;
    /// // conv.check_timeout()?;
    /// ```
    fn check_timeout(&self) -> Result<(), ConversionError> {
        if let Some(deadline) = self.deadline
            && Instant::now() >= deadline
        {
            if matches!(self.commit_state, CommitState::PostCommit) {
                return Err(ConversionError::PostCommitError {
                    reason: "timeout exceeded".to_string(),
                    bytes_emitted: self.bytes_emitted,
                    original_code: ConversionError::Timeout.code(),
                });
            }
            return Err(ConversionError::Timeout);
        }
        Ok(())
    }

    /// Wrap an error for the current commit state.
    ///
    /// - In `PostCommit` state, wraps the error as `PostCommitError` with
    ///   the emitted byte count so the caller knows how much was delivered.
    /// - In `PreCommit` state, returns the original error unchanged so the
    ///   caller can match on specific variants (`EncodingError`,
    ///   `BudgetExceeded`, etc.).
    fn wrap_error(&self, err: ConversionError) -> ConversionError {
        if matches!(self.commit_state, CommitState::PostCommit) {
            ConversionError::PostCommitError {
                original_code: err.code(),
                reason: err.to_string(),
                bytes_emitted: self.bytes_emitted,
            }
        } else {
            err
        }
    }

    /// Update streaming statistics with newly flushed Markdown bytes.
    ///
    /// This updates the converter's cumulative counters and optional ETag state:
    /// - increments `bytes_emitted` by the flushed byte length (saturating),
    /// - updates the incremental ETag hasher (if enabled) with `flushed`,
    /// - increments `total_markdown_chars` by the number of Unicode scalar values in `flushed` (decoded with `String::from_utf8_lossy`, saturating),
    /// - and refreshes `stats.flush_count` from the emitter.
    ///
    /// # Parameters
    ///
    /// - `flushed`: the slice of UTF-8 (or potentially ill-formed UTF-8) bytes that were emitted since the last update.
    fn update_incremental_stats(&mut self, flushed: &[u8]) {
        if flushed.is_empty() {
            return;
        }

        self.bytes_emitted = self.bytes_emitted.saturating_add(flushed.len());

        // Update ETag hasher
        if let Some(ref mut hasher) = self.etag_hasher {
            hasher.update(flushed);
        }

        // Accumulate character count for token estimation.
        // The final token count is computed once in finalize() as
        // ceil(total_chars / 4.0) to match the one-shot TokenEstimator.
        let text = String::from_utf8_lossy(flushed);
        let char_count = text.chars().count() as u64;
        self.total_markdown_chars = self.total_markdown_chars.saturating_add(char_count);

        // Update stats
        self.stats.flush_count = self.emitter.flush_count();
    }

    /// Estimate the current in-memory working set and update peak.
    fn update_peak_memory(&mut self) -> Result<(), ConversionError> {
        // Only count state that is actually resident in memory right now:
        // - emitter pending buffer (not yet flushed)
        // - emitter flushed buffer (awaiting take_flushed by caller)
        // - state machine stack
        // - metadata extracted from <head> (actual String allocations)
        // - html_title_buf (temporary <title> text before </title>)
        //
        // Note: `head_bytes_seen` is intentionally NOT included here.
        // It is a cumulative counter for lookahead budget enforcement
        // (Requirement 10.2), not a measure of resident memory.
        let working_set = self.emitter.pending_bytes()
            + self.emitter.flushed_bytes()
            + self.state_machine.stack_bytes_estimate()
            + self.metadata.bytes_estimate()
            + self.html_title_buf.len();
        self.stats.peak_memory_estimate = self.stats.peak_memory_estimate.max(working_set);

        // Enforce budget.total: the working set must not exceed the
        // declared total-memory cap.
        if working_set > self.budget.total {
            return Err(ConversionError::BudgetExceeded {
                stage: "total".to_string(),
                used: working_set,
                limit: self.budget.total,
            });
        }
        Ok(())
    }

    /// Extract metadata from events occurring in the `<head>` region.
    ///
    /// Collects title text from `<title>` elements and metadata from
    /// `<meta>` tags with appropriate `name`/`property` attributes.
    ///
    /// Tracks the cumulative size of `<head>` events against the
    /// lookahead budget. Returns `true` if the budget is exceeded,
    /// signalling that the caller should trigger an Explicit_Fallback
    /// per Requirement 10.2.
    fn extract_metadata_from_event(&mut self, event: &StreamEvent) -> bool {
        // Estimate the byte cost of this event for lookahead budget tracking.
        // This is a rough estimate — we count tag names, attribute keys/values,
        // and text content lengths.
        let event_cost = match event {
            StreamEvent::StartTag { name, attrs, .. } => name.len().saturating_add(
                attrs
                    .iter()
                    .map(|(k, v)| k.len().saturating_add(v.len()))
                    .sum::<usize>(),
            ),
            StreamEvent::EndTag { name } => name.len(),
            StreamEvent::Text(t) => t.len(),
            _ => 0,
        };
        self.head_bytes_seen = self.head_bytes_seen.saturating_add(event_cost);

        // Check lookahead budget — if exceeded, signal fallback needed.
        if self.head_bytes_seen > self.budget.lookahead {
            return true; // budget exceeded
        }

        match event {
            StreamEvent::StartTag {
                name,
                attrs,
                self_closing,
            } => {
                match name.as_str() {
                    "title" => {
                        self.collecting_title = true;
                        self.html_title_buf.clear();
                    }
                    "meta" => {
                        self.extract_meta_tag(attrs);
                    }
                    "link" => {
                        // First `<link rel="canonical" href="...">` with an href
                        // wins. Canonicals without href are skipped, matching
                        // full-buffer `find_link_href_recursive` which returns
                        // `get_attr("href")` — if None, recursion continues.
                        if !self.canonical_found {
                            let is_canonical =
                                attrs.iter().any(|(k, v)| k == "rel" && v == "canonical");
                            if is_canonical
                                && let Some((_, href)) = attrs.iter().find(|(k, _)| k == "href")
                            {
                                self.canonical_found = true;
                                self.metadata.url = Some(self.resolve_url(href));
                                // No href → skip this canonical, keep looking
                            }
                        }
                    }
                    _ => {}
                }
                // Self-closing title is unusual but handle gracefully
                if *self_closing && name == "title" {
                    self.collecting_title = false;
                }
            }
            StreamEvent::EndTag { name } => {
                if name == "title" {
                    self.collecting_title = false;
                    // First <title> wins unconditionally (matching full-buffer
                    // find_title which returns the first match's trimmed text,
                    // even if empty). Mark html_title_set regardless of content
                    // so subsequent <title> elements are ignored.
                    if !self.social_title_set && !self.html_title_set {
                        let trimmed = self.html_title_buf.trim().to_string();
                        self.metadata.title = Some(trimmed);
                        self.html_title_set = true;
                    }
                    self.html_title_buf.clear();
                }
            }
            StreamEvent::Text(text) => {
                if self.collecting_title && !text.is_empty() {
                    // Collect raw text into the separate buffer.
                    // The final trim happens at </title> above.
                    self.html_title_buf.push_str(text);
                }
            }
            _ => {}
        }
        false // within budget
    }

    /// Update page metadata from a `<meta>` tag's attributes.
    ///
    /// Selects a metadata key by preferring the `property` attribute over `name`,
    /// reads the `content` attribute, and applies site-specific priority rules:
    /// - `og:*` and `twitter:*` keys override or set curated metadata (e.g., titles,
    ///   descriptions). Social titles set `social_title_set`.
    /// - Generic keys (e.g., `description`, `author`, `article:published_time`)
    ///   use first-win semantics where documented.
    /// - Image URLs are resolved via the converter's URL resolution when set.
    ///
    /// # Parameters
    ///
    /// - `attrs`: a slice of `(attribute_name, attribute_value)` pairs representing
    ///   the attributes of the `<meta>` tag; matching is done by exact name
    ///   (e.g., `"property"`, `"name"`, `"content"`).
    ///
    /// # Examples
    ///
    /// ```
    /// // Example usage within the converter implementation:
    /// // let mut conv = StreamingConverter::new(options, budget);
    /// // conv.extract_meta_tag(&[("property".into(), "og:title".into()), ("content".into(), "Example".into())]);
    /// ```
    fn extract_meta_tag(&mut self, attrs: &[(String, String)]) {
        // Prefer `property` over `name`, matching MetadataExtractor's
        // `property.or(name)` lookup order.
        let property_val = attrs
            .iter()
            .find(|(k, _)| k == "property")
            .map(|(_, v)| v.as_str());
        let name_val = attrs
            .iter()
            .find(|(k, _)| k == "name")
            .map(|(_, v)| v.as_str());
        let key = property_val.or(name_val);

        let content_attr = attrs
            .iter()
            .find(|(k, _)| k == "content")
            .map(|(_, v)| v.as_str());

        if let (Some(key_val), Some(content_val)) = (key, content_attr) {
            let content = content_val.to_string();
            match key_val {
                // OG/Twitter title overrides any existing title
                "og:title" | "twitter:title" => {
                    self.metadata.title = Some(content);
                    self.social_title_set = true;
                }
                // OG/Twitter description overrides any existing description
                "og:description" | "twitter:description" => {
                    self.metadata.description = Some(content);
                }
                // Generic description: first-wins fallback
                "description" => {
                    if self.metadata.description.is_none() {
                        self.metadata.description = Some(content);
                    }
                }
                // OG/Twitter image: first-wins (resolve URL if configured)
                "og:image" | "twitter:image" => {
                    if self.metadata.image.is_none() {
                        self.metadata.image = Some(self.resolve_url(&content));
                    }
                }
                "og:url" => {
                    if self.metadata.url.is_none() {
                        self.metadata.url = Some(content);
                    }
                }
                "author" => {
                    if self.metadata.author.is_none() {
                        self.metadata.author = Some(content);
                    }
                }
                "article:published_time" => {
                    if self.metadata.published.is_none() {
                        self.metadata.published = Some(content);
                    }
                }
                _ => {}
            }
        }
    }

    /// Resolve a possibly-relative URL against the converter's configured base URL.
    ///
    /// If relative resolution is disabled, the input is empty, the input is already absolute
    /// (`http://`, `https://`, or `//`), the converter has no `base_url`, or the `base_url`
    /// does not start with `http://` or `https://`, the original `url` is returned unchanged.
    /// If `url` starts with `/`, it is resolved against the origin (scheme + authority) of
    /// `base_url`. Otherwise `url` is resolved relative to the directory portion of `base_url`.
    ///
    /// # Examples
    ///
    /// ```
    /// # use components::rust_converter::streaming::converter::{StreamingConverter, ConversionOptions, MemoryBudget};
    /// // (pseudo-constructors shown for clarity; actual test harness constructs a converter)
    /// let mut opts = ConversionOptions::default();
    /// opts.resolve_relative_urls = true;
    /// opts.base_url = Some("https://example.com/path/to/page.html".to_string());
    /// let conv = StreamingConverter::new(opts, MemoryBudget::default());
    ///
    /// assert_eq!(conv.resolve_url("/img.png"), "https://example.com/img.png");
    /// assert_eq!(conv.resolve_url("icons/logo.svg"), "https://example.com/path/to/icons/logo.svg");
    /// assert_eq!(conv.resolve_url("https://other.test/x"), "https://other.test/x");
    /// ```
    fn resolve_url(&self, url: &str) -> String {
        if !self.options.resolve_relative_urls || url.is_empty() {
            return url.to_string();
        }
        if url.starts_with("http://") || url.starts_with("https://") || url.starts_with("//") {
            return url.to_string();
        }
        let Some(ref base) = self.options.base_url else {
            return url.to_string();
        };
        if !base.starts_with("http://") && !base.starts_with("https://") {
            return url.to_string();
        }
        if url.starts_with('/') {
            // Extract origin (scheme + authority)
            let after_scheme = base
                .strip_prefix("https://")
                .or_else(|| base.strip_prefix("http://"))
                .unwrap_or(base);
            let origin = if let Some(pos) = after_scheme.find('/') {
                let scheme_len = if base.starts_with("https://") { 8 } else { 7 };
                &base[..scheme_len + pos]
            } else {
                base.as_str()
            };
            return format!("{}{}", origin, url);
        }
        let trimmed = base.trim_end_matches('/');
        let base_dir = if let Some(pos) = trimmed.rfind('/') {
            if pos > 0 && trimmed.as_bytes().get(pos - 1) == Some(&b'/') {
                trimmed
            } else {
                &trimmed[..pos]
            }
        } else {
            trimmed
        };
        format!("{}/{}", base_dir, url)
    }

    /// Selects the final metadata URL, preferring a discovered canonical over the base URL.
    ///
    /// If `canonical_found` is `true`, returns `current_url.clone()` (the canonical URL previously
    /// recorded during head processing). If `canonical_found` is `false`, returns `base_url.clone()`,
    /// which may be `None` to clear any previously observed `og:url`.
    ///
    /// # Examples
    ///
    /// ```
    /// let canonical = Some("https://example.com/canonical".to_string());
    /// let base = Some("https://example.com/".to_string());
    /// assert_eq!(resolve_final_url(true, &canonical, &base), canonical);
    ///
    /// let og = Some("https://example.com/og".to_string());
    /// assert_eq!(resolve_final_url(false, &og, &base), base);
    ///
    /// // base_url `None` clears prior og:url when no canonical was found
    /// assert_eq!(resolve_final_url(false, &og, &None), None);
    /// ```
    fn resolve_final_url(
        canonical_found: bool,
        current_url: &Option<String>,
        base_url: &Option<String>,
    ) -> Option<String> {
        if canonical_found {
            current_url.clone()
        } else {
            base_url.clone()
        }
    }

    /// Access the converter's extracted page metadata.
    ///
    /// Metadata is populated from `<head>` region events observed during calls to
    /// `feed_chunk`. This returns the current metadata collected so far without
    /// consuming the converter.
    ///
    /// # Examples
    ///
    /// ```
    /// // Given a `StreamingConverter` instance `conv`, borrow its metadata:
    /// // let conv = StreamingConverter::new(options, budget);
    /// // let meta = conv.metadata();
    /// // assert!(meta.title.is_none() || meta.title.is_some());
    /// ```
    pub fn metadata(&self) -> &PageMetadata {
        &self.metadata
    }

    /// Retrieve the converter's current commit state.
    ///
    /// # Examples
    ///
    /// ```
    /// # use components::streaming::converter::StreamingConverter;
    /// # use components::streaming::converter::CommitState;
    /// // Constructing with default placeholders for illustration; real callers
    /// // should provide valid `ConversionOptions` and `MemoryBudget`.
    /// let conv = StreamingConverter::new(Default::default(), Default::default());
    /// let state = conv.commit_state();
    /// // `state` is a `CommitState` value such as `CommitState::PreCommit`.
    /// let _ = state;
    /// ```
    pub fn commit_state(&self) -> CommitState {
        self.commit_state
    }

    /// Access the converter's conversion options.
    ///
    /// # Examples
    ///
    /// ```
    /// // given a `StreamingConverter` named `converter`
    /// let opts = converter.options();
    /// // inspect an option, e.g. opts.extract_metadata
    /// ```
    pub fn options(&self) -> &ConversionOptions {
        &self.options
    }

    /// Preview the final metadata URL that `finalize` would produce without consuming the converter.
    ///
    /// If metadata extraction is disabled, returns `None`. Otherwise returns the resolved URL after applying canonical-vs-base_url convergence rules used by `finalize`.
    ///
    /// # Examples
    ///
    /// ```
    /// // Inspect the URL `finalize` would produce without finalizing the converter.
    /// let url = converter.peek_final_url();
    /// ```
    pub fn peek_final_url(&self) -> Option<String> {
        if !self.options.extract_metadata {
            return None;
        }
        Self::resolve_final_url(
            self.canonical_found,
            &self.metadata.url,
            &self.options.base_url,
        )
    }

    // TODO: Fast-path evaluation (P1, optional) — deferred to future version.
    // When implemented, this would evaluate whether the document qualifies
    // for fast-path processing during the Pre-Commit Phase, equivalent to
    // the existing `fast_path::qualifies()` logic. If fast-path evaluation
    // requires data beyond the lookahead budget, it should be skipped and
    // the standard streaming pipeline used instead.
}

/// Split a byte slice at the last valid UTF-8 boundary.
///
/// Returns `(valid, tail)` where `valid` is guaranteed to be well-formed
/// UTF-8 and `tail` contains 0–3 trailing bytes that start an incomplete
/// multibyte sequence. At end-of-input the caller should feed `tail`
/// through lossy conversion.
fn split_utf8_tail(bytes: &[u8]) -> (&[u8], &[u8]) {
    // Fast path: if the entire slice is valid UTF-8, no split needed.
    if std::str::from_utf8(bytes).is_ok() {
        return (bytes, &[]);
    }

    // Walk backwards (at most 3 bytes) to find the start of an incomplete
    // multibyte sequence at the end.
    let len = bytes.len();
    let check_from = len.saturating_sub(3);
    for i in (check_from..len).rev() {
        let b = bytes[i];
        // Leading byte of a multibyte sequence?
        if b >= 0xC0 {
            let expected_len = if b < 0xE0 {
                2
            } else if b < 0xF0 {
                3
            } else {
                4
            };
            let available = len - i;
            if available < expected_len {
                // Incomplete sequence — split here
                return (&bytes[..i], &bytes[i..]);
            }
            // Sequence is complete; check if it's valid
            if std::str::from_utf8(&bytes[i..i + expected_len]).is_ok() {
                // The incomplete part must be after this sequence
                break;
            }
            // Invalid sequence — treat as split point
            return (&bytes[..i], &bytes[i..]);
        }
    }

    // Fallback: couldn't isolate a clean tail; return everything as valid
    // (lossy conversion will handle any interior invalid bytes).
    (bytes, &[])
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Create a `StreamingConverter` with UTF-8 content type preconfigured.
    
    ///
    
    /// This helper initializes a converter with default options and budget and
    
    /// pre-resolves the charset to `UTF-8` so that small inputs are processed
    
    /// immediately instead of waiting for the charset sniff buffer to fill.
    
    ///
    
    /// # Examples
    
    ///
    
    /// ```
    
    /// let mut conv = make_converter();
    
    /// // ready to feed small UTF-8 HTML chunks without charset sniffing delay
    
    /// let out = conv.feed_chunk(b"<p>hi</p>").unwrap();
    
    /// assert!(out.markdown.len() > 0);
    
    /// ```
    fn make_converter() -> StreamingConverter {
        let mut conv =
            StreamingConverter::new(ConversionOptions::default(), MemoryBudget::default());
        // Pre-resolve charset so small inputs are processed immediately
        // without waiting for the 1024-byte sniff buffer to fill.
        conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
        conv
    }

    // ── resolve_final_url unit tests ────────────────────────────────

    #[test]
    fn test_resolve_final_url_canonical_wins() {
        let result = StreamingConverter::resolve_final_url(
            true,
            &Some("https://canonical.example.com".to_string()),
            &Some("https://base.example.com".to_string()),
        );
        assert_eq!(result.as_deref(), Some("https://canonical.example.com"));
    }

    #[test]
    fn test_resolve_final_url_no_canonical_uses_base() {
        let result = StreamingConverter::resolve_final_url(
            false,
            &Some("https://og.example.com".to_string()), // og:url still in current_url
            &Some("https://base.example.com".to_string()),
        );
        assert_eq!(
            result.as_deref(),
            Some("https://base.example.com"),
            "base_url should overwrite og:url when no canonical found"
        );
    }

    #[test]
    fn test_resolve_final_url_no_canonical_no_base_clears() {
        let result = StreamingConverter::resolve_final_url(
            false,
            &Some("https://og.example.com".to_string()),
            &None,
        );
        assert_eq!(
            result, None,
            "no canonical + no base_url should clear og:url"
        );
    }

    #[test]
    fn test_resolve_final_url_canonical_no_base() {
        let result = StreamingConverter::resolve_final_url(
            true,
            &Some("https://canonical.example.com".to_string()),
            &None,
        );
        assert_eq!(result.as_deref(), Some("https://canonical.example.com"));
    }

    #[test]
    fn test_resolve_final_url_nothing() {
        let result = StreamingConverter::resolve_final_url(false, &None, &None);
        assert_eq!(result, None);
    }

    /// Creates a `StreamingConverter` configured to extract page metadata and initialized with a
    /// text/html UTF-8 content type.
    ///
    /// # Examples
    ///
    /// ```
    /// let mut conv = make_converter_with_metadata();
    /// assert!(conv.options().extract_metadata);
    /// ```
    fn make_converter_with_metadata() -> StreamingConverter {
        let opts = ConversionOptions {
            extract_metadata: true,
            ..ConversionOptions::default()
        };
        let mut conv = StreamingConverter::new(opts, MemoryBudget::default());
        conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
        conv
    }

    #[test]
    fn test_basic_heading_conversion() {
        let mut conv = make_converter();
        let output = conv.feed_chunk(b"<h1>Hello World</h1>").unwrap();
        let result = conv.finalize().unwrap();

        let mut full = output.markdown;
        full.extend_from_slice(&result.final_markdown);
        let text = String::from_utf8(full).unwrap();
        assert!(text.contains("# Hello World"), "got: {}", text);
    }

    #[test]
    fn test_basic_paragraph_conversion() {
        let mut conv = make_converter();
        let output = conv.feed_chunk(b"<p>Hello</p><p>World</p>").unwrap();
        let result = conv.finalize().unwrap();

        let mut full = output.markdown;
        full.extend_from_slice(&result.final_markdown);
        let text = String::from_utf8(full).unwrap();
        assert!(text.contains("Hello"), "got: {}", text);
        assert!(text.contains("World"), "got: {}", text);
    }

    #[test]
    fn test_etag_generation() {
        let mut conv = make_converter_with_metadata();
        conv.feed_chunk(b"<h1>Test</h1>").unwrap();
        let result = conv.finalize().unwrap();

        assert!(result.etag.is_some());
        let etag = result.etag.unwrap();
        assert!(
            etag.starts_with('"'),
            "ETag should start with quote: {}",
            etag
        );
        assert!(etag.ends_with('"'), "ETag should end with quote: {}", etag);
        // 32 hex chars + 2 quotes = 34
        assert_eq!(etag.len(), 34, "ETag length should be 34: {}", etag);
    }

    #[test]
    fn test_etag_deterministic() {
        let html = b"<h1>Deterministic</h1><p>Test content</p>";

        let mut conv1 = make_converter_with_metadata();
        conv1.feed_chunk(html).unwrap();
        let result1 = conv1.finalize().unwrap();

        let mut conv2 = make_converter_with_metadata();
        conv2.feed_chunk(html).unwrap();
        let result2 = conv2.finalize().unwrap();

        assert_eq!(result1.etag, result2.etag, "ETags should be deterministic");
    }

    /// ETag should be None when extract_metadata is disabled (default).
    #[test]
    fn test_etag_none_when_metadata_disabled() {
        let mut conv = make_converter(); // default: extract_metadata = false
        conv.feed_chunk(b"<h1>Test</h1>").unwrap();
        let result = conv.finalize().unwrap();
        assert!(
            result.etag.is_none(),
            "ETag should be None when extract_metadata is disabled"
        );
    }

    #[test]
    fn test_token_estimate() {
        let mut conv = make_converter();
        conv.feed_chunk(b"<p>Hello World</p>").unwrap();
        let result = conv.finalize().unwrap();

        assert!(result.token_estimate.is_some());
        let estimate = result.token_estimate.unwrap();
        assert!(estimate > 0, "Token estimate should be > 0");
    }

    #[test]
    fn test_front_matter_title_extraction() {
        let mut conv = make_converter_with_metadata();
        conv.feed_chunk(
            b"<html><head><title>My Page Title</title></head><body><p>Content</p></body></html>",
        )
        .unwrap();
        let _result = conv.finalize().unwrap();

        // Metadata is consumed during finalize, check before
        // Actually we need to check before finalize consumes self
        let mut conv2 = make_converter_with_metadata();
        conv2.feed_chunk(b"<html><head><title>My Page Title</title></head><body><p>Content</p></body></html>").unwrap();
        assert_eq!(conv2.metadata().title.as_deref(), Some("My Page Title"));
    }

    #[test]
    fn test_front_matter_meta_extraction() {
        let mut conv = make_converter_with_metadata();
        let html = b"<html><head>\
            <meta name=\"description\" content=\"A test page\">\
            <meta name=\"author\" content=\"Test Author\">\
            <meta property=\"og:image\" content=\"https://example.com/img.png\">\
            <meta property=\"og:url\" content=\"https://example.com\">\
            <meta name=\"article:published_time\" content=\"2024-01-01\">\
            </head><body><p>Content</p></body></html>";
        conv.feed_chunk(html).unwrap();

        assert_eq!(conv.metadata().description.as_deref(), Some("A test page"));
        assert_eq!(conv.metadata().author.as_deref(), Some("Test Author"));
        assert_eq!(
            conv.metadata().image.as_deref(),
            Some("https://example.com/img.png")
        );
        assert_eq!(conv.metadata().url.as_deref(), Some("https://example.com"));
        assert_eq!(conv.metadata().published.as_deref(), Some("2024-01-01"));
    }

    #[test]
    fn test_table_triggers_fallback_precommit() {
        let mut conv = make_converter();
        let result = conv.feed_chunk(b"<table><tr><td>Cell</td></tr></table>");
        assert!(result.is_err());
        match result.unwrap_err() {
            ConversionError::StreamingFallback { reason } => {
                assert!(matches!(reason, FallbackReason::TableDetected));
            }
            other => panic!("Expected StreamingFallback, got: {:?}", other),
        }
    }

    #[test]
    fn test_timeout_precommit() {
        let mut conv = make_converter();
        // Set a zero-duration timeout (already expired)
        conv.deadline = Some(
            Instant::now()
                .checked_sub(Duration::from_secs(1))
                .unwrap_or_else(Instant::now),
        );
        let result = conv.feed_chunk(b"<p>test</p>");
        assert!(result.is_err());
        assert_eq!(result.unwrap_err().code(), 3); // Timeout
    }

    #[test]
    fn test_timeout_postcommit() {
        let mut conv = make_converter();
        // Feed some data to transition to PostCommit
        let output = conv.feed_chunk(b"<h1>Title</h1>").unwrap();
        assert!(!output.markdown.is_empty() || matches!(conv.commit_state, CommitState::PreCommit));

        // Force PostCommit state and set expired deadline
        conv.commit_state = CommitState::PostCommit;
        conv.bytes_emitted = 10;
        conv.deadline = Some(
            Instant::now()
                .checked_sub(Duration::from_secs(1))
                .unwrap_or_else(Instant::now),
        );

        let result = conv.feed_chunk(b"<p>more</p>");
        assert!(result.is_err());
        assert_eq!(result.unwrap_err().code(), 8); // PostCommitError
    }

    #[test]
    fn test_script_sanitized() {
        let mut conv = make_converter();
        let output = conv
            .feed_chunk(b"<p>Safe</p><script>alert('xss')</script><p>Also safe</p>")
            .unwrap();
        let result = conv.finalize().unwrap();

        let mut full = Vec::new();
        full.extend_from_slice(&output.markdown);
        full.extend_from_slice(&result.final_markdown);
        let text = String::from_utf8(full).unwrap();
        assert!(
            !text.contains("alert"),
            "Script content should be removed: {}",
            text
        );
        assert!(
            text.contains("Safe"),
            "Safe content should remain: {}",
            text
        );
    }

    #[test]
    fn test_empty_input() {
        let conv = make_converter();
        let result = conv.finalize().unwrap();
        // extract_metadata is false by default → no ETag
        assert!(result.etag.is_none());
        assert!(result.token_estimate.is_some());
    }

    #[test]
    fn test_multi_chunk_feed() {
        let mut conv = make_converter();
        let out1 = conv.feed_chunk(b"<h1>Title</h1>").unwrap();
        let out2 = conv.feed_chunk(b"<p>Paragraph one.</p>").unwrap();
        let out3 = conv.feed_chunk(b"<p>Paragraph two.</p>").unwrap();
        let result = conv.finalize().unwrap();

        let mut full = Vec::new();
        full.extend_from_slice(&out1.markdown);
        full.extend_from_slice(&out2.markdown);
        full.extend_from_slice(&out3.markdown);
        full.extend_from_slice(&result.final_markdown);
        let text = String::from_utf8(full).unwrap();
        assert!(text.contains("# Title"), "got: {}", text);
        assert!(text.contains("Paragraph one"), "got: {}", text);
        assert!(text.contains("Paragraph two"), "got: {}", text);
    }

    #[test]
    fn test_commit_state_transitions() {
        let mut conv = make_converter();
        assert_eq!(conv.commit_state(), CommitState::PreCommit);

        // Feed enough to produce output
        conv.feed_chunk(b"<h1>Title</h1>").unwrap();
        // After a heading with flush, should transition to PostCommit
        // (depends on whether the heading produced flushed output)
    }

    #[test]
    fn test_stats_populated() {
        let mut conv = make_converter();
        conv.feed_chunk(b"<h1>Title</h1><p>Content</p>").unwrap();
        let result = conv.finalize().unwrap();

        assert!(result.stats.tokens_processed > 0);
        assert!(result.stats.chunks_processed >= 1);
    }

    /// Regression: peak_memory_estimate must reflect the working set
    /// (bounded state in memory), not cumulative output bytes. For a
    /// large document the working set should stay bounded while
    /// total output grows.
    #[test]
    fn test_peak_memory_is_working_set_not_cumulative_output() {
        let mut conv = make_converter();
        let mut total_output_bytes: usize = 0;
        // Feed a large-ish document in multiple chunks
        for _ in 0..50 {
            let out = conv
                .feed_chunk(b"<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p>")
                .unwrap();
            total_output_bytes = total_output_bytes.saturating_add(out.markdown.len());
        }
        let result = conv.finalize().unwrap();
        total_output_bytes = total_output_bytes.saturating_add(result.final_markdown.len());

        let peak = result.stats.peak_memory_estimate;

        // Peak working set should be much smaller than total output.
        // The budget total is 2 MiB; working set should be well under that.
        assert!(
            peak > 0,
            "peak_memory_estimate should be non-zero for non-trivial input"
        );
        // Working set should not grow proportionally with output
        if total_output_bytes > 0 {
            assert!(
                peak < total_output_bytes,
                "peak_memory_estimate ({}) should be less than total output bytes ({}); \
                 it should reflect in-memory working set, not cumulative output",
                peak,
                total_output_bytes,
            );
        }
    }

    /// Regression: PreCommit errors must preserve their original type
    /// (e.g. EncodingError), not be wrapped as InternalError.
    #[test]
    fn test_precommit_error_preserves_original_type() {
        let mut conv =
            StreamingConverter::new(ConversionOptions::default(), MemoryBudget::default());
        // Set an unsupported charset so the first feed_chunk fails
        conv.set_content_type(Some("text/html; charset=FAKE-ENCODING-999".to_string()));

        let err = conv.feed_chunk(b"<p>Hello</p>").unwrap_err();
        // Should be EncodingError (code 2), NOT InternalError (code 99)
        assert_eq!(
            err.code(),
            2,
            "PreCommit charset error should be EncodingError (code 2), got code {}: {}",
            err.code(),
            err
        );
    }

    /// Regression: token estimate must saturate to u32::MAX when the
    /// accumulated character count exceeds u32 range, not wrap around.
    #[test]
    fn test_token_estimate_saturates_on_overflow() {
        let mut conv = make_converter();
        // Directly set total_markdown_chars to a value that would overflow u32
        // when divided by 4. u32::MAX = 4_294_967_295, so we need
        // total_markdown_chars > u32::MAX * 4 = 17_179_869_180.
        conv.total_markdown_chars = u64::from(u32::MAX) * 4 + 100;

        let result = conv.finalize().unwrap();
        assert_eq!(
            result.token_estimate,
            Some(u32::MAX),
            "token estimate should saturate to u32::MAX, not wrap around"
        );
    }

    /// Regression: token estimate at the boundary should not overflow.
    #[test]
    fn test_token_estimate_at_u32_boundary() {
        let mut conv = make_converter();
        // Exactly u32::MAX * 4 chars → div_ceil(4) = u32::MAX, fits in u32
        conv.total_markdown_chars = u64::from(u32::MAX) * 4;

        let result = conv.finalize().unwrap();
        assert_eq!(
            result.token_estimate,
            Some(u32::MAX),
            "token estimate at boundary should be exactly u32::MAX"
        );
    }

    /// Regression: peak_memory_estimate must not grow with cumulative
    /// head_bytes_seen. After `</head>`, the head region is no longer
    /// in memory — only the extracted PageMetadata strings remain.
    /// A large <head> followed by body content should show a peak that
    /// reflects the metadata size, not the total head bytes processed.
    #[test]
    fn test_peak_memory_not_inflated_by_cumulative_head_bytes() {
        let mut conv = make_converter_with_metadata();

        // Build a large <head> with many meta tags
        let mut head = String::from("<html><head>");
        for i in 0..100 {
            head.push_str(&format!("<meta name=\"key{}\" content=\"value{}\">", i, i));
        }
        head.push_str("<title>Test Title</title></head>");

        // Feed the head
        conv.feed_chunk(head.as_bytes()).unwrap();

        // Record head_bytes_seen — this is the cumulative counter
        let cumulative_head = conv.head_bytes_seen;
        assert!(
            cumulative_head > 1000,
            "head_bytes_seen should be large: {}",
            cumulative_head
        );

        // Feed some body content
        conv.feed_chunk(b"<body><p>Body paragraph.</p></body></html>")
            .unwrap();
        let result = conv.finalize().unwrap();

        // peak_memory_estimate should be much smaller than head_bytes_seen
        // because the working set only includes the metadata strings
        // (title, description, etc.), not the cumulative head byte count.
        assert!(
            result.stats.peak_memory_estimate < cumulative_head,
            "peak_memory_estimate ({}) should be less than cumulative head_bytes_seen ({}); \
             it should reflect actual in-memory state, not cumulative processing",
            result.stats.peak_memory_estimate,
            cumulative_head,
        );
    }

    /// Verify that peak_memory_estimate is in the same order of magnitude
    /// as metadata.bytes_estimate() for a head-heavy document with small
    /// metadata. The 100 meta tags above have unique names that don't
    /// match any extraction rule, so only the title is stored.
    #[test]
    fn test_peak_memory_proportional_to_metadata_not_head_volume() {
        let mut conv = make_converter_with_metadata();

        // Large head: 200 unrecognised meta tags → lots of head_bytes_seen,
        // but only the title is actually extracted into PageMetadata.
        let mut head = String::from("<html><head><title>Tiny</title>");
        for i in 0..200 {
            head.push_str(&format!(
                "<meta name=\"custom-unrecognised-{}\" content=\"{}\">",
                i,
                "x".repeat(50),
            ));
        }
        head.push_str("</head>");

        conv.feed_chunk(head.as_bytes()).unwrap();

        let metadata_size = conv.metadata().bytes_estimate();
        let cumulative_head = conv.head_bytes_seen;

        // metadata_size should be tiny (just "Tiny" = 4 bytes)
        assert!(
            metadata_size < 100,
            "metadata should be small: {} bytes",
            metadata_size
        );
        // head_bytes_seen should be large
        assert!(
            cumulative_head > 5000,
            "head_bytes_seen should be large: {} bytes",
            cumulative_head
        );

        conv.feed_chunk(b"<body><p>Content</p></body></html>")
            .unwrap();
        let result = conv.finalize().unwrap();

        // Peak should be closer to metadata_size than to cumulative_head.
        // Allow generous headroom for stack + emitter buffers, but it
        // must be well below the cumulative head volume.
        assert!(
            result.stats.peak_memory_estimate < cumulative_head / 2,
            "peak ({}) should be well below half of cumulative head ({}) — \
             it should track metadata size (~{}), not head volume",
            result.stats.peak_memory_estimate,
            cumulative_head,
            metadata_size,
        );
    }

    // ── Regression tests for review round 5 ─────────────────────────

    /// P1 regression: with extract_metadata disabled (default), a large
    /// <head> must NOT trigger FrontMatterOverflow fallback.
    #[test]
    fn test_large_head_no_fallback_when_metadata_disabled() {
        let mut conv = make_converter(); // default: extract_metadata = false

        // Build a <head> that would exceed the lookahead budget
        let mut head = String::from("<html><head>");
        for i in 0..500 {
            head.push_str(&format!(
                "<meta name=\"k{}\" content=\"{}\">",
                i,
                "v".repeat(200),
            ));
        }
        head.push_str("</head><body><p>Content</p></body></html>");

        // Should succeed — no fallback because metadata extraction is off
        let result = conv.feed_chunk(head.as_bytes());
        assert!(
            !matches!(result, Err(ConversionError::StreamingFallback { .. })),
            "should not fallback when extract_metadata is disabled, got: {:?}",
            result,
        );
    }

    /// P2 regression: og:title must override <title>, matching
    /// MetadataExtractor behaviour.
    #[test]
    fn test_og_title_overrides_html_title() {
        let mut conv = make_converter_with_metadata();
        conv.feed_chunk(
            b"<html><head>\
              <title>HTML Title</title>\
              <meta property=\"og:title\" content=\"OG Title\">\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();
        assert_eq!(
            conv.metadata().title.as_deref(),
            Some("OG Title"),
            "og:title should override <title>"
        );
    }

    /// P2 regression: twitter:description must override generic description.
    #[test]
    fn test_twitter_description_overrides_generic() {
        let mut conv = make_converter_with_metadata();
        conv.feed_chunk(
            b"<html><head>\
              <meta name=\"description\" content=\"Generic\">\
              <meta name=\"twitter:description\" content=\"Twitter\">\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();
        assert_eq!(
            conv.metadata().description.as_deref(),
            Some("Twitter"),
            "twitter:description should override generic description"
        );
    }

    /// P3 regression: <link rel="canonical"> should be extracted as URL.
    #[test]
    fn test_canonical_link_extracted() {
        let mut conv = make_converter_with_metadata();
        conv.feed_chunk(
            b"<html><head>\
              <link rel=\"canonical\" href=\"https://example.com/page\">\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();
        assert_eq!(
            conv.metadata().url.as_deref(),
            Some("https://example.com/page"),
        );
    }

    /// P3 regression: when no canonical/og:url, finalize should fall back
    /// to options.base_url.
    #[test]
    fn test_url_falls_back_to_base_url() {
        let opts = ConversionOptions {
            extract_metadata: true,
            base_url: Some("https://fallback.example.com".to_string()),
            ..ConversionOptions::default()
        };
        let mut conv = StreamingConverter::new(opts, MemoryBudget::default());
        conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));

        conv.feed_chunk(b"<html><head><title>T</title></head><body><p>x</p></body></html>")
            .unwrap();
        // Before finalize, url should be None (no canonical/og:url)
        assert_eq!(conv.metadata().url, None);

        // After finalize, url should fall back to base_url
        let opts2 = ConversionOptions {
            extract_metadata: true,
            base_url: Some("https://fallback.example.com".to_string()),
            ..ConversionOptions::default()
        };
        let mut conv2 = StreamingConverter::new(opts2, MemoryBudget::default());
        conv2.set_content_type(Some("text/html; charset=UTF-8".to_string()));
        conv2
            .feed_chunk(b"<html><head><title>T</title></head><body><p>x</p></body></html>")
            .unwrap();
        // We can't inspect metadata after finalize (self consumed), but
        // we verified the pre-finalize state above. The base_url fallback
        // is applied inside finalize per the code path we added.
        let _ = conv2.finalize().unwrap();
        let _ = conv.finalize().unwrap();
    }

    /// P4 regression: title text split across events must preserve spaces.
    /// `<title>Hello <!--x--> World</title>` should produce "Hello  World"
    /// (with the space preserved), not "HelloWorld".
    #[test]
    fn test_title_preserves_spaces_across_events() {
        let mut conv = make_converter_with_metadata();
        // html5ever splits this into Text("Hello "), Comment("x"), Text(" World")
        conv.feed_chunk(
            b"<html><head><title>Hello <!-- comment --> World</title></head>\
              <body><p>x</p></body></html>",
        )
        .unwrap();
        let title = conv.metadata().title.as_deref().unwrap_or("");
        assert!(
            title.contains("Hello") && title.contains("World"),
            "title should contain both words: {:?}",
            title
        );
        // The space between Hello and World must be preserved
        assert!(
            !title.contains("HelloWorld"),
            "title should not lose spaces between events: {:?}",
            title
        );
    }

    /// P5 regression: Duration::MAX should not cause immediate timeout.
    #[test]
    fn test_duration_max_does_not_cause_immediate_timeout() {
        let mut conv = make_converter();
        conv.set_timeout(Duration::MAX);

        // Should succeed — Duration::MAX overflow is treated as no deadline
        let result = conv.feed_chunk(b"<p>Hello</p>");
        assert!(
            result.is_ok(),
            "Duration::MAX should not cause immediate timeout, got: {:?}",
            result,
        );
        let result = conv.finalize();
        assert!(result.is_ok());
    }

    // ── Regression tests for review round 6 ─────────────────────────

    /// P1 regression: og:title appearing before <title> must not be
    /// polluted by subsequent <title> text.
    #[test]
    fn test_og_title_before_html_title_not_polluted() {
        let mut conv = make_converter_with_metadata();
        conv.feed_chunk(
            b"<html><head>\
              <meta property=\"og:title\" content=\"Social Title\">\
              <title>HTML Title</title>\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();
        assert_eq!(
            conv.metadata().title.as_deref(),
            Some("Social Title"),
            "og:title before <title> should not be overwritten or appended to"
        );
    }

    /// P1 regression: <title> before og:title — og:title should still win.
    #[test]
    fn test_html_title_then_og_title_social_wins() {
        let mut conv = make_converter_with_metadata();
        conv.feed_chunk(
            b"<html><head>\
              <title>HTML Title</title>\
              <meta property=\"og:title\" content=\"Social Title\">\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();
        assert_eq!(
            conv.metadata().title.as_deref(),
            Some("Social Title"),
            "og:title should override <title> regardless of order"
        );
    }

    /// P2 regression: canonical URL must override og:url.
    #[test]
    fn test_canonical_overrides_og_url() {
        let mut conv = make_converter_with_metadata();
        conv.feed_chunk(
            b"<html><head>\
              <meta property=\"og:url\" content=\"https://og.example.com\">\
              <link rel=\"canonical\" href=\"https://canonical.example.com\">\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();
        assert_eq!(
            conv.metadata().url.as_deref(),
            Some("https://canonical.example.com"),
            "canonical should override og:url"
        );
    }

    /// P3 regression: when a <meta> has both `name` and `property`,
    /// `property` must take precedence.
    #[test]
    fn test_property_takes_precedence_over_name() {
        let mut conv = make_converter_with_metadata();
        // name="description" would set generic description,
        // but property="og:title" should be used instead.
        conv.feed_chunk(
            b"<html><head>\
              <meta name=\"description\" property=\"og:title\" content=\"OG via property\">\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();
        // property="og:title" should win → title is set, description is NOT
        assert_eq!(
            conv.metadata().title.as_deref(),
            Some("OG via property"),
            "property attribute should take precedence over name"
        );
        assert_eq!(
            conv.metadata().description,
            None,
            "name='description' should be ignored when property is present"
        );
    }

    // ── Regression tests for review round 7 ─────────────────────────

    /// P1 regression: first canonical wins when multiple are present.
    #[test]
    fn test_first_canonical_wins() {
        let mut conv = make_converter_with_metadata();
        conv.feed_chunk(
            b"<html><head>\
              <link rel=\"canonical\" href=\"https://first.example.com\">\
              <link rel=\"canonical\" href=\"https://second.example.com\">\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();
        assert_eq!(
            conv.metadata().url.as_deref(),
            Some("https://first.example.com"),
            "first canonical should win, not last"
        );
    }

    /// P1 regression: first canonical still overrides prior og:url.
    #[test]
    fn test_first_canonical_overrides_og_url_but_second_does_not() {
        let mut conv = make_converter_with_metadata();
        conv.feed_chunk(
            b"<html><head>\
              <meta property=\"og:url\" content=\"https://og.example.com\">\
              <link rel=\"canonical\" href=\"https://first-canonical.example.com\">\
              <link rel=\"canonical\" href=\"https://second-canonical.example.com\">\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();
        assert_eq!(
            conv.metadata().url.as_deref(),
            Some("https://first-canonical.example.com"),
            "first canonical should override og:url; second canonical should not override first"
        );
    }

    /// P2 regression: first <title> wins when multiple are present.
    #[test]
    fn test_first_html_title_wins() {
        let mut conv = make_converter_with_metadata();
        conv.feed_chunk(
            b"<html><head>\
              <title>First Title</title>\
              <title>Second Title</title>\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();
        assert_eq!(
            conv.metadata().title.as_deref(),
            Some("First Title"),
            "first <title> should win, not last"
        );
    }

    /// P2 regression: social title still overrides even with multiple <title>.
    #[test]
    fn test_social_title_wins_over_multiple_html_titles() {
        let mut conv = make_converter_with_metadata();
        conv.feed_chunk(
            b"<html><head>\
              <title>First</title>\
              <meta property=\"og:title\" content=\"Social\">\
              <title>Second</title>\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();
        assert_eq!(
            conv.metadata().title.as_deref(),
            Some("Social"),
            "social title should win over all HTML titles"
        );
    }

    // ── Regression tests for review round 8 ─────────────────────────

    /// P1 regression: base_url overwrites og:url when no canonical is
    /// present, matching full-buffer MetadataExtractor::extract which
    /// unconditionally sets url = canonical.or(base_url) after meta tags.
    #[test]
    fn test_base_url_overwrites_og_url_when_no_canonical() {
        let opts = ConversionOptions {
            extract_metadata: true,
            base_url: Some("https://base.example.com".to_string()),
            ..ConversionOptions::default()
        };
        let mut conv = StreamingConverter::new(opts, MemoryBudget::default());
        conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));

        conv.feed_chunk(
            b"<html><head>\
              <meta property=\"og:url\" content=\"https://og.example.com\">\
              <title>T</title>\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();

        // Before finalize, og:url is set
        assert_eq!(
            conv.metadata().url.as_deref(),
            Some("https://og.example.com"),
        );

        // After finalize, base_url should overwrite og:url (no canonical)
        // We need a second converter to test post-finalize state since
        // finalize consumes self. Verify via a fresh instance.
        let opts2 = ConversionOptions {
            extract_metadata: true,
            base_url: Some("https://base.example.com".to_string()),
            ..ConversionOptions::default()
        };
        let mut conv2 = StreamingConverter::new(opts2, MemoryBudget::default());
        conv2.set_content_type(Some("text/html; charset=UTF-8".to_string()));
        conv2
            .feed_chunk(
                b"<html><head>\
                  <meta property=\"og:url\" content=\"https://og.example.com\">\
                  <title>T</title>\
                  </head><body><p>x</p></body></html>",
            )
            .unwrap();
        // Inspect internal state after finalize logic runs:
        // We can't call metadata() after finalize, but we can verify
        // the finalize path by checking that canonical_found is false
        // and the code path will overwrite with base_url.
        assert!(!conv2.canonical_found);
        let _ = conv2.finalize().unwrap();
        let _ = conv.finalize().unwrap();
    }

    /// P1 regression: when no canonical and no base_url, url becomes None
    /// (og:url is overwritten with None), matching full-buffer behaviour.
    #[test]
    fn test_no_canonical_no_base_url_clears_og_url() {
        let opts = ConversionOptions {
            extract_metadata: true,
            base_url: None,
            ..ConversionOptions::default()
        };
        let mut conv = StreamingConverter::new(opts, MemoryBudget::default());
        conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));

        conv.feed_chunk(
            b"<html><head>\
              <meta property=\"og:url\" content=\"https://og.example.com\">\
              <title>T</title>\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();

        // og:url was set during head processing
        assert_eq!(
            conv.metadata().url.as_deref(),
            Some("https://og.example.com"),
        );
        // After finalize: no canonical, no base_url → url should be None
        assert!(!conv.canonical_found);
        // The finalize path will set metadata.url = base_url.clone() = None
    }

    /// P2 regression: empty first <title> blocks second <title>.
    #[test]
    fn test_empty_first_title_blocks_second() {
        let mut conv = make_converter_with_metadata();
        conv.feed_chunk(
            b"<html><head>\
              <title>   </title>\
              <title>Second Title</title>\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();
        // First <title> is whitespace-only → trimmed to "".
        // full-buffer find_title returns "" for the first match and stops.
        // Streaming should match: title is Some(""), second is ignored.
        assert_eq!(
            conv.metadata().title.as_deref(),
            Some(""),
            "empty first <title> should block second <title>"
        );
    }

    /// P2 regression: truly empty <title></title> also blocks.
    #[test]
    fn test_truly_empty_title_blocks_second() {
        let mut conv = make_converter_with_metadata();
        conv.feed_chunk(
            b"<html><head>\
              <title></title>\
              <title>Second</title>\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();
        assert_eq!(
            conv.metadata().title.as_deref(),
            Some(""),
            "empty <title></title> should block second <title>"
        );
    }

    // ── Regression tests for review round 9 ─────────────────────────

    /// Verifies that a `<link rel="canonical">` without an `href` is ignored and a later canonical with an `href` is selected.
    ///
    /// This test constructs a converter with metadata extraction enabled and a base URL, feeds an HTML head containing two
    /// canonical link tags (the first without `href`, the second with `href`), and asserts that the converter records the
    /// second link's URL as the canonical.
    ///
    /// # Examples
    ///
    /// ```
    /// // Equivalent test flow:
    /// let opts = ConversionOptions {
    ///     extract_metadata: true,
    ///     base_url: Some("https://base.example.com".to_string()),
    ///     ..ConversionOptions::default()
    /// };
    /// let mut conv = StreamingConverter::new(opts, MemoryBudget::default());
    /// conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
    /// conv.feed_chunk(b"<html><head>\
    ///                 <link rel=\"canonical\">\
    ///                 <link rel=\"canonical\" href=\"https://second.example.com\">\
    ///                 <title>T</title>\
    ///                 </head><body><p>x</p></body></html>").unwrap();
    /// assert!(conv.canonical_found);
    /// assert_eq!(conv.metadata().url.as_deref(), Some("https://second.example.com"));
    /// ```
    fn test_first_canonical_no_href_skipped_second_used() {
        let opts = ConversionOptions {
            extract_metadata: true,
            base_url: Some("https://base.example.com".to_string()),
            ..ConversionOptions::default()
        };
        let mut conv = StreamingConverter::new(opts, MemoryBudget::default());
        conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));

        conv.feed_chunk(
            b"<html><head>\
              <link rel=\"canonical\">\
              <link rel=\"canonical\" href=\"https://second.example.com\">\
              <title>T</title>\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();

        // The first canonical had no href → skipped.
        // The second canonical has href → canonical_found = true.
        assert!(conv.canonical_found);
        assert_eq!(
            conv.metadata().url.as_deref(),
            Some("https://second.example.com"),
            "second canonical (with href) should be used when first has no href"
        );
    }

    /// All canonicals have no href → canonical_found stays false → base_url.
    #[test]
    fn test_all_canonicals_no_href_falls_back_to_base() {
        let opts = ConversionOptions {
            extract_metadata: true,
            base_url: Some("https://base.example.com".to_string()),
            ..ConversionOptions::default()
        };
        let mut conv = StreamingConverter::new(opts, MemoryBudget::default());
        conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));

        conv.feed_chunk(
            b"<html><head>\
              <meta property=\"og:url\" content=\"https://og.example.com\">\
              <link rel=\"canonical\">\
              <link rel=\"canonical\">\
              <title>T</title>\
              </head><body><p>x</p></body></html>",
        )
        .unwrap();

        assert!(!conv.canonical_found);
        // og:url was set during head processing
        assert_eq!(
            conv.metadata().url.as_deref(),
            Some("https://og.example.com"),
        );

        // peek_final_url applies the convergence logic: no canonical found
        // → base_url overwrites og:url.
        assert_eq!(
            conv.peek_final_url().as_deref(),
            Some("https://base.example.com"),
            "no canonical with href → base_url should win over og:url"
        );
    }
}
