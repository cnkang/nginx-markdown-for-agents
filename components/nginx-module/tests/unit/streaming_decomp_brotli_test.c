/*
 * Test: streaming_decomp_brotli
 *
 * Brotli-enabled variant of the streaming decompression unit tests.
 * Compiled with -DNGX_HTTP_BROTLI to exercise Brotli-specific code paths
 * including error classification, trailing data rejection, and
 * truncation detection.
 *
 * This file includes the shared streaming_decomp_test.c source so that
 * all common tests run under the Brotli-enabled build as well.
 */
#include "streaming_decomp_test.c"
