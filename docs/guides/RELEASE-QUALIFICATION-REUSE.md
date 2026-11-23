# Release Qualification Reuse

This workflow avoids repeating the long fuzz qualification and short soak when
the exact release candidate has already passed them during a complete
preflight run.

1. Push the candidate commit to a branch and dispatch `release-packages.yml`
   for that branch with `require_blocking=true` and `final_metadata=true`.
2. Wait for the entire workflow run to complete successfully. Its release gate
   then uploads an attempt-specific qualification bundle.
3. Point the release tag at the same full commit SHA. The tag run checks the
   source run, artifact, attestation, and both qualification records before it
   reuses them.

The tag workflow reuses only fuzz qualification and short-soak results.
Package builds, compatibility evidence, release candidate gates, performance
checks, signing, and publication still run in the tag workflow. If the source
run, artifact, or record is missing, expired, or does not match, the tag
workflow runs both long checks normally. This preflight does not approve or
publish a release.
