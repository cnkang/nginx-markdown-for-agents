//! Bootstrap mode: prepare a runnable NGINX runtime.
//!
//! In Bootstrap_Mode the harness prepares a module-enabled NGINX
//! binary either directly or by bridging to
//! `tools/lib/nginx_markdown_native_build.sh`.

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
fn try_direct_prepare() -> Result<Option<BootstrapResult>> {
    let candidates = ["nginx", "nginx-debug"];
    for name in candidates {
        if let Ok(output) = std::process::Command::new("which").arg(name).output()
            && output.status.success()
        {
            let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
            let nginx_bin = PathBuf::from(&path);
            if nginx_bin.exists() {
                let module_path = nginx_bin.parent().unwrap_or(Path::new("")).to_path_buf();
                return Ok(Some(BootstrapResult {
                    nginx_bin,
                    module_path,
                }));
            }
        }
    }
    Ok(None)
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
        bail!("Bootstrap bridge failed: stdout={stdout}; stderr={stderr}");
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
