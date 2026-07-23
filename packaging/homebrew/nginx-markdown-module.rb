class NginxMarkdownModule < Formula
  desc "NGINX module for HTML-to-Markdown conversion"
  homepage "https://github.com/cnkang/nginx-markdown-for-agents"
  # Checked-in Homebrew formula source.
  #
  # This file is the current-release fallback/template for the Homebrew tap
  # publish workflow. The authoritative installable formula lives in the tap
  # repository after a release: `homebrew-tap-publish.yml` rewrites both `url`
  # and `sha256` to the target release tag archive and pushes the result to the
  # tap, where it becomes the authoritative state users install from.
  # Do not update this file manually for unreleased development branches.
  #
  # The values below are kept pinned to the latest 0.8.x tag (a real, verified
  # url/sha256 pair) so the gate and post-release verify workflows operate on a
  # self-consistent source. They are NOT meant for direct `brew install` from
  # this checked-in path; install from the tap repository instead.
  url "https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.8.3.tar.gz"
  sha256 "dac472ba4016ab909cb08e3661fec1afb0358bf7fc02582aed2e1d369d82aedf"
  license "BSD-2-Clause"

  depends_on "cbindgen" => :build
  depends_on "nginx" => :build
  depends_on "pkgconf" => :build
  depends_on "brotli"
  depends_on "openssl@3"
  depends_on "pcre2"

  # The Rust toolchain is installed with the repository's checksum-verifying
  # rustup helper rather than the Homebrew `rust` formula, because the crate
  # MSRV can briefly exceed Homebrew's Rust version. The helper pins the
  # architecture-specific rustup-init bytes before execution.
  TOOLCHAIN_VERSION = "1.97.0".freeze

  def install
    rustup_home = "#{buildpath}/rustup"
    cargo_home = "#{buildpath}/cargo"
    ENV["RUSTUP_HOME"] = rustup_home
    ENV["CARGO_HOME"] = cargo_home
    rustup_arch = Hardware::CPU.arm? ? "arm64" : "amd64"
    system "bash",
           (buildpath/"packaging/scripts/install-verified-rustup.sh").to_s,
           "--os", "darwin",
           "--arch", rustup_arch,
           "--toolchain", TOOLCHAIN_VERSION,
           "--checksums", (buildpath/"packaging/checksums.sha256").to_s
    ENV.prepend_path "PATH", "#{cargo_home}/bin"

    # Enable Brotli streaming decompression explicitly so the official Homebrew
    # artifact does not rely on auto-detection alone.
    ENV["NGX_MARKDOWN_BROTLI_STREAMING"] = "on"

    system "make", "build"

    nginx_version = Formula["nginx"].version.to_s
    odie "Unable to detect Homebrew nginx version" if nginx_version.blank?

    nginx_archive = "nginx-#{nginx_version}.tar.gz"
    system "curl", "--proto", "=https", "--tlsv1.2", "-fsSL",
           "https://nginx.org/download/#{nginx_archive}",
           "-o", nginx_archive
    system "bash",
           (buildpath/"packaging/scripts/verify-checksum.sh").to_s,
           "-f", nginx_archive,
           "-i", "nginx-#{nginx_version}",
           "-c", (buildpath/"packaging/checksums.sha256").to_s
    system "tar", "-xzf", nginx_archive

    cd "nginx-#{nginx_version}" do
      args = [
        "--with-compat",
        "--add-dynamic-module=#{buildpath}/components/nginx-module",
        "--with-cc-opt=-I#{formula_opt_include("openssl@3")} " \
        "-I#{formula_opt_include("pcre2")} -I#{formula_opt_include("brotli")}",
        "--with-ld-opt=-L#{formula_opt_lib("openssl@3")} " \
        "-L#{formula_opt_lib("pcre2")} -L#{formula_opt_lib("brotli")}",
      ]
      system "./configure", *args
      system "make", "modules"
      (lib/"nginx/modules").install "objs/ngx_http_markdown_filter_module.so"
    end
  end

  def caveats
    <<~EOS
      This module requires NGINX to be built with the module loaded.
      Add to your nginx.conf:
        load_module #{opt_lib}/nginx/modules/ngx_http_markdown_filter_module.so;
    EOS
  end

  test do
    module_path = lib/"nginx/modules/ngx_http_markdown_filter_module.so"
    assert_path_exists module_path
    assert_match "libbrotlidec", shell_output("otool -L #{module_path}")

    (testpath/"nginx.conf").write <<~EOS
      load_module #{module_path};
      pid #{testpath}/nginx.pid;
      error_log #{testpath}/error.log;
      events {}
      http {}
    EOS
    system formula_opt_bin("nginx")/"nginx", "-t", "-p", testpath,
           "-c", testpath/"nginx.conf"
  end
end
