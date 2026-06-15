/*
 * Streaming Fallback State Machine — Decision Engine Interface
 *
 * Pure-function decision engine for the streaming fallback state machine
 * defined in the streaming fallback state machine design.  Given a current state context and an
 * incoming event, the engine returns a deterministic decision (new state,
 * action, reason code) without side effects.
 *
 * This header declares the event enum, minimal decision context, and the
 * public decision function prototype.  The state, action, reason, and
 * decision types themselves live in ngx_http_markdown_filter_module.h
 * because they are shared across multiple compilation units.
 */

#ifndef NGX_HTTP_MARKDOWN_STREAM_STATE_H_INCLUDED_
#define NGX_HTTP_MARKDOWN_STREAM_STATE_H_INCLUDED_

#include "ngx_http_markdown_filter_module.h"


/*
 * Event enum: stimuli that drive state transitions.
 *
 * Each value represents a single observable condition or request that
 * the decision engine must react to.  The engine never interprets raw
 * HTTP data; callers translate low-level conditions into events before
 * invoking the decision function.
 */
typedef enum {
    NGX_HTTP_MD_EVENT_ELIGIBLE = 0,         /* Request eligible for streaming */
    NGX_HTTP_MD_EVENT_NOT_ELIGIBLE,         /* Request not eligible */
    NGX_HTTP_MD_EVENT_STREAMING_START,      /* Streaming begins */
    NGX_HTTP_MD_EVENT_PARSER_UNSUITABLE,    /* Parser cannot handle content */
    NGX_HTTP_MD_EVENT_HARD_EXCLUDED,        /* Content type excluded */
    NGX_HTTP_MD_EVENT_FULL_DOC_FEATURE,     /* Full-document feature required */
    NGX_HTTP_MD_EVENT_BUDGET_INIT_FAILURE,  /* Budget/init failed */
    NGX_HTTP_MD_EVENT_REPLAY_OVERFLOW,      /* Replay buffer overflowed */
    NGX_HTTP_MD_EVENT_RESOURCE_LIMIT,       /* Resource limit exceeded */
    NGX_HTTP_MD_EVENT_STRICT_ETAG,          /* Strict ETag requires full buf */
    NGX_HTTP_MD_EVENT_LOOK_BEHIND_OVERFLOW, /* Look-behind overflow */
    NGX_HTTP_MD_EVENT_AUTO_RISK,            /* Auto risk assessment trigger */
    NGX_HTTP_MD_EVENT_COMMIT,              /* Header commit requested */
    NGX_HTTP_MD_EVENT_ERROR,               /* Post-commit error occurred */
    NGX_HTTP_MD_EVENT_ON_ERROR_PASS,       /* on_error=pass policy event */
    NGX_HTTP_MD_EVENT_ON_ERROR_REJECT      /* on_error=reject policy event */
} ngx_http_markdown_stream_event_e;


/*
 * Decision context: minimal inputs for the decision function.
 *
 * The caller populates this struct from the request context before
 * each call.  The decision function reads these fields but never
 * modifies them (const-correct usage encouraged).
 */
typedef struct {
    ngx_http_markdown_stream_state_e  current_state;
    ngx_flag_t                        replay_available;
    ngx_flag_t                        headers_committed;
    ngx_flag_t                        within_resource_limits;
    ngx_uint_t                        on_error_policy;
} ngx_http_markdown_stream_ctx_t;


/*
 * Compute the next decision given the current context and event.
 *
 * This is a pure function: no allocations, no I/O, no global state.
 * The result is fully determined by the input parameters.
 *
 * Returns:
 *   A decision struct with new_state, action, and reason fields.
 *   The function always returns a valid decision; unrecognized
 *   state/event combinations produce PASSTHROUGH with reason
 *   NOT_ELIGIBLE.
 */
ngx_http_markdown_decision_t
ngx_http_markdown_stream_decide(const ngx_http_markdown_stream_ctx_t *ctx,
                                ngx_http_markdown_stream_event_e event);


#endif /* NGX_HTTP_MARKDOWN_STREAM_STATE_H_INCLUDED_ */
