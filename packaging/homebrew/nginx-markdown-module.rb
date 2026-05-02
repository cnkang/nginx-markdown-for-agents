class NginxMarkdownModule < Formula
  desc "NGINX module for HTML-to-Markdown conversion"
  homepage "https://github.com/cnkang/nginx-markdown-for-agents"
  url "https://github.com/cnkang/nginx-markdown-for-agents/archive/v0.6.0.tar.gz"
  sha256 "ac5080ee598b9a7cba229c4306efe8d5177dd3df2404f8a5534a103be4860715"
  license "BSD-2-Clause"

  depends_on "rust" => :build
  depends_on "pcre2"
  depends_on "openssl@3"

  def install
    system "make", "build"
    system "make", "install", "DESTDIR=#{prefix}"
  end

  def caveats
    <<~EOS
      This module requires NGINX to be built with the module loaded.
      Add to your nginx.conf:
        load_module #{opt_lib}/nginx/modules/ngx_http_markdown_filter_module.so;
    EOS
  end

  test do
    assert File.exist?(lib/"nginx/modules/ngx_http_markdown_filter_module.so"),
           "Shared object must exist after install"
  end
end
