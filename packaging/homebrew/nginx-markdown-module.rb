class NginxMarkdownModule < Formula
  desc "NGINX module for HTML-to-Markdown conversion"
  homepage "https://github.com/cnkang/nginx-markdown-for-agents"
  # Source formula for the Homebrew tap publish workflow.
  # The workflow rewrites `url` and `sha256` to the target release tag
  # before pushing to the tap repository.
  url "https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.8.2.tar.gz"
  sha256 "ed1164d69a29656b935633e104667f3a2b833e071839d3a691aa115c1cb9fc7d"
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
