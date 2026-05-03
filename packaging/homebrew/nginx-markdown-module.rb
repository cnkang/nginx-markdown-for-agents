class NginxMarkdownModule < Formula
  desc "NGINX module for HTML-to-Markdown conversion"
  homepage "https://github.com/cnkang/nginx-markdown-for-agents"
  url "https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.6.0.tar.gz"
  # SHA-256 from git archive HEAD (self-consistent at commit time).
  # After pushing the v0.6.0 tag, regenerate from the GitHub archive:
  #   curl -sL https://github.com/cnkang/nginx-markdown-for-agents/archive/refs/tags/v0.6.0.tar.gz | sha256sum
  # GitHub archives include a commit-date prefix that differs from git
  # archive output, so the SHA will change.
  sha256 "c7a30b80d03a202ac7501e563d17f64874b8e7ffbfecd498ed469b1b8cd1a584"
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
