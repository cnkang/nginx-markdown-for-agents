# Migration to 1.0.0-rc.1

The candidate needs no configuration migration.

The 1.0.0-rc.1 candidate keeps every directive, default, HTTP behavior,
metric, diagnostic field, reason code, and FFI entry point from v0.9.4. A
configuration that runs on v0.9.4 runs on the candidate without an edit.

The candidate changes the package version only. Debian and RPM package
metadata uses the mapped prerelease version, so the package sorts below the
final `1.0.0` release.

The candidate is not yet published. v0.9.4 remains the latest public stable
release.
