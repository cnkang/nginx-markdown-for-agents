//! Property-based tests for dynconf generation counter monotonicity (Property 6).
//!
//! **Validates: Requirements 3.7**
//!
//! For any sequence of N successful dynconf reloads within a single worker
//! starting from the initial load, the generation counter in the active
//! snapshot SHALL equal N (starting from 1), and each subsequent snapshot's
//! generation SHALL be strictly greater than the previous. Cross-worker
//! convergence is verified through matching `active_digest` values, not
//! through generation counter equality.
//!
//! This test simulates the generation counter at the Rust level by:
//! - Calling `parse_dynconf` with varying JSON inputs (simulating file reloads)
//! - Tracking the generation counter as the C module would (increment on success)
//! - Verifying monotonicity, correct counting, and digest-based convergence

use nginx_markdown_converter::dynconf::{
    DynconfResult, ErrorPolicy, FilterValue, LogVerbosity, PruneNoiseValue, parse_dynconf,
};
use proptest::prelude::*;

// ─── Worker Simulation ────────────────────────────────────────────────────────

/// Simulates a single worker's dynconf reload state, mirroring the C module's
/// worker-local generation counter behavior.
#[derive(Clone, Debug, PartialEq, Eq)]
struct EffectiveDynconfFields {
    filter: FilterValue,
    prune_noise: PruneNoiseValue,
    log_verbosity: LogVerbosity,
    error_policy: ErrorPolicy,
    streaming_buffer: u64,
}

impl Default for EffectiveDynconfFields {
    fn default() -> Self {
        Self {
            filter: FilterValue::On,
            prune_noise: PruneNoiseValue::Off,
            log_verbosity: LogVerbosity::Info,
            error_policy: ErrorPolicy::Pass,
            streaming_buffer: 65_536,
        }
    }
}

impl EffectiveDynconfFields {
    fn apply_present(&mut self, result: &DynconfResult) {
        if let Some(value) = result.filter {
            self.filter = value;
        }
        if let Some(value) = result.prune_noise {
            self.prune_noise = value;
        }
        if let Some(value) = result.log_verbosity {
            self.log_verbosity = value;
        }
        if let Some(value) = result.error_policy {
            self.error_policy = value;
        }
        if let Some(value) = result.streaming_buffer {
            self.streaming_buffer = value;
        }
    }
}

struct WorkerDynconfState {
    /// Worker-local monotonically increasing counter (starts at 1 on first load).
    generation: u64,
    /// The active snapshot's source_digest (SHA-256 over raw bytes).
    source_digest: Option<String>,
    /// The active snapshot's active_digest (SHA-256 over canonical JSON).
    active_digest: Option<String>,
    /// Static/http baseline used to construct each new complete snapshot.
    static_fields: EffectiveDynconfFields,
    /// Effective fields in the current active snapshot.
    effective_fields: EffectiveDynconfFields,
}

impl WorkerDynconfState {
    fn new() -> Self {
        Self {
            generation: 0,
            source_digest: None,
            active_digest: None,
            static_fields: EffectiveDynconfFields::default(),
            effective_fields: EffectiveDynconfFields::default(),
        }
    }

    /// Attempt a reload with the given raw bytes. On success, increment
    /// generation and update digests. Returns the new generation on success.
    fn reload(&mut self, raw_bytes: &[u8]) -> Result<u64, ()> {
        match parse_dynconf(raw_bytes) {
            Ok(result) => {
                self.generation += 1;
                self.effective_fields = self.static_fields.clone();
                self.effective_fields.apply_present(&result);
                self.source_digest = Some(result.source_digest);
                self.active_digest = Some(result.active_digest);
                Ok(self.generation)
            }
            Err(_) => Err(()),
        }
    }
}

// ─── Strategies ───────────────────────────────────────────────────────────────

