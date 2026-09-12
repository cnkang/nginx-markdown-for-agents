# ADR-0028: Source-Archive Provenance Anchors on the Commit, Not the Tag Archive

## Status

Accepted

## Date

2026-09-12

## Context

The 0.9.2 release records where its source came from. The first implementation
stored a SHA-256 digest of the GitHub tag archive in
`packaging/source-archive-digests.sha256`, then required every tag release to
find its own entry there and fail closed when it was missing.

No implementation can satisfy that requirement. The registry file is part of the tree, so
committing an entry changes the tree, and GitHub generates a tag archive from the
tree at that tag. No one can therefore record an entry describing a tag's own archive before
the tag exists, and the digest recorded afterwards never matches
the archive the tag produces. Two independent reviews reached this conclusion
independently, and the digest requirement in the release-manifest validator had
to make the digest optional as a consequence.

## Decision

- The pre-release provenance anchor is the **commit identity**. Candidate
  evidence records the commit the release was cut from, and the release gates
  verify it, so an unreviewed commit cannot back a tag.
- The registry records the digest of the **published** archive. A maintainer
  creates that entry **after** the tag's publication, in a later commit. Because the tag's
  tree is already fixed at that point, the recorded digest stays valid.
- The release manifest carries `source.sha256` when the registry has an entry and
  omits it otherwise. The manifest validator treats the field as **optional** for
  tag releases and still checks its format when it is present.
- A missing entry produces a `::warning::` annotation on the release run, naming
  the command that records it, so the follow-up cannot be forgotten silently.

## Consequences

- No release step derives a digest from a live download of the tag archive, which
  was the original concern: such a value anchors nothing.
- The first release of a tag publishes a manifest without `source.sha256`. The
  digest becomes available once the maintainer records it, and later runs of the
  same tag reuse the recorded value.
- Reviews that expect a fail-closed digest check at tag time will keep reporting
  this design as a defect. This ADR is the recorded answer: we removed the check deliberately, and
  re-adding it reintroduces the circularity.
