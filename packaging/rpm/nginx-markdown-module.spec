Name:           nginx-markdown-module
Version:        0.6.0
Release:        1%{?dist}
Summary:        NGINX module for HTML-to-Markdown conversion

License:        BSD-2-Clause
URL:            https://github.com/user/nginx-markdown-for-agents
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  gcc
BuildRequires:  make
BuildRequires:  pcre-devel
BuildRequires:  zlib-devel
BuildRequires:  openssl-devel
Requires:       nginx >= 1.18.0

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

%check
make test-rust
make test-nginx-unit

%files
%doc README.md
%license LICENSE
%{_libdir}/nginx/modules/ngx_http_markdown_filter_module.so

%changelog
* Sat May 02 2026 NGINX Markdown Maintainers <maintainers@nginx-markdown.dev> - 0.6.0-1
- Initial RPM package for v0.6.0
