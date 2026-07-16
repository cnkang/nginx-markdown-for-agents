//! CLI parser, flag definitions, and top-level dispatch.
//!
//! Parses subcommands and flags, resolves defaults, and dispatches to the
//! appropriate execution path.  The `NGINX_BIN` environment variable is
//! treated as equivalent to `--nginx-bin` when the flag is absent.

use anyhow::{Context, Result, bail};
use clap::{CommandFactory, Parser, Subcommand, ValueEnum};
use std::path::PathBuf;
use std::time::Duration;

/// Execution profile controlling which scenario cases are run.
#[derive(Clone, Debug, Default, ValueEnum)]
pub enum Profile {
    /// Run a minimal set of fast smoke cases.
    #[default]
    Smoke,
    /// Run the full scenario suite.
    Full,
    /// Run extended stress cases.
    Stress,
}

/// Top-level CLI for the E2E harness.
#[derive(Parser, Debug, Clone)]
#[command(
    name = "e2e-harness",
    about = "Rust-first E2E test harness for nginx-markdown-for-agents",
    version
)]
pub struct Cli {
    /// Path to a module-enabled NGINX binary (activates Reuse_Mode).
    /// Equivalent to the NGINX_BIN environment variable.
    #[arg(long, env = "NGINX_BIN", global = true)]
    pub nginx_bin: Option<PathBuf>,

    /// Port for the NGINX listener (default: dynamic allocation).
    #[arg(long, global = true)]
    pub port: Option<u16>,

    /// Port for the upstream fixture backend (default: dynamic allocation).
    #[arg(long, global = true)]
    pub upstream_port: Option<u16>,

    /// Execution profile.
    #[arg(long, default_value = "smoke", global = true)]
    pub profile: Profile,

    /// Write a machine-readable JSON report to this path.
    #[arg(long, global = true)]
    pub json_report: Option<PathBuf>,

    /// Retain temporary runtime configuration and artifacts after the run.
    #[arg(long, default_value_t = false, global = true)]
    pub keep_artifacts: bool,

    /// Per-scenario timeout in seconds (default: 60).
    #[arg(long, default_value_t = 60, global = true)]
    pub timeout: u64,

    /// List registered scenarios and exit.
    #[arg(long, global = true)]
    pub list: bool,

    #[command(subcommand)]
    pub command: Option<Command>,
}

/// Subcommands supported by the harness.
#[derive(Subcommand, Debug, Clone)]
pub enum Command {
    /// Run all registered scenarios for the selected profile.
    Suite,
    /// Run a single named scenario.
    Scenario {
        /// Name of the scenario to run (e.g. `conditional-requests`).
        name: String,
    },
}

/// Parse CLI arguments and dispatch to the appropriate execution path.
///
/// # Returns
///
/// `Ok(())` when all executed scenarios pass; an error otherwise.
pub fn run() -> Result<()> {
    let cli = Cli::parse();

    if cli.list {
        return list_scenarios();
    }

    let timeout = Duration::from_secs(cli.timeout);

    match &cli.command {
        Some(Command::Suite) => run_suite(&cli, timeout),
        Some(Command::Scenario { name }) => run_scenario(&cli, name, timeout),
        None => {
            let mut cmd = Cli::command();
            cmd.print_help()?;
            println!();
            Ok(())
        }
    }
}

/// Print all registered scenario names to stdout and return.
fn list_scenarios() -> Result<()> {
    let names = crate::scenarios::registered_names();
    for name in &names {
        println!("{name}");
    }
    Ok(())
}

/// Run all registered scenarios for the selected profile.
///
/// # Arguments
///
/// * `cli` - Parsed CLI arguments.
/// * `timeout` - Per-scenario timeout.
fn run_suite(cli: &Cli, timeout: Duration) -> Result<()> {
    let resolved_cli = resolve_cli_nginx_bin(cli)?;
    let names = crate::scenarios::registered_names();
    let mut all_passed = true;

    for name in &names {
        let passed = run_scenario_inner(&resolved_cli, name, timeout)?;
        if !passed {
            all_passed = false;
        }
    }

    if !all_passed {
        anyhow::bail!("One or more scenarios failed");
    }
    Ok(())
}

