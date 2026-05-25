# RPM SOURCES Directory

This directory holds source tarballs used by `rpmbuild` during package creation.

## Usage

When building the RPM package, the source tarball is generated from the
repository and placed here:

```bash
# Generate source tarball (typically done by CI)
git archive --format=tar.gz --prefix=nginx-module-markdown-0.7.0/ \
    -o packaging/rpm/SOURCES/nginx-module-markdown-0.7.0.tar.gz HEAD
```

The `Source0` field in the SPEC file references this tarball by name.

## CI Workflow

In the CI pipeline (`release-rpm.yml`), the source tarball is generated
automatically from the tagged commit. This directory serves as the
standard `rpmbuild` SOURCES location.

## Contents

Source tarballs are not committed to version control (see `.gitignore`).
They are generated at build time from the repository state.
