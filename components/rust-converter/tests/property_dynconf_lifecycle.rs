//! Property-based tests for dynconf file lifecycle and source_digest semantics (Property 34).
//!
//! **Validates: Requirements 3.8, 4.4**
//!
//! Tests the dynconf watcher state machine transitions:
//! - disabled, no_file, invalid_without_lkg, active, lkg_preserved
//!
//! Verifies that:
//! - Active + file deleted/permission denied/stat failure: state=lkg_preserved, generation unchanged
//! - lkg_preserved + file reappears valid: promote to active, generation++
//! - First invalid load (no LKG): stays invalid_without_lkg (NOT lkg_preserved)
//! - invalid_without_lkg + valid promotes to active; invalid_without_lkg + removal returns to no_file
//! - source_digest = SHA-256 over the raw bytes that produced the served LKG snapshot
//! - Absent key vs explicitly set default produce different active_digest

use nginx_markdown_converter::dynconf::{
    compute_source_digest, parse_dynconf, DynconfResult,
};
use proptest::prelude::*;

// ─── Dynconf Watcher State Machine ────────────────────────────────────────────

/// Dynconf watcher states per Requirement 3.8 and Requirement 4.4.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum DynconfState {
    /// Dynconf not enabled — all snapshot fields null.
    Disabled,
    /// Enabled watcher that has never observed a file.
    NoFile,
    /// First file load failed, no LKG exists.
    InvalidWithoutLkg,
    /// Valid snapshot is active and being served.
    Active,
    /// File disappeared/unreadable/invalid after a valid LKG existed; LKG preserved.
    LkgPreserved,
}

/// Simulated file-system events delivered to the watcher.
#[derive(Debug, Clone)]
#[allow(dead_code)]
enum FileEvent {
    /// File appears with valid content (raw bytes).
    ValidFile(Vec<u8>),
    /// File appears with invalid content.
    InvalidFile(Vec<u8>),
    /// File disappears (deleted, permission denied, stat failure).
    FileDisappeared(DisappearReason),
}

/// Reasons a file might disappear per Requirement 3.8.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum DisappearReason {
    FileDeleted,
    PermissionDenied,
    StatFailure,
}

/// Simulated dynconf watcher that models the C module's state machine.
/// This mirrors the actual watcher behavior defined in Requirement 3.8 and the
/// lifecycle state transition table.
#[derive(Debug, Clone)]
struct DynconfWatcher {
    state: DynconfState,
    generation: Option<u64>,
    /// SHA-256 over raw bytes of the LKG file content.
    source_digest: Option<String>,
    /// SHA-256 over canonical JSON of the served snapshot.
    active_digest: Option<String>,
    /// Same as active_digest when in active/lkg_preserved (always canonical form).
    lkg_digest: Option<String>,
    /// Last error description (bounded, redacted).
    last_error: Option<String>,
    /// The raw bytes of the LKG snapshot (for source_digest verification).
    lkg_raw_bytes: Option<Vec<u8>>,
    /// The parsed result of the LKG snapshot.
    lkg_result: Option<DynconfResult>,
}

impl DynconfWatcher {
    /// Create a new watcher in the disabled state.
    fn new_disabled() -> Self {
        Self {
            state: DynconfState::Disabled,
            generation: None,
            source_digest: None,
            active_digest: None,
            lkg_digest: None,
            last_error: None,
            lkg_raw_bytes: None,
            lkg_result: None,
        }
    }

    /// Create a new watcher in the no_file state (enabled, no file seen yet).
    fn new_enabled() -> Self {
        Self {
            state: DynconfState::NoFile,
            generation: None,
            source_digest: None,
            active_digest: None,
            lkg_digest: None,
            last_error: None,
            lkg_raw_bytes: None,
            lkg_result: None,
        }
    }

