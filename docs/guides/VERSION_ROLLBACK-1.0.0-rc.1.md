# Version Rollback: 1.0.0-rc.1

The 1.0.0-rc.1 candidate is not yet published. v0.9.4 remains the latest public
stable release.

This guide restores the previous module package after a candidate test.

## Before the change

Record these values for the running server.

- NGINX version and package origin.
- NGINX package revision.
- Module package version.
- Architecture and libc.
- Ubuntu ABI provide when the module comes from the Ubuntu archive.

Keep the previous module package in a local path. Do not rely on a package
cache that the next upgrade can clear.

## Rollback steps

1. Stop new traffic to the node or remove it from the load balancer.
2. Compare the installed NGINX package with the recorded revision. If NGINX
   changed during the candidate test, restore the recorded NGINX package
   first. The saved module package refuses an install when the NGINX version
   differs.
3. Simulate the package transaction and confirm the package manager retains
   the intended NGINX package.

   ```bash
   apt-get -s install --allow-downgrades ./<previous-module-package>.deb
   ```

   On an RPM system, simulate installing the previous module package with:

   ```bash
   sudo rpm -U --test --oldpackage ./<previous-module-package>.rpm
   ```

4. Review the transaction, then install the saved module package.

   ```bash
   sudo apt-get install --allow-downgrades ./<previous-module-package>.deb
   ```

   On an RPM system, use the matching `dnf downgrade` command for the recorded
   package, or `rpm -U --oldpackage <previous-module-package>.rpm`.

5. Confirm the installed module package version.

   ```bash
   dpkg-query -W -f='${Package} ${Version}\n' \
     nginx-module-markdown-for-agents \
     nginx-module-markdown-for-agents-ubuntu 2>/dev/null
   ```

   On an RPM system, use `rpm -q --qf '%{VERSION}-%{RELEASE}\n' nginx-module-markdown-for-agents`.

6. Test the configuration, then restart NGINX.

   ```bash
   sudo nginx -t
   sudo systemctl restart nginx
   ```

   A reload keeps the running master process and its module mapping. A restart
   loads the restored package.

7. Send one HTML request and one `Accept: text/markdown` request. Confirm each
   response matches the previous release behavior.
8. Run `nginx-markdown-doctor --json` and review any failed check.
9. Return the node to service after the checks pass.

## Failure handling

A matching NGINX version alone does not prove that a module can load. If the
restored module still fails to load, confirm the NGINX package matches the
recorded revision and restore it if needed. Keep the configuration unchanged
during the rollback so a configuration error cannot hide a module mismatch.