/// Run a single named scenario.
///
/// # Arguments
///
/// * `cli` - Parsed CLI arguments.
/// * `name` - Scenario name to look up in the registry.
/// * `timeout` - Per-scenario timeout.
fn run_scenario(cli: &Cli, name: &str, timeout: Duration) -> Result<()> {
    let resolved_cli = resolve_cli_nginx_bin(cli)?;
    let passed = run_scenario_inner(&resolved_cli, name, timeout)?;
    if !passed {
        anyhow::bail!("Scenario '{name}' failed");
    }
    Ok(())
}

/// Inner helper: run one scenario and return whether it passed.
///
/// Handles JSON report writing and artifact retention.
///
/// # Arguments
///
/// * `cli` - Parsed CLI arguments.
/// * `name` - Scenario name.
/// * `timeout` - Per-scenario timeout.
///
/// # Returns
///
/// `true` if the scenario passed, `false` otherwise.
fn run_scenario_inner(cli: &Cli, name: &str, timeout: Duration) -> Result<bool> {
    use crate::fixtures::FixtureSpec;
    use crate::runtime::{RuntimeMode, ScenarioRuntime};
    use crate::scenarios::ScenarioContext;

    let mode = RuntimeMode::from_cli(cli.nginx_bin.clone());
    let nginx_bin = mode
        .nginx_bin()
        .context("resolved runtime mode does not include an nginx binary")?
        .to_path_buf();
    let ctx = ScenarioContext::new(
        name,
        mode,
        cli.port,
        cli.upstream_port,
        cli.profile.clone(),
        cli.keep_artifacts,
        timeout,
    )?;

    let runtime_base = std::env::temp_dir().join(format!(
        "e2e-harness-{name}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis()
    ));
    let runtime = ScenarioRuntime::prepare(
        name,
        &runtime_base,
        nginx_bin.clone(),
        ctx.port,
        ctx.upstream_port,
    )?;
    crate::artifacts::write_invocation_json(
        &runtime.scenario_artifact_dir,
        name,
        &nginx_bin,
        ctx.port,
        ctx.upstream_port,
    )?;

    let tokio_rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(1)
        .enable_all()
        .build()
        .context("failed to create tokio runtime for fixture backend")?;
    let fixture_spec = FixtureSpec {
        listen_port: Some(ctx.upstream_port),
        routes: Vec::new(),
    };
    let mut fixture = tokio_rt
        .block_on(crate::fixtures::http_backend::start_fixture(fixture_spec))
        .context("failed to start fixture backend")?;

    let mut nginx = crate::process::NginxProcess::start(
        &runtime.nginx_bin,
        &runtime.runtime_dir,
        &runtime.nginx_conf(),
        &runtime.base_url,
        timeout,
    )
    .context("failed to start nginx process")?;

    let report = crate::scenarios::run_named(name, ctx)?;

    let _ = nginx.stop_graceful(timeout);
    tokio_rt.block_on(async {
        fixture.stop().await;
    });
    let _ = crate::artifacts::cleanup_artifacts(
        &runtime.artifact_dir,
        cli.keep_artifacts,
        report.passed,
    );
    if report.passed && !cli.keep_artifacts {
        let _ = std::fs::remove_dir_all(&runtime.runtime_dir);
    }

    if let Some(report_path) = &cli.json_report {
        crate::artifacts::append_report(report_path, &report)?;
    }

    if !report.passed
        && let Some(msg) = &report.failure_message
    {
        eprintln!("[FAIL] scenario={} assertion_failure={}", report.name, msg);
    }

    Ok(report.passed)
}

fn resolve_cli_nginx_bin(cli: &Cli) -> Result<Cli> {
    if cli.nginx_bin.is_some() {
        return Ok(cli.clone());
    }

    let bootstrap_dir = std::env::temp_dir().join(format!(
        "e2e-harness-bootstrap-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis()
    ));
    std::fs::create_dir_all(&bootstrap_dir)
        .with_context(|| format!("failed to create bootstrap dir {}", bootstrap_dir.display()))?;
    let prepared = crate::bootstrap::prepare(&bootstrap_dir).with_context(
        || "Bootstrap_Mode could not prepare a runnable module-enabled NGINX runtime",
    )?;
    if !prepared.nginx_bin.exists() {
        bail!(
            "bootstrap completed but nginx binary is missing at {}",
            prepared.nginx_bin.display()
        );
    }

    let mut resolved = cli.clone();
    resolved.nginx_bin = Some(prepared.nginx_bin);
    Ok(resolved)
}
