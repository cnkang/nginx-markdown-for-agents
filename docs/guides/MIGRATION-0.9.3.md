# Migrate to 0.9.3

0.9.3 needs no configuration migration. A configuration file that passes
`nginx -t` on 0.9.2 passes on 0.9.3.

## 1. Confirm that no migration is necessary

1. Test the current configuration against the new module:

   ```bash
   sudo nginx -t
   ```

2. Read the error name when the test fails. A configuration error names a
   directive. A module error names the binary compatibility check.

3. Do these steps when the error is a module error:

   a. Read your NGINX version:

      ```bash
      nginx -v 2>&1
      ```

   b. Install the artifact that matches that version. See
      [UPGRADE-TO-0.9.3.md](UPGRADE-TO-0.9.3.md).

## 2. Move from 0.9.1 or older

A configuration from 0.9.1 or older can contain directives that 0.9.2 removed.
Use [MIGRATION-0.9.2.md](MIGRATION-0.9.2.md) first. Then apply this guide.

## 3. What stays the same

| Contract | State in 0.9.3 |
|---|---|
| Active directives | 20, unchanged |
| Default values | Unchanged |
| Metrics families | Unchanged |
| Diagnostics document | Unchanged |
| Reason codes | Unchanged |
| FFI ABI version | 3 |
| Minimum NGINX source version | 1.24.0 |

## 4. Related

- [0.9.3-breaking-changes.md](0.9.3-breaking-changes.md)
- [UBUNTU-LTS-NGINX-COMPATIBILITY.md](UBUNTU-LTS-NGINX-COMPATIBILITY.md)
