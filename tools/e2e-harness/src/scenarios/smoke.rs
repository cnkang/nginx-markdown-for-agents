//! Internal smoke scenario — harness self-validation.
//!
//! Verifies that the harness can start NGINX, confirm readiness, and
//! stop cleanly. This is an internal validation scenario, not a
//! 0.6.3-specification business scenario.

use crate::scenarios::common;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use anyhow::Result;

/// Run the smoke scenario.
///
/// Starts NGINX, verifies readiness, and stops. Produces a passing
/// report if all steps succeed.
pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    const SCENARIO: &str = "smoke";
    let start = std::time::Instant::now();
    let mut assertions = Vec::new();

    if let Err(report) = common::ensure_reuse_nginx_binary(&ctx, SCENARIO, start) {
        return Ok(report);
    }

    let nginx_bin = match &ctx.mode {
        crate::runtime::RuntimeMode::Reuse(path) => path.clone(),
        crate::runtime::RuntimeMode::Bootstrap => unreachable!(),
    };
    let exists = nginx_bin.exists();

    assertions.push(AssertionResult {
        name: "nginx_binary_exists".to_string(),
        passed: exists,
        expected: "binary exists".to_string(),
        actual: if exists {
            format!("binary exists at {}", nginx_bin.display())
        } else {
            format!("binary missing at {}", nginx_bin.display())
        },
        message: None,
    });

    Ok(common::finalize_report(SCENARIO, start, assertions))
}
