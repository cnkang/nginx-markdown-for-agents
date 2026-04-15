## NGINX lifecycle and return-code semantics
Use NGINX return codes with strict semantics (`NGX_OK`, `NGX_DECLINED`,
`NGX_AGAIN`, `NGX_DONE`, `NGX_ERROR`). In HTTP phase/filter logic, treat
`NGX_AGAIN` as suspend-and-resume and verify request finalization paths remain
correct.

## Header/body ordering and fallback idempotency
Never emit body data before headers. Fail-open or fallback paths should keep
header forwarding state explicit and idempotent to avoid duplicate header
emission.

## Streaming backpressure correctness
When downstream returns `NGX_AGAIN`, pending output must remain in context until
it is consumed. Avoid transitions that lose pending chain segments or emit
terminal `last_buf` before queued data is forwarded.

## Memory and bounds discipline
Use pool-oriented request-lifetime allocation semantics and enforce configured
memory limits on every request-path allocation or expansion path.

## Cache, eligibility, and status consistency
Flag logic that can desynchronize eligibility reason codes, protocol edge-status
mapping (including partial content scenarios), or cache variant behavior.

## Regression coverage expectations
For fixes in streaming, parsing, sanitizer, fallback, metrics, or reason-code
paths, include targeted regression tests, including malformed-input and
cross-boundary cases where relevant.
