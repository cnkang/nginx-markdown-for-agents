/*
 * Test: header_plan
 *
 * Validates the header plan atomic application and rollback logic.
 * A header plan is a list of set/delete/modify operations that must
 * be applied atomically: either all succeed or all are rolled back.
 *
 * State machine:
 *   EMPTY -> BUILDING (add operations) -> APPLY -> [success] -> APPLIED
 *                                               -> [failure] -> ROLLED_BACK
 *
 * Corresponds to task B04.6.
 *
 * Rules: 15 (FFI struct changes → both sides), 29 (clear flags after
 * gated op succeeds, not before).
 */

#include "../include/test_common.h"


enum {
    NGX_OK    =  0,
    NGX_ERROR = -1
};

/* Header operation types */
typedef enum {
    HEADER_OP_SET = 0,
    HEADER_OP_DELETE,
    HEADER_OP_MODIFY
} header_op_type_t;

#define MAX_PLAN_OPS 16
#define MAX_HEADERS  16

/* A single header operation in the plan */
typedef struct {
    header_op_type_t  op;
    const char       *name;
    const char       *value;       /* NULL for DELETE */
} header_op_t;

/* Simulated response headers (simple key-value store) */
typedef struct {
    const char  *name;
    const char  *value;
    int          present;
} header_entry_t;

typedef struct {
    header_entry_t  entries[MAX_HEADERS];
    int             count;
} header_table_t;

/* The header plan */
typedef struct {
    header_op_t   ops[MAX_PLAN_OPS];
    int           op_count;
    int           applied;
    int           rolled_back;
} header_plan_t;


/*
 * Initialize a header plan.
 */
static void
header_plan_init(header_plan_t *plan)
{
    memset(plan, 0, sizeof(*plan));
}


/*
 * Add an operation to the plan.
 */
static int
header_plan_add(header_plan_t *plan, header_op_type_t op,
    const char *name, const char *value)
{
    if (plan->op_count >= MAX_PLAN_OPS) {
        return NGX_ERROR;
    }

    plan->ops[plan->op_count].op = op;
    plan->ops[plan->op_count].name = name;
    plan->ops[plan->op_count].value = value;
    plan->op_count++;
    return NGX_OK;
}


/*
 * Find a header in the table by name.
 */
static int
header_table_find(header_table_t *table, const char *name)
{
    int i;

    for (i = 0; i < table->count; i++) {
        if (table->entries[i].present &&
            strcmp(table->entries[i].name, name) == 0) {
            return i;
        }
    }
    return -1;
}


/*
 * Set a header in the table (add or update).
 */
static int
header_table_set(header_table_t *table, const char *name, const char *value)
{
    int idx;

    idx = header_table_find(table, name);
    if (idx >= 0) {
        table->entries[idx].value = value;
        return NGX_OK;
    }

    if (table->count >= MAX_HEADERS) {
        return NGX_ERROR;
    }

    table->entries[table->count].name = name;
    table->entries[table->count].value = value;
    table->entries[table->count].present = 1;
    table->count++;
    return NGX_OK;
}


/*
 * Delete a header from the table.
 */
static int
header_table_delete(header_table_t *table, const char *name)
{
    int idx;

    idx = header_table_find(table, name);
    if (idx >= 0) {
        table->entries[idx].present = 0;
        return NGX_OK;
    }
    /* Deleting non-existent header is not an error */
    return NGX_OK;
}


/*
 * Apply a header plan atomically.  If any operation fails (simulated
 * by fail_at_op index), all previously applied operations are rolled back.
 *
 * Parameters:
 *   plan       — the plan to apply
 *   table      — the header table to modify
 *   fail_at_op — index at which to simulate failure (-1 for no failure)
 *
 * Returns:
 *   NGX_OK on full success
 *   NGX_ERROR on failure (with rollback performed)
 */
static int
header_plan_apply(header_plan_t *plan, header_table_t *table, int fail_at_op)
{
    /* Save a snapshot for rollback */
    header_table_t snapshot;
    int i;
    int rc;

    memcpy(&snapshot, table, sizeof(snapshot));

    for (i = 0; i < plan->op_count; i++) {
        if (i == fail_at_op) {
            /* Simulate failure: rollback */
            memcpy(table, &snapshot, sizeof(*table));
            plan->rolled_back = 1;
            plan->applied = 0;
            return NGX_ERROR;
        }

        switch (plan->ops[i].op) {
        case HEADER_OP_SET:
            rc = header_table_set(table, plan->ops[i].name,
                plan->ops[i].value);
            if (rc != NGX_OK) {
                memcpy(table, &snapshot, sizeof(*table));
                plan->rolled_back = 1;
                plan->applied = 0;
                return NGX_ERROR;
            }
            break;

        case HEADER_OP_DELETE:
            header_table_delete(table, plan->ops[i].name);
            break;

        case HEADER_OP_MODIFY:
            rc = header_table_set(table, plan->ops[i].name,
                plan->ops[i].value);
            if (rc != NGX_OK) {
                memcpy(table, &snapshot, sizeof(*table));
                plan->rolled_back = 1;
                plan->applied = 0;
                return NGX_ERROR;
            }
            break;
        }
    }

    plan->applied = 1;
    plan->rolled_back = 0;
    return NGX_OK;
}


/* ── Test: successful atomic application ──────────────────────── */

