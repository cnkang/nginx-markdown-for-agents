//! E2E Harness — CLI entrypoint and top-level error handling.
//!
//! This binary is the `Rust_E2E_Runner` described in the 0.6.3 design.
//! It supports three top-level commands:
//!
//! ```text
//! e2e-harness suite
//! e2e-harness scenario <name>
//! e2e-harness --list
//! ```
//!
//! See `cli.rs` for the full flag surface and dispatch logic.

mod artifacts;
mod assertions;
mod bootstrap;
mod cli;
mod fixtures;
mod http;
mod process;
mod runtime;
mod scenarios;

use anyhow::Result;

fn main() -> Result<()> {
    cli::run()
}
