//! Memory budget management for the streaming pipeline.
//!
//! Provides [`MemoryBudget`] which enforces per-request memory limits
//! across all pipeline stages, preventing unbounded memory growth.

use crate::error::ConversionError;

/// Memory budget configuration for the streaming converter.
///
/// Each sub-budget is drawn from the total budget. The converter checks
/// allocations against these limits at runtime and returns
/// [`ConversionError::BudgetExceeded`] when a limit is breached.
///
/// # Defaults
///
/// | Field | Default | Rationale |
/// |-------|---------|-----------|
/// | `total` | 2 MiB | Covers bounded state for most documents |
/// | `state_stack` | 64 KiB | ~1000 nesting levels at ~64 B each |
/// | `output_buffer` | 256 KiB | One large block-level element |
/// | `charset_sniff` | 1024 B | Matches `detect_charset` scan range |
/// | `lookahead` | 64 KiB | Sufficient for `<head>` metadata |
#[derive(Debug, Clone)]
pub struct MemoryBudget {
    /// Total memory budget in bytes.
    pub total: usize,
    /// Budget for the structural state stack.
    pub state_stack: usize,
    /// Budget for the pending Markdown output buffer.
    pub output_buffer: usize,
    /// Budget for the charset sniff buffer (fixed).
    pub charset_sniff: usize,
    /// Budget for lookahead buffering (front matter, etc.).
    pub lookahead: usize,
}

impl Default for MemoryBudget {
    /// Creates a `MemoryBudget` populated with sensible default byte limits for streaming.
    ///
    /// Defaults:
    /// - `total = 2 * 1024 * 1024` (2 MiB)
    /// - `state_stack = 64 * 1024` (64 KiB)
    /// - `output_buffer = 256 * 1024` (256 KiB)
    /// - `charset_sniff = 1024` (1 KiB)
    /// - `lookahead = 64 * 1024` (64 KiB)
    ///
    /// # Examples
    ///
    /// ```
    /// let b = MemoryBudget::default();
    /// assert_eq!(b.total, 2 * 1024 * 1024);
    /// assert_eq!(b.state_stack, 64 * 1024);
    /// assert_eq!(b.output_buffer, 256 * 1024);
    /// assert_eq!(b.charset_sniff, 1024);
    /// assert_eq!(b.lookahead, 64 * 1024);
    /// ```
    fn default() -> Self {
        Self {
            total: 2 * 1024 * 1024,    // 2 MiB
            state_stack: 64 * 1024,    // 64 KiB
            output_buffer: 256 * 1024, // 256 KiB
            charset_sniff: 1024,       // 1 KiB (fixed)
            lookahead: 64 * 1024,      // 64 KiB
        }
    }
}

impl MemoryBudget {
    /// Validate that allocating `additional` bytes does not exceed the budget for a pipeline stage.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::BudgetExceeded` if adding `additional` to `current` would overflow
    /// or if the resulting total exceeds `limit`. On integer overflow the error's `stage` will be
    /// formatted as `"{stage} (integer overflow)"` and `used` will be `usize::MAX`; when the limit is
    /// exceeded `stage` will be the provided stage name and `used` will be `current + additional`.
    ///
    /// # Examples
    ///
    /// ```rust
    /// # use components::rust_converter::streaming::budget::check_allocation;
    /// let _ = check_allocation("state_stack", 0, 100, 1024).unwrap();
    /// ```
    #[cfg(feature = "streaming")]
    pub fn check_allocation(
        stage: &str,
        current: usize,
        additional: usize,
        limit: usize,
    ) -> Result<(), ConversionError> {
        let new_total =
            current
                .checked_add(additional)
                .ok_or_else(|| ConversionError::BudgetExceeded {
                    stage: format!("{} (integer overflow)", stage),
                    used: usize::MAX,
                    limit,
                })?;
        if new_total > limit {
            return Err(ConversionError::BudgetExceeded {
                stage: stage.to_string(),
                used: new_total,
                limit,
            });
        }
        Ok(())
    }

    /// Validate that adding `additional` bytes to the state stack does not exceed the configured budget.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::BudgetExceeded` if `current + additional` would exceed the `state_stack` limit or if the addition overflows.
    ///
    /// # Examples
    ///
    /// ```
    /// let b = MemoryBudget::default();
    /// assert!(b.check_state_stack(0, 100).is_ok());
    /// ```
    #[cfg(feature = "streaming")]
    pub fn check_state_stack(
        &self,
        current: usize,
        additional: usize,
    ) -> Result<(), ConversionError> {
        Self::check_allocation("state_stack", current, additional, self.state_stack)
    }

    /// Validate that allocating `additional` bytes on top of `current` will not exceed the output buffer budget.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::BudgetExceeded` if `current + additional` would be greater than the configured output buffer limit
    /// or if the addition overflows.
    ///
    /// # Examples
    ///
    /// ```
    /// let b = MemoryBudget::default();
    /// assert!(b.check_output_buffer(0, 1024).is_ok());
    /// ```
    #[cfg(feature = "streaming")]
    pub fn check_output_buffer(
        &self,
        current: usize,
        additional: usize,
    ) -> Result<(), ConversionError> {
        Self::check_allocation("output_buffer", current, additional, self.output_buffer)
    }

