class NginxMarkdownModule < Formula
  desc "NGINX module for HTML-to-Markdown conversion"
  homepage "https://github.com/cnkang/nginx-markdown-for-agents"
  url "https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.8.3.tar.gz"
  # Regenerate sha256 after publishing the v0.8.3 tag:
  #   curl -sL https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.8.3.tar.gz | sha256sum
  sha256 "PLACEHOLDER_UPDATE_AFTER_TAG_PUBLISH"
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
        "--with-cc-opt=-I#{Formula["openssl@3"].opt_include} -I#{Formula["pcre2"].opt_include}",
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
