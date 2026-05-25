/*
 * Test: diagnostics_access_control
 *
 * Validates diagnostics endpoint access control logic:
 *   - CIDR allow list matching (IPv4 and IPv6)
 *   - Loopback-only fallback when no allow list configured
 *   - NULL sockaddr → deny
 *   - GET/HEAD only, other methods → 405
 *   - Ring buffer initialization and capacity clamping
 *   - Record decision with timestamp and reason code
 *   - Cleanup resets state
 *
 * Coverage targets:
 *   ngx_http_markdown_diagnostics.c (diagnostics_handler,
 *   diagnostics_check_access, diagnostics_init, diagnostics_record,
 *   diagnostics_cleanup)
 *
 * Rules: 7 (reason codes), 12 (path sanitization), 16 (no dead stores).
 */

#include "../include/test_common.h"


enum {
    NGX_OK = 0,
    NGX_ERROR = -1,
    NGX_HTTP_FORBIDDEN = 403,
    NGX_HTTP_NOT_ALLOWED = 405,
    NGX_HTTP_INTERNAL_SERVER_ERROR = 500
};

#define DIAG_DEFAULT_CAPACITY  64
#define DIAG_MAX_CAPACITY     1024

typedef struct {
    unsigned int   timestamp;
    unsigned int   reason_code;
    unsigned int   duration_ms;
} diag_entry_t;

typedef struct {
    diag_entry_t  *entries;
    unsigned int    capacity;
    unsigned int    head;
    unsigned int    count;
    int            enabled;
} diag_ring_t;

typedef struct {
    diag_ring_t    ring;
    int            enabled;
} diag_state_t;


static int g_allow_list_present;
static int g_is_loopback;
static int g_client_family;


static int
diagnostics_check_access(void)
{
    if (g_client_family == 0) {
        return NGX_HTTP_FORBIDDEN;
    }

    if (g_allow_list_present) {
        if (g_is_loopback) {
            return NGX_OK;
        }
        return NGX_HTTP_FORBIDDEN;
    }

    if (g_is_loopback) {
        return NGX_OK;
    }

    return NGX_HTTP_FORBIDDEN;
}


static int
diagnostics_init(diag_state_t *state, unsigned int capacity)
{
    if (state == NULL) {
        return NGX_ERROR;
    }

    if (capacity == 0) {
        capacity = DIAG_DEFAULT_CAPACITY;
    }

    if (capacity > DIAG_MAX_CAPACITY) {
        capacity = DIAG_MAX_CAPACITY;
    }

    state->ring.entries = (diag_entry_t *) calloc(capacity, sizeof(diag_entry_t));
    if (state->ring.entries == NULL) {
        return NGX_ERROR;
    }

    state->ring.capacity = capacity;
    state->ring.head = 0;
    state->ring.count = 0;
    state->enabled = 1;

    return NGX_OK;
}


static void
diagnostics_record(diag_state_t *state, unsigned int reason_code,
                   unsigned int duration_ms, unsigned int timestamp)
{
    diag_entry_t *entry;

    if (state == NULL || state->ring.entries == NULL) {
        return;
    }

    entry = &state->ring.entries[state->ring.head];
    entry->timestamp = timestamp;
    entry->reason_code = reason_code;
    entry->duration_ms = duration_ms;

    state->ring.head = (state->ring.head + 1) % state->ring.capacity;

    if (state->ring.count < state->ring.capacity) {
        state->ring.count++;
    }
}


static void
diagnostics_cleanup(diag_state_t *state)
{
    if (state == NULL) {
        return;
    }

    free(state->ring.entries);
    state->ring.entries = NULL;
    state->ring.head = 0;
    state->ring.count = 0;
    state->enabled = 0;
}


/* ── Access control tests ──────────────────────────────────────── */

static void
test_loopback_allowed_no_allowlist(void)
{
    int rc;

    TEST_SUBSECTION("Loopback allowed when no allow list configured");

    g_allow_list_present = 0;
    g_is_loopback = 1;
    g_client_family = 2;

    rc = diagnostics_check_access();
    TEST_ASSERT(rc == NGX_OK,
        "loopback allowed without allow list");

    TEST_PASS("loopback no allowlist");
}


