//! Runtime directory preparation and scenario environment handles.
//!
//! Responsible for:
//! - creating an isolated runtime directory per scenario invocation;
//! - writing scenario-specific `nginx.conf`;
//! - creating log paths;
//! - exposing the scenario base URL and runtime handles.
//!
//! The module deliberately does not assume a single `load_module` layout.
//! In Reuse_Mode the harness adapts to the supplied binary; in Bootstrap_Mode
//! the harness prepares the layout through `bootstrap.rs`.

#![allow(dead_code)]

use anyhow::{Context, Result};
use std::path::{Path, PathBuf};

/// Execution mode for the harness.
#[derive(Clone, Debug)]
pub enum RuntimeMode {
    /// Reuse a prebuilt module-enabled NGINX binary.
    Reuse(PathBuf),
    /// Prepare a runnable runtime (directly or via bootstrap).
    Bootstrap,
}

impl RuntimeMode {
    /// Determine the runtime mode from an optional binary path.
    ///
    /// # Arguments
    ///
    /// * `nginx_bin` - Path supplied via `--nginx-bin` or `NGINX_BIN`.
    ///
    /// # Returns
    ///
    /// `Reuse` when a path is provided; `Bootstrap` otherwise.
    pub fn from_cli(nginx_bin: Option<PathBuf>) -> Self {
        match nginx_bin {
            Some(path) => RuntimeMode::Reuse(path),
            None => RuntimeMode::Bootstrap,
        }
    }

    /// Return the NGINX binary path if this is Reuse_Mode.
    pub fn nginx_bin(&self) -> Option<&Path> {
        match self {
            RuntimeMode::Reuse(p) => Some(p.as_path()),
            RuntimeMode::Bootstrap => None,
        }
    }

    /// Return `true` when operating in Reuse_Mode.
    pub fn is_reuse(&self) -> bool {
        matches!(self, RuntimeMode::Reuse(_))
    }
}

/// A prepared scenario runtime environment.
///
/// Holds paths and handles needed by scenario code without requiring
/// scenarios to rediscover global paths or reconstruct URLs ad hoc.
#[derive(Debug)]
pub struct ScenarioRuntime {
    /// Root directory for this scenario's runtime files.
    pub runtime_dir: PathBuf,
    /// Directory for scenario artifacts (logs, captures, reports).
    pub artifact_dir: PathBuf,
    /// Per-scenario artifact subdirectory.
    pub scenario_artifact_dir: PathBuf,
    /// Base URL for the NGINX listener (e.g. `http://127.0.0.1:8080`).
    pub base_url: String,
    /// Port the NGINX listener is bound to.
    pub port: u16,
    /// Port the upstream fixture backend is bound to.
    pub upstream_port: u16,
    /// Path to the NGINX binary (resolved from mode).
    pub nginx_bin: PathBuf,
}

impl ScenarioRuntime {
    /// Prepare a scenario runtime under `base_dir`.
    ///
    /// Creates the runtime directory tree and writes a minimal `nginx.conf`
    /// stub.  Actual NGINX startup is handled by `process.rs`.
    ///
    /// # Arguments
    ///
    /// * `scenario_name` - Used to name the scenario artifact subdirectory.
    /// * `base_dir` - Parent directory for the runtime tree.
    /// * `nginx_bin` - Resolved NGINX binary path.
    /// * `port` - NGINX listener port.
    /// * `upstream_port` - Upstream fixture port.
    ///
    /// # Returns
    ///
    /// A fully prepared `ScenarioRuntime` on success.
    pub fn prepare(
        scenario_name: &str,
        base_dir: &Path,
        nginx_bin: PathBuf,
        port: u16,
        upstream_port: u16,
    ) -> Result<Self> {
        let runtime_dir = base_dir.join("runtime");
        let artifact_dir = base_dir.join("artifacts");
        let scenario_artifact_dir = artifact_dir.join("scenarios").join(scenario_name);

        std::fs::create_dir_all(&runtime_dir)?;
        std::fs::create_dir_all(runtime_dir.join("logs"))?;
        std::fs::create_dir_all(&scenario_artifact_dir)?;

        // Write a minimal nginx.conf stub; scenarios may override this.
        let conf_path = runtime_dir.join("nginx.conf");
        write_nginx_conf_stub(&conf_path, scenario_name, &nginx_bin, port, upstream_port)?;

        let base_url = format!("http://127.0.0.1:{port}");

        Ok(ScenarioRuntime {
            runtime_dir,
            artifact_dir,
            scenario_artifact_dir,
            base_url,
            port,
            upstream_port,
            nginx_bin,
        })
    }

