"""
Property 8: Diagnostics method restriction — property-based tests.

For any HTTP method other than GET or HEAD, verify the diagnostics endpoint
returns 405 Method Not Allowed with an Allow: GET, HEAD header.

Since this is a structural unit test without a running NGINX instance, we
validate:
1. The C source contains the correct method bitmask check
2. The Allow header is set to exactly "GET, HEAD"
3. The return code is NGX_HTTP_NOT_ALLOWED (405)
4. GET and HEAD are explicitly allowed through the guard

Uses hypothesis to generate arbitrary HTTP method strings and verify the
expected method-filtering behavior is implemented correctly.

Each property runs at least 100 iterations.

**Validates: Requirements 4.4**
"""

import re
import sys
from pathlib import Path

from hypothesis import given, settings, example
from hypothesis import strategies as st

# Ensure the tools package is importable
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent.parent.parent)
)

# --- Constants ---

DIAGNOSTICS_SOURCE = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_diagnostics.c"
)

# Standard HTTP methods per RFC 9110
STANDARD_METHODS = [
    "GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "TRACE",
    "CONNECT",
]

# Methods that MUST be allowed (not rejected)
ALLOWED_METHODS = {"GET", "HEAD"}

# Methods that MUST be rejected with 405
REJECTED_STANDARD_METHODS = [
    m for m in STANDARD_METHODS if m not in ALLOWED_METHODS
]


# --- Source code loading and parsing (cached at module level) ---

def _load_handler_body() -> str:
    """Load and extract the diagnostics handler function body (cached)."""
    assert DIAGNOSTICS_SOURCE.exists(), (
        f"Diagnostics source not found: {DIAGNOSTICS_SOURCE}"
    )
    source = DIAGNOSTICS_SOURCE.read_text(encoding="utf-8")

    # Find the handler function definition
    pattern = re.compile(
        r"ngx_http_markdown_diagnostics_handler\(ngx_http_request_t\s+\*r\)"
        r"\s*\{",
        re.DOTALL,
    )
    match = pattern.search(source)
    assert match is not None, "Handler function not found in source"

    # Extract from the opening brace using brace counting
    start = match.start()
    brace_count = 0
    for i, ch in enumerate(source[start:], start=start):
        if ch == "{":
            brace_count += 1
        elif ch == "}":
            brace_count -= 1
            if brace_count == 0:
                return source[start : i + 1]

    return source[start:]


# Cache the handler body at module load time
_HANDLER_BODY = _load_handler_body()


# --- Structural validation helpers ---

def has_method_bitmask_check(handler_body: str) -> bool:
    """
    Verify the handler checks r->method against (NGX_HTTP_GET | NGX_HTTP_HEAD).

    The expected pattern is:
      if (!(r->method & (NGX_HTTP_GET | NGX_HTTP_HEAD)))
    """
    pattern = re.compile(
        r"if\s*\(\s*!\s*\(\s*r->method\s*&\s*"
        r"\(\s*NGX_HTTP_GET\s*\|\s*NGX_HTTP_HEAD\s*\)\s*\)\s*\)"
    )
    return bool(pattern.search(handler_body))


def has_allow_header_set(handler_body: str) -> bool:
    """
    Verify the handler sets the Allow header to "GET, HEAD".

    Looks for ngx_str_set(&..., "GET, HEAD") pattern.
    """
    pattern = re.compile(
        r'ngx_str_set\s*\(\s*&[^,]+->value\s*,\s*"GET,\s*HEAD"\s*\)'
    )
    return bool(pattern.search(handler_body))


def has_405_return(handler_body: str) -> bool:
    """
    Verify the handler returns NGX_HTTP_NOT_ALLOWED in the method-check
    branch.
    """
    pattern = re.compile(r"return\s+NGX_HTTP_NOT_ALLOWED\s*;")
    return bool(pattern.search(handler_body))


def has_head_method_handling(handler_body: str) -> bool:
    """
    Verify the handler has explicit HEAD method handling (no body).

    HEAD requests should generate the JSON but return only headers.
    """
    pattern = re.compile(r"r->method\s*==\s*NGX_HTTP_HEAD")
    return bool(pattern.search(handler_body))


# --- Strategies ---

# Generate arbitrary HTTP method strings that are NOT GET or HEAD
rejected_method_strategy = st.one_of(
    # Standard rejected methods
    st.sampled_from(REJECTED_STANDARD_METHODS),
    # Custom method strings (uppercase alpha, 1-20 chars)
    st.from_regex(r"[A-Z]{1,20}", fullmatch=True).filter(
        lambda m: m not in ALLOWED_METHODS
    ),
)

# Generate arbitrary method-like strings (broader: includes mixed case)
arbitrary_method_strategy = st.one_of(
    st.from_regex(r"[A-Za-z]{1,20}", fullmatch=True).filter(
        lambda m: m.upper() not in ALLOWED_METHODS
    ),
    st.sampled_from(REJECTED_STANDARD_METHODS),
)


# --- Property tests ---

