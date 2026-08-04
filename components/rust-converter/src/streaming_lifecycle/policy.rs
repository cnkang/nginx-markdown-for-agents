//! Event Failure Policy classification.
//!
//! Classifies all 19 EventKinds into their failure-record policy:
//! - `required`: ERROR, BUDGET_INIT_FAILURE, RESOURCE_LIMIT
//! - `reuse_persisted_ledger`: RESUME_DRAIN
//! - `forbidden`: all other 15 events

use super::types::EventKind;

/// Event failure policy classification.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EventFailurePolicy {
    /// Event requires a non-null FailureRecord.
    Required,
    /// Event reuses the persisted ledger; carries no new record.
    ReusePersisted,
    /// Event forbids a new FailureRecord.
    Forbidden,
}

/// Classify an event kind into its failure-record policy.
///
/// - ERROR, BUDGET_INIT_FAILURE, RESOURCE_LIMIT → Required
/// - RESUME_DRAIN → ReusePersisted
/// - All other 15 events → Forbidden
pub fn event_failure_policy(kind: EventKind) -> EventFailurePolicy {
    match kind {
        EventKind::Error | EventKind::BudgetInitFailure | EventKind::ResourceLimit => {
            EventFailurePolicy::Required
        }
        EventKind::ResumeDrain => EventFailurePolicy::ReusePersisted,
        EventKind::Eligible
        | EventKind::NotEligible
        | EventKind::StreamingStart
        | EventKind::ParserUnsuitable
        | EventKind::HardExcluded
        | EventKind::FullDocFeature
        | EventKind::ReplayOverflow
        | EventKind::StrictEtag
        | EventKind::LookBehindOverflow
        | EventKind::AutoRisk
        | EventKind::Commit
        | EventKind::UpstreamEnd
        | EventKind::ClientAbort
        | EventKind::BodyFilterReentry
        | EventKind::Cleanup => EventFailurePolicy::Forbidden,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn required_events_correctly_classified() {
        assert_eq!(
            event_failure_policy(EventKind::Error),
            EventFailurePolicy::Required
        );
        assert_eq!(
            event_failure_policy(EventKind::BudgetInitFailure),
            EventFailurePolicy::Required
        );
        assert_eq!(
            event_failure_policy(EventKind::ResourceLimit),
            EventFailurePolicy::Required
        );
    }

    #[test]
    fn reuse_persisted_events_correctly_classified() {
        assert_eq!(
            event_failure_policy(EventKind::ResumeDrain),
            EventFailurePolicy::ReusePersisted
        );
    }

    #[test]
    fn forbidden_events_correctly_classified() {
        let forbidden_events = [
            EventKind::Eligible,
            EventKind::NotEligible,
            EventKind::StreamingStart,
            EventKind::ParserUnsuitable,
            EventKind::HardExcluded,
            EventKind::FullDocFeature,
            EventKind::ReplayOverflow,
            EventKind::StrictEtag,
            EventKind::LookBehindOverflow,
            EventKind::AutoRisk,
            EventKind::Commit,
            EventKind::UpstreamEnd,
            EventKind::ClientAbort,
            EventKind::BodyFilterReentry,
            EventKind::Cleanup,
        ];
        for event in &forbidden_events {
            assert_eq!(
                event_failure_policy(*event),
                EventFailurePolicy::Forbidden,
                "Event {:?} should be Forbidden",
                event
            );
        }
        assert_eq!(forbidden_events.len(), 15);
    }

    #[test]
    fn all_19_events_classified() {
        let all_events = [
            EventKind::Eligible,
            EventKind::NotEligible,
            EventKind::StreamingStart,
            EventKind::ParserUnsuitable,
            EventKind::HardExcluded,
            EventKind::FullDocFeature,
            EventKind::BudgetInitFailure,
            EventKind::ReplayOverflow,
            EventKind::ResourceLimit,
            EventKind::StrictEtag,
            EventKind::LookBehindOverflow,
            EventKind::AutoRisk,
            EventKind::Commit,
            EventKind::Error,
            EventKind::UpstreamEnd,
            EventKind::ResumeDrain,
            EventKind::ClientAbort,
            EventKind::BodyFilterReentry,
            EventKind::Cleanup,
        ];
        assert_eq!(all_events.len(), 19);
        /* Exhaustive: every variant is covered. The match in
         * event_failure_policy has no wildcard, so new variants cause a
         * compile error. */
        for event in &all_events {
            let _ = event_failure_policy(*event);
        }
    }
}
