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
  depends_on "rust" => :build
  depends_on "openssl@3"
  depends_on "pcre2"

  def install
    system "make", "build"

    nginx_version = Formula["nginx"].version.to_s
    odie "Unable to detect Homebrew nginx version" if nginx_version.blank?

    nginx_archive = "nginx-#{nginx_version}.tar.gz"
    system "curl", "-fsSL", "https://nginx.org/download/#{nginx_archive}",
           "-o", nginx_archive
    system "tar", "-xzf", nginx_archive

    cd "nginx-#{nginx_version}" do
      args = [
        "--with-compat",
        "--add-dynamic-module=#{buildpath}/components/nginx-module",
        "--with-cc-opt=-I#{formula_opt_include("openssl@3")} -I#{formula_opt_include("pcre2")}",
        "--with-ld-opt=-L#{formula_opt_lib("openssl@3")} -L#{formula_opt_lib("pcre2")}",
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
    assert_path_exists lib/"nginx/modules/ngx_http_markdown_filter_module.so"
  end
end
