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
2. Simulate the package transaction and confirm the package manager retains
   the intended NGINX package.

   ```bash
   apt-get -s install --allow-downgrades ./<previous-module-package>.deb
   ```

3. Review the transaction, then install the saved module package.

   ```bash
   sudo apt-get install --allow-downgrades ./<previous-module-package>.deb
   ```

   On an RPM system, use the matching `dnf downgrade` command for the recorded
   package, or `rpm -U --oldpackage <previous-module-package>.rpm`.

4. Confirm the installed module version.

   ```bash
   nginx -V 2>&1 | tr ' ' '\n' | grep ngx_http_markdown
   ```

5. Test and reload NGINX.

   ```bash
   sudo nginx -t
   sudo systemctl reload-or-restart nginx
   ```

6. Send one HTML request and one `Accept: text/markdown` request. Confirm each
   response matches the previous release behavior.
7. Run `nginx-markdown-doctor --json` and review any failed check.
8. Return the node to service after the checks pass.

## Failure handling

A matching NGINX version alone does not prove that a module can load. If the
previous package fails to load, restore the recorded NGINX package revision as
well. Keep the configuration unchanged during the rollback so a configuration
error cannot hide a module mismatch.