    /// Process a file event through the state machine.
    /// Returns the new state after the transition.
    fn process_event(&mut self, event: &FileEvent) -> DynconfState {
        match (&self.state, event) {
            // ── Disabled: no transitions ──
            (DynconfState::Disabled, _) => {
                // Disabled watcher ignores all events
                DynconfState::Disabled
            }

            // ── NoFile + valid file → Active (generation=1) ──
            (DynconfState::NoFile, FileEvent::ValidFile(raw_bytes)) => {
                let result = parse_dynconf(raw_bytes)
                    .expect("ValidFile event must contain parseable bytes");
                self.state = DynconfState::Active;
                self.generation = Some(1);
                self.source_digest = Some(result.source_digest.clone());
                self.active_digest = Some(result.active_digest.clone());
                self.lkg_digest = Some(result.active_digest.clone());
                self.last_error = None;
                self.lkg_raw_bytes = Some(raw_bytes.clone());
                self.lkg_result = Some(result);
                DynconfState::Active
            }

            // ── NoFile + invalid file → InvalidWithoutLkg ──
            (DynconfState::NoFile, FileEvent::InvalidFile(_)) => {
                self.state = DynconfState::InvalidWithoutLkg;
                // No LKG exists; generation and all snapshot digests remain null
                self.last_error = Some("validation_failure".to_string());
                DynconfState::InvalidWithoutLkg
            }

            // ── NoFile + file disappeared → stays NoFile ──
            (DynconfState::NoFile, FileEvent::FileDisappeared(_)) => {
                DynconfState::NoFile
            }

            // ── InvalidWithoutLkg + valid → Active (generation=1) ──
            (DynconfState::InvalidWithoutLkg, FileEvent::ValidFile(raw_bytes)) => {
                let result = parse_dynconf(raw_bytes)
                    .expect("ValidFile event must contain parseable bytes");
                self.state = DynconfState::Active;
                self.generation = Some(1);
                self.source_digest = Some(result.source_digest.clone());
                self.active_digest = Some(result.active_digest.clone());
                self.lkg_digest = Some(result.active_digest.clone());
                self.last_error = None;
                self.lkg_raw_bytes = Some(raw_bytes.clone());
                self.lkg_result = Some(result);
                DynconfState::Active
            }

            // ── InvalidWithoutLkg + invalid → stays InvalidWithoutLkg ──
            (DynconfState::InvalidWithoutLkg, FileEvent::InvalidFile(_)) => {
                // Stays in invalid_without_lkg
                self.last_error = Some("validation_failure".to_string());
                DynconfState::InvalidWithoutLkg
            }

            // ── InvalidWithoutLkg + removal → NoFile ──
            (DynconfState::InvalidWithoutLkg, FileEvent::FileDisappeared(_)) => {
                self.state = DynconfState::NoFile;
                self.last_error = None;
                DynconfState::NoFile
            }

            // ── Active + valid → Active (generation++) ──
            (DynconfState::Active, FileEvent::ValidFile(raw_bytes)) => {
                let result = parse_dynconf(raw_bytes)
                    .expect("ValidFile event must contain parseable bytes");
                let current = self.generation.unwrap();
                self.generation = Some(current + 1);
                self.source_digest = Some(result.source_digest.clone());
                self.active_digest = Some(result.active_digest.clone());
                self.lkg_digest = Some(result.active_digest.clone());
                self.last_error = None;
                self.lkg_raw_bytes = Some(raw_bytes.clone());
                self.lkg_result = Some(result);
                DynconfState::Active
            }

            // ── Active + invalid → LkgPreserved ──
            (DynconfState::Active, FileEvent::InvalidFile(_)) => {
                self.state = DynconfState::LkgPreserved;
                // generation, source_digest, active_digest, lkg_digest unchanged
                self.last_error = Some("validation_failure".to_string());
                DynconfState::LkgPreserved
            }

            // ── Active + file disappeared → LkgPreserved ──
            (DynconfState::Active, FileEvent::FileDisappeared(reason)) => {
                self.state = DynconfState::LkgPreserved;
                // generation unchanged, digests reflect LKG
                self.last_error = Some(format!("{:?}", reason));
                DynconfState::LkgPreserved
            }

            // ── LkgPreserved + valid → Active (generation++) ──
            (DynconfState::LkgPreserved, FileEvent::ValidFile(raw_bytes)) => {
                let result = parse_dynconf(raw_bytes)
                    .expect("ValidFile event must contain parseable bytes");
                let current = self.generation.unwrap();
                self.state = DynconfState::Active;
                self.generation = Some(current + 1);
                self.source_digest = Some(result.source_digest.clone());
                self.active_digest = Some(result.active_digest.clone());
                self.lkg_digest = Some(result.active_digest.clone());
                self.last_error = None;
                self.lkg_raw_bytes = Some(raw_bytes.clone());
                self.lkg_result = Some(result);
                DynconfState::Active
            }

            // ── LkgPreserved + invalid → stays LkgPreserved ──
            (DynconfState::LkgPreserved, FileEvent::InvalidFile(_)) => {
                // LKG still preserved, generation unchanged
                self.last_error = Some("validation_failure".to_string());
                DynconfState::LkgPreserved
            }

            // ── LkgPreserved + file disappeared → stays LkgPreserved ──
            (DynconfState::LkgPreserved, FileEvent::FileDisappeared(reason)) => {
                // LKG still preserved per Req 3.8
                self.last_error = Some(format!("{:?}", reason));
                DynconfState::LkgPreserved
            }
        }
    }
}

// ─── Strategies ───────────────────────────────────────────────────────────────

