//! Decision engine for Markdown conversion eligibility.
//!
//! Implements a pure function that evaluates request context and
//! produces a conversion decision with a reason code. Every decision
//! path has a corresponding reason code for observability.
//!
//! # Design
//!
//! The decision engine is a pure function: same context always produces
//! the same result. No side effects, no I/O. The C caller provides
//! context, the engine returns (decision, reason_code).

pub mod reason_code;

/// Conversion decision result.
#[derive(Debug, Clone, PartialEq)]
pub enum Decision {
    /// Proceed with HTML-to-Markdown conversion.
    Convert,

    /// Skip conversion, pass through original content.
    Skip(SkipReason),
}

/// Reason for skipping conversion.
#[derive(Debug, Clone, PartialEq)]
pub enum SkipReason {
    /// Accept header indicates client does not prefer text/markdown.
    SkipAccept,

    /// No Accept header was present.
    SkipNoAccept,

    /// Conditional request resulted in 304 Not Modified.
    SkipConditional,

    /// Decompression failed; pass through compressed content.
    FailDecompression,

    /// Parse timeout exceeded.
    ParseTimeout,

    /// Parse budget exceeded.
    ParseBudgetExceeded,

    /// Response not eligible (method, status, content-type, etc.).
    NotEligible,

    /// Module disabled for this request.
    Disabled,
}

/// Reason code string for each decision path.
impl SkipReason {
    /// Return the reason code string for logging and metrics.
    pub fn code(&self) -> &'static str {
        match self {
            SkipReason::SkipAccept => "SKIP_ACCEPT",
            SkipReason::SkipNoAccept => "SKIP_NO_ACCEPT",
            SkipReason::SkipConditional => "SKIP_CONDITIONAL",
            SkipReason::FailDecompression => "FAIL_DECOMPRESSION",
            SkipReason::ParseTimeout => "PARSE_TIMEOUT",
            SkipReason::ParseBudgetExceeded => "PARSE_BUDGET_EXCEEDED",
            SkipReason::NotEligible => "NOT_ELIGIBLE",
            SkipReason::Disabled => "DISABLED",
        }
    }
}

/// Request context snapshot for decision making.
///
/// All fields are copied from the live request at decision time
/// to ensure the decision is based on a consistent snapshot.
#[derive(Debug, Clone)]
pub struct DecisionContext {
    /// Whether the module is enabled for this request.
    pub enabled: bool,
    /// Whether the response is eligible for conversion.
    pub eligible: bool,
    /// Whether Accept negotiation indicates text/markdown preference.
    pub accept_prefers_markdown: bool,
    /// Whether the Accept header was present.
    pub accept_header_present: bool,
    /// Whether a conditional request matches (304 applicable).
    pub conditional_not_modified: bool,
    /// Whether decompression succeeded (false = failed).
    pub decompression_ok: bool,
    /// Whether parsing timed out.
    pub parse_timed_out: bool,
    /// Whether parsing exceeded budget.
    pub parse_budget_exceeded: bool,
}

/// Make a conversion decision based on request context.
///
/// Pure function: same context → same result. No side effects.
///
/// # Decision Order
///
/// 1. Disabled → Skip(Disabled)
/// 2. Not eligible → Skip(NotEligible)
/// 3. No Accept header → Skip(SkipNoAccept)
/// 4. Accept does not prefer markdown → Skip(SkipAccept)
/// 5. Conditional 304 → Skip(SkipConditional)
/// 6. Decompression failed → Skip(FailDecompression)
/// 7. Parse timeout → Skip(ParseTimeout)
/// 8. Parse budget exceeded → Skip(ParseBudgetExceeded)
/// 9. All checks pass → Convert
pub fn make_decision(ctx: &DecisionContext) -> Decision {
    if !ctx.enabled {
        return Decision::Skip(SkipReason::Disabled);
    }

    if !ctx.eligible {
        return Decision::Skip(SkipReason::NotEligible);
    }

    if !ctx.accept_header_present {
        return Decision::Skip(SkipReason::SkipNoAccept);
    }

    if !ctx.accept_prefers_markdown {
        return Decision::Skip(SkipReason::SkipAccept);
    }

    if ctx.conditional_not_modified {
        return Decision::Skip(SkipReason::SkipConditional);
    }

    if !ctx.decompression_ok {
        return Decision::Skip(SkipReason::FailDecompression);
    }

    if ctx.parse_timed_out {
        return Decision::Skip(SkipReason::ParseTimeout);
    }

    if ctx.parse_budget_exceeded {
        return Decision::Skip(SkipReason::ParseBudgetExceeded);
    }

    Decision::Convert
}