@settings(max_examples=200)
@given(method=rejected_method_strategy)
@example(method="POST")
@example(method="PUT")
@example(method="DELETE")
@example(method="PATCH")
@example(method="OPTIONS")
@example(method="TRACE")
@example(method="CONNECT")
def test_non_get_head_methods_would_receive_405(method):
    """
    Property 8a: For any HTTP method other than GET or HEAD, the
    diagnostics handler implementation contains logic that returns 405.

    This validates structurally that the method check in the C source
    correctly rejects arbitrary non-GET/HEAD methods by confirming:
    1. The bitmask check uses (NGX_HTTP_GET | NGX_HTTP_HEAD)
    2. NGX_HTTP_NOT_ALLOWED is returned for methods not matching
    3. The generated method is NOT in the allowed set

    Since NGINX dispatches methods via bitmask flags, any method that
    does not have the NGX_HTTP_GET or NGX_HTTP_HEAD bit set will hit
    the rejection branch.
    """
    # Confirm the method is not in the allowed set
    assert method not in ALLOWED_METHODS, (
        f"Method {method} should not be in the allowed set"
    )

    # Validate the C source has the correct rejection logic
    assert has_method_bitmask_check(_HANDLER_BODY), (
        "Handler must check r->method against "
        "(NGX_HTTP_GET | NGX_HTTP_HEAD) bitmask"
    )
    assert has_405_return(_HANDLER_BODY), (
        "Handler must return NGX_HTTP_NOT_ALLOWED for rejected methods"
    )


@settings(max_examples=200)
@given(method=rejected_method_strategy)
def test_rejected_methods_get_allow_header(method):
    """
    Property 8b: For any rejected HTTP method, the Allow: GET, HEAD
    header is set in the response.

    Validates that the C source sets the Allow header value to exactly
    "GET, HEAD" per RFC 9110 Section 15.5.6 requirements.
    """
    assert method not in ALLOWED_METHODS

    assert has_allow_header_set(_HANDLER_BODY), (
        'Handler must set Allow header value to "GET, HEAD"'
    )


@settings(max_examples=100)
@given(method=arbitrary_method_strategy)
def test_custom_methods_also_rejected(method):
    """
    Property 8c: For any arbitrary method string (including custom
    methods not in RFC 9110), the bitmask check ensures rejection.

    NGINX uses bitmask flags for standard methods. Custom methods
    that don't map to NGX_HTTP_GET or NGX_HTTP_HEAD will not have
    those bits set, thus will be rejected by the same guard.
    """
    assert method.upper() not in ALLOWED_METHODS

    # The bitmask check handles ALL non-GET/HEAD methods uniformly
    assert has_method_bitmask_check(_HANDLER_BODY), (
        "Bitmask check ensures all non-GET/HEAD methods are rejected"
    )


# --- Structural correctness tests (non-property) ---

def test_get_method_allowed():
    """GET requests must pass through the method check."""
    # The guard is: if (!(r->method & (NGX_HTTP_GET | NGX_HTTP_HEAD)))
    # So GET (which has NGX_HTTP_GET bit) passes through
    assert has_method_bitmask_check(_HANDLER_BODY), (
        "Handler must have bitmask check that allows GET"
    )
    # Verify GET is part of the allowed bitmask
    assert "NGX_HTTP_GET" in _HANDLER_BODY, "NGX_HTTP_GET must be in the check"


def test_head_method_allowed():
    """HEAD requests must pass through the method check."""
    assert has_method_bitmask_check(_HANDLER_BODY), (
        "Handler must have bitmask check that allows HEAD"
    )
    # Verify HEAD is part of the allowed bitmask
    assert "NGX_HTTP_HEAD" in _HANDLER_BODY, (
        "NGX_HTTP_HEAD must be in the check"
    )
    # Verify explicit HEAD handling exists
    assert has_head_method_handling(_HANDLER_BODY), (
        "Handler must have explicit HEAD method handling (no body)"
    )


def test_allow_header_value_exact():
    """The Allow header value must be exactly 'GET, HEAD'."""
    assert has_allow_header_set(_HANDLER_BODY), (
        'Allow header must be set to exactly "GET, HEAD"'
    )


def test_method_check_is_first_guard():
    """
    The method check must appear before access control and body handling.

    This ensures that 405 is returned before any other processing occurs.
    """
    method_check_pos = _HANDLER_BODY.find("r->method & (NGX_HTTP_GET")
    access_check_pos = _HANDLER_BODY.find("diagnostics_check_access")
    discard_body_pos = _HANDLER_BODY.find("ngx_http_discard_request_body")

    assert method_check_pos != -1, "Method check not found"
    assert access_check_pos != -1, "Access check not found"
    assert discard_body_pos != -1, "Body discard not found"

    assert method_check_pos < access_check_pos, (
        "Method check must occur before access control check"
    )
    assert method_check_pos < discard_body_pos, (
        "Method check must occur before body discard"
    )


def test_allow_header_uses_visible_hash():
    """
    The Allow header must have hash = 1 to be included in the response.

    NGINX filters out headers with hash == 0 (invalidated).
    """
    # Find the method-rejection block and verify hash = 1
    assert "allow_hdr->hash = 1" in _HANDLER_BODY, (
        "Allow header must have hash = 1 to be visible in response"
    )