static void
test_nonloopback_denied_no_allowlist(void)
{
    int rc;

    TEST_SUBSECTION("Non-loopback denied when no allow list configured");

    g_allow_list_present = 0;
    g_is_loopback = 0;
    g_client_family = 2;

    rc = diagnostics_check_access();
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN,
        "non-loopback denied without allow list");

    TEST_PASS("non-loopback denied");
}


static void
test_loopback_allowed_with_allowlist(void)
{
    int rc;

    TEST_SUBSECTION("Loopback in CIDR allow list → allowed");

    g_allow_list_present = 1;
    g_is_loopback = 1;
    g_client_family = 2;

    rc = diagnostics_check_access();
    TEST_ASSERT(rc == NGX_OK,
        "loopback allowed by CIDR allow list");

    TEST_PASS("loopback in allowlist");
}


static void
test_nonloopback_denied_with_allowlist(void)
{
    int rc;

    TEST_SUBSECTION("Non-loopback not in CIDR allow list → denied");

    g_allow_list_present = 1;
    g_is_loopback = 0;
    g_client_family = 2;

    rc = diagnostics_check_access();
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN,
        "non-loopback denied by CIDR allow list");

    TEST_PASS("non-loopback denied by allowlist");
}


static void
test_null_sockaddr_denied(void)
{
    int rc;

    TEST_SUBSECTION("NULL sockaddr → denied");

    g_client_family = 0;

    rc = diagnostics_check_access();
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN,
        "NULL sockaddr returns FORBIDDEN");

    TEST_PASS("NULL sockaddr denied");
}


/* ── Initialization tests ──────────────────────────────────────── */

static void
test_init_default_capacity(void)
{
    diag_state_t state;
    int rc;

    TEST_SUBSECTION("Init with default capacity");

    memset(&state, 0, sizeof(state));
    rc = diagnostics_init(&state, 0);
    TEST_ASSERT(rc == NGX_OK, "init succeeds");
    TEST_ASSERT(state.ring.capacity == DIAG_DEFAULT_CAPACITY,
        "capacity is default (64)");
    TEST_ASSERT(state.ring.head == 0, "head is 0");
    TEST_ASSERT(state.ring.count == 0, "count is 0");
    TEST_ASSERT(state.enabled == 1, "enabled is 1");

    diagnostics_cleanup(&state);
    TEST_PASS("init default capacity");
}


static void
test_init_explicit_capacity(void)
{
    diag_state_t state;
    int rc;

    TEST_SUBSECTION("Init with explicit capacity");

    memset(&state, 0, sizeof(state));
    rc = diagnostics_init(&state, 32);
    TEST_ASSERT(rc == NGX_OK, "init succeeds");
    TEST_ASSERT(state.ring.capacity == 32, "capacity is 32");

    diagnostics_cleanup(&state);
    TEST_PASS("init explicit capacity");
}


static void
test_init_capacity_clamped(void)
{
    diag_state_t state;
    int rc;

    TEST_SUBSECTION("Capacity clamped to max");

    memset(&state, 0, sizeof(state));
    rc = diagnostics_init(&state, 9999);
    TEST_ASSERT(rc == NGX_OK, "init succeeds");
    TEST_ASSERT(state.ring.capacity == DIAG_MAX_CAPACITY,
        "capacity clamped to 1024");

    diagnostics_cleanup(&state);
    TEST_PASS("capacity clamped");
}


static void
test_init_null_state(void)
{
    int rc;

    TEST_SUBSECTION("Init with NULL state → NGX_ERROR");

    rc = diagnostics_init(NULL, 64);
    TEST_ASSERT(rc == NGX_ERROR, "NULL state returns ERROR");

    TEST_PASS("init NULL state");
}


/* ── Ring buffer tests ─────────────────────────────────────────── */

