# RPM SOURCES Directory

This directory holds source tarballs used by `rpmbuild` during package creation.

## Usage

When building the RPM package, the build generates the source tarball from
the repository and places it here:

```bash
# Generate source tarball (typically done by CI). Archive the exact signed
# release tag (e.g. v0.9.2) or the release-selected commit so the tarball
# matches the package version and the release checksums — never a moving HEAD.
git archive --format=tar.gz --prefix=nginx-module-markdown-for-agents-0.9.2/ \
    -o packaging/rpm/SOURCES/nginx-module-markdown-for-agents-0.9.2.tar.gz v0.9.2
```

The `Source0` field in the SPEC file references this tarball by name.

## CI Workflow

In the CI pipeline (`release-rpm.yml`), the pipeline generates the source
tarball automatically from the tagged commit. This directory serves as the
standard `rpmbuild` SOURCES location.

## Contents

Source tarballs are not committed to version control (see `.gitignore`).
The build generates them at build time from the repository state.
