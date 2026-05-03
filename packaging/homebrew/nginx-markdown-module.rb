class NginxMarkdownModule < Formula
  desc "NGINX module for HTML-to-Markdown conversion"
  homepage "https://github.com/cnkang/nginx-markdown-for-agents"
  url "https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.6.0.tar.gz"
  # NOTE: SHA-256 is generated from git archive HEAD.  After pushing the
  # v0.6.0 tag, regenerate from the actual GitHub archive artifact:
  #   curl -sL https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.6.0.tar.gz | sha256sum
  # and update this value.  GitHub archives include a commit-date prefix
  # that differs from git archive, so the SHA will change.
  sha256 "e03bfda89faeb44aa419c9dc8e79ce8058e092505af1f2b764d1d70bfb1af81a"
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