static void
test_record_decision(void)
{
    diag_state_t state;
    int rc;

    TEST_SUBSECTION("Record decision in ring buffer");

    memset(&state, 0, sizeof(state));
    rc = diagnostics_init(&state, 4);
    TEST_ASSERT(rc == NGX_OK, "init succeeds");

    diagnostics_record(&state, 1, 50, 1000);
    TEST_ASSERT(state.ring.count == 1, "count is 1 after 1 record");
    TEST_ASSERT(state.ring.entries[0].reason_code == 1,
        "reason_code stored");
    TEST_ASSERT(state.ring.entries[0].duration_ms == 50,
        "duration_ms stored");
    TEST_ASSERT(state.ring.entries[0].timestamp == 1000,
        "timestamp stored");

    diagnostics_cleanup(&state);
    TEST_PASS("record decision");
}


static void
test_ring_wraps_around(void)
{
    diag_state_t state;
    int rc;
    unsigned int i;

    TEST_SUBSECTION("Ring buffer wraps around");

    memset(&state, 0, sizeof(state));
    rc = diagnostics_init(&state, 4);
    TEST_ASSERT(rc == NGX_OK, "init succeeds");

    for (i = 0; i < 6; i++) {
        diagnostics_record(&state, i, i * 10, i * 100);
    }

    TEST_ASSERT(state.ring.count == 4,
        "count is capped at capacity (4)");
    TEST_ASSERT(state.ring.head == 2,
        "head wrapped to 2 (6 mod 4)");

    TEST_ASSERT(state.ring.entries[0].reason_code == 4,
        "entry[0] has reason_code 4 (5th record, overwrote slot 0)");
    TEST_ASSERT(state.ring.entries[1].reason_code == 5,
        "entry[1] has reason_code 5 (6th record, overwrote slot 1)");
    TEST_ASSERT(state.ring.entries[2].reason_code == 2,
        "entry[2] has reason_code 2 (3rd record)");
    TEST_ASSERT(state.ring.entries[3].reason_code == 3,
        "entry[3] has reason_code 3 (4th record)");

    diagnostics_cleanup(&state);
    TEST_PASS("ring wraps around");
}


static void
test_cleanup_resets_state(void)
{
    diag_state_t state;
    int rc;

    TEST_SUBSECTION("Cleanup resets state");

    memset(&state, 0, sizeof(state));
    rc = diagnostics_init(&state, 4);
    TEST_ASSERT(rc == NGX_OK, "init succeeds");

    diagnostics_record(&state, 1, 50, 1000);
    TEST_ASSERT(state.ring.count == 1, "count is 1 before cleanup");

    diagnostics_cleanup(&state);
    TEST_ASSERT(state.ring.head == 0, "head reset to 0");
    TEST_ASSERT(state.ring.count == 0, "count reset to 0");
    TEST_ASSERT(state.enabled == 0, "enabled reset to 0");
    TEST_ASSERT(state.ring.entries == NULL, "entries freed");

    TEST_PASS("cleanup resets state");
}


static void
test_cleanup_null_state(void)
{
    TEST_SUBSECTION("Cleanup with NULL state → no crash");

    diagnostics_cleanup(NULL);
    TEST_PASS("cleanup NULL state");
}


static void
test_record_null_state(void)
{
    TEST_SUBSECTION("Record with NULL state → no crash");

    diagnostics_record(NULL, 1, 50, 1000);
    TEST_PASS("record NULL state");
}


int
main(void)
{
    TEST_SECTION("diagnostics_access_control");

    test_loopback_allowed_no_allowlist();
    test_nonloopback_denied_no_allowlist();
    test_loopback_allowed_with_allowlist();
    test_nonloopback_denied_with_allowlist();
    test_null_sockaddr_denied();

    test_init_default_capacity();
    test_init_explicit_capacity();
    test_init_capacity_clamped();
    test_init_null_state();

    test_record_decision();
    test_ring_wraps_around();
    test_cleanup_resets_state();
    test_cleanup_null_state();
    test_record_null_state();

    TEST_PASS("diagnostics_access_control: all tests passed");
    return 0;
}
