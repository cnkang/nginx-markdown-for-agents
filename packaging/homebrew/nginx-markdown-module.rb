class NginxMarkdownModule < Formula
  desc "NGINX module for HTML-to-Markdown conversion"
  homepage "https://github.com/user/nginx-markdown-for-agents"
  url "https://github.com/user/nginx-markdown-for-agents/archive/v0.6.0.tar.gz"
  sha256 "TBD"
  license "BSD-2-Clause"

  depends_on "rust" => :build
  depends_on "pcre2"
  depends_on "openssl@3"

  def install
    system "make", "build"
    system "make", "install", "DESTDIR=#{prefix}"
  end

  test do
    system "make", "test-rust"
    system "make", "test-nginx-unit"
  end
end