/// Generate a valid dynconf JSON document with optional whitespace variations.
/// All generated documents are semantically valid (schema_version=1, known keys).
fn valid_dynconf_document() -> impl Strategy<Value = String> {
    (
        prop_oneof![Just("on"), Just("off")],
        prop_oneof![Just("on"), Just("off")],
        prop_oneof![Just("error"), Just("warn"), Just("info"), Just("debug")],
        prop_oneof![
            Just("pass"),
            Just("fail_closed"),
            Just("status 429"),
            Just("status 503")
        ],
        (65536u64..=1_073_741_824u64),
        // Include subsets of fields
        any::<[bool; 5]>(),
    )
        .prop_map(
            |(filter, prune, log_verb, error_pol, stream_buf, include)| {
                let mut fields = vec!["\"schema_version\": 1".to_string()];
                if include[0] {
                    fields.push(format!("\"filter\": \"{}\"", filter));
                }
                if include[1] {
                    fields.push(format!("\"prune_noise\": \"{}\"", prune));
                }
                if include[2] {
                    fields.push(format!("\"log_verbosity\": \"{}\"", log_verb));
                }
                if include[3] {
                    fields.push(format!("\"error_policy\": \"{}\"", error_pol));
                }
                if include[4] {
                    fields.push(format!("\"streaming_buffer\": {}", stream_buf));
                }
                format!("{{{}}}", fields.join(", "))
            },
        )
}

/// Generate a sequence of N valid dynconf documents (simulating N reloads).
fn reload_sequence(max_len: usize) -> impl Strategy<Value = Vec<String>> {
    proptest::collection::vec(valid_dynconf_document(), 1..=max_len)
}

/// Generate formatting variations of the same semantic JSON content.
/// These produce different source_digest but identical active_digest.
fn formatting_variants_of(base: &str) -> Vec<String> {
    vec![
        // Variant 1: extra whitespace after colons
        base.replace(": ", ":  "),
        // Variant 2: extra whitespace before closing brace
        base.replace("}", "  }"),
        // Variant 3: newlines between fields
        base.replace(", ", ",\n  "),
        // Variant 4: tabs instead of spaces
        base.replace(": ", ":\t"),
        // Variant 5: trailing whitespace
        format!("{base}  \n"),
    ]
}

