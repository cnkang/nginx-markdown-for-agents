Name:           nginx-module-markdown-for-agents
Version:        %{version}
Release:        nginx%{nginx_version}.1%{?dist}
Summary:        NGINX Markdown filter module for AI agents

License:        BSD-2-Clause
URL:            https://github.com/cnkang/nginx-markdown-for-agents
Source0:        %{name}-%{version}.tar.gz

Requires:       nginx-r%{nginx_version}
Requires:       nginx >= 1:%{nginx_version}
Conflicts:      nginx >= 1:%{nginx_version_ceil}

%description
NGINX dynamic filter module that converts HTML responses to Markdown
format for AI agent consumption.

Built against nginx.org stable %{nginx_version}.

WARNING: This module is built for nginx.org %{nginx_version} ONLY. NGINX
dynamic modules require an exact version match — the core loader rejects
any version difference (including a patch release) before signature
checks. The RPM dependency requires the nginx.org nginx-r%{nginx_version}
capability in addition to the epoch-aware version bounds. A package that
merely reports the same version without the expected ABI capability is
rejected before installation. It will NOT work with distro-provided,
vendor-patched, OpenResty, Tengine, or custom-built NGINX binaries, or with
any other NGINX version.

Features:
- Streaming and full-buffer conversion engines
- Content negotiation via Accept headers (q-value based)
- Automatic decompression with bounded budget
- CommonMark/GFM flavor support
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
install -m 0644 docs/guides/PACKAGE_INSTALLATION.md \
    %{buildroot}/usr/share/doc/nginx-markdown-for-agents/PACKAGE_INSTALLATION.md
install -m 0644 docs/guides/PACKAGE_COMPATIBILITY.md \
    %{buildroot}/usr/share/doc/nginx-markdown-for-agents/PACKAGE_COMPATIBILITY.md

install -d %{buildroot}/usr/share/licenses/nginx-markdown-for-agents
install -m 0644 LICENSE \
    %{buildroot}/usr/share/licenses/nginx-markdown-for-agents/LICENSE

install -d %{buildroot}/usr/libexec/nginx-markdown-for-agents
install -m 0755 preremove.sh \
    %{buildroot}/usr/libexec/nginx-markdown-for-agents/preremove.sh

%pre
# The nginx-r%{nginx_version} capability above is provided by the official
# nginx.org nginx package. RPM resolves that capability before %pre, so a
# same-EVR distro-rebuilt, vendor-patched, or custom package without the
# expected ABI provenance is rejected before this script runs.
# Guard: the running NGINX executable must also match the exact version this
# module was built against. The loader performs the final signature check.
# Fail the transaction here with an explicit message instead of leaving
# the operator with a module NGINX refuses to load.
# $1==1 during upgrade, $1==2 during erase — run the guard only for
# install/upgrade (not erase), and tolerate a missing nginx binary
# (the RPM dependencies still enforce the capability and floor/ceiling).
if [ "$1" -ne 0 ]; then
    if command -v nginx >/dev/null 2>&1; then
        GUARD_NGINX_VERSION="$(nginx -v 2>&1 | sed -n 's/.*nginx version: nginx\/\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p')"
        if [ -n "${GUARD_NGINX_VERSION}" ] && [ "${GUARD_NGINX_VERSION}" != "%{nginx_version}" ]; then
            echo "ERROR: installed NGINX version ${GUARD_NGINX_VERSION} does not match the exact version %{nginx_version} this module was built for" >&2
            echo "  This package provides a dynamic module for nginx.org %{nginx_version} ONLY." >&2
            echo "  The NGINX core loader rejects any other version (including a patch release)." >&2
            echo "  Install nginx-%{nginx_version} and retry." >&2
            exit 1
        fi
    fi
fi

%post
PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH
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
  /usr/share/doc/nginx-markdown-for-agents/PACKAGE_COMPATIBILITY.md
======================================================================
EOF

# The pre-uninstall guard refuses final removal while the active NGINX
# configuration still loads the module, and blocks removal whenever the
# active configuration cannot be inspected at all. On hosts where such an
# inspection is impossible, operators who have verified the include graph
# themselves may acknowledge that explicitly by creating the sentinel file
# below before running the RPM removal:
#   sudo touch /etc/nginx/markdown-module-force-remove
#   sudo rpm -e <package>
# The file persists until the operator deletes it, so every affected
# removal transaction stays explicit rather than inheriting unsettable
# scriptlet environment state.
%preun
if [ "$1" -eq 0 ]; then
    /bin/bash /usr/libexec/nginx-markdown-for-agents/preremove.sh remove
fi

%files
/usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so
/usr/share/doc/nginx-markdown-for-agents/README.md
/usr/share/doc/nginx-markdown-for-agents/INSTALL.md
/usr/share/doc/nginx-markdown-for-agents/PACKAGE_INSTALLATION.md
/usr/share/doc/nginx-markdown-for-agents/PACKAGE_COMPATIBILITY.md
%attr(0755,root,root) /usr/libexec/nginx-markdown-for-agents/preremove.sh
%license /usr/share/licenses/nginx-markdown-for-agents/LICENSE

%changelog
* Thu Jul 30 2026 cnkang <liukang@noreply.github.com> - 0.9.2-nginx%{nginx_version}.1
- v0.9.2: Diagnostics reason_to_code mapping fix, C reason code constants
  synchronized (decompression series 4-11), OTel subsystem removal,
  safe dynconf file restore guidance, public surface contract drift gate,
  release gates 0.9.2

* Wed Jul 29 2026 cnkang <liukang@noreply.github.com> - 0.9.1-nginx%{nginx_version}.1
- v0.9.1: Breaking — Rust baseline 1.97, streaming_engine removed, non-semantic
  flavors removed, FFI ABI reset to version 1, incomplete OTel controls
  reject-only, trusted_proxies main-only; hybrid zero-copy output, streaming
  decompression routing (gzip/deflate/Brotli), performance evidence gate

* Fri Jul 03 2026 cnkang <liukang@noreply.github.com> - 0.9.0-nginx%{nginx_version}.1
- v0.9.0: Breaking — Config V2, profile system, error policy consolidation,
  inflight guard, metrics consolidation, reason code lowercase

* Tue Jun 10 2026 cnkang <liukang@noreply.github.com> - 0.8.0-nginx1.26.3.1
- v0.8.0: True streaming contract, fallback state machine, streaming
  observability, streaming security enforcement, release matrix source of
  truth, streaming configuration directives

* Sat May 18 2026 cnkang <liukang@noreply.github.com> - 0.7.0-nginx1.26.3.1
- v0.7.0: Package redesign per 0.7.0 release package naming and layout — correct naming, nginx version
  binding in Release tag, minimum version constraint, safe %post script

* Sat May 17 2026 cnkang <liukang@noreply.github.com> - 0.7.0-1
- v0.7.0: Rust-first architecture, bounded decompression, Accept negotiation,
  conditional requests, decision engine, DEB/RPM packaging, K8s deployment

* Wed May 06 2026 cnkang <liukang@noreply.github.com> - 0.6.1-1
- v0.6.1: harness Rules 27-31, output-safety risk pack, dynconf two-phase reload

* Sat May 02 2026 cnkang <liukang@noreply.github.com> - 0.6.0-1
- Initial RPM package for v0.6.0