/// Return the reason code string for a decision.
pub fn decision_reason_code(decision: &Decision) -> &'static str {
    match decision {
        Decision::Convert => "CONVERT",
        Decision::Skip(reason) => reason.code(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn default_ctx() -> DecisionContext {
        DecisionContext {
            enabled: true,
            eligible: true,
            accept_prefers_markdown: true,
            accept_header_present: true,
            conditional_not_modified: false,
            decompression_ok: true,
            parse_timed_out: false,
            parse_budget_exceeded: false,
        }
    }

    #[test]
    fn test_convert_happy_path() {
        let ctx = default_ctx();
        let d = make_decision(&ctx);
        assert_eq!(d, Decision::Convert);
        assert_eq!(decision_reason_code(&d), "CONVERT");
    }

    #[test]
    fn test_disabled() {
        let mut ctx = default_ctx();
        ctx.enabled = false;
        let d = make_decision(&ctx);
        assert_eq!(d, Decision::Skip(SkipReason::Disabled));
        assert_eq!(decision_reason_code(&d), "DISABLED");
    }

    #[test]
    fn test_not_eligible() {
        let mut ctx = default_ctx();
        ctx.eligible = false;
        let d = make_decision(&ctx);
        assert_eq!(d, Decision::Skip(SkipReason::NotEligible));
    }

    #[test]
    fn test_no_accept_header() {
        let mut ctx = default_ctx();
        ctx.accept_header_present = false;
        ctx.accept_prefers_markdown = false;
        let d = make_decision(&ctx);
        assert_eq!(d, Decision::Skip(SkipReason::SkipNoAccept));
    }

    #[test]
    fn test_accept_does_not_prefer_markdown() {
        let mut ctx = default_ctx();
        ctx.accept_prefers_markdown = false;
        let d = make_decision(&ctx);
        assert_eq!(d, Decision::Skip(SkipReason::SkipAccept));
    }

    #[test]
    fn test_conditional_304() {
        let mut ctx = default_ctx();
        ctx.conditional_not_modified = true;
        let d = make_decision(&ctx);
        assert_eq!(d, Decision::Skip(SkipReason::SkipConditional));
    }

    #[test]
    fn test_decompression_failed() {
        let mut ctx = default_ctx();
        ctx.decompression_ok = false;
        let d = make_decision(&ctx);
        assert_eq!(d, Decision::Skip(SkipReason::FailDecompression));
    }

    #[test]
    fn test_parse_timeout() {
        let mut ctx = default_ctx();
        ctx.parse_timed_out = true;
        let d = make_decision(&ctx);
        assert_eq!(d, Decision::Skip(SkipReason::ParseTimeout));
    }

    #[test]
    fn test_parse_budget_exceeded() {
        let mut ctx = default_ctx();
        ctx.parse_budget_exceeded = true;
        let d = make_decision(&ctx);
        assert_eq!(d, Decision::Skip(SkipReason::ParseBudgetExceeded));
    }

    #[test]
    fn test_idempotent() {
        let ctx = default_ctx();
        let d1 = make_decision(&ctx);
        let d2 = make_decision(&ctx);
        assert_eq!(d1, d2);
    }

    #[test]
    fn test_all_skip_reasons_have_codes() {
        let reasons = [
            SkipReason::SkipAccept,
            SkipReason::SkipNoAccept,
            SkipReason::SkipConditional,
            SkipReason::FailDecompression,
            SkipReason::ParseTimeout,
            SkipReason::ParseBudgetExceeded,
            SkipReason::NotEligible,
            SkipReason::Disabled,
        ];

        for reason in &reasons {
            let code = reason.code();
            assert!(!code.is_empty(), "Reason {:?} has empty code", reason);
            assert!(!code.contains(' '), "Reason code '{}' contains space", code);
        }

        // All codes unique
        let codes: Vec<&str> = reasons.iter().map(|r| r.code()).collect();
        for i in 0..codes.len() {
            for j in (i + 1)..codes.len() {
                assert_ne!(
                    codes[i], codes[j],
                    "Codes '{}' and '{}' collide",
                    codes[i], codes[j]
                );
            }
        }
    }
}