// ─── Property Tests ───────────────────────────────────────────────────────────

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    /// Property: After N successful reloads, generation == N (starting from 1).
    /// Each reload strictly increases the counter.
    ///
    /// **Validates: Requirements 3.7**
    #[test]
    fn prop_generation_equals_reload_count(
        docs in reload_sequence(20)
    ) {
        let mut worker = WorkerDynconfState::new();
        let mut prev_generation: u64 = 0;

        for (i, doc) in docs.iter().enumerate() {
            let result = worker.reload(doc.as_bytes());
            prop_assert!(result.is_ok(), "valid doc should parse: {}", doc);

            let current_gen = result.unwrap();
            let expected = (i as u64) + 1;

            // Generation equals N (number of successful reloads)
            prop_assert_eq!(
                current_gen, expected,
                "after {} reloads, generation should be {}, got {}",
                i + 1, expected, current_gen
            );

            // Strictly increasing (never same, never decreasing)
            prop_assert!(
                current_gen > prev_generation,
                "generation must be strictly increasing: {} > {} failed",
                current_gen, prev_generation
            );

            prev_generation = current_gen;
        }

        // Final check: generation equals total successful reloads
        prop_assert_eq!(worker.generation, docs.len() as u64);
    }

    /// Property: Generation strictly increases even when content is identical
    /// across reloads (same source_digest and active_digest).
    ///
    /// **Validates: Requirements 3.7**
    #[test]
    fn prop_generation_increases_on_identical_reloads(
        doc in valid_dynconf_document(),
        repeat_count in 2usize..10
    ) {
        let mut worker = WorkerDynconfState::new();
        let mut generations: Vec<u64> = Vec::new();

        for _ in 0..repeat_count {
            let current_gen = worker.reload(doc.as_bytes()).unwrap();
            generations.push(current_gen);
        }

        // All generations strictly increasing
        for window in generations.windows(2) {
            prop_assert!(
                window[1] > window[0],
                "generation must strictly increase: {} > {} failed",
                window[1], window[0]
            );
        }

        // Final generation equals repeat count
        prop_assert_eq!(
            *generations.last().unwrap(),
            repeat_count as u64
        );
    }

    /// Property: Formatting-only changes (source_digest changes, active_digest
    /// unchanged) still increment generation.
    ///
    /// **Validates: Requirements 3.7**
    #[test]
    fn prop_formatting_only_change_increments_generation(
        filter in prop_oneof![Just("on"), Just("off")],
        prune in prop_oneof![Just("on"), Just("off")],
    ) {
        let base = format!(
            r#"{{"schema_version": 1, "filter": "{}", "prune_noise": "{}"}}"#,
            filter, prune
        );

        let mut worker = WorkerDynconfState::new();

        // Initial load
        let gen1 = worker.reload(base.as_bytes()).unwrap();
        prop_assert_eq!(gen1, 1);
        let initial_active_digest = worker.active_digest.clone().unwrap();
        let initial_source_digest = worker.source_digest.clone().unwrap();

        // Generate formatting variants (different bytes, same semantics)
        let variants = formatting_variants_of(&base);
        let mut exercised_variants = 0;

        for (i, variant) in variants.iter().enumerate() {
            // Skip variants that don't actually parse (malformed JSON)
            let parse_result = parse_dynconf(variant.as_bytes());
            if parse_result.is_err() {
                continue;
            }
            let parsed = parse_result.unwrap();

            // Verify: source_digest differs (different bytes)
            if parsed.source_digest == initial_source_digest {
                // This variant happens to be byte-identical, skip
                continue;
            }

            // Verify: active_digest is the same (same semantic content)
            prop_assert_eq!(
                &parsed.active_digest, &initial_active_digest,
                "formatting variant {} should have same active_digest", i
            );

            // Reload with the formatting variant
            let current_gen = worker.reload(variant.as_bytes()).unwrap();

            // Generation STILL increments (requirement 3.16 explicitly states this)
            prop_assert!(
                current_gen > gen1,
                "formatting-only change must still increment generation: {} > {} (variant {})",
                current_gen, gen1, i
            );

            // source_digest is different
            prop_assert_ne!(
                worker.source_digest.as_ref().unwrap(),
                &initial_source_digest,
                "formatting variant must produce different source_digest"
            );

            // active_digest is unchanged
            prop_assert_eq!(
                worker.active_digest.as_ref().unwrap(),
                &initial_active_digest,
                "formatting variant must produce same active_digest"
            );

            exercised_variants += 1;
        }

        // The property must exercise at least one real formatting variant;
        // a vacuous pass (no variant parsed) is a test harness failure.
        prop_assert!(
            exercised_variants > 0,
            "at least one formatting variant must reach the assertions"
        );
    }

    /// Property: Cross-worker convergence uses active_digest, not generation.
    /// Two workers processing different reload sequences that end with the same
    /// final document reach the same active_digest. Generation counters are
    /// independent per worker and must NOT be used for convergence checks.
    ///
    /// **Validates: Requirements 3.7**
    #[test]
    fn prop_cross_worker_convergence_via_active_digest(
        docs_worker1 in reload_sequence(5),
        docs_worker2 in reload_sequence(5),
        final_doc in valid_dynconf_document(),
    ) {
        // Worker 1: processes its own sequence then the shared final doc
        let mut worker1 = WorkerDynconfState::new();
        for doc in &docs_worker1 {
            worker1.reload(doc.as_bytes()).unwrap();
        }
        worker1.reload(final_doc.as_bytes()).unwrap();

        // Worker 2: processes a different sequence then the same final doc
        let mut worker2 = WorkerDynconfState::new();
        for doc in &docs_worker2 {
            worker2.reload(doc.as_bytes()).unwrap();
        }
        worker2.reload(final_doc.as_bytes()).unwrap();

        // Cross-worker convergence: active_digest matches because both loaded
        // the same final document (same canonical representation)
        prop_assert_eq!(
            worker1.active_digest.as_ref().unwrap(),
            worker2.active_digest.as_ref().unwrap(),
            "workers processing same final doc must have same active_digest"
        );

        // source_digest also matches (same final raw bytes)
        prop_assert_eq!(
            worker1.source_digest.as_ref().unwrap(),
            worker2.source_digest.as_ref().unwrap(),
            "workers loading same bytes must have same source_digest"
        );

        // Generation counters reflect local history and may differ.
        // The requirement explicitly states convergence is via active_digest,
        // NOT via generation counter equality. Generation depends on how many
        // reloads each worker has performed independently.
        let w1_gen = worker1.generation;
        let w2_gen = worker2.generation;
        // Both have at least 1 (the final doc) plus their sequence lengths
        prop_assert_eq!(w1_gen, (docs_worker1.len() as u64) + 1);
        prop_assert_eq!(w2_gen, (docs_worker2.len() as u64) + 1);
    }

    /// Property: source_digest equality implies byte-identical input.
    /// Different valid JSON bytes produce different source_digest values.
    ///
    /// **Validates: Requirements 3.7**
    #[test]
    fn prop_source_digest_detects_byte_changes(
        doc1 in valid_dynconf_document(),
        doc2 in valid_dynconf_document(),
    ) {
        let r1 = parse_dynconf(doc1.as_bytes());
        let r2 = parse_dynconf(doc2.as_bytes());

        if let (Ok(res1), Ok(res2)) = (r1, r2) {
            if doc1.as_bytes() == doc2.as_bytes() {
                // Byte-identical -> same source_digest
                prop_assert_eq!(&res1.source_digest, &res2.source_digest);
            } else {
                // Different bytes -> different source_digest (with overwhelming probability)
                // SHA-256 collision is astronomically unlikely
                prop_assert_ne!(
                    &res1.source_digest, &res2.source_digest,
                    "different bytes should produce different source_digest"
                );
            }
        }
    }
}