    /// Path to the generated `nginx.conf`.
    pub fn nginx_conf(&self) -> PathBuf {
        self.runtime_dir.join("nginx.conf")
    }

    /// Path to the NGINX error log.
    pub fn error_log(&self) -> PathBuf {
        self.runtime_dir.join("nginx-error.log")
    }

    /// Path to the NGINX access log.
    pub fn access_log(&self) -> PathBuf {
        self.runtime_dir.join("nginx-access.log")
    }
}

/// Write an `nginx.conf` to `path`.
///
/// For the `brotli-streaming` scenario, produces a full multi-location
/// config that loads the module .so and configures Brotli streaming
/// routes.  For all other scenarios, writes a minimal stub that the
/// scenario setup code layers additional configuration on top of.
///
/// # Arguments
///
/// * `path` - Destination path for the config file.
/// * `scenario_name` - Scenario name used to select config layout.
/// * `nginx_bin` - NGINX binary path (used to locate module .so).
/// * `port` - NGINX listener port.
/// * `upstream_port` - Upstream fixture port.
fn write_nginx_conf_stub(
    path: &Path,
    scenario_name: &str,
    nginx_bin: &Path,
    port: u16,
    upstream_port: u16,
) -> Result<()> {
    let runtime_dir = path
        .parent()
        .context("nginx.conf path does not have a parent runtime directory")?;
    let error_log = runtime_dir.join("nginx-error.log");
    let access_log = runtime_dir.join("nginx-access.log");
    let pid_path = runtime_dir.join("nginx.pid");
    let content = if scenario_name == "brotli-streaming" {
        brotli_nginx_conf(
            &error_log,
            &access_log,
            &pid_path,
            nginx_bin,
            port,
            upstream_port,
        )
    } else {
        format!(
            "# Generated by e2e-harness — do not edit manually\n\
worker_processes 1;\n\
error_log {} info;\n\
pid {};\n\
events {{ worker_connections 64; }}\n\
http {{\n\
    access_log {};\n\
    upstream fixture_backend {{\n\
        server 127.0.0.1:{upstream_port};\n\
    }}\n\
    server {{\n\
        listen {port};\n\
        location / {{\n\
            proxy_pass http://fixture_backend;\n\
        }}\n\
    }}\n\
}}\n",
            error_log.display(),
            pid_path.display(),
            access_log.display(),
        )
    };
    std::fs::write(path, content)?;
    Ok(())
}

