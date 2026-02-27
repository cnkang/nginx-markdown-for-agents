/*
 * Common Test Utilities Header
 * 
 * This header provides common includes, macros, and utilities
 * for all test files in components/nginx-module/tests.
 * 
 * Usage:
 *   #include "test_common.h"
 *   // ... rest of your test code
 */

#ifndef TEST_COMMON_H
#define TEST_COMMON_H

/* Standard C library headers */
#include <stdarg.h>   /* For va_start, va_end, va_list */
#include <stdio.h>    /* For printf, fprintf */
#include <stdlib.h>   /* For malloc, free, exit */
#include <string.h>   /* For memset, memcpy, strcmp */
#include <stdint.h>   /* For uint8_t, uint32_t, etc. */

/*
 * Macro to suppress unused parameter warnings
 * 
 * Use this in stub functions that need to match nginx API
 * signatures but don't use all parameters.
 * 
 * Example:
 *   ngx_int_t stub_function(ngx_http_request_t *r) {
 *       UNUSED(r);
 *       return NGX_OK;
 *   }
 */
#define UNUSED(x) (void)(x)

/*
 * Test assertion macro
 * 
 * Checks a condition and exits with error if it fails.
 * Provides file and line number information.
 * 
 * Example:
 *   TEST_ASSERT(result == NGX_OK, "Function should return NGX_OK");
 */
#define TEST_ASSERT(condition, message) \
    do { \
        if (!(condition)) { \
            fprintf(stderr, "\n✗ ASSERTION FAILED: %s\n", message); \
            fprintf(stderr, "  at %s:%d\n", __FILE__, __LINE__); \
            fprintf(stderr, "  condition: %s\n\n", #condition); \
            exit(1); \
        } \
    } while(0)

/*
 * Test pass macro
 * 
 * Prints a success message with checkmark.
 * 
 * Example:
 *   TEST_PASS("Compression detection works correctly");
 */
#define TEST_PASS(message) \
    printf("  ✓ %s\n", message)

/*
 * Test fail macro
 * 
 * Prints a failure message and exits.
 * 
 * Example:
 *   TEST_FAIL("Unexpected return value");
 */
#define TEST_FAIL(message) \
    do { \
        fprintf(stderr, "\n✗ TEST FAILED: %s\n", message); \
        fprintf(stderr, "  at %s:%d\n\n", __FILE__, __LINE__); \
        exit(1); \
    } while(0)

/*
 * Test section header macro
 * 
 * Prints a formatted section header for test output.
 * 
 * Example:
 *   TEST_SECTION("Compression Detection Tests");
 */
#define TEST_SECTION(title) \
    printf("\n%s\n", title)

/*
 * Test subsection header macro
 * 
 * Prints a formatted subsection header.
 * 
 * Example:
 *   TEST_SUBSECTION("Testing gzip format");
 */
#define TEST_SUBSECTION(title) \
    printf("\nTest: %s\n", title)

/*
 * Verify macro (non-fatal assertion)
 * 
 * Checks a condition and prints warning if it fails,
 * but doesn't exit. Useful for optional checks.
 * 
 * Example:
 *   VERIFY(size > 0, "Size should be positive");
 */
#define VERIFY(condition, message) \
    do { \
        if (!(condition)) { \
            fprintf(stderr, "  ⚠ WARNING: %s\n", message); \
            fprintf(stderr, "    at %s:%d\n", __FILE__, __LINE__); \
        } \
    } while(0)

/*
 * Array size macro
 * 
 * Returns the number of elements in a static array.
 * 
 * Example:
 *   int arr[] = {1, 2, 3};
 *   size_t count = ARRAY_SIZE(arr);  // Returns 3
 */
#define ARRAY_SIZE(arr) (sizeof(arr) / sizeof((arr)[0]))

/*
 * String equality check macro
 * 
 * Compares two strings and returns true if equal.
 * Handles NULL pointers safely.
 * 
 * Example:
 *   if (STR_EQ(result, "expected")) { ... }
 */
#define STR_EQ(a, b) \
    ((a) && (b) && strcmp((a), (b)) == 0)

/*
 * Memory comparison macro
 * 
 * Compares two memory regions and returns true if equal.
 * 
 * Example:
 *   if (MEM_EQ(buf1, buf2, size)) { ... }
 */
#define MEM_EQ(a, b, size) \
    (memcmp((a), (b), (size)) == 0)

#endif /* TEST_COMMON_H */
