# CI execution and evidence reuse

These changes target future development on `main`. They do not change the
`v0.9.3` tag or its release run.

## One short-soak measurement per release candidate

The release package workflow measures the candidate in the independent
short-soak step, then uploads its manifest and qualification record. The final
release gates reuse that record instead of running another 30-minute soak.

The `verify` mode checks the existing record against the manifest and current
git HEAD. It checks duration, concurrency, scenario results, worker RSS, and
peak memory. Missing evidence, skipped runs, stale candidates, and failed
thresholds block the gate. Verification does not launch NGINX or write a record.
The earlier measurement and the final evidence therefore use the same record.

For a record from the current checkout, run:

```sh
python3 tools/release/gates/validate_soak_qualification.py \
  --mode verify \
  --manifest /path/to/short-soak-scenario-manifest.json \
  --record-input /path/to/soak-qualification-record.json
```

Use `--mode real` to create new measurements. Fixture mode remains a test-only
entry point that permits explicit skip records. Real measurements print the
load duration and concurrency, progress after each load chunk, and the start
of RSS drain sampling. Each progress message flushes immediately.

Removing the second soak saves about 31 minutes per release gate. This estimate
uses the first successful soak in [release run 41395399789][release-run], which
took 31 minutes and 12 seconds. Actual times vary.

## Independent checks and obsolete PR revisions

- Runtime Regressions builds its own NGINX runtime and can overlap Rust Quality.
  Both required checks retain their names and results. In [CI run
  41401844181][ci-run], runtime checks took 2 minutes and 29 seconds after Rust
  Quality finished. Available runners determine the actual time saved.
- SonarCloud restores the Rust cache before native analysis and C coverage,
  because both paths also compile the Rust converter. Cache hits determine
  the benefit. Both coverage runs remain required.
- ClusterFuzzLite PR and Homebrew PR checks cancel superseded runs of the same
  PR. Different PRs keep separate groups. Homebrew push runs use unique run
  IDs, so each main-branch revision retains its validation.

## Audit decisions

The audit covered all workflow files. CodeQL already separates language jobs
and restores Rust caches before its C build. Nightly performance gates already
reuse their measured raw report.

The release Docker job still waits for fuzz qualification. The workflow tests
encode this resource policy, and final release gates consume producer evidence.
Changing that dependency needs a separate policy decision. CI and SonarCloud
also retain separate coverage collection because their triggers, credentials,
and evidence consumers differ. The small repeated Doctor fixture checks do not
justify a broader change.

[release-run]: https://github.com/cnkang/nginx-markdown-for-agents/actions/runs/41395399789/job/125199748043
[ci-run]: https://github.com/cnkang/nginx-markdown-for-agents/actions/runs/41401844181
