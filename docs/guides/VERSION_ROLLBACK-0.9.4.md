# Version Rollback: 0.9.4

v0.9.4 is the published compatibility maintenance release. Keep the previous
module package before upgrading so you can restore the matching module if
validation fails.

## Check the NGINX package and ABI

Record the installed NGINX revision and, on Ubuntu, its ABI provide:

```bash
dpkg-query -W -f='${Version}\n' nginx
dpkg-query -W -f='${Provides}\n' nginx | tr ',' '\n' | grep nginx-abi
```

Use the previous module only with the exact NGINX package revision and
architecture for which it has verified evidence. A matching ABI provide does
not prove module loadability across package revisions. If the old module does
not match the installed NGINX, rebuild and verify it before restoring service.

## Restore a DEB package

1. Simulate the transaction and confirm APT will retain the intended NGINX
   package:

   ```bash
   apt-get -s install --allow-downgrades ./<previous-module-package>.deb
   ```

2. Review the transaction, then install the saved module package:

   ```bash
   sudo apt-get install --allow-downgrades ./<previous-module-package>.deb
   ```

3. Test the configuration, then reload or restart NGINX:

   ```bash
   sudo nginx -t && sudo systemctl reload-or-restart nginx
   ```

## Restore a source installation

Install the previous module built for the exact installed NGINX version, then
run `sudo nginx -t` before reloading or restarting the service.

If the previous module cannot load, remove the module load directive and every
`markdown_*` directive before starting NGINX without the module. Keep the
original configuration file so you can restore it after installing a verified
module.
