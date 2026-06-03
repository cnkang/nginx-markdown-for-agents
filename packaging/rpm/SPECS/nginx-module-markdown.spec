Name:           nginx-module-markdown-for-agents
Version:        %{version}
Release:        nginx%{nginx_version}.1%{?dist}
Summary:        NGINX Markdown filter module for AI agents

License:        BSD-2-Clause
URL:            https://github.com/cnkang/nginx-markdown-for-agents
Source0:        %{name}-%{version}.tar.gz

Requires:       nginx >= 1:%{nginx_version_floor}
Requires:       nginx < 1:%{nginx_version_ceil}

%description
NGINX dynamic filter module that converts HTML responses to Markdown
format for AI agent consumption.

Built against nginx.org stable %{nginx_version}.

WARNING: This module is compatible with nginx.org releases
%{nginx_version_floor} (inclusive) through %{nginx_version_ceil} (exclusive).
It will NOT work with distro-provided, vendor-patched, OpenResty, Tengine,
or custom-built NGINX binaries. NGINX validates module binary compatibility
signature at load time; mismatched versions will fail to load.

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
%setup -q -n nginx-module-markdown-for-agents-%{version}

%build
# No-op: release-rpm.yml packages a prebuilt dynamic module.

%install
rm -rf %{buildroot}

install -d %{buildroot}/usr/lib64/nginx/modules
install -m 0644 ngx_http_markdown_filter_module.so \
    %{buildroot}/usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so

install -d %{buildroot}/usr/share/doc/nginx-markdown-for-agents
install -m 0644 README.md \
    %{buildroot}/usr/share/doc/nginx-markdown-for-agents/README.md
install -m 0644 docs/guides/INSTALL.md \
    %{buildroot}/usr/share/doc/nginx-markdown-for-agents/INSTALL.md
install -m 0644 docs/COMPATIBILITY.md \
    %{buildroot}/usr/share/doc/nginx-markdown-for-agents/COMPATIBILITY.md

install -d %{buildroot}/usr/share/licenses/nginx-markdown-for-agents
install -m 0644 LICENSE \
    %{buildroot}/usr/share/licenses/nginx-markdown-for-agents/LICENSE

%post
cat >&2 <<'EOF'
======================================================================
nginx-markdown-for-agents module installed successfully.

To enable the module:
  1. Add to nginx.conf (top-level, before http block):
     load_module /usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so;

  2. Verify configuration:
     sudo nginx -t

  3. Reload NGINX:
     sudo systemctl reload nginx

For compatibility information, see:
  /usr/share/doc/nginx-markdown-for-agents/COMPATIBILITY.md
======================================================================
EOF

%files
/usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so
/usr/share/doc/nginx-markdown-for-agents/README.md
/usr/share/doc/nginx-markdown-for-agents/INSTALL.md
/usr/share/doc/nginx-markdown-for-agents/COMPATIBILITY.md
%license /usr/share/licenses/nginx-markdown-for-agents/LICENSE

%changelog
* Sat May 18 2026 cnkang <liukang@noreply.github.com> - 0.7.0-nginx1.26.3.1
- v0.7.0: Package redesign per spec 31 — correct naming, nginx version
  binding in Release tag, minimum version constraint, safe %post script

* Sat May 17 2026 cnkang <liukang@noreply.github.com> - 0.7.0-1
- v0.7.0: Rust-first architecture, bounded decompression, Accept negotiation,
  conditional requests, decision engine, DEB/RPM packaging, K8s deployment

* Wed May 06 2026 cnkang <liukang@noreply.github.com> - 0.6.1-1
- v0.6.1: harness Rules 27-31, output-safety risk pack, dynconf two-phase reload

* Sat May 02 2026 cnkang <liukang@noreply.github.com> - 0.6.0-1
- Initial RPM package for v0.6.0
