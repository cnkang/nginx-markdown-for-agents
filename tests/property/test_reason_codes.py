"""Property-based checks for the canonical reason registry and log schema.

The registry is the source of truth. These tests intentionally do not copy
the production C or Rust mappings; they validate registry metadata and the
observable invariants that consumers rely on.

Run with::

    python3 -m pytest tests/property/test_reason_codes.py -v
"""

from pathlib import Path
import re
import tomllib

import hypothesis.strategies as st
from hypothesis import given, settings


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPOSITORY_ROOT / "components/rust-converter/reason_registry.toml"

with REGISTRY_PATH.open("rb") as registry_file:
    REGISTRY = tomllib.load(registry_file)

REASONS = tuple(REGISTRY["reasons"])
REASONS_BY_KEY = {reason["key"]: reason for reason in REASONS}
REASON_KEYS = tuple(REASONS_BY_KEY)

CANONICAL_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
EVENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
VALID_OUTCOMES = {"converted", "skipped", "failed_open", "failed_closed",
                  "aborted"}
VALID_STAGES = {"eligibility", "decompression", "parsing", "conversion",
                "precommit", "postcommit", "delivery", "dynconf"}
VALID_ERROR_ORIGINS = {"allocation", "downstream", "invariant", "format",
                       "truncated", "timeout", "memory_budget", "internal",
                       "none"}
FAILURE_OUTCOMES = {"failed_open", "failed_closed", "aborted"}

LOG_ERROR = 0
LOG_WARN = 1
LOG_INFO = 2
LOG_DEBUG = 3
NGX_LOG_INFO = 7
NGX_LOG_WARN = 5

REASON_KEYS_STRATEGY = st.sampled_from(REASON_KEYS)
VERBOSITY_STRATEGY = st.sampled_from([LOG_ERROR, LOG_WARN, LOG_INFO, LOG_DEBUG])


def is_failure_outcome(reason_key: str) -> bool:
    """Return whether registry metadata classifies the reason as a failure."""

    return REASONS_BY_KEY[reason_key]["outcome"] in FAILURE_OUTCOMES


def should_emit(verbosity: int, reason_key: str) -> bool:
    """Model the decision logger's warn/error suppression rule."""

    return verbosity >= LOG_INFO or is_failure_outcome(reason_key)


def expected_nginx_log_level(reason_key: str) -> int:
    """Return the NGINX level selected from canonical outcome metadata."""

    return NGX_LOG_WARN if is_failure_outcome(reason_key) else NGX_LOG_INFO


def request_state(reason_key: str) -> str:
    """Reduce a canonical reason to the bounded request-state vocabulary."""

    reason = REASONS_BY_KEY[reason_key]
    if reason_key == "disabled":
        return "NOT_ENABLED"
    if reason["outcome"] == "skipped":
        return "SKIPPED"
    if reason["outcome"] == "converted":
        return "CONVERTED"
    return "FAILED"


def failure_outcome_for_policy(on_error: str) -> str:
    """Return the terminal reason selected by the error policy."""

    if on_error == "pass":
        return "failed_open"
    if on_error == "reject":
        return "failed_closed"
    raise ValueError(f"unsupported error policy: {on_error}")


def format_decision_log_entry(
    reason_key: str,
    method: str,
    uri: str,
    content_type: str,
    verbosity: int,
    event: str = "-",
    filter_value: str | None = None,
    accept: str | None = None,
    status: int | None = None,
    error_category: str | None = None,
) -> str:
    """Format the bounded fields emitted by the decision logger."""

    reason = REASONS_BY_KEY[reason_key]
    fields = [
        f"outcome={reason['outcome']}",
        f"stage={reason['default_stage']}",
        f"reason={reason_key}",
        f"event={event}",
        f"method={method}",
        f"uri={uri}",
        f"content_type={content_type}",
    ]
    if error_category is not None:
        fields.insert(4, f"category={error_category}")
    if verbosity == LOG_DEBUG:
        fields.extend([
            f"filter_value={filter_value}",
            f"accept={accept}",
            f"status={status}",
        ])
    return "markdown: " + " ".join(fields)


def parse_log_fields(entry: str) -> dict[str, str]:
    """Extract key/value fields from a structured decision log line."""

    prefix = "markdown: "
    if entry.startswith(prefix):
        entry = entry[len(prefix):]
    fields = {}
    for part in entry.split():
        if "=" in part:
            key, value = part.split("=", 1)
            fields[key] = value
    return fields


def test_registry_has_current_contract_shape():
    """The active 0.9.2 registry has one contiguous 27-entry projection."""

    discriminants = [reason["discriminant"] for reason in REASONS]
    assert len(REASONS) == 27
    assert discriminants == list(range(len(REASONS)))
    assert len(REASONS_BY_KEY) == len(REASONS)
    assert REGISTRY["metadata"]["schema_version"] == 1


def test_registry_has_no_compatibility_alias_surface():
    """Compatibility aliases stay in migration history, not active metadata."""

    assert all("legacy_keys" not in reason for reason in REASONS)


