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

  # Dynamic modules are ABI-bound to the exact Homebrew nginx dependency.
  # Increment the formula revision whenever nginx is upgraded so Homebrew
  # rebuilds this module against the new nginx binary.
  revision 1

  depends_on "cbindgen" => :build
  depends_on "cmake" => :build
  depends_on "pkgconf" => :build
  depends_on "nginx"
  depends_on "pcre2"

  # A host Homebrew Brotli can lag the fixed release, and the module would
  # then load the vulnerable decoder at runtime. Vendor the fixed decoder
  # instead (CVE-2026-111497, fixed in 1.2.1) and link it statically.
  resource "brotli" do
    url "https://github.com/google/brotli/archive/refs/tags/v1.2.1.tar.gz"
    sha256 "98d35d11a78dc453aebb7d10c3748f3f73ff78c8267c0dc697b8631f67bf7e8f"
  end

  # The Rust toolchain is installed with the repository's checksum-verifying
  # rustup helper rather than the Homebrew `rust` formula, because the crate
  # MSRV can briefly exceed Homebrew's Rust version. The helper pins the
  # architecture-specific rustup-init bytes before execution.
  TOOLCHAIN_VERSION = "1.98.1".freeze

  def install
    rustup_home = "#{buildpath}/rustup"
    cargo_home = "#{buildpath}/cargo"
    ENV["RUSTUP_HOME"] = rustup_home
    ENV["CARGO_HOME"] = cargo_home
    rustup_arch = Hardware::CPU.arm? ? "arm64" : "amd64"
    system "bash",
           (buildpath/"packaging/scripts/install-verified-rustup.sh").to_s,
           "--arch", rustup_arch,
           "--os", "darwin",
           "--toolchain", TOOLCHAIN_VERSION,
           "--checksums", (buildpath/"packaging/checksums.sha256").to_s
    ENV.prepend_path "PATH", "#{cargo_home}/bin"

    # Enable Brotli streaming decompression explicitly so the official Homebrew
    # artifact does not rely on auto-detection alone.
    ENV["NGX_MARKDOWN_BROTLI_STREAMING"] = "on"

    # Build the vendored decoder as static archives beside the keg and point
    # pkg-config at them, so the configure probe links the bundled copy
    # instead of any host Brotli.
    brotli_prefix = libexec/"brotli"
    resource("brotli").stage do
      system "cmake", "-S", ".", "-B", "build",
             "-DCMAKE_BUILD_TYPE=Release",
             "-DBUILD_SHARED_LIBS=OFF",
             "-DBROTLI_DISABLE_TESTS=ON",
             "-DBROTLI_BUILD_TOOLS=OFF",
             "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
             "-DCMAKE_INSTALL_PREFIX=#{brotli_prefix}",
             "-DCMAKE_INSTALL_LIBDIR=lib"
      system "cmake", "--build", "build"
      system "cmake", "--install", "build"
    end
    ENV["PKG_CONFIG_PATH"] = "#{brotli_prefix}/lib/pkgconfig"

    system "make", "build"

    nginx_version = Formula["nginx"].version.to_s
    odie "Unable to detect Homebrew nginx version" if nginx_version.blank?
    openssl_formula = nginx_openssl_formula

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
        "--with-cc-opt=-I#{formula_opt_include(openssl_formula)} " \
        "-I#{formula_opt_include("pcre2")} -I#{brotli_prefix}/include",
        "--with-ld-opt=-L#{formula_opt_lib(openssl_formula)} " \
        "-L#{formula_opt_lib("pcre2")} -L#{brotli_prefix}/lib",
      ]
      system "./configure", *args
      system "make", "modules"
      (lib/"nginx/modules").install "objs/ngx_http_markdown_filter_module.so"
    end
  end

  def nginx_openssl_formula
    # A dependency can be declared tap-qualified (for example
    # "homebrew/core/openssl@3"), and Dependency#name keeps that full form,
    # so the match reads the bare formula name through Homebrew's own
    # helper (the audit requires it over manual name splitting).  The
    # return value keeps the declared spelling: the opt-prefix helpers
    # strip the tap component themselves.
    dependencies = Formula["nginx"].deps.select do |dependency|
      Utils.name_from_full_name(dependency.name).match?(/\Aopenssl(?:@\d+)?\z/)
    end
    odie "Unable to detect Homebrew nginx OpenSSL dependency" \
      unless dependencies.one?

    dependencies.first.name
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
    # The decoder is vendored, so the module must not load a host Brotli.
    refute_match "libbrotlidec", shell_output("otool -L #{module_path}")
    nginx_version = Formula["nginx"].version.to_s
    assert_match(/\A\d+\.\d+\.\d+\z/, nginx_version)
    assert_match(%r{nginx/#{Regexp.escape(nginx_version)}\s*\z},
                 shell_output("#{formula_opt_bin("nginx")}/nginx -v 2>&1"))

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