static void
test_atomic_apply_success(void)
{
    header_plan_t plan;
    header_table_t table;
    int rc;
    int idx;

    TEST_SUBSECTION("Atomic application succeeds");

    memset(&table, 0, sizeof(table));
    header_table_set(&table, "Content-Type", "text/html");
    header_table_set(&table, "ETag", "\"abc123\"");

    header_plan_init(&plan);
    header_plan_add(&plan, HEADER_OP_SET, "Content-Type", "text/markdown");
    header_plan_add(&plan, HEADER_OP_SET, "Vary", "Accept");
    header_plan_add(&plan, HEADER_OP_DELETE, "ETag", NULL);

    rc = header_plan_apply(&plan, &table, -1);
    TEST_ASSERT(rc == NGX_OK, "plan applies successfully");
    TEST_ASSERT(plan.applied == 1, "applied flag set");
    TEST_ASSERT(plan.rolled_back == 0, "rolled_back flag not set");

    /* Verify Content-Type was changed */
    idx = header_table_find(&table, "Content-Type");
    TEST_ASSERT(idx >= 0, "Content-Type exists");
    TEST_ASSERT(strcmp(table.entries[idx].value, "text/markdown") == 0,
        "Content-Type is text/markdown");

    /* Verify Vary was added */
    idx = header_table_find(&table, "Vary");
    TEST_ASSERT(idx >= 0, "Vary header added");
    TEST_ASSERT(strcmp(table.entries[idx].value, "Accept") == 0,
        "Vary is Accept");

    /* Verify ETag was deleted */
    idx = header_table_find(&table, "ETag");
    TEST_ASSERT(idx < 0, "ETag deleted");

    TEST_PASS("atomic application succeeds");
}


/* ── Test: failure triggers rollback ──────────────────────────── */

static void
test_failure_triggers_rollback(void)
{
    header_plan_t plan;
    header_table_t table;
    int rc;
    int idx;

    TEST_SUBSECTION("Failure triggers rollback");

    memset(&table, 0, sizeof(table));
    header_table_set(&table, "Content-Type", "text/html");
    header_table_set(&table, "Content-Length", "1234");

    header_plan_init(&plan);
    header_plan_add(&plan, HEADER_OP_SET, "Content-Type", "text/markdown");
    header_plan_add(&plan, HEADER_OP_SET, "Vary", "Accept");
    header_plan_add(&plan, HEADER_OP_DELETE, "Content-Length", NULL);

    /* Simulate failure at operation index 2 (the DELETE) */
    rc = header_plan_apply(&plan, &table, 2);
    TEST_ASSERT(rc == NGX_ERROR, "plan fails at op 2");
    TEST_ASSERT(plan.applied == 0, "applied flag not set");
    TEST_ASSERT(plan.rolled_back == 1, "rolled_back flag set");

    /* Verify table is unchanged (rolled back to snapshot) */
    idx = header_table_find(&table, "Content-Type");
    TEST_ASSERT(idx >= 0, "Content-Type still exists");
    TEST_ASSERT(strcmp(table.entries[idx].value, "text/html") == 0,
        "Content-Type rolled back to text/html");

    idx = header_table_find(&table, "Content-Length");
    TEST_ASSERT(idx >= 0, "Content-Length still exists (not deleted)");
    TEST_ASSERT(strcmp(table.entries[idx].value, "1234") == 0,
        "Content-Length value preserved");

    /* Vary should NOT be present (rolled back) */
    idx = header_table_find(&table, "Vary");
    TEST_ASSERT(idx < 0, "Vary not present (rolled back)");

    TEST_PASS("failure triggers complete rollback");
}


/* ── Test: failure at first operation ─────────────────────────── */

static void
test_failure_at_first_op(void)
{
    header_plan_t plan;
    header_table_t table;
    int rc;
    int idx;

    TEST_SUBSECTION("Failure at first operation");

    memset(&table, 0, sizeof(table));
    header_table_set(&table, "X-Original", "value");

    header_plan_init(&plan);
    header_plan_add(&plan, HEADER_OP_SET, "X-New", "new-value");
    header_plan_add(&plan, HEADER_OP_DELETE, "X-Original", NULL);

    /* Fail at first op */
    rc = header_plan_apply(&plan, &table, 0);
    TEST_ASSERT(rc == NGX_ERROR, "fails at first op");
    TEST_ASSERT(plan.rolled_back == 1, "rolled_back set");

    /* Table unchanged */
    idx = header_table_find(&table, "X-Original");
    TEST_ASSERT(idx >= 0, "X-Original preserved");
    idx = header_table_find(&table, "X-New");
    TEST_ASSERT(idx < 0, "X-New not added");

    TEST_PASS("failure at first operation handled");
}


/* ── Test: empty plan applies as no-op ────────────────────────── */

static void
test_empty_plan_noop(void)
{
    header_plan_t plan;
    header_table_t table;
    int rc;

    TEST_SUBSECTION("Empty plan applies as no-op");

    memset(&table, 0, sizeof(table));
    header_table_set(&table, "Content-Type", "text/html");

    header_plan_init(&plan);

    rc = header_plan_apply(&plan, &table, -1);
    TEST_ASSERT(rc == NGX_OK, "empty plan succeeds");
    TEST_ASSERT(plan.applied == 1, "applied flag set");
    TEST_ASSERT(plan.rolled_back == 0, "no rollback");

    TEST_PASS("empty plan applies as no-op");
}


int
main(void)
{
    TEST_SECTION("header_plan");

    test_atomic_apply_success();
    test_failure_triggers_rollback();
    test_failure_at_first_op();
    test_empty_plan_noop();

    TEST_PASS("header_plan: all tests passed");
    return 0;
}
