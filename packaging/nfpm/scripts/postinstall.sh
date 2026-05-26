#!/bin/bash
# postinstall.sh — nFPM post-installation script for nginx-module-markdown-for-agents.
#
# Usage:
#   postinstall.sh [configure]
#
# This script is invoked by the package manager after successful installation.
# It displays instructions for enabling the module. It does NOT modify any
# system state: no nginx.conf edits, no reload/restart, no snippet enablement.
#
# Exit codes:
#   0  Success
#   1  Error (unknown argument)

set -e

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
    configure)
        cat >&2 <<'EOF'
======================================================================
nginx-module-markdown-for-agents module installed successfully.

To enable the module:

  --- Debian/Ubuntu (DEB) ---
  1. Create a symlink to enable the module configuration:
       sudo ln -sf /usr/share/nginx/modules-available/mod-markdown.conf \
           /etc/nginx/modules-enabled/50-mod-markdown.conf

  2. Verify configuration:
       sudo nginx -t

  3. Reload NGINX:
       sudo systemctl reload nginx

  --- RHEL/AlmaLinux/Amazon Linux (RPM) ---
  1. Add to /etc/nginx/nginx.conf (top-level, before http block):
       load_module modules/ngx_http_markdown_filter_module.so;

  2. Verify configuration:
       sudo nginx -t

  3. Reload NGINX:
       sudo systemctl reload nginx

For more information, see:
  /usr/share/doc/nginx-module-markdown-for-agents/README.md
======================================================================
EOF
        ;;
    abort-upgrade|abort-remove|abort-deconfigure)
        # dpkg-specific lifecycle events — no action needed
        ;;
    *)
        info "postinstall called with unknown argument: $ACTION"
        exit 1
        ;;
esac

exit 0
