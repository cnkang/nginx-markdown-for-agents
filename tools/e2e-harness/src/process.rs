//! NGINX process lifecycle management.
//!
//! Handles starting, monitoring, and stopping NGINX child processes.
//! The `Drop` implementation ensures no orphan processes remain.

#![allow(dead_code)]

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
    /// * `runtime_prefix` - Isolated prefix for NGINX runtime paths.
    /// * `config_path` - Path to the `nginx.conf`.
    /// * `base_url` - Base URL for readiness probing.
    /// * `timeout` - Maximum time to wait for readiness.
    ///
    /// # Returns
    ///
    /// An `NginxProcess` handle on success.
    pub fn start(
        nginx_bin: &Path,
        runtime_prefix: &Path,
        config_path: &Path,
        base_url: &str,
        timeout: Duration,
    ) -> Result<Self> {
        Self::start_command(
            build_nginx_command(nginx_bin, runtime_prefix, config_path),
            base_url,
            timeout,
        )
    }

    fn start_command(mut command: Command, base_url: &str, timeout: Duration) -> Result<Self> {
        let child = command.spawn()?;
        let pid = child.id();
        let mut process = NginxProcess {
            child: Some(child),
            pid,
            timeout,
        };

        wait_ready_with_child(process.child.as_mut(), base_url, timeout)?;
        Ok(process)
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
        wait_ready_with_child(None, base_url, timeout)
    }
}

fn build_nginx_command(nginx_bin: &Path, runtime_prefix: &Path, config_path: &Path) -> Command {
    let mut command = Command::new(nginx_bin);
    command
        .arg("-p")
        .arg(runtime_prefix)
        .arg("-c")
        .arg(config_path)
        .arg("-g")
        .arg("daemon off;");
    command
}

fn wait_ready_with_child(
    mut child: Option<&mut Child>,
    base_url: &str,
    timeout: Duration,
) -> Result<()> {
    let start = Instant::now();
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()?;

    loop {
        if let Some(ref mut c) = child
            && let Ok(Some(status)) = c.try_wait()
        {
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;

    #[cfg(unix)]
    unsafe extern "C" {
        fn kill(pid: i32, signal: i32) -> i32;
        fn waitpid(pid: i32, status: *mut i32, options: i32) -> i32;
    }

    #[cfg(unix)]
    struct ProcessCleanupGuard(Option<i32>);

    #[cfg(unix)]
    impl Drop for ProcessCleanupGuard {
        fn drop(&mut self) {
            if let Some(pid) = self.0 {
                unsafe {
                    kill(pid, 9);
                    waitpid(pid, std::ptr::null_mut(), 0);
                }
            }
        }
    }

    #[test]
    fn nginx_command_uses_isolated_runtime_prefix() {
        let command = build_nginx_command(
            Path::new("/opt/nginx/sbin/nginx"),
            Path::new("/tmp/e2e-runtime"),
            Path::new("/tmp/e2e-runtime/nginx.conf"),
        );
        let args: Vec<&OsStr> = command.get_args().collect();

        assert_eq!(
            args,
            [
                OsStr::new("-p"),
                OsStr::new("/tmp/e2e-runtime"),
                OsStr::new("-c"),
                OsStr::new("/tmp/e2e-runtime/nginx.conf"),
                OsStr::new("-g"),
                OsStr::new("daemon off;"),
            ]
        );
    }

    #[cfg(unix)]
    #[test]
    fn start_reaps_child_when_readiness_times_out() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let pid_file = temp.path().join("fake-nginx.pid");
        let mut command = Command::new("/bin/sh");
        command
            .arg("-c")
            .arg(format!("echo $$ > '{}'; exec sleep 30", pid_file.display()));

        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let port = listener.local_addr()?.port();
        drop(listener);

        let error = match NginxProcess::start_command(
            command,
            &format!("http://127.0.0.1:{port}/"),
            Duration::from_millis(150),
        ) {
            Ok(_) => panic!("fake NGINX unexpectedly passed readiness"),
            Err(error) => error,
        };
        assert!(
            error.to_string().contains("did not become ready"),
            "unexpected start failure: {error:#}"
        );

        let pid_deadline = Instant::now() + Duration::from_secs(1);
        while !pid_file.exists() && Instant::now() < pid_deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        let pid: i32 = std::fs::read_to_string(&pid_file)?.trim().parse()?;
        let mut cleanup = ProcessCleanupGuard(Some(pid));
        let wait_result = unsafe { waitpid(pid, std::ptr::null_mut(), 1) };
        assert_eq!(
            wait_result, -1,
            "child PID {pid} was still running or left as a zombie"
        );
        cleanup.0 = None;

        Ok(())
    }
}
