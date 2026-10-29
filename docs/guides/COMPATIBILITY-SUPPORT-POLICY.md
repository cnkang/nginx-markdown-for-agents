# Compatibility Support Policy

This document defines the support contracts that the release matrix records.
It keeps four ideas separate: source compatibility, upstream binaries,
distribution compatibility, and published assets.

## 1. The four contracts

| Contract | Question | Owner | Recorded in |
|---|---|---|---|
| Source compatibility floor | Can I build the module for this NGINX version? | Maintainer | `policy_exclusions` and the source rows |
| Upstream official binaries | Does the project publish an nginx.org artifact for this exact build? | Release workflow | `dynamic-module`, `deb-package`, and `rpm-package` rows with `source: nginx.org` |
| Distribution compatibility | Does the module load on the NGINX package that a distribution ships? | Release workflow | Rows with `source: ubuntu-default-repo` |
| Published assets | Does a given tag contain a downloadable file? | Release history | The GitHub Release assets for that tag |

A row in the matrix states an intention for the next release. It does not
prove that an old tag contains the file. Read the tag before you download.

## 2. Support tiers

| Tier | Meaning | Release blocking |
|---|---|---|
| `supported` | Build, load, and request checks pass on a recorded environment. | Yes |
| `best-effort` | The project may build an artifact. The row does not block a release. | No |
| `experimental` | The project publishes the artifact without a compatibility promise. | No |
| `unsupported` | No artifact and no commitment. | No |

The `release_blocking` flag is the machine-readable form of the third column.
A supported row must be release-blocking. The matrix validator enforces that
rule.

## 3. How a version becomes supported

1. The weekly discovery workflow reads the nginx.org download page. It adds a
   new version as `best-effort`, `pending`, and not release-blocking.
2. A maintainer runs the release workflow on a branch. The workflow builds the
   module, loads it, and serves a real request.
3. The workflow writes one evidence record per target.
4. A maintainer runs the promotion tool with those records:

   ```bash
   python3 tools/release/matrix/promote_matrix.py \
     --evidence docs/evidence/<release>/compat/<target>.json \
     --demote-superseded
   ```

   `--demote-superseded` moves older patches of the same minor track to
   `best-effort`. The older row keeps its artifact history. The row stops
   blocking a release.
5. The maintainer regenerates the projections and runs the matrix gate:

   ```bash
   python3 tools/release/matrix/generate_release_contract_matrix.py --write
   python3 tools/render_release_matrix_docs.py --write
   make release-matrix-check
   ```

6. The evidence gate must pass:

   ```bash
   make compatibility-evidence-check
   ```

Discovery never promotes a version. Promotion always needs evidence.

## 4. Current targets

**nginx.org**: the latest stable and the latest mainline release, glibc and
musl, amd64 and arm64. The compatibility floor is 1.24.0. The 1.24.0, 1.26.3,
and 1.28.3 baseline rows keep their prebuilt artifacts because nginx.org still
serves those releases.

**Ubuntu LTS**: 24.04 and 26.04, amd64 and arm64. Each Ubuntu row binds to one
package revision and one ABI provide. The release workflow refuses to build
when the archive revision differs from the recorded revision. This rule makes
the maintainer review the new revision before a release can use it.

## 5. Maintainer duties

| Event | Action | Deadline |
|---|---|---|
| nginx.org publishes a new stable or mainline release | Run the release workflow, promote the version, demote the older patch | Before the next release |
| Ubuntu publishes a new NGINX revision in the same ABI | Update the revision in the matrix and the evidence | Before the next release |
| Ubuntu publishes a revision with a new ABI | Rebuild the module, publish a new package, write an operations note | Before the next release |
| A CVE affects the bundled decoder | Rebuild every artifact that links a decoder | Before the next release |
| A supported row has no evidence record | Either produce a record or change the tier | Before the next release |

## 6. What the project does not promise

- The project does not promise a binary for every historical patch.
- The project does not promise that an nginx.org package works on a
  distribution package with the same version string.
- The project does not promise that an old module loads after an ABI change.
- The project does not publish a package for an architecture that has no
  verified evidence.

## 7. Checks

| Command | Purpose |
|---|---|
| `make release-matrix-check` | Matrix schema, ABI binding, and projection drift |
| `make compatibility-evidence-check` | Evidence contract and verified rows |
| `make release-gates-check-093` | The full 0.9.3 release gate |
