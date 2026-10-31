# Ubuntu LTS NGINX Compatibility

This guide explains how the Markdown filter module runs on the NGINX package
that Canonical ships in the Ubuntu archive. The module is built from the
Ubuntu source package for the release you run. It is not the nginx.org build.

**Scope**: Ubuntu 24.04 LTS (Noble) and Ubuntu 26.04 LTS (Resolute), amd64 and
arm64.

**Status**: Verified for the releases and package revisions in the support
matrix. Run `bash tools/doctor/nginx-markdown-doctor.sh` to check your host.

## 1. Why the Ubuntu build is different

Ubuntu compiles NGINX with its own configure options and its own security
patches. The result has a different binary compatibility signature from an
nginx.org build of the same upstream version.

NGINX compares that signature when it loads a dynamic module. A module built
against nginx.org `1.24.0` does not load on the Ubuntu `1.24.0` package. The
reverse is also true. The version string is not enough to decide
compatibility.

Ubuntu also changes the package revision when it backports a security fix. A
revision change can keep the ABI, or it can change the ABI. Read the package's
ABI provide, then verify module loading on that exact revision. Ubuntu published
[USN-8563-2](https://ubuntu.com/security/notices/USN-8563-2) and
[USN-8563-4](https://ubuntu.com/security/notices/USN-8563-4) after such a
change affected external modules.

## 2. Check your host

1. Read the installed NGINX package revision:

   ```bash
   dpkg-query -W -f='${Version}\n' nginx
   ```

2. Read the ABI capability that the package provides:

   ```bash
   dpkg-query -W -f='${Provides}\n' nginx
   ```

   The output contains a token such as `nginx-abi-1.24.0-1`.

3. Read the configure arguments:

   ```bash
   nginx -V 2>&1 | tr ' ' '\n' | grep -E 'with-compat|modules-path'
   ```

   Ubuntu builds include `--with-compat`. That option is necessary for a
   third-party module to load.

4. Compare the revision with the support matrix:

   ```bash
   python3 - <<'PY'
   import json
   matrix = json.load(open("tools/release-matrix.json"))
   for entry in matrix["entries"]:
       if entry.get("source") == "ubuntu-default-repo":
           print(entry["nginx_version"], entry["os"], entry["arch"],
                 entry["support_tier"], entry["verification_state"])
   PY
   ```

## 3. Install the Ubuntu-native package

The package name is `nginx-module-markdown-for-agents-ubuntu`. It is not the
same package as `nginx-module-markdown-for-agents`, which targets nginx.org
builds. The two packages conflict because they own the same module file.

1. Download the package for your suite and architecture from the release
   page after publication. Download `SHA256SUMS` and `SHA256SUMS.asc` from
   the same release.

2. Verify the `SHA256SUMS` signature before you verify the checksum. Read the
   expected fingerprint from [GPG_KEY_MANAGEMENT.md](GPG_KEY_MANAGEMENT.md)
   through a channel other than the release download page:

   ```bash
   RELEASE_KEY_PATH=packaging/nginx-markdown-for-agents-release.asc
   TRUSTED_FINGERPRINT="<signing-subkey fingerprint from GPG_KEY_MANAGEMENT.md>"
   GNUPGDIR="$(mktemp -d)"
   gpg --batch --homedir "${GNUPGDIR}" --import "${RELEASE_KEY_PATH}"
   SIGNING_FPR="$(gpg --batch --homedir "${GNUPGDIR}" --status-fd=1 \
     --verify SHA256SUMS.asc SHA256SUMS \
     | awk '$2 == "VALIDSIG" { print toupper($3); exit }')"
   test "${SIGNING_FPR}" = "${TRUSTED_FINGERPRINT}"
   ```

   The last command fails when the signature is missing or comes from another
   key. The key file itself is not an independent channel. Only the
   fingerprint comparison establishes the signer.

3. Verify the checksum:

   ```bash
   DEB="nginx-module-markdown-for-agents-ubuntu_<version>_ubuntu-<suite>_nginx-<revision>_<arch>.deb"
   CHECKSUM_ENTRY="$(
     awk -v filename="${DEB}" '
       $2 == filename { entry = $0; count++ }
       END { if (count != 1) exit 1; print entry }
     ' SHA256SUMS
   )" || { echo "Expected exactly one SHA256SUMS entry for ${DEB}" >&2; exit 1; }
   printf '%s\n' "${CHECKSUM_ENTRY}" | sha256sum --check -
   ```

   This fails when the selected package has no checksum entry or when the
   entry does not match its bytes.

4. Install the package. Do not remove the manual load line yet.

   ```bash
   sudo apt-get install ./nginx-module-markdown-for-agents-ubuntu_<version>_ubuntu-<suite>_nginx-<revision>_<arch>.deb
   ```

5. Confirm that the package enabled the module:

   ```bash
   test -L /etc/nginx/modules-enabled/50-mod-markdown.conf && echo link-present
   ```

   The output must read `link-present`. When it does not, the package did not
   enable the module. Keep the manual `load_module` line from Section 5 and
   follow Section 4 by hand.

6. Remove the manual load line when you installed the module by hand in
   Section 5 and the link is present. The package now loads the same module
   through `/etc/nginx/modules-enabled/`. NGINX aborts with `module ... is
   already loaded` when two load lines name the same module.

   Comment out or delete the `load_module` line in `/etc/nginx/nginx.conf`.
   Then confirm that no load line for the module remains:

   ```bash
   sudo grep -n '^[[:space:]]*load_module' /etc/nginx/nginx.conf
   ```

   The output must not list `ngx_http_markdown_filter_module`. Keep the
   `markdown_filter` directives. The package provides the same module, so the
   directives stay valid.

7. Confirm that NGINX accepts the configuration:

   ```bash
   sudo nginx -t
   ```


The package depends on `nginx-abi-<version>-<n>`, on `nginx-common`, and on a
fixed `libbrotli1`. An ABI change makes the module dependency unsatisfied.
APT can retain the old NGINX package or propose removal of the module.
Neither outcome guarantees a successful upgrade or restart.
Module removal leaves user configuration intact. Remaining module directives
can make `nginx -t` fail with `unknown directive`.

**A failed installation.** The post-install script adds one entry under
`/etc/nginx`: the link `/etc/nginx/modules-enabled/50-mod-markdown.conf`. The
script does not run `nginx -t`. The script does not reload or restart NGINX.
A failed install therefore leaves the running process unchanged. When
`/etc/nginx/modules-enabled` is absent, the script changes nothing.

Uncomment the manual `load_module` line from step 6 when you had one. Delete
the link, then test the configuration:

```bash
sudo rm -f /etc/nginx/modules-enabled/50-mod-markdown.conf
sudo nginx -t
```

The test must pass before you start or reload NGINX. Remove the package when
apt reports it as installed:

```bash
sudo apt-get remove nginx-module-markdown-for-agents-ubuntu
```

## 4. Enable the module

The package installs a snippet at
`/usr/share/nginx/modules-available/mod-markdown.conf`. The post-install
script links the snippet into `/etc/nginx/modules-enabled/`. The Ubuntu
`nginx.conf` includes that directory at the main context.

The module loads with `markdown_filter` set to `off`. Loading the module does
not change the response for any request until you enable the filter.

To enable the filter for one server, add this line to the server block:

```nginx
markdown_filter on;
```

To confirm the result, run these commands:

```bash
sudo nginx -t
sudo systemctl reload nginx
curl -sD - -o /dev/null -H 'Accept: text/markdown' http://localhost/
```

The response must contain `Content-Type: text/markdown` and a `Vary: Accept`
header.

## 5. Build from source on Ubuntu

Use this path when your package revision is newer than the verified revision,
or when you build your own NGINX package.

1. Read the suite and the architecture of the host:

   ```bash
   ARCH="$(dpkg --print-architecture)"
   SUITE="$(sed -n 's/^VERSION_ID="\{0,1\}\([0-9.]*\)"\{0,1\}/\1/p' /etc/os-release)"
   printf 'suite=%s arch=%s\n' "${SUITE}" "${ARCH}"
   ```

   Use these values in the steps below. The script refuses an architecture
   that differs from the host.

2. Install the build dependencies:

   ```bash
   sudo apt-get update
   sudo apt-get install -y nginx nginx-dev build-essential dpkg-dev \
     libpcre2-dev zlib1g-dev libssl-dev libbrotli-dev pkg-config git
   ```

3. Provision the pinned Rust toolchain for the host architecture, and make it
   visible to root:

   ```bash
   bash packaging/scripts/install-verified-rustup.sh --arch "${ARCH}" --toolchain 1.98.1
   sudo env PATH="$(dirname "$(command -v cargo)"):${PATH}" rustc --version
   ```

   The build script does not download a compiler. It reads `rust-toolchain.toml`
   and stops when `rustc` is missing or is a different version. A `sudo` shell
   does not inherit your `~/.cargo/bin` path, so run the build with an explicit
   `PATH` or provision the toolchain for root.

4. Build the module against the installed NGINX:

   ```bash
   sudo -E env PATH="${PATH}" bash packaging/scripts/build-ubuntu-native-module.sh \
     --suite "${SUITE}" --arch "${ARCH}" --out dist/ubuntu-native \
     --install-deps --negative-control
   ```

   The script reads the configure arguments from `nginx -V`, retrieves the
   matching source package, and compiles the module. It fails when the source
   revision differs from the installed binary revision.

5. Verify the module:

   ```bash
   sudo bash packaging/scripts/verify-ubuntu-native-module.sh \
     --module dist/ubuntu-native/ngx_http_markdown_filter_module.so \
     --out dist/ubuntu-native/ubuntu-native-ledger.json \
     --suite "${SUITE}" \
     --incompatible dist/ubuntu-native/ngx_http_markdown_filter_module.incompatible.so
   ```

6. Remove `nginx-module-markdown-for-agents-ubuntu` first when the package is
   present. Two load lines for the same module stop NGINX. Then install the
   module file:

   ```bash
   sudo install -m 0644 \
     dist/ubuntu-native/ngx_http_markdown_filter_module.so \
     /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so
   ```

7. Add the load directive to the main context of `/etc/nginx/nginx.conf`:

   ```nginx
   load_module modules/ngx_http_markdown_filter_module.so;
   ```

8. Test the configuration, then reload NGINX:

   ```bash
   sudo nginx -t
   sudo systemctl reload nginx
   ```

   The test must pass before the reload. A failure names the module path or the
   ABI mismatch.

## 6. Handle a security update

1. Read the NGINX changelog before you upgrade:

   ```bash
   apt-get changelog nginx
   ```

2. Back up the complete configuration, including links and file modes:

   ```bash
   backup_dir="$(mktemp -d "$HOME/nginx-backup.XXXXXX")"
   sudo tar -C /etc -cpf "$backup_dir/nginx-config.tar" nginx
   dpkg-query -W -f='${Package} ${Version}\n' nginx nginx-common \
     nginx-module-markdown-for-agents-ubuntu > "$backup_dir/packages.txt"
   ```

   Keep the previous signed package files beside this backup.

3. Refresh package metadata and simulate the upgrade:

   ```bash
   sudo apt-get update
   apt-get -s upgrade
   apt-get -s install nginx nginx-common
   ```

   Review held packages, removals, and dependency conflicts before installation.
   Do not force broken dependencies or remove the ABI dependency.
   Keep NGINX unchanged until a matching module is available, or disable the
   module with the procedure below. Retaining old packages can delay security fixes.

4. If the transaction removes `nginx-module-markdown-for-agents-ubuntu`, complete step 5a first.
   Test the edited configuration before installation.
   Apply the reviewed transaction, then test the configuration again:

   ```bash
   sudo nginx -t
   sudo apt-get install nginx nginx-common
   sudo nginx -t
   ```

5. Use these steps before removing the module, or after a module error:

   a. Find every module directive and load line:

      ```bash
      sudo grep -R -n -E 'markdown_[[:alnum:]_]+|load_module.*markdown' /etc/nginx
      sudoedit /etc/nginx/nginx.conf
      ```

      Edit each file from the search results. Disable all module directives,
      including `markdown_filter`, limits, cache settings, metrics, and diagnostics.
      Disable manual Markdown `load_module` lines. Preserve unrelated configuration.

   b. Simulate removal, then remove the package:

      ```bash
      apt-get -s remove nginx-module-markdown-for-agents-ubuntu
      sudo apt-get remove nginx-module-markdown-for-agents-ubuntu
      ```

      Skip this step when you built the module from source in Section 5. That
      path installs no package.

      The removal script deletes
      `/etc/nginx/modules-enabled/50-mod-markdown.conf` before it deletes the
      module file. The link needs no manual cleanup. The script deletes the
      link only when it points at
      `/usr/share/nginx/modules-available/mod-markdown.conf`. Inspect custom
      snippets and links before changing them. Disable only the Markdown
      load configuration that you own. Preserve unrelated files and links.

   c. Disable the manual load line from Section 5 when it is still active.
      Comment out or delete this line in `/etc/nginx/nginx.conf`:

      ```nginx
      load_module modules/ngx_http_markdown_filter_module.so;
      ```

      That module no longer matches the updated NGINX binary. NGINX fails to
      start while the load line stays active.

      Review the search results again. No active module directive may remain
      when the module is absent.

   d. Test before starting or reloading NGINX:

      ```bash
      sudo nginx -t
      sudo systemctl reload-or-restart nginx
      ```

   e. Install or build a module for the new revision. Section 3 and
      Section 5 give the two paths.

Never copy a module from another Ubuntu release. Never keep a module that
fails the load test.

## 7. Roll back

A matching ABI is necessary, but it does not prove module loadability.
Use the previous module only with its verified package revision and configuration.
For another revision, rebuild or verify the module before restoring service.

1. Simulate installation of the saved package:

   ```bash
   apt-get -s install ./<previous-package-file>
   sudo apt-get install --allow-downgrades ./<previous-package-file>
   ```

2. Test and reload:

   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

Restore configuration only after restoring compatible packages:

Review files added after the backup. Remove only identified files from the failed
upgrade before restoring. Archive extraction does not remove additional files.

```bash
sudo tar -C /etc -xpf "$backup_dir/nginx-config.tar"
sudo nginx -t
sudo systemctl reload-or-restart nginx
```

Keep a copy of the previous package file when you upgrade. The support matrix
records the exact package revisions that this project verified.

## 8. Evidence and limits

The project records one evidence file per verified target under
`docs/evidence/<release>/compat/`. Each file contains the package revision,
the ABI provide, the source digest, the module checksum, the configure
argument digest, and every test result.

On 2026-10-31, fresh arm64 Ubuntu containers reported these archive candidates:

| Suite | NGINX revision | ABI provide |
|---|---|---|
| 24.04 | `1.24.0-2ubuntu7.19` | `nginx-abi-1.24.0-1` |
| 26.04 | `1.28.3-2ubuntu1.12` | `nginx-abi-1.28.3-1` |

[USN-8955-1](https://ubuntu.com/security/notices/USN-8955-1) lists the same fixed revisions.
Both archives also exposed base revisions with the same ABI provide.
No real ABI-changing upgrade pair was available for this check.
APT hold and removal outcomes therefore remain conditional, not verified upgrade results.
The archive check does not establish module loadability on another revision.

Limits:

- Ubuntu moves package revisions without notice. A revision that is newer than
  the recorded one needs a new build and a new load test.
- The verified architecture set is amd64 and arm64. Other architectures are
  not supported.
- The module links the Ubuntu `libbrotli1` decoder. The package requires the
  revision that contains the decoder fix for CVE-2026-111497.
