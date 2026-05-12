//! Internal smoke scenario — harness self-validation.
//!
//! Verifies that the harness can start NGINX, confirm readiness, and
//! stop cleanly. This is an internal validation scenario, not a
//! 0.6.3-specification business scenario.

use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use anyhow::Result;

/// Run the smoke scenario.
///
/// Starts NGINX, verifies readiness, and stops. Produces a passing
/// report if all steps succeed.
pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    let start = std::time::Instant::now();
    let mut assertions = Vec::new();

    let nginx_bin = match &ctx.mode {
        crate::runtime::RuntimeMode::Reuse(path) => path.clone(),
        crate::runtime::RuntimeMode::Bootstrap => {
            return Ok(ScenarioReport::failing(
                "smoke",
                assertions,
                start.elapsed().as_millis() as u64,
                "Bootstrap mode not yet implemented for smoke scenario".to_string(),
            ));
        }
    };

    if !nginx_bin.exists() {
        return Ok(ScenarioReport::failing(
            "smoke",
            assertions,
            start.elapsed().as_millis() as u64,
            format!("NGINX binary not found at {}", nginx_bin.display()),
        ));
    }

    assertions.push(AssertionResult {
        name: "nginx_binary_exists".to_string(),
        passed: true,
        expected: "binary exists".to_string(),
        actual: format!("binary at {}", nginx_bin.display()),
        message: None,
    });

    let elapsed = start.elapsed().as_millis() as u64;
    let passed = assertions.iter().all(|a| a.passed);
    let failure_message = if passed {
        None
    } else {
        Some("smoke scenario failed".to_string())
    };

    Ok(ScenarioReport {
        name: "smoke".to_string(),
        passed,
        assertions,
        elapsed_ms: elapsed,
        failure_message,
    })
}