fn brotli_nginx_conf(
    error_log: &Path,
    access_log: &Path,
    pid_path: &Path,
    nginx_bin: &Path,
    port: u16,
    upstream_port: u16,
) -> String {
    let load_module = find_module(nginx_bin)
        .map(|path| format!("load_module {};\n", path.display()))
        .unwrap_or_default();
    format!(
        "{load_module}worker_processes 1;\n\
error_log {} info;\n\
pid {};\n\
events {{ worker_connections 1024; }}\n\
http {{\n\
    access_log {};\n\
    upstream fixture_backend {{ server 127.0.0.1:{upstream_port}; }}\n\
    server {{\n\
        listen 127.0.0.1:{port};\n\
        location /streaming/ {{\n\
            markdown_filter on;\n\
            markdown_accept wildcard;\n\
            markdown_profile streaming_first;\n\
            markdown_streaming force;\n\
            markdown_auto_decompress on;\n\
            markdown_cache_validation off;\n\
            markdown_limits memory=64m streaming_buffer=64m timeout=120s;\n\
            markdown_stream_precommit_buffer 16m;\n\
            markdown_error_policy pass;\n\
            markdown_log_verbosity info;\n\
            proxy_http_version 1.1;\n\
            proxy_buffering off;\n\
            proxy_set_header Connection \"\";\n\
            proxy_pass http://fixture_backend/;\n\
        }}\n\
        location /cache-full/ {{\n\
            markdown_filter on;\n\
            markdown_accept wildcard;\n\
            markdown_profile balanced;\n\
            markdown_streaming off;\n\
            markdown_auto_decompress on;\n\
            markdown_cache_validation full;\n\
            markdown_limits memory=64m timeout=120s;\n\
            markdown_error_policy pass;\n\
            proxy_pass http://fixture_backend/;\n\
        }}\n\
        location /non-streaming/ {{\n\
            markdown_filter on;\n\
            markdown_accept wildcard;\n\
            markdown_profile balanced;\n\
            markdown_streaming off;\n\
            markdown_auto_decompress on;\n\
            markdown_cache_validation off;\n\
            markdown_limits memory=64m timeout=120s;\n\
            markdown_error_policy pass;\n\
            proxy_pass http://fixture_backend/;\n\
        }}\n\
        location /auto-decompress-off/ {{\n\
            markdown_filter on;\n\
            markdown_accept wildcard;\n\
            markdown_streaming force;\n\
            markdown_auto_decompress off;\n\
            markdown_error_policy pass;\n\
            proxy_pass http://fixture_backend/;\n\
        }}\n\
        location /tight-budget/ {{\n\
            markdown_filter on;\n\
            markdown_accept wildcard;\n\
            markdown_streaming force;\n\
            markdown_auto_decompress on;\n\
            markdown_cache_validation off;\n\
            markdown_limits memory=64m streaming_buffer=64m timeout=120s;\n\
            markdown_stream_precommit_buffer 16m;\n\
            markdown_decompress_max_size 1k;\n\
            markdown_error_policy pass;\n\
            proxy_pass http://fixture_backend/;\n\
        }}\n\
        location = /markdown-metrics {{ markdown_metrics; }}\n\
    }}\n\
}}\n",
        error_log.display(),
        pid_path.display(),
        access_log.display(),
    )
}

fn find_module(nginx_bin: &Path) -> Option<PathBuf> {
    let bin_dir = nginx_bin.parent()?;
    let sibling = bin_dir.join("ngx_http_markdown_filter_module.so");
    if sibling.is_file() {
        return Some(sibling);
    }
    let lib = bin_dir
        .parent()
        .map(|prefix| prefix.join("lib/ngx_http_markdown_filter_module.so"))?;
    lib.is_file().then_some(lib)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn runtime_mode_from_cli_resolves_correctly() {
        let mode_some = RuntimeMode::from_cli(Some(PathBuf::from("/usr/bin/nginx")));
        assert!(mode_some.is_reuse());
        assert_eq!(mode_some.nginx_bin(), Some(Path::new("/usr/bin/nginx")));

        let mode_none = RuntimeMode::from_cli(None);
        assert!(!mode_none.is_reuse());
        assert!(matches!(mode_none, RuntimeMode::Bootstrap));
    }

    #[test]
    fn generated_nginx_conf_uses_runtime_log_and_pid_paths() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let conf_path = temp.path().join("nginx.conf");

        write_nginx_conf_stub(
            &conf_path,
            "smoke",
            Path::new("/opt/nginx/sbin/nginx"),
            18080,
            18081,
        )?;

        let content = std::fs::read_to_string(&conf_path)?;
        assert!(content.contains(&format!(
            "error_log {} info;",
            temp.path().join("nginx-error.log").display()
        )));
        assert!(content.contains(&format!("pid {};", temp.path().join("nginx.pid").display())));
        assert!(content.contains(&format!(
            "access_log {};",
            temp.path().join("nginx-access.log").display()
        )));
        assert!(content.contains("listen 18080;"));
        assert!(content.contains("server 127.0.0.1:18081;"));
        Ok(())
    }

    #[test]
    fn scenario_runtime_creates_nginx_prefix_log_directory() -> Result<()> {
        let temp = tempfile::tempdir()?;

        let runtime = ScenarioRuntime::prepare(
            "smoke",
            temp.path(),
            PathBuf::from("/opt/nginx/sbin/nginx"),
            18080,
            18081,
        )?;

        assert!(runtime.runtime_dir.join("logs").is_dir());
        Ok(())
    }
}
