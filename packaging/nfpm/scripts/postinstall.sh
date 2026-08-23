#!/bin/bash
# postinstall.sh — nFPM post-installation script for nginx-module-markdown-for-agents.
#
# Usage:
#   postinstall.sh [configure|1|2]
#
# This script is invoked by the package manager after successful installation.
# It displays instructions for enabling the module. It does NOT modify any
# system state: no nginx.conf edits, no reload/restart, no snippet enablement.
# The DEB enablement path is a main-context load_module in nginx.conf
# (nginx.org builds do not include /etc/nginx/modules-enabled/*.conf by
# default), and the RPM path is the same main-context directive.
#
# Exit codes:
#   0  Success

set -e

##############################################################################
# Executable-trust invariant: establish trusted PATH before any command
# resolution. The literal empty assignment ensures a caller-controlled
# environment variable of the same name cannot influence the resolved set.
# Tests override TRUSTED_PATH_ROOT to redirect resolution into a sandbox.
##############################################################################

TRUSTED_PATH_ROOT=""
PATH="${TRUSTED_PATH_ROOT}/usr/sbin:${TRUSTED_PATH_ROOT}/usr/bin:${TRUSTED_PATH_ROOT}/sbin:${TRUSTED_PATH_ROOT}/bin"
export PATH

##############################################################################
# Helpers
##############################################################################

info() {
    printf '[nginx-module-markdown] %s\n' "$1" >&2
}

##############################################################################
# Main
##############################################################################

ACTION="${1:-configure}"

case "$ACTION" in
    configure|1|2)
        cat >&2 <<'EOF'
======================================================================
nginx-module-markdown-for-agents module installed successfully.

To enable the module:

  --- Debian/Ubuntu (DEB) ---
  1. Add the load_module directive at the TOP LEVEL of /etc/nginx/nginx.conf
     (before the http block):
       load_module /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so;

     NOTE: this package targets nginx.org builds, whose default nginx.conf
     does NOT include /etc/nginx/modules-enabled/*.conf.  A symlink in
     modules-enabled is therefore ignored by the default configuration —
     the main-context load_module above is the supported enablement path.

  2. Verify configuration:
       sudo nginx -t

  3. Reload NGINX:
       sudo systemctl reload nginx

  --- RHEL/AlmaLinux/Amazon Linux (RPM) ---
  1. Add to /etc/nginx/nginx.conf (top-level, before http block):
       load_module /usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so;

  2. Verify configuration:
       sudo nginx -t

  3. Reload NGINX:
       sudo systemctl reload nginx

For more information, see:
  /usr/share/doc/nginx-markdown-for-agents/README.md
======================================================================
EOF
        ;;
    abort-upgrade|abort-remove|abort-deconfigure)
        # dpkg-specific lifecycle events — no action needed
        ;;
    *)
        info "postinstall called with unknown argument: $ACTION"
        ;;
esac

exit 0
