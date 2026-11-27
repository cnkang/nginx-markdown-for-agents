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

Save the previous module package and the recorded NGINX packages outside the
package cache. Keep the matching packages if your distribution splits NGINX
across several packages.

## Rollback steps

1. Stop new traffic to the node or remove it from the load balancer.
2. Compare the installed NGINX package with the recorded revision. If the
   revision matches, go to step 3. Otherwise remove the candidate module
   before you restore NGINX. The candidate can require the newer NGINX ABI.

   Back up the active configuration. Temporarily disable the Markdown module
   load and its directives, test the configuration, and stop NGINX.

   ```bash
   sudo nginx -t && sudo systemctl stop nginx
   ```

   Set `MODULE_PACKAGE` to the installed module package name. Use
   `nginx-module-markdown-for-agents` for nginx.org packages or
   `nginx-module-markdown-for-agents-ubuntu` for Ubuntu archive builds.
   Set `NGINX_DEBS` or `NGINX_RPMS` to a Bash array of local package paths
   for the recorded NGINX revision. Use absolute paths or start each path with
   `./`.

   Review each simulation before you run the matching change. Proceed only if
   the simulation succeeds and targets the intended packages.

   On DEB systems, simulate the module removal:

   ```bash
   sudo apt-get -s remove "$MODULE_PACKAGE"
   ```

   After the simulation shows the intended changes, remove the module:

   ```bash
   sudo apt-get remove "$MODULE_PACKAGE"
   ```

   Then simulate the NGINX downgrade:

   ```bash
   sudo apt-get -s install --allow-downgrades "${NGINX_DEBS[@]}"
   ```

   After the simulation shows the intended changes, downgrade NGINX:

   ```bash
   sudo apt-get install --allow-downgrades "${NGINX_DEBS[@]}"
   ```

   On RPM systems, `rpm --test` reports conflicts and dependency problems but
   no transaction listing. Confirm the installed module package and simulate
   the removal:

   ```bash
   rpm -q "$MODULE_PACKAGE"
   sudo rpm -e --test "$MODULE_PACKAGE"
   ```

   After the simulation succeeds, remove the module:

   ```bash
   sudo rpm -e "$MODULE_PACKAGE"
   ```

   Confirm the installed NGINX packages and the target package files, then
   simulate the downgrade:

   ```bash
   rpm -qa --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' 'nginx*'
   rpm -qp --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' "${NGINX_RPMS[@]}"
   sudo rpm -U --test --oldpackage "${NGINX_RPMS[@]}"
   ```

   After the simulation succeeds, downgrade NGINX:

   ```bash
   sudo rpm -U --oldpackage "${NGINX_RPMS[@]}"
   ```

3. Confirm NGINX matches the recorded revision. Set `MODULE_DEB` or
   `MODULE_RPM` to the local path of the previous module package. Simulate
   the module transaction and confirm it leaves the NGINX revision in place.
   Use an absolute path or start the path with `./`.

   ```bash
   sudo apt-get -s install --allow-downgrades "$MODULE_DEB"
   ```

   On an RPM system, simulate installing the previous module package with:

   ```bash
   sudo rpm -U --test --oldpackage "$MODULE_RPM"
   ```

4. Review the transaction, then install the saved module package.

   ```bash
   sudo apt-get install --allow-downgrades "$MODULE_DEB"
   ```

   On an RPM system:

   ```bash
   sudo rpm -U --oldpackage "$MODULE_RPM"
   ```

5. Confirm the installed module package version.

   ```bash
   dpkg-query -W -f='${Package} ${Version}\n' \
     nginx-module-markdown-for-agents \
     nginx-module-markdown-for-agents-ubuntu 2>/dev/null
   ```

   On an RPM system, use `rpm -q --qf '%{VERSION}-%{RELEASE}\n' nginx-module-markdown-for-agents`.

6. Restore any configuration you changed in step 2. Test the configuration,
   then restart NGINX.

   ```bash
   sudo nginx -t && sudo systemctl restart nginx
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
recorded revision. Follow step 2 if the revision differs.
