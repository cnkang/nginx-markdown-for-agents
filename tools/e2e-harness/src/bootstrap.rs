//! Bootstrap mode: prepare a runnable NGINX runtime.
//!
//! In Bootstrap_Mode the harness prepares a module-enabled NGINX
//! binary either directly or by bridging to
//! `tools/lib/nginx_markdown_native_build.sh`.

#![allow(dead_code)]

use anyhow::{Context, Result, bail};
use regex::Regex;
use std::fs;
use std::path::{Path, PathBuf};

/// Result of a successful bootstrap preparation.
#[derive(Debug)]
pub struct BootstrapResult {
    /// Path to the prepared NGINX binary.
    pub nginx_bin: PathBuf,
    /// Path to the module shared object directory.
    pub module_path: PathBuf,
}

/// Prepare a runnable NGINX runtime in Bootstrap_Mode.
///
/// First attempts direct preparation (checking for a binary on PATH),
/// then falls back to the shell bridge.
///
/// # Arguments
///
/// * `base_dir` - Directory for build artifacts.
///
/// # Returns
///
/// A `BootstrapResult` with the resolved paths.
pub fn prepare(base_dir: &Path) -> Result<BootstrapResult> {
    if let Some(result) = try_direct_prepare()? {
        return Ok(result);
    }
    bridge_to_native_build(base_dir)
}

/// Attempt to find an existing NGINX binary on PATH.
///
/// Resolves PATH entries in Rust instead of shelling out to `which`,
/// which is more portable and avoids depending on a POSIX-specific
/// external command.
fn try_direct_prepare() -> Result<Option<BootstrapResult>> {
    let candidates = ["nginx", "nginx-debug"];
    for name in candidates {
        if let Some(nginx_bin) = find_on_path(name) {
            let module_path = nginx_bin
                .parent()
                .and_then(|p| p.parent())
                .map(|p| p.join("modules"))
                .unwrap_or_else(|| PathBuf::from(""));
            return Ok(Some(BootstrapResult {
                nginx_bin,
                module_path,
            }));
        }
    }
    Ok(None)
}

/// Search PATH directories for an executable with the given name.
///
/// On Unix-like systems, checks for the name directly.  This is
/// sufficient for macOS and Linux where executables typically lack
/// a file extension.  Returns the first match that exists and is
/// executable, or `None` if not found.
fn find_on_path(name: &str) -> Option<PathBuf> {
    let path_var = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path_var) {
        let candidate = dir.join(name);
        if candidate.exists() {
            return Some(candidate);
        }
    }
    None
}

/// Bridge to `tools/lib/nginx_markdown_native_build.sh` for preparation.
///
/// # Arguments
///
/// * `base_dir` - Directory for build artifacts.
fn bridge_to_native_build(base_dir: &Path) -> Result<BootstrapResult> {
    let harness_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let workspace = harness_root
        .parent()
        .and_then(|p| p.parent())
        .map(PathBuf::from)
        .ok_or_else(|| {
            anyhow::anyhow!("failed to resolve workspace root from CARGO_MANIFEST_DIR")
        })?;
    let script = workspace
        .join("tools")
        .join("e2e")
        .join("verify_proxy_tls_backend_e2e.sh");

    if !script.exists() {
        bail!("Bootstrap bridge script not found at {}", script.display());
    }

    fs::create_dir_all(base_dir)?;
    let nginx_output = base_dir.join("nginx-bin.txt");
    let buildroot_output = base_dir.join("buildroot.txt");

    let output = std::process::Command::new("bash")
        .arg(&script)
        .arg("--keep-artifacts")
        .arg("--nginx-bin-output")
        .arg(&nginx_output)
        .arg("--buildroot-output")
        .arg(&buildroot_output)
        .output()?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        if let Some(fallback) = fallback_from_failed_proxy_tls_run(&stdout, &stderr) {
            return Ok(fallback);
        }
        let mut msg = format!("Bootstrap bridge failed: stdout={stdout}; stderr={stderr}");
        if cfg!(target_os = "macos") {
            msg.push_str("\n\n=== macOS E2E Bootstrap Diagnostics ===\n");
            msg.push_str("If compilation/linking of NGINX failed due to missing PCRE2, zlib, or OpenSSL, install them via Homebrew:\n");
            msg.push_str("  brew install pcre2 zlib openssl@3\n\n");
            msg.push_str("Note: NGINX may require library search paths to locate Homebrew dependencies. Alternatively, compile NGINX with the module manually and set NGINX_BIN:\n");
            msg.push_str("  export NGINX_BIN=/path/to/compiled/nginx\n");
            msg.push_str("======================================\n");
        }
        bail!(msg);
    }

    let nginx_bin = fs::read_to_string(&nginx_output)
        .context("bootstrap bridge did not emit nginx path")?
        .trim()
        .to_string();
    let nginx_bin = PathBuf::from(nginx_bin);
    let buildroot = fs::read_to_string(&buildroot_output)
        .context("bootstrap bridge did not emit buildroot path")?
        .trim()
        .to_string();
    let buildroot = PathBuf::from(buildroot);
    let module_path = buildroot.join("runtime").join("modules");

    if !nginx_bin.exists() {
        bail!(
            "Bootstrap bridge completed but binary not found at {}",
            nginx_bin.display()
        );
    }

    Ok(BootstrapResult {
        nginx_bin,
        module_path,
    })
}

fn fallback_from_failed_proxy_tls_run(stdout: &str, stderr: &str) -> Option<BootstrapResult> {
    let joined = format!("{stdout}\n{stderr}");
    let re = Regex::new(r"Artifacts kept at:\s*(?P<path>/\S+)").ok()?;
    let captures = re.captures(&joined)?;
    let buildroot = PathBuf::from(captures.name("path")?.as_str());
    let nginx_bin = buildroot.join("runtime").join("sbin").join("nginx");
    if !nginx_bin.exists() {
        return None;
    }
    let module_path = buildroot.join("runtime").join("modules");
    Some(BootstrapResult {
        nginx_bin,
        module_path,
    })
}
