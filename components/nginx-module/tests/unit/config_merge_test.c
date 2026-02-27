/*
 * Test: config_merge
 * Description: configuration merging
 */

#include "test_common.h"

#define UNSET_INT (-1)
#define UNSET_SIZE ((size_t) -1)

typedef struct {
    int enabled;
    size_t max_size;
    int on_error;
    int flavor;
    int on_wildcard;
    int auto_decompress;
    const char *auth_cookie_patterns;
} conf_t;

static void
merge_conf(conf_t *child, const conf_t *parent)
{
    if (child->enabled == UNSET_INT) child->enabled = (parent->enabled == UNSET_INT) ? 0 : parent->enabled;
    if (child->max_size == UNSET_SIZE) child->max_size = (parent->max_size == UNSET_SIZE) ? (10 * 1024 * 1024) : parent->max_size;
    if (child->on_error == UNSET_INT) child->on_error = (parent->on_error == UNSET_INT) ? 0 : parent->on_error;
    if (child->flavor == UNSET_INT) child->flavor = (parent->flavor == UNSET_INT) ? 0 : parent->flavor;
    if (child->on_wildcard == UNSET_INT) child->on_wildcard = (parent->on_wildcard == UNSET_INT) ? 0 : parent->on_wildcard;
    if (child->auto_decompress == UNSET_INT) child->auto_decompress = (parent->auto_decompress == UNSET_INT) ? 1 : parent->auto_decompress;
    if (child->auth_cookie_patterns == NULL) child->auth_cookie_patterns = parent->auth_cookie_patterns;
}

static conf_t
unset_conf(void)
{
    conf_t c;
    c.enabled = UNSET_INT;
    c.max_size = UNSET_SIZE;
    c.on_error = UNSET_INT;
    c.flavor = UNSET_INT;
    c.on_wildcard = UNSET_INT;
    c.auto_decompress = UNSET_INT;
    c.auth_cookie_patterns = NULL;
    return c;
}

static void
test_inherit_from_parent(void)
{
    conf_t parent = unset_conf();
    conf_t child = unset_conf();

    TEST_SUBSECTION("Child inherits parent values");

    parent.enabled = 1;
    parent.max_size = 5 * 1024 * 1024;
    parent.on_error = 1;
    parent.flavor = 1;
    parent.on_wildcard = 1;
    parent.auto_decompress = 0;
    parent.auth_cookie_patterns = "session*";

    merge_conf(&child, &parent);

    TEST_ASSERT(child.enabled == 1, "enabled should inherit");
    TEST_ASSERT(child.max_size == parent.max_size, "max_size should inherit");
    TEST_ASSERT(child.on_error == parent.on_error, "on_error should inherit");
    TEST_ASSERT(child.flavor == parent.flavor, "flavor should inherit");
    TEST_ASSERT(child.on_wildcard == parent.on_wildcard, "on_wildcard should inherit");
    TEST_ASSERT(child.auto_decompress == 0, "auto_decompress should inherit");
    TEST_ASSERT(STR_EQ(child.auth_cookie_patterns, "session*"), "auth cookies should inherit");
    TEST_PASS("Inheritance works");
}

static void
test_child_override(void)
{
    conf_t parent = unset_conf();
    conf_t child = unset_conf();

    TEST_SUBSECTION("Child override wins");

    parent.enabled = 0;
    parent.max_size = 2 * 1024 * 1024;
    parent.flavor = 0;

    child.enabled = 1;
    child.max_size = 1024;
    child.flavor = 1;

    merge_conf(&child, &parent);

    TEST_ASSERT(child.enabled == 1, "child enabled override should remain");
    TEST_ASSERT(child.max_size == 1024, "child max_size override should remain");
    TEST_ASSERT(child.flavor == 1, "child flavor override should remain");
    TEST_PASS("Override precedence works");
}

static void
test_defaults_when_both_unset(void)
{
    conf_t parent = unset_conf();
    conf_t child = unset_conf();

    TEST_SUBSECTION("Defaults applied when parent and child are unset");
    merge_conf(&child, &parent);
    TEST_ASSERT(child.enabled == 0, "default enabled should be off");
    TEST_ASSERT(child.max_size == 10 * 1024 * 1024, "default max_size should be 10MB");
    TEST_ASSERT(child.on_error == 0, "default on_error should be pass");
    TEST_ASSERT(child.flavor == 0, "default flavor should be commonmark");
    TEST_ASSERT(child.on_wildcard == 0, "default on_wildcard should be off");
    TEST_ASSERT(child.auto_decompress == 1, "default auto_decompress should be on");
    TEST_PASS("Defaults are correct");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("config_merge Tests\n");
    printf("========================================\n");

    test_inherit_from_parent();
    test_child_override();
    test_defaults_when_both_unset();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
