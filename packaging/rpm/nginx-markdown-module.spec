Name:           nginx-module-markdown-for-agents
Version:        0.7.0
Release:        1%{?dist}
Summary:        NGINX module for HTML-to-Markdown conversion

License:        BSD-2-Clause
URL:            https://github.com/cnkang/nginx-markdown-for-agents
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  gcc
BuildRequires:  make
BuildRequires:  cargo
BuildRequires:  rustc
BuildRequires:  pcre-devel
BuildRequires:  zlib-devel
BuildRequires:  openssl-devel
BuildRequires:  nginx-devel >= 1.26.3
Requires:       nginx >= 1.26.3

%description
NGINX filter module that converts HTML responses to Markdown
for AI agent consumption. Supports streaming, GFM/MDX flavors,
noise pruning, token estimation, and conditional requests.

%prep
%setup -q

%build
make build

%install
make install DESTDIR=%{buildroot}

%files
%doc README.md
%license LICENSE
%{_libdir}/nginx/modules/ngx_http_markdown_module.so

%changelog
* Wed May 06 2026 cnkang <liukang@noreply.github.com> - 0.6.1-1
- v0.6.1: harness Rules 27-31, output-safety risk pack, dynconf two-phase reload

* Sat May 02 2026 cnkang <liukang@noreply.github.com> - 0.6.0-1
- Initial RPM package for v0.6.0
