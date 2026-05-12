//! NGINX process lifecycle management.
//!
//! Handles starting, monitoring, and stopping NGINX child processes.
//! The `Drop` implementation ensures no orphan processes remain.

use anyhow::{Result, bail};
use std::path::Path;
use std::process::{Child, Command};
use std::time::{Duration, Instant};

/// Managed NGINX process handle.
///
/// On drop, the process is stopped gracefully (SIGTERM) then forcibly
/// (SIGKILL) if it does not exit within the timeout, ensuring no
/// orphan processes remain.
pub struct NginxProcess {
    child: Option<Child>,
    pid: u32,
    timeout: Duration,
}

impl NginxProcess {
    /// Start NGINX with the given config and wait for it to become ready.
    ///
    /// # Arguments
    ///
    /// * `nginx_bin` - Path to the NGINX binary.
    /// * `config_path` - Path to the `nginx.conf`.
    /// * `base_url` - Base URL for readiness probing.
    /// * `timeout` - Maximum time to wait for readiness.
    ///
    /// # Returns
    ///
    /// An `NginxProcess` handle on success.
    pub fn start(
        nginx_bin: &Path,
        config_path: &Path,
        base_url: &str,
        timeout: Duration,
    ) -> Result<Self> {
        let mut child = Command::new(nginx_bin)
            .arg("-c")
            .arg(config_path)
            .arg("-g")
            .arg("daemon off;")
            .spawn()?;

        let pid = child.id();
        wait_ready_with_child(&mut child, base_url, timeout)?;
        Ok(NginxProcess {
            child: Some(child),
            pid,
            timeout,
        })
    }

    /// Wait for NGINX to become ready by probing the base URL.
    ///
    /// Readiness is defined as the HTTP server accepting connections
    /// and returning any response (2xx, 3xx, 4xx, or even 5xx).
    ///
    /// # Arguments
    ///
    /// * `base_url` - URL to probe.
    /// * `timeout` - Maximum wait duration.
    pub fn wait_ready(&self, base_url: &str, timeout: Duration) -> Result<()> {
        // Keep the method for external callers and tests.
        let mut no_child = Command::new("true").spawn()?;
        wait_ready_with_child(&mut no_child, base_url, timeout)
    }
}

fn wait_ready_with_child(child: &mut Child, base_url: &str, timeout: Duration) -> Result<()> {
    let start = Instant::now();
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()?;

    loop {
        if let Ok(Some(status)) = child.try_wait() {
            bail!("NGINX exited before readiness (status: {status}) while probing {base_url}");
        }
        match client.get(base_url).send() {
            Ok(_) => return Ok(()),
            Err(_) => {
                if start.elapsed() > timeout {
                    bail!(
                        "NGINX did not become ready within {:?} while probing {}",
                        timeout,
                        base_url
                    );
                }
                std::thread::sleep(Duration::from_millis(100));
            }
        }
    }
}

impl NginxProcess {
    /// Stop NGINX gracefully (SIGTERM) and wait for exit.
    ///
    /// # Arguments
    ///
    /// * `timeout` - Maximum time to wait after SIGTERM before SIGKILL.
    pub fn stop_graceful(&mut self, timeout: Duration) -> Result<()> {
        if let Some(child) = self.child.as_mut() {
            #[cfg(unix)]
            {
                let _ = Command::new("kill")
                    .arg("-TERM")
                    .arg(self.pid.to_string())
                    .status();
            }
            #[cfg(not(unix))]
            {
                let _ = child.kill();
            }

            let start = Instant::now();
            loop {
                match child.try_wait() {
                    Ok(Some(_status)) => {
                        self.child = None;
                        return Ok(());
                    }
                    Ok(None) => {
                        if start.elapsed() > timeout {
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(50));
                    }
                    Err(e) => bail!("Error waiting for NGINX exit: {e}"),
                }
            }
        }
        self.stop_force()
    }

    /// Force-stop NGINX with SIGKILL.
    pub fn stop_force(&mut self) -> Result<()> {
        if let Some(child) = self.child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
            self.child = None;
        }
        Ok(())
    }

    /// Best-effort confirmation that the process has exited.
    pub fn confirm_exited(&mut self) -> bool {
        if let Some(child) = self.child.as_mut() {
            matches!(child.try_wait(), Ok(Some(_)))
        } else {
            true
        }
    }
}

impl Drop for NginxProcess {
    fn drop(&mut self) {
        let _ = self.stop_graceful(Duration::from_secs(5));
        if self.child.is_some() {
            let _ = self.stop_force();
        }
    }
}