/// Generate a valid dynconf JSON document as raw bytes.
fn valid_dynconf_bytes() -> impl Strategy<Value = Vec<u8>> {
    (
        prop::option::of(prop_oneof![Just("on"), Just("off")]),
        prop::option::of(prop_oneof![Just("on"), Just("off")]),
        prop::option::of(prop_oneof![
            Just("error"),
            Just("warn"),
            Just("info"),
            Just("debug"),
        ]),
        prop::option::of(prop_oneof![Just("pass"), Just("fail_closed")]),
        prop::option::of(65536u64..=1_073_741_824u64),
    )
        .prop_map(|(filter, prune, log, error, stream)| {
            let mut fields = vec!["\"schema_version\": 1".to_string()];
            if let Some(f) = filter {
                fields.push(format!("\"filter\": \"{}\"", f));
            }
            if let Some(p) = prune {
                fields.push(format!("\"prune_noise\": \"{}\"", p));
            }
            if let Some(l) = log {
                fields.push(format!("\"log_verbosity\": \"{}\"", l));
            }
            if let Some(e) = error {
                fields.push(format!("\"error_policy\": \"{}\"", e));
            }
            if let Some(s) = stream {
                fields.push(format!("\"streaming_buffer\": {}", s));
            }
            format!("{{{}}}", fields.join(", ")).into_bytes()
        })
}