def test_registry_metadata_is_valid():
    """Every registry entry has valid bounded taxonomy metadata."""

    for reason in REASONS:
        assert CANONICAL_REASON_PATTERN.fullmatch(reason["key"])
        assert reason["operator_visible"] is True
        assert reason["outcome"] in VALID_OUTCOMES
        assert reason["default_stage"] in VALID_STAGES
        assert reason["default_origin"] in VALID_ERROR_ORIGINS
        assert set(reason["allowed_origins"]).issubset(VALID_ERROR_ORIGINS)


@given(reason_key=REASON_KEYS_STRATEGY)
@settings(max_examples=200)
def test_every_reason_key_is_canonical(reason_key):
    """Generated reason strings remain lowercase bounded snake_case."""

    assert CANONICAL_REASON_PATTERN.fullmatch(reason_key)
    assert not any(character.isupper() for character in reason_key)


def test_registry_covers_all_request_states():
    """The bounded request-state projection has no uncovered state."""

    assert {request_state(key) for key in REASON_KEYS} == {
        "NOT_ENABLED", "SKIPPED", "CONVERTED", "FAILED"
    }


@given(reason_key=REASON_KEYS_STRATEGY, verbosity=VERBOSITY_STRATEGY)
@settings(max_examples=200)
def test_verbosity_gating_uses_outcome_metadata(reason_key, verbosity):
    """Info/debug emit all outcomes; warn/error emit failures only."""

    emitted = should_emit(verbosity, reason_key)
    assert emitted == (verbosity >= LOG_INFO or is_failure_outcome(reason_key))


@given(reason_key=REASON_KEYS_STRATEGY)
@settings(max_examples=100)
def test_nginx_log_level_uses_outcome_metadata(reason_key):
    """Failure outcomes use WARN and all other outcomes use INFO."""

    expected = NGX_LOG_WARN if is_failure_outcome(reason_key) else NGX_LOG_INFO
    assert expected_nginx_log_level(reason_key) == expected


@given(on_error=st.sampled_from(["pass", "reject"]))
@settings(max_examples=50)
def test_error_policy_selects_distinct_terminal_reasons(on_error):
    """Pass and reject retain distinct canonical terminal outcomes."""

    selected = failure_outcome_for_policy(on_error)
    assert selected in REASONS_BY_KEY
    assert REASONS_BY_KEY[selected]["outcome"] == selected
    assert failure_outcome_for_policy("pass") != failure_outcome_for_policy(
        "reject"
    )


@given(
    reason_key=REASON_KEYS_STRATEGY,
    method=st.sampled_from(["GET", "HEAD", "POST"]),
    uri=st.from_regex(r"/[a-z0-9/_-]{1,50}", fullmatch=True),
    content_type=st.sampled_from(["text/html", "text/html;", "application/json"]),
    verbosity=VERBOSITY_STRATEGY,
    event=st.one_of(
        st.just("-"),
        st.from_regex(r"[a-z][a-z0-9_]{0,30}", fullmatch=True),
    ),
    filter_value=st.sampled_from(["on", "off"]),
    accept=st.sampled_from(["text/markdown", "*/*", "-"]),
    status=st.sampled_from([200, 304, 500]),
)
@settings(max_examples=200)
def test_emitted_log_has_required_canonical_fields(
    reason_key,
    method,
    uri,
    content_type,
    verbosity,
    event,
    filter_value,
    accept,
    status,
):
    """Every emitted decision line contains outcome/stage/reason/event."""

    if not should_emit(verbosity, reason_key):
        return
    entry = format_decision_log_entry(
        reason_key,
        method,
        uri,
        content_type,
        verbosity,
        event=event,
        filter_value=filter_value,
        accept=accept,
        status=status,
    )
    fields = parse_log_fields(entry)
    assert fields["outcome"] == REASONS_BY_KEY[reason_key]["outcome"]
    assert fields["stage"] == REASONS_BY_KEY[reason_key]["default_stage"]
    assert fields["reason"] == reason_key
    assert fields["event"] == event
    assert {"method", "uri", "content_type"}.issubset(fields)


@given(reason_key=REASON_KEYS_STRATEGY)
@settings(max_examples=100)
def test_debug_log_adds_only_bounded_context(reason_key):
    """Debug fields are present only in debug output and remain bounded."""

    entry = format_decision_log_entry(
        reason_key,
        "GET",
        "/docs",
        "text/html",
        LOG_DEBUG,
        filter_value="on",
        accept="text/markdown",
        status=200,
    )
    fields = parse_log_fields(entry)
    assert {"filter_value", "accept", "status"}.issubset(fields)
    assert EVENT_PATTERN.fullmatch(fields["event"]) or fields["event"] == "-"


def test_failure_metadata_is_partitioned_from_non_failure_metadata():
    """The registry provides a disjoint outcome partition for log gating."""

    failures = {key for key in REASON_KEYS if is_failure_outcome(key)}
    non_failures = set(REASON_KEYS) - failures
    assert failures
    assert non_failures
    assert failures.isdisjoint(non_failures)
