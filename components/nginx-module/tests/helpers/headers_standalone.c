/*
 * Standalone harness for header update logic in unit tests.
 * It provides minimal nginx-compatible types/mocks and reuses
 * the shared production implementation.
 */

#include "headers_standalone_types.h"
#include "../../src/ngx_http_markdown_headers_impl.h"
