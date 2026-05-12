//! Scenario registry and core types.
//!
//! Provides `ScenarioContext`, `ScenarioReport`, `AssertionResult`, and the
//! global scenario registry used by `cli.rs` for dispatch.

pub mod accept_negotiation;
pub mod auth_cache;
pub mod conditional_requests;
pub mod metrics_endpoint;
pub mod smoke;
pub mod status_codes;

use crate::cli::Profile;
use crate::runtime::RuntimeMode;
use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::time::Duration;

/// Context provided to every scenario at execution time.
#[derive(Debug)]
pub struct ScenarioContext {
    /// Scenario name (registry key).
    pub name: String,
    /// Resolved runtime mode.
    pub mode: RuntimeMode,
    /// NGINX listener port (explicit or dynamically allocated).
    pub port: u16,
    /// Upstream fixture port (explicit or dynamically allocated).
    pub upstream_port: u16,
    /// Execution profile.
    pub profile: Profile,
    /// Whether to retain artifacts on success.
    pub keep_artifacts: bool,
    /// Per-scenario timeout.
    pub timeout: Duration,
}

impl ScenarioContext {
    /// Create a new scenario context.
    ///
    /// # Arguments
    ///
    /// * `name` - Scenario name.
    /// * `mode` - Runtime mode (Reuse or Bootstrap).
    /// * `port` - Optional explicit NGINX port.
    /// * `upstream_port` - Optional explicit upstream port.
    /// * `profile` - Execution profile.
    /// * `keep_artifacts` - Artifact retention flag.
    /// * `timeout` - Per-scenario timeout.
    ///
    /// # Returns
    ///
    /// A `ScenarioContext` with ports allocated dynamically when not
    /// explicitly provided.
    pub fn new(
        name: &str,
        mode: RuntimeMode,
        port: Option<u16>,
        upstream_port: Option<u16>,
        profile: Profile,
        keep_artifacts: bool,
        timeout: Duration,
    ) -> Result<Self> {
        let port = port.unwrap_or_else(|| allocate_ephemeral_port_or_fallback(30080));
        let upstream_port =
            upstream_port.unwrap_or_else(|| allocate_ephemeral_port_or_fallback(31080));
        Ok(ScenarioContext {
            name: name.to_string(),
            mode,
            port,
            upstream_port,
            profile,
            keep_artifacts,
            timeout,
        })
    }
}

/// Allocate an ephemeral port by binding to port 0 and reading the assigned port.
///
/// The socket is immediately closed so the port is available for reuse.
fn allocate_ephemeral_port_or_fallback(base: u16) -> u16 {
    use std::net::TcpListener;
    match TcpListener::bind("127.0.0.1:0") {
        Ok(listener) => listener
            .local_addr()
            .map(|addr| addr.port())
            .unwrap_or(base),
        Err(_) => {
            let pid_offset = (std::process::id() % 1000) as u16;
            base.saturating_add(pid_offset)
        }
    }
}

/// Result of a single assertion within a scenario.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AssertionResult {
    /// Short name identifying the assertion.
    pub name: String,
    /// Whether the assertion passed.
    pub passed: bool,
    /// Human-readable expected value.
    pub expected: String,
    /// Human-readable actual value.
    pub actual: String,
    /// Optional diagnostic message on failure.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

/// Report produced by a completed scenario.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ScenarioReport {
    /// Scenario name.
    pub name: String,
    /// Whether the overall scenario passed.
    pub passed: bool,
    /// Individual assertion results.
    pub assertions: Vec<AssertionResult>,
    /// Elapsed wall-clock time in milliseconds.
    pub elapsed_ms: u64,
    /// Aggregate failure message (first failure summary).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub failure_message: Option<String>,
}

impl ScenarioReport {
    /// Create a passing report with the given name and assertions.
    pub fn passing(name: &str, assertions: Vec<AssertionResult>, elapsed_ms: u64) -> Self {
        ScenarioReport {
            name: name.to_string(),
            passed: true,
            assertions,
            elapsed_ms,
            failure_message: None,
        }
    }

    /// Create a failing report with the given name, assertions, and message.
    pub fn failing(
        name: &str,
        assertions: Vec<AssertionResult>,
        elapsed_ms: u64,
        message: String,
    ) -> Self {
        ScenarioReport {
            name: name.to_string(),
            passed: false,
            assertions,
            elapsed_ms,
            failure_message: Some(message),
        }
    }
}

/// Type alias for a scenario entry point function.
type ScenarioFn = fn(ScenarioContext) -> Result<ScenarioReport>;

/// A registered scenario.
struct ScenarioEntry {
    name: &'static str,
    run: ScenarioFn,
}

/// Global scenario registry.
static SCENARIOS: &[ScenarioEntry] = &[
    ScenarioEntry {
        name: "smoke",
        run: smoke::run,
    },
    ScenarioEntry {
        name: "conditional-requests",
        run: conditional_requests::run,
    },
    ScenarioEntry {
        name: "metrics-endpoint",
        run: metrics_endpoint::run,
    },
    ScenarioEntry {
        name: "status-codes",
        run: status_codes::run,
    },
    ScenarioEntry {
        name: "auth-cache",
        run: auth_cache::run,
    },
    ScenarioEntry {
        name: "accept-negotiation",
        run: accept_negotiation::run,
    },
];

/// Return the names of all registered scenarios in registration order.
pub fn registered_names() -> Vec<&'static str> {
    SCENARIOS.iter().map(|e| e.name).collect()
}

/// Run a named scenario by looking it up in the registry.
///
/// # Arguments
///
/// * `name` - Scenario name to look up.
/// * `ctx` - Scenario context.
///
/// # Returns
///
/// The scenario report on success, or an error if the name is not found
/// or the scenario execution fails.
pub fn run_named(name: &str, ctx: ScenarioContext) -> Result<ScenarioReport> {
    let entry = SCENARIOS
        .iter()
        .find(|e| e.name == name)
        .ok_or_else(|| anyhow::anyhow!("Unknown scenario: '{name}'"))?;
    (entry.run)(ctx)
}