/// Generate invalid dynconf content (guaranteed to fail parse_dynconf).
fn invalid_dynconf_bytes() -> impl Strategy<Value = Vec<u8>> {
    prop_oneof![
        // Invalid JSON
        Just(b"not json at all".to_vec()),
        Just(b"{invalid}".to_vec()),
        Just(b"".to_vec()),
        // Missing schema_version
        Just(br#"{"filter": "on"}"#.to_vec()),
        // Wrong schema_version
        Just(br#"{"schema_version": 2}"#.to_vec()),
        Just(br#"{"schema_version": "1"}"#.to_vec()),
        // Unknown keys
        Just(br#"{"schema_version": 1, "unknown_key": "val"}"#.to_vec()),
        Just(br#"{"schema_version": 1, "memory_budget": 1000}"#.to_vec()),
        // Invalid types
        Just(br#"{"schema_version": 1, "filter": 42}"#.to_vec()),
        Just(br#"{"schema_version": 1, "streaming_buffer": "2m"}"#.to_vec()),
        // Out of range
        Just(br#"{"schema_version": 1, "streaming_buffer": 100}"#.to_vec()),
    ]
}

/// Generate a disappear reason.
fn disappear_reason() -> impl Strategy<Value = DisappearReason> {
    prop_oneof![
        Just(DisappearReason::FileDeleted),
        Just(DisappearReason::PermissionDenied),
        Just(DisappearReason::StatFailure),
    ]
}

/// Generate a file event for the state machine.
fn file_event() -> impl Strategy<Value = FileEvent> {
    prop_oneof![
        valid_dynconf_bytes().prop_map(FileEvent::ValidFile),
        invalid_dynconf_bytes().prop_map(FileEvent::InvalidFile),
        disappear_reason().prop_map(FileEvent::FileDisappeared),
    ]
}

/// Generate a sequence of file events.
fn event_sequence(max_len: usize) -> impl Strategy<Value = Vec<FileEvent>> {
    proptest::collection::vec(file_event(), 1..=max_len)
}

// ─── Property Tests ───────────────────────────────────────────────────────────

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// Property: Active + file disappeared → lkg_preserved with generation unchanged.
    /// Covers file_deleted, permission_denied, and stat_failure.
    ///
    /// **Validates: Requirements 3.8, 4.4**
    #[test]
    fn prop_active_disappear_preserves_lkg(
        initial_bytes in valid_dynconf_bytes(),
        reason in disappear_reason(),
    ) {
        let mut watcher = DynconfWatcher::new_enabled();

        // Load initial valid file → Active
        watcher.process_event(&FileEvent::ValidFile(initial_bytes.clone()));
        prop_assert_eq!(watcher.state, DynconfState::Active);
        prop_assert_eq!(watcher.generation, Some(1));

        let gen_before = watcher.generation;
        let source_digest_before = watcher.source_digest.clone();
        let active_digest_before = watcher.active_digest.clone();
        let lkg_digest_before = watcher.lkg_digest.clone();

        // File disappears
        let new_state = watcher.process_event(&FileEvent::FileDisappeared(reason));

        // Must transition to lkg_preserved
        prop_assert_eq!(new_state, DynconfState::LkgPreserved);
        prop_assert_eq!(watcher.state, DynconfState::LkgPreserved);

        // Generation unchanged
        prop_assert_eq!(watcher.generation, gen_before);

        // All digests reflect LKG (unchanged)
        prop_assert_eq!(&watcher.source_digest, &source_digest_before);
        prop_assert_eq!(&watcher.active_digest, &active_digest_before);
        prop_assert_eq!(&watcher.lkg_digest, &lkg_digest_before);

        // last_error records a redacted failure category
        prop_assert!(watcher.last_error.is_some());
    }

    /// Property: lkg_preserved + valid file → promote to active, generation++.
    ///
    /// **Validates: Requirements 3.8, 4.4**
    #[test]
    fn prop_lkg_preserved_valid_promotes_to_active(
        initial_bytes in valid_dynconf_bytes(),
        invalid_bytes in invalid_dynconf_bytes(),
        recovery_bytes in valid_dynconf_bytes(),
    ) {
        let mut watcher = DynconfWatcher::new_enabled();

        // Load initial valid → Active (gen=1)
        watcher.process_event(&FileEvent::ValidFile(initial_bytes.clone()));
        prop_assert_eq!(watcher.state, DynconfState::Active);
        prop_assert_eq!(watcher.generation, Some(1));

        // Invalid file → LkgPreserved (gen stays 1)
        watcher.process_event(&FileEvent::InvalidFile(invalid_bytes));
        prop_assert_eq!(watcher.state, DynconfState::LkgPreserved);
        prop_assert_eq!(watcher.generation, Some(1));

        // Valid file reappears → Active (gen increments to 2)
        let new_state = watcher.process_event(&FileEvent::ValidFile(recovery_bytes.clone()));
        prop_assert_eq!(new_state, DynconfState::Active);
        prop_assert_eq!(watcher.generation, Some(2));
        prop_assert!(watcher.last_error.is_none());

        // Digests updated to new file
        let expected_source = compute_source_digest(&recovery_bytes);
        prop_assert_eq!(watcher.source_digest.as_ref().unwrap(), &expected_source);
    }

    /// Property: First invalid load (no LKG) → invalid_without_lkg, NOT lkg_preserved.
    ///
    /// **Validates: Requirements 3.8, 4.4**
    #[test]
    fn prop_first_invalid_is_invalid_without_lkg(
        invalid_bytes in invalid_dynconf_bytes(),
    ) {
        let mut watcher = DynconfWatcher::new_enabled();
        prop_assert_eq!(watcher.state, DynconfState::NoFile);

        // First file is invalid
        let new_state = watcher.process_event(&FileEvent::InvalidFile(invalid_bytes));

        // Must be invalid_without_lkg, NOT lkg_preserved
        prop_assert_eq!(new_state, DynconfState::InvalidWithoutLkg);
        prop_assert_ne!(new_state, DynconfState::LkgPreserved);

        // No generation, no digests (no LKG exists)
        prop_assert_eq!(watcher.generation, None);
        prop_assert_eq!(watcher.source_digest, None);
        prop_assert_eq!(watcher.active_digest, None);
        prop_assert_eq!(watcher.lkg_digest, None);

        // last_error present
        prop_assert!(watcher.last_error.is_some());
    }

    /// Property: invalid_without_lkg + valid → active; invalid_without_lkg + removal → no_file.
    ///
    /// **Validates: Requirements 3.8, 4.4**
    #[test]
    fn prop_invalid_without_lkg_transitions(
        invalid_bytes in invalid_dynconf_bytes(),
        valid_bytes in valid_dynconf_bytes(),
        reason in disappear_reason(),
        try_valid_first in any::<bool>(),
    ) {
        let mut watcher = DynconfWatcher::new_enabled();

        // Get to invalid_without_lkg
        watcher.process_event(&FileEvent::InvalidFile(invalid_bytes));
        prop_assert_eq!(watcher.state, DynconfState::InvalidWithoutLkg);

        if try_valid_first {
            // invalid_without_lkg + valid → active
            let new_state = watcher.process_event(&FileEvent::ValidFile(valid_bytes));
            prop_assert_eq!(new_state, DynconfState::Active);
            prop_assert_eq!(watcher.generation, Some(1));
            prop_assert!(watcher.source_digest.is_some());
            prop_assert!(watcher.active_digest.is_some());
            prop_assert!(watcher.lkg_digest.is_some());
        } else {
            // invalid_without_lkg + removal → no_file
            let new_state = watcher.process_event(&FileEvent::FileDisappeared(reason));
            prop_assert_eq!(new_state, DynconfState::NoFile);
            // No LKG exists, no candidate digest exposed
            prop_assert_eq!(watcher.generation, None);
            prop_assert_eq!(watcher.source_digest, None);
            prop_assert_eq!(watcher.active_digest, None);
            prop_assert_eq!(watcher.lkg_digest, None);
        }
    }

    /// Property: source_digest = SHA-256 over the raw bytes that produced the served LKG.
    /// In lkg_preserved state, source_digest reflects the LKG, NOT the rejected candidate.
    ///
    /// **Validates: Requirements 3.8, 4.4**
    #[test]
    fn prop_source_digest_reflects_lkg_bytes(
        initial_bytes in valid_dynconf_bytes(),
        invalid_bytes in invalid_dynconf_bytes(),
    ) {
        let mut watcher = DynconfWatcher::new_enabled();

        // Load valid → Active
        watcher.process_event(&FileEvent::ValidFile(initial_bytes.clone()));
        prop_assert_eq!(watcher.state, DynconfState::Active);

        // Compute expected source_digest from LKG raw bytes
        let expected_source_digest = compute_source_digest(&initial_bytes);
        prop_assert_eq!(
            watcher.source_digest.as_ref().unwrap(),
            &expected_source_digest
        );

        // Invalid reload → LkgPreserved
        watcher.process_event(&FileEvent::InvalidFile(invalid_bytes.clone()));
        prop_assert_eq!(watcher.state, DynconfState::LkgPreserved);

        // source_digest still reflects the LKG raw bytes, NOT the invalid candidate
        prop_assert_eq!(
            watcher.source_digest.as_ref().unwrap(),
            &expected_source_digest,
            "source_digest in lkg_preserved must reflect LKG bytes, not rejected candidate"
        );

        // Verify against independently computed digest
        let lkg_bytes = watcher.lkg_raw_bytes.as_ref().unwrap();
        let independent_digest = compute_source_digest(lkg_bytes);
        prop_assert_eq!(
            watcher.source_digest.as_ref().unwrap(),
            &independent_digest
        );
    }

    /// Property: Absent key vs explicitly set default produce different active_digest.
    /// An omitted key SHALL remain absent from the canonical digest input, while
    /// an explicitly supplied value (even if it matches the "default") produces
    /// a different canonical representation.
    ///
    /// **Validates: Requirements 3.8, 4.4**
    #[test]
    fn prop_absent_vs_explicit_default_different_digest(
        filter_val in prop_oneof![Just("on"), Just("off")],
    ) {
        // Document with filter absent
        let absent = br#"{"schema_version": 1}"#;
        // Document with filter explicitly set
        let explicit = format!(r#"{{"schema_version": 1, "filter": "{}"}}"#, filter_val);

        let result_absent = parse_dynconf(absent).unwrap();
        let result_explicit = parse_dynconf(explicit.as_bytes()).unwrap();

        // active_digest MUST differ (absent key not defaulted into digest)
        prop_assert_ne!(
            result_absent.active_digest, result_explicit.active_digest,
            "absent key and explicit '{}' must produce different active_digest",
            filter_val
        );
    }

    /// Property: State machine invariant — generation never decreases, only
    /// increments on successful reload (valid file in active/lkg_preserved).
    ///
    /// **Validates: Requirements 3.8, 4.4**
    #[test]
    fn prop_generation_monotonic_through_lifecycle(
        events in event_sequence(15),
    ) {
        let mut watcher = DynconfWatcher::new_enabled();
        let mut max_generation: u64 = 0;

        for event in &events {
            watcher.process_event(event);

            if let Some(current_gen) = watcher.generation {
                prop_assert!(
                    current_gen >= max_generation,
                    "generation must never decrease: current {} < previous {}",
                    current_gen, max_generation
                );
                max_generation = current_gen;
            }
        }
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    /// Property: Full state machine transitions are deterministic — same event
    /// sequence always produces the same final state and snapshot values.
    ///
    /// **Validates: Requirements 3.8, 4.4**
    #[test]
    fn prop_state_machine_deterministic(
        events in event_sequence(10),
    ) {
        let mut watcher1 = DynconfWatcher::new_enabled();
        let mut watcher2 = DynconfWatcher::new_enabled();

        for event in &events {
            watcher1.process_event(event);
            watcher2.process_event(event);
        }

        prop_assert_eq!(watcher1.state, watcher2.state);
        prop_assert_eq!(watcher1.generation, watcher2.generation);
        prop_assert_eq!(watcher1.source_digest, watcher2.source_digest);
        prop_assert_eq!(watcher1.active_digest, watcher2.active_digest);
        prop_assert_eq!(watcher1.lkg_digest, watcher2.lkg_digest);
    }

    /// Property: In lkg_preserved, consecutive disappear events do not change
    /// the generation or digests — they only update last_error.
    ///
    /// **Validates: Requirements 3.8, 4.4**
    #[test]
    fn prop_lkg_preserved_stable_under_repeated_disappear(
        initial_bytes in valid_dynconf_bytes(),
        reasons in proptest::collection::vec(disappear_reason(), 2..=5),
    ) {
        let mut watcher = DynconfWatcher::new_enabled();

        // Establish LKG
        watcher.process_event(&FileEvent::ValidFile(initial_bytes.clone()));
        // Trigger lkg_preserved via first disappear
        watcher.process_event(&FileEvent::FileDisappeared(reasons[0]));
        prop_assert_eq!(watcher.state, DynconfState::LkgPreserved);

        let gen_snapshot = watcher.generation;
        let source_snapshot = watcher.source_digest.clone();
        let active_snapshot = watcher.active_digest.clone();
        let lkg_snapshot = watcher.lkg_digest.clone();

        // Additional disappear events must not change generation/digests
        for reason in &reasons[1..] {
            watcher.process_event(&FileEvent::FileDisappeared(*reason));
            prop_assert_eq!(watcher.state, DynconfState::LkgPreserved);
            prop_assert_eq!(watcher.generation, gen_snapshot);
            prop_assert_eq!(&watcher.source_digest, &source_snapshot);
            prop_assert_eq!(&watcher.active_digest, &active_snapshot);
            prop_assert_eq!(&watcher.lkg_digest, &lkg_snapshot);
        }
    }

    /// Property: source_digest in active state always equals SHA-256 of raw bytes
    /// that produced the current snapshot (verified independently).
    ///
    /// **Validates: Requirements 3.8, 4.4**
    #[test]
    fn prop_source_digest_is_sha256_of_raw_bytes(
        raw_bytes in valid_dynconf_bytes(),
    ) {
        let result = parse_dynconf(&raw_bytes).unwrap();

        // Independently compute SHA-256 of the raw bytes
        let expected = compute_source_digest(&raw_bytes);

        prop_assert_eq!(
            &result.source_digest, &expected,
            "source_digest must be SHA-256 of raw input bytes"
        );

        // Verify it's a 64-char hex string (SHA-256 = 32 bytes = 64 hex chars)
        prop_assert_eq!(result.source_digest.len(), 64);
        prop_assert!(result.source_digest.chars().all(|c| c.is_ascii_hexdigit()));
    }

    /// Property: lkg_digest always equals the active_digest of the LKG
    /// (the canonical form), never the source_digest.
    ///
    /// **Validates: Requirements 3.8, 4.4**
    #[test]
    fn prop_lkg_digest_is_active_digest_of_lkg(
        initial_bytes in valid_dynconf_bytes(),
        second_bytes in valid_dynconf_bytes(),
    ) {
        let mut watcher = DynconfWatcher::new_enabled();

        // Load initial → Active
        watcher.process_event(&FileEvent::ValidFile(initial_bytes));
        prop_assert_eq!(watcher.state, DynconfState::Active);

        // In active state: lkg_digest == active_digest (the LKG IS the active snapshot)
        prop_assert_eq!(&watcher.lkg_digest, &watcher.active_digest);

        // Load second valid → Active (gen=2)
        watcher.process_event(&FileEvent::ValidFile(second_bytes));
        prop_assert_eq!(watcher.state, DynconfState::Active);
        prop_assert_eq!(watcher.generation, Some(2));

        // lkg_digest still equals active_digest (new LKG is the new active)
        prop_assert_eq!(&watcher.lkg_digest, &watcher.active_digest);
    }
}

// ─── Deterministic State Machine Tests ────────────────────────────────────────

#[test]
fn test_lifecycle_disabled_ignores_events() {
    let mut watcher = DynconfWatcher::new_disabled();

    // All events ignored in disabled state
    watcher.process_event(&FileEvent::ValidFile(
        br#"{"schema_version": 1, "filter": "on"}"#.to_vec(),
    ));
    assert_eq!(watcher.state, DynconfState::Disabled);
    assert_eq!(watcher.generation, None);

    watcher.process_event(&FileEvent::InvalidFile(b"bad".to_vec()));
    assert_eq!(watcher.state, DynconfState::Disabled);

    watcher.process_event(&FileEvent::FileDisappeared(DisappearReason::FileDeleted));
    assert_eq!(watcher.state, DynconfState::Disabled);
}

#[test]
fn test_lifecycle_no_file_to_active() {
    let mut watcher = DynconfWatcher::new_enabled();
    assert_eq!(watcher.state, DynconfState::NoFile);

    let raw = br#"{"schema_version": 1, "filter": "on"}"#;
    watcher.process_event(&FileEvent::ValidFile(raw.to_vec()));

    assert_eq!(watcher.state, DynconfState::Active);
    assert_eq!(watcher.generation, Some(1));
    assert!(watcher.source_digest.is_some());
    assert!(watcher.active_digest.is_some());
    assert_eq!(&watcher.lkg_digest, &watcher.active_digest);
    assert!(watcher.last_error.is_none());
}

#[test]
fn test_lifecycle_no_file_to_invalid_without_lkg() {
    let mut watcher = DynconfWatcher::new_enabled();

    watcher.process_event(&FileEvent::InvalidFile(b"bad json".to_vec()));

    assert_eq!(watcher.state, DynconfState::InvalidWithoutLkg);
    assert_eq!(watcher.generation, None);
    assert_eq!(watcher.source_digest, None);
    assert_eq!(watcher.active_digest, None);
    assert_eq!(watcher.lkg_digest, None);
    assert!(watcher.last_error.is_some());
}

#[test]
fn test_lifecycle_invalid_without_lkg_to_active() {
    let mut watcher = DynconfWatcher::new_enabled();

    // First invalid → invalid_without_lkg
    watcher.process_event(&FileEvent::InvalidFile(b"bad".to_vec()));
    assert_eq!(watcher.state, DynconfState::InvalidWithoutLkg);

    // Valid appears → active
    let raw = br#"{"schema_version": 1, "prune_noise": "on"}"#;
    watcher.process_event(&FileEvent::ValidFile(raw.to_vec()));

    assert_eq!(watcher.state, DynconfState::Active);
    assert_eq!(watcher.generation, Some(1));
    assert!(watcher.last_error.is_none());
}

#[test]
fn test_lifecycle_invalid_without_lkg_removal_to_no_file() {
    let mut watcher = DynconfWatcher::new_enabled();

    // First invalid → invalid_without_lkg
    watcher.process_event(&FileEvent::InvalidFile(b"bad".to_vec()));
    assert_eq!(watcher.state, DynconfState::InvalidWithoutLkg);

    // File removed → no_file
    watcher.process_event(&FileEvent::FileDisappeared(DisappearReason::FileDeleted));
    assert_eq!(watcher.state, DynconfState::NoFile);
    assert_eq!(watcher.generation, None);
}

#[test]
fn test_lifecycle_active_invalid_to_lkg_preserved() {
    let mut watcher = DynconfWatcher::new_enabled();

    let raw = br#"{"schema_version": 1, "filter": "off"}"#;
    watcher.process_event(&FileEvent::ValidFile(raw.to_vec()));
    assert_eq!(watcher.state, DynconfState::Active);
    assert_eq!(watcher.generation, Some(1));

    let gen_before = watcher.generation;
    let digest_before = watcher.source_digest.clone();

    // Invalid reload
    watcher.process_event(&FileEvent::InvalidFile(b"corrupt".to_vec()));
    assert_eq!(watcher.state, DynconfState::LkgPreserved);
    assert_eq!(watcher.generation, gen_before); // unchanged
    assert_eq!(watcher.source_digest, digest_before); // reflects LKG
    assert!(watcher.last_error.is_some());
}

#[test]
fn test_lifecycle_active_disappear_all_reasons() {
    for reason in &[
        DisappearReason::FileDeleted,
        DisappearReason::PermissionDenied,
        DisappearReason::StatFailure,
    ] {
        let mut watcher = DynconfWatcher::new_enabled();
        let raw = br#"{"schema_version": 1, "log_verbosity": "debug"}"#;
        watcher.process_event(&FileEvent::ValidFile(raw.to_vec()));

        let gen_before = watcher.generation;
        watcher.process_event(&FileEvent::FileDisappeared(*reason));

        assert_eq!(watcher.state, DynconfState::LkgPreserved);
        assert_eq!(watcher.generation, gen_before);
        assert!(watcher.last_error.is_some());
    }
}

#[test]
fn test_lifecycle_lkg_preserved_recovery() {
    let mut watcher = DynconfWatcher::new_enabled();

    // Establish LKG
    let raw1 = br#"{"schema_version": 1, "filter": "on"}"#;
    watcher.process_event(&FileEvent::ValidFile(raw1.to_vec()));
    assert_eq!(watcher.generation, Some(1));

    // Transition to lkg_preserved
    watcher.process_event(&FileEvent::FileDisappeared(DisappearReason::FileDeleted));
    assert_eq!(watcher.state, DynconfState::LkgPreserved);
    assert_eq!(watcher.generation, Some(1));

    // Recover with new valid file
    let raw2 = br#"{"schema_version": 1, "filter": "off"}"#;
    watcher.process_event(&FileEvent::ValidFile(raw2.to_vec()));

    assert_eq!(watcher.state, DynconfState::Active);
    assert_eq!(watcher.generation, Some(2)); // incremented
    assert!(watcher.last_error.is_none());

    // Digests updated to new file
    let expected_source = compute_source_digest(raw2);
    assert_eq!(watcher.source_digest.as_ref().unwrap(), &expected_source);
}

#[test]
fn test_source_digest_equals_sha256_of_raw_bytes() {
    let raw = br#"{"schema_version": 1, "filter": "on", "streaming_buffer": 131072}"#;
    let result = parse_dynconf(raw).unwrap();

    // Independent SHA-256 computation
    let expected = compute_source_digest(raw);
    assert_eq!(result.source_digest, expected);
}

#[test]
fn test_absent_key_vs_explicit_default_different_active_digest() {
    // Document with no optional keys (all absent)
    let absent_all = br#"{"schema_version": 1}"#;
    // Document with filter explicitly set to "on"
    let explicit_on = br#"{"schema_version": 1, "filter": "on"}"#;
    // Document with filter explicitly set to "off"
    let explicit_off = br#"{"schema_version": 1, "filter": "off"}"#;

    let r_absent = parse_dynconf(absent_all).unwrap();
    let r_on = parse_dynconf(explicit_on).unwrap();
    let r_off = parse_dynconf(explicit_off).unwrap();

    // All three active_digests must be different
    assert_ne!(
        r_absent.active_digest, r_on.active_digest,
        "absent filter and explicit 'on' must differ"
    );
    assert_ne!(
        r_absent.active_digest, r_off.active_digest,
        "absent filter and explicit 'off' must differ"
    );
    assert_ne!(
        r_on.active_digest, r_off.active_digest,
        "explicit 'on' and 'off' must differ"
    );
}

#[test]
fn test_absent_streaming_buffer_vs_explicit_default() {
    // No streaming_buffer key
    let absent = br#"{"schema_version": 1}"#;
    // Explicit streaming_buffer at the documented default (2m = 2097152)
    let explicit_default = br#"{"schema_version": 1, "streaming_buffer": 2097152}"#;

    let r_absent = parse_dynconf(absent).unwrap();
    let r_explicit = parse_dynconf(explicit_default).unwrap();

    // Must produce different active_digest
    assert_ne!(
        r_absent.active_digest, r_explicit.active_digest,
        "absent streaming_buffer and explicit default value must produce different active_digest"
    );
}

#[test]
fn test_full_lifecycle_scenario() {
    // Complete lifecycle scenario exercising all transitions
    let mut watcher = DynconfWatcher::new_enabled();

    // Start: NoFile
    assert_eq!(watcher.state, DynconfState::NoFile);

    // Step 1: Invalid file appears → InvalidWithoutLkg
    watcher.process_event(&FileEvent::InvalidFile(b"garbage".to_vec()));
    assert_eq!(watcher.state, DynconfState::InvalidWithoutLkg);
    assert_eq!(watcher.generation, None);

    // Step 2: File removed → NoFile
    watcher.process_event(&FileEvent::FileDisappeared(DisappearReason::FileDeleted));
    assert_eq!(watcher.state, DynconfState::NoFile);

    // Step 3: Valid file appears → Active (gen=1)
    let raw1 = br#"{"schema_version": 1, "filter": "on"}"#;
    watcher.process_event(&FileEvent::ValidFile(raw1.to_vec()));
    assert_eq!(watcher.state, DynconfState::Active);
    assert_eq!(watcher.generation, Some(1));

    // Step 4: Another valid file → Active (gen=2)
    let raw2 = br#"{"schema_version": 1, "filter": "off"}"#;
    watcher.process_event(&FileEvent::ValidFile(raw2.to_vec()));
    assert_eq!(watcher.state, DynconfState::Active);
    assert_eq!(watcher.generation, Some(2));

    // Step 5: File deleted → LkgPreserved (gen still 2)
    watcher.process_event(&FileEvent::FileDisappeared(DisappearReason::FileDeleted));
    assert_eq!(watcher.state, DynconfState::LkgPreserved);
    assert_eq!(watcher.generation, Some(2));

    // Step 6: Permission denied → LkgPreserved (gen still 2)
    watcher.process_event(&FileEvent::FileDisappeared(DisappearReason::PermissionDenied));
    assert_eq!(watcher.state, DynconfState::LkgPreserved);
    assert_eq!(watcher.generation, Some(2));

    // Step 7: Invalid file → LkgPreserved (gen still 2)
    watcher.process_event(&FileEvent::InvalidFile(b"bad again".to_vec()));
    assert_eq!(watcher.state, DynconfState::LkgPreserved);
    assert_eq!(watcher.generation, Some(2));

    // Step 8: Valid file reappears → Active (gen=3)
    let raw3 = br#"{"schema_version": 1, "prune_noise": "on"}"#;
    watcher.process_event(&FileEvent::ValidFile(raw3.to_vec()));
    assert_eq!(watcher.state, DynconfState::Active);
    assert_eq!(watcher.generation, Some(3));
    assert!(watcher.last_error.is_none());

    // Verify source_digest reflects the latest valid bytes
    let expected = compute_source_digest(raw3);
    assert_eq!(watcher.source_digest.as_ref().unwrap(), &expected);
}
