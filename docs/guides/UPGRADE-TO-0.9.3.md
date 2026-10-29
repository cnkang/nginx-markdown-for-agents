# Upgrade to 0.9.3

0.9.3 is a compatibility maintenance release candidate, and 0.9.3 is not yet
published. Use the release-tag downloads after publication. An existing 0.9.2
configuration works without a change. This guide covers the artifact choice,
the upgrade steps, and the checks that prove the result.

## 1. Choose the artifact family

The artifact families are not interchangeable.

| Your NGINX | Use | Do not use |
|---|---|---|
| nginx.org package or source build | The DEB, RPM, or module tarball whose name contains `nginx-<version>` | The Ubuntu-native package |
| Ubuntu package from the Ubuntu archive | `nginx-module-markdown-for-agents-ubuntu` with `ubuntu-<suite>` in the name | The nginx.org DEB or tarball |
| Another source build | A source build for your exact NGINX version | Any prebuilt binary |

To read your NGINX origin, run:

```bash
nginx -V 2>&1 | head -1
```

A line that ends with `(Ubuntu)` identifies the Ubuntu package.

## 2a. Upgrade an nginx.org installation

1. Read your current version:

   ```bash
   nginx -v 2>&1
   ```

2. Confirm that the release matrix marks the version as supported:

   ```bash
   python3 tools/render_release_matrix_docs.py --release-notes
   ```

3. Download the package for that version and architecture from the release
   page after publication. Download `SHA256SUMS` and `SHA256SUMS.asc` too.

4. Verify the signature of `SHA256SUMS` before the checksum. Use the key from
   the git repository and the fingerprint in
   [GPG_KEY_MANAGEMENT.md](GPG_KEY_MANAGEMENT.md). A `VALIDSIG` line must name
   that fingerprint. [PACKAGE_INSTALLATION.md](PACKAGE_INSTALLATION.md) has the
   complete command block, including the `TRUSTED_FINGERPRINT` check.

5. Verify the checksum:

   ```bash
   sha256sum -c SHA256SUMS --ignore-missing
   ```

6. Remove the old package and install the new one:

   ```bash
   sudo apt-get remove nginx-module-markdown-for-agents
   sudo apt-get install ./nginx-module-markdown-for-agents_0.9.3_nginx-<version>_<arch>.deb
   ```

   The package name stays the same across module versions. The NGINX version
   in the filename must match your installed NGINX.

7. Test and reload:

   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

## 2b. Upgrade an Ubuntu-native installation

1. Record the installed package revision:

   ```bash
   dpkg-query -W -f='${Version}\n' nginx
   ```

2. Compare it with the verified revision in
   [UBUNTU-LTS-NGINX-COMPATIBILITY.md](UBUNTU-LTS-NGINX-COMPATIBILITY.md) and in
   the support matrix.

3. Download the package for your suite and architecture.

4. Install it:

   ```bash
   sudo apt-get install ./nginx-module-markdown-for-agents-ubuntu_0.9.3_ubuntu-<suite>_nginx-<revision>_<arch>.deb
   ```

5. Test and reload:

   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

## 3. Verify the result

1. Confirm the Markdown response:

   ```bash
   curl -sD - -o /dev/null -H 'Accept: text/markdown' http://localhost/
   ```

   The output must contain `Content-Type: text/markdown` and `Vary: Accept`.

2. Confirm the HTML response:

   ```bash
   curl -s -H 'Accept: text/html' http://localhost/ | head -3
   ```

   The output must contain HTML, not Markdown.

3. Run the doctor:

   ```bash
   bash tools/doctor/nginx-markdown-doctor.sh
   ```

## 4. Roll back

Keep the package file that you replaced. A rollback is safe only when the
NGINX ABI did not change.

```bash
sudo apt-get install ./<previous-package-file>
sudo nginx -t && sudo systemctl reload nginx
```

See [VERSION_ROLLBACK-0.9.3.md](VERSION_ROLLBACK-0.9.3.md) for the full
procedure and for the checks that show an ABI change.

## 5. What does not change

- The 20 active directives keep their names, syntax, and defaults.
- The metrics, diagnostics, and reason-code contracts keep their shape.
- The FFI ABI stays at version 3.
- The minimum supported NGINX source version stays at 1.24.0.

## 6. Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| `module is not binary compatible` | The module was built for another NGINX build | Install the artifact for your exact NGINX version, or build from source |
| `cannot open shared object file` | The module file is missing | Reinstall the package |
| `unknown directive "markdown_filter"` | The module is not loaded | Add the `load_module` directive, or enable the package snippet |
| Apt removes the module during an upgrade | The NGINX ABI changed | Install a module for the new revision |
