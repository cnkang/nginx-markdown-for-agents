# Version Rollback: 0.9.3

This guide returns a host from 0.9.3 to the previous module version. 0.9.3 is
not yet published, so use it after the release. A rollback is safe only when
the NGINX ABI did not change.

## 1. Decide whether a rollback is possible

1. Record the current NGINX package revision:

   ```bash
   dpkg-query -W -f='${Version}\n' nginx
   ```

2. Read the ABI capability:

   ```bash
   dpkg-query -W -f='${Provides}\n' nginx | tr ',' '\n' | grep nginx-abi
   ```

3. Find the same two values in the record of the version you want to return
   to. When both values match, the old module can load. When either value
   differs, the old module cannot load, and the only options are a rebuild or
   a return of the NGINX package.

## 2. Roll back a DEB installation

1. Keep the package file that you replaced during the upgrade.

2. Install it:

   ```bash
   sudo apt-get install ./<previous-package-file>
   ```

3. Test the configuration:

   ```bash
   sudo nginx -t
   ```

4. Reload:

   ```bash
   sudo systemctl reload nginx
   ```

## 3. Roll back a source installation

1. Build or restore the previous module file.

2. Install it over the current file:

   ```bash
   sudo install -m 0644 <previous-module>.so \
     /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so
   ```

3. Test and reload:

   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

## 4. Roll back when the ABI changed

A module cannot cross an ABI change. Use one of these paths:

- Rebuild the previous module version against the current NGINX revision.
- Return the NGINX package to the previous revision, then install the previous
  module.

Do not copy a module between hosts with different NGINX revisions.

## 5. Confirm the rollback

1. Read the module version:

   ```bash
   bash tools/doctor/nginx-markdown-doctor.sh
   ```

2. Confirm the Markdown response:

   ```bash
   curl -sD - -o /dev/null -H 'Accept: text/markdown' http://localhost/
   ```

3. Read the NGINX error log when the response is not Markdown:

   ```bash
   sudo tail -50 /var/log/nginx/error.log
   ```

## 6. Related

- [UPGRADE-TO-0.9.3.md](UPGRADE-TO-0.9.3.md)
- [0.9.3-breaking-changes.md](0.9.3-breaking-changes.md)
- [UBUNTU-LTS-NGINX-COMPATIBILITY.md](UBUNTU-LTS-NGINX-COMPATIBILITY.md)