    /// Validate that adding `additional` bytes to `current` bytes does not exceed the lookahead budget.
    ///
    /// Returns `Ok(())` when the resulting used bytes are within the configured lookahead limit.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::BudgetExceeded` when `current + additional` would be greater than the configured lookahead limit or when an integer overflow occurs while computing the sum.
    ///
    /// # Examples
    ///
    /// ```
    /// let budget = MemoryBudget::default();
    /// assert!(budget.check_lookahead(0, 1024).is_ok());
    /// ```
    #[cfg(feature = "streaming")]
    pub fn check_lookahead(
        &self,
        current: usize,
        additional: usize,
    ) -> Result<(), ConversionError> {
        Self::check_allocation("lookahead", current, additional, self.lookahead)
    }

    /// Validate a proposed increase against the overall memory budget.
    ///
    /// Returns `Ok(())` when `current_total + additional` does not exceed the configured
    /// total budget.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::BudgetExceeded` when the addition would exceed the total
    /// budget or when the addition overflows (reported with a stage message that includes
    /// `"integer overflow"`).
    ///
    /// # Examples
    ///
    /// ```
    /// # use components::rust_converter::streaming::budget::MemoryBudget;
    /// # use components::rust_converter::streaming::budget::ConversionError;
    /// let budget = MemoryBudget::default();
    /// // within limit
    /// assert!(budget.check_total(0, 1024).is_ok());
    /// // exceeding limit returns a BudgetExceeded error
    /// let err = budget.check_total(budget.total, 1).unwrap_err();
    /// assert_eq!(err.code(), 6);
    /// ```
    #[cfg(feature = "streaming")]
    pub fn check_total(
        &self,
        current_total: usize,
        additional: usize,
    ) -> Result<(), ConversionError> {
        Self::check_allocation("total", current_total, additional, self.total)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_values() {
        let budget = MemoryBudget::default();
        assert_eq!(budget.total, 2 * 1024 * 1024);
        assert_eq!(budget.state_stack, 64 * 1024);
        assert_eq!(budget.output_buffer, 256 * 1024);
        assert_eq!(budget.charset_sniff, 1024);
        assert_eq!(budget.lookahead, 64 * 1024);
    }

    #[test]
    fn test_check_allocation_within_limit() {
        assert!(MemoryBudget::check_allocation("test", 100, 50, 200).is_ok());
    }

    #[test]
    fn test_check_allocation_at_limit() {
        assert!(MemoryBudget::check_allocation("test", 100, 100, 200).is_ok());
    }

    #[test]
    fn test_check_allocation_exceeds_limit() {
        let err = MemoryBudget::check_allocation("test_stage", 150, 100, 200).unwrap_err();
        assert_eq!(err.code(), 6);
        assert!(format!("{}", err).contains("test_stage"));
    }

    #[test]
    fn test_check_allocation_saturating_overflow() {
        // usize::MAX + 1 should be caught by checked_add and report overflow.
        let err =
            MemoryBudget::check_allocation("overflow", usize::MAX, 1, usize::MAX - 1).unwrap_err();
        assert_eq!(err.code(), 6);
        assert!(format!("{}", err).contains("overflow"));
    }

    #[test]
    fn test_check_state_stack() {
        let budget = MemoryBudget::default();
        assert!(budget.check_state_stack(0, 100).is_ok());
        let err = budget.check_state_stack(64 * 1024, 1).unwrap_err();
        assert_eq!(err.code(), 6);
    }

    #[test]
    fn test_check_output_buffer() {
        let budget = MemoryBudget::default();
        assert!(budget.check_output_buffer(0, 256 * 1024).is_ok());
        let err = budget.check_output_buffer(256 * 1024, 1).unwrap_err();
        assert_eq!(err.code(), 6);
    }

    #[test]
    fn test_check_lookahead() {
        let budget = MemoryBudget::default();
        assert!(budget.check_lookahead(0, 64 * 1024).is_ok());
        let err = budget.check_lookahead(64 * 1024, 1).unwrap_err();
        assert_eq!(err.code(), 6);
    }

    #[test]
    fn test_check_total() {
        let budget = MemoryBudget::default();
        assert!(budget.check_total(0, 2 * 1024 * 1024).is_ok());
        let err = budget.check_total(2 * 1024 * 1024, 1).unwrap_err();
        assert_eq!(err.code(), 6);
    }

    // --- Regression tests ---

    /// Regression: integer overflow in check_allocation must be detected
    /// explicitly via checked_add, not masked by saturating_add. The error
    /// message should indicate an overflow occurred.
    #[test]
    fn test_check_allocation_overflow_detected_explicitly() {
        let err = MemoryBudget::check_allocation("test", usize::MAX, 1, usize::MAX).unwrap_err();
        assert_eq!(err.code(), 6);
        let msg = format!("{}", err);
        assert!(
            msg.contains("integer overflow"),
            "Overflow error should mention 'integer overflow', got: {}",
            msg
        );
    }

    /// Regression: overflow with both operands large should also be caught.
    #[test]
    fn test_check_allocation_double_large_overflow() {
        let half = usize::MAX / 2 + 1;
        let err = MemoryBudget::check_allocation("stage", half, half, usize::MAX).unwrap_err();
        assert_eq!(err.code(), 6);
        assert!(format!("{}", err).contains("integer overflow"));
    }
}