// ─── Deterministic Tests ──────────────────────────────────────────────────────

#[test]
fn test_generation_counter_basic_sequence() {
    let mut worker = WorkerDynconfState::new();

    let docs = [
        r#"{"schema_version": 1, "filter": "on"}"#,
        r#"{"schema_version": 1, "filter": "off"}"#,
        r#"{"schema_version": 1, "filter": "on", "prune_noise": "on"}"#,
        r#"{"schema_version": 1, "streaming_buffer": 131072}"#,
        r#"{"schema_version": 1}"#,
    ];

    for (i, doc) in docs.iter().enumerate() {
        let current_gen = worker.reload(doc.as_bytes()).unwrap();
        assert_eq!(
            current_gen,
            (i as u64) + 1,
            "generation mismatch at reload {}",
            i + 1
        );
    }

    assert_eq!(worker.generation, 5);
}

#[test]
fn test_generation_not_incremented_on_failure() {
    let mut worker = WorkerDynconfState::new();

    // Successful initial load
    worker
        .reload(br#"{"schema_version": 1, "filter": "on"}"#)
        .unwrap();
    assert_eq!(worker.generation, 1);

    // Failed reload (invalid JSON) — generation unchanged
    let result = worker.reload(br#"{"invalid"}"#);
    assert!(result.is_err());
    assert_eq!(worker.generation, 1);

    // Failed reload (unknown key) — generation unchanged
    let result = worker.reload(br#"{"schema_version": 1, "unknown_key": "bad"}"#);
    assert!(result.is_err());
    assert_eq!(worker.generation, 1);

    // Successful reload — generation increments
    worker
        .reload(br#"{"schema_version": 1, "filter": "off"}"#)
        .unwrap();
    assert_eq!(worker.generation, 2);
}

#[test]
fn test_formatting_only_change_produces_same_active_digest() {
    // These are semantically identical but byte-different
    let compact = r#"{"schema_version":1,"filter":"on"}"#;
    let spaced = r#"{"schema_version": 1, "filter": "on"}"#;
    let extra_space = r#"{  "schema_version" : 1 , "filter" : "on"  }"#;

    let r_compact = parse_dynconf(compact.as_bytes()).unwrap();
    let r_spaced = parse_dynconf(spaced.as_bytes()).unwrap();
    let r_extra = parse_dynconf(extra_space.as_bytes()).unwrap();

    // All have the same active_digest (canonical normalization)
    assert_eq!(r_compact.active_digest, r_spaced.active_digest);
    assert_eq!(r_spaced.active_digest, r_extra.active_digest);

    // But different source_digest (byte-level difference)
    assert_ne!(r_compact.source_digest, r_spaced.source_digest);
    assert_ne!(r_spaced.source_digest, r_extra.source_digest);
}

#[test]
fn test_formatting_only_change_still_increments_generation() {
    let mut worker = WorkerDynconfState::new();

    // Load compact form
    let compact = r#"{"schema_version":1,"filter":"on"}"#;
    let gen1 = worker.reload(compact.as_bytes()).unwrap();
    assert_eq!(gen1, 1);
    let active1 = worker.active_digest.clone().unwrap();

    // Load spaced form (same semantics, different bytes)
    let spaced = r#"{"schema_version": 1, "filter": "on"}"#;
    let gen2 = worker.reload(spaced.as_bytes()).unwrap();
    assert_eq!(gen2, 2); // Generation increments!

    // active_digest unchanged (formatting-only change)
    assert_eq!(worker.active_digest.as_ref().unwrap(), &active1);

    // source_digest changed (different bytes)
    assert_ne!(
        worker.source_digest.as_ref().unwrap(),
        &parse_dynconf(compact.as_bytes()).unwrap().source_digest
    );
}

#[test]
fn test_cross_worker_convergence_not_via_generation() {
    // Worker 1: one reload
    let mut worker1 = WorkerDynconfState::new();
    worker1
        .reload(br#"{"schema_version": 1, "filter": "on"}"#)
        .unwrap();
    assert_eq!(worker1.generation, 1);

    // Worker 2: three reloads, ending with same document
    let mut worker2 = WorkerDynconfState::new();
    worker2
        .reload(br#"{"schema_version": 1, "filter": "off"}"#)
        .unwrap();
    worker2
        .reload(br#"{"schema_version": 1, "prune_noise": "on"}"#)
        .unwrap();
    worker2
        .reload(br#"{"schema_version": 1, "filter": "on"}"#)
        .unwrap();
    assert_eq!(worker2.generation, 3);

    // Generations differ — this is expected
    assert_ne!(worker1.generation, worker2.generation);

    // active_digest matches — THIS is how convergence is verified
    assert_eq!(
        worker1.active_digest.as_ref().unwrap(),
        worker2.active_digest.as_ref().unwrap()
    );
}

#[test]
fn test_generation_starts_at_one() {
    let mut worker = WorkerDynconfState::new();
    assert_eq!(worker.generation, 0); // Before any reload

    worker.reload(br#"{"schema_version": 1}"#).unwrap();
    assert_eq!(worker.generation, 1); // First successful reload = 1
}

#[test]
fn test_generation_never_decreases() {
    let mut worker = WorkerDynconfState::new();
    let mut max_generation: u64 = 0;

    let docs = [
        r#"{"schema_version": 1}"#,
        r#"{"schema_version": 1, "filter": "on"}"#,
        r#"{"schema_version": 1, "filter": "off"}"#,
        r#"{"schema_version": 1, "prune_noise": "on"}"#,
        r#"{"schema_version": 1, "log_verbosity": "debug"}"#,
        r#"{"schema_version": 1, "error_policy": "pass"}"#,
        r#"{"schema_version": 1, "streaming_buffer": 65536}"#,
        r#"{"schema_version": 1, "filter": "on", "streaming_buffer": 131072}"#,
    ];

    for doc in &docs {
        let current_gen = worker.reload(doc.as_bytes()).unwrap();
        assert!(
            current_gen > max_generation,
            "generation must never decrease: {} > {}",
            current_gen,
            max_generation
        );
        max_generation = current_gen;
    }
}

#[test]
fn test_omitted_fields_reset_to_static_baseline_and_c_watcher_contract() {
    let mut worker = WorkerDynconfState::new();
    worker.static_fields = EffectiveDynconfFields {
        filter: FilterValue::On,
        prune_noise: PruneNoiseValue::Off,
        log_verbosity: LogVerbosity::Warn,
        error_policy: ErrorPolicy::Status503,
        streaming_buffer: 131_072,
    };

    worker
        .reload(
            br#"{"schema_version":1,"filter":"off","prune_noise":"on","log_verbosity":"debug","error_policy":"pass","streaming_buffer":262144}"#,
        )
        .unwrap();
    worker
        .reload(br#"{"schema_version":1,"filter":"on"}"#)
        .unwrap();

    assert_eq!(worker.effective_fields.filter, FilterValue::On);
    assert_eq!(worker.effective_fields.prune_noise, PruneNoiseValue::Off);
    assert_eq!(worker.effective_fields.log_verbosity, LogVerbosity::Warn);
    assert_eq!(worker.effective_fields.error_policy, ErrorPolicy::Status503);
    assert_eq!(worker.effective_fields.streaming_buffer, 131_072);

    let c_watcher = include_str!("../../nginx-module/src/ngx_http_markdown_dynconf_impl.h");
    assert!(c_watcher.contains("static_snapshot"));
    assert!(c_watcher.contains("staging_snapshot = watcher->static_snapshot"));
    assert!(c_watcher.contains("ngx_http_markdown_dynconf_snapshot_from_conf"));
    assert!(c_watcher.contains("digest_state.generation++"));
}
