Name:           nginx-module-markdown
Version:        0.7.0
Release:        1%{?dist}
Summary:        NGINX module for serving Markdown to AI agents

License:        BSD-2-Clause
URL:            https://github.com/cnkang/nginx-markdown-for-agents
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  cargo
BuildRequires:  rustc >= 1.91
BuildRequires:  pcre-devel
BuildRequires:  zlib-devel
BuildRequires:  openssl-devel
BuildRequires:  nginx-devel >= 1.24.0

Requires:       nginx >= 1.24.0
Requires:       nginx-abi-1.24

%description
NGINX dynamic filter module that converts HTML responses to Markdown
format for AI agent consumption.

Features:
- Streaming and full-buffer conversion engines
- Content negotiation via Accept headers (q-value based)
- Automatic decompression with bounded budget
- GFM/MDX flavor support
- Noise pruning and token estimation
- Conditional request support (ETag, If-Modified-Since)
- Prometheus metrics endpoint
- Dynamic configuration with dry-run validation

The module is installed as a dynamic module (.so) and must be explicitly
enabled via load_module directive in nginx.conf.

%prep
%setup -q

%build
make build

%install
rm -rf %{buildroot}
install -d %{buildroot}%{_libdir}/nginx/modules
install -m 0755 build/ngx_http_markdown_module.so \
    %{buildroot}%{_libdir}/nginx/modules/ngx_http_markdown_module.so

install -d %{buildroot}%{_sysconfdir}/nginx/modules-available
cat > %{buildroot}%{_sysconfdir}/nginx/modules-available/mod-markdown.conf <<'SNIPPET'
# nginx-module-markdown: Uncomment the line below to enable the module.
# After enabling, run: nginx -t && systemctl reload nginx
#load_module modules/ngx_http_markdown_module.so;
SNIPPET

%post
# Post-install: print instructions for enabling the module.
# The module is NOT enabled by default (conservative enable per design.md).
SNIPPET_FILE="%{_sysconfdir}/nginx/modules-available/mod-markdown.conf"

if [ -f "${SNIPPET_FILE}" ]; then
    echo "--------------------------------------------------------------"
    echo " nginx-module-markdown installed successfully."
    echo ""
    echo " The module is NOT enabled by default."
    echo " To enable it:"
    echo ""
    echo "   1. Edit ${SNIPPET_FILE}"
    echo "      Uncomment the load_module line."
    echo ""
    echo "   2. Or add to /etc/nginx/nginx.conf (top-level):"
    echo "      load_module modules/ngx_http_markdown_module.so;"
    echo ""
    echo "   3. Test and reload:"
    echo "      nginx -t && systemctl reload nginx"
    echo ""
    echo " Module binary: %{_libdir}/nginx/modules/ngx_http_markdown_module.so"
    echo "--------------------------------------------------------------"
fi

%postun
# Post-uninstall: remove the snippet file on full removal (not upgrade).
if [ "$1" -eq 0 ]; then
    SNIPPET_FILE="%{_sysconfdir}/nginx/modules-available/mod-markdown.conf"
    if [ -f "${SNIPPET_FILE}" ]; then
        rm -f "${SNIPPET_FILE}"
    fi
    if [ -d "%{_sysconfdir}/nginx/modules-available" ]; then
        rmdir "%{_sysconfdir}/nginx/modules-available" 2>/dev/null || true
    fi
    echo "nginx-module-markdown: module snippet removed."
fi

%files
%doc README.md
%license LICENSE
%{_libdir}/nginx/modules/ngx_http_markdown_module.so
%config(noreplace) %{_sysconfdir}/nginx/modules-available/mod-markdown.conf

%changelog
* Sat May 17 2026 cnkang <liukang@noreply.github.com> - 0.7.0-1
- v0.7.0: Rust-first architecture, bounded decompression, Accept negotiation,
  conditional requests, decision engine, DEB/RPM packaging, K8s deployment

* Wed May 06 2026 cnkang <liukang@noreply.github.com> - 0.6.1-1
- v0.6.1: harness Rules 27-31, output-safety risk pack, dynconf two-phase reload

* Sat May 02 2026 cnkang <liukang@noreply.github.com> - 0.6.0-1
- Initial RPM package for v0.6.0
