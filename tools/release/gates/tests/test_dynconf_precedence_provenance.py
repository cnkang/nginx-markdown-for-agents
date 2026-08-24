"""
Property 28: Dynconf precedence and provenance.

For arbitrary combinations of http/server/location explicit settings, dynconf
values, and request variables, verify the resolved effective value matches the
frozen precedence ladder:

  request_variable > server/location explicit (block bit) > dynconf >
  http baseline > built-in default

Additionally verifies:
- Field-specific provenance matches the winning precedence level
- Absent dynconf key = "do not override"
- Block-mask propagation from parent to child

**Validates: Requirements 3.13, 3.14, 3.15, 4.12**
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional
from pathlib import Path

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st


REPO_ROOT = Path(__file__).resolve().parents[4]
PRECEDENCE_CONTRACT = json.loads(
    (REPO_ROOT / "schemas" / "dynconf-precedence-v1.json").read_text(
        encoding="utf-8"
    )
)
PRECEDENCE_SOURCES = tuple(
    entry["source"]
    for entry in PRECEDENCE_CONTRACT["five_tier_precedence_hierarchy"]
)

# Assert the complete precedence source order at module load so silent
# schema drift (e.g. a reordered tier) is caught immediately.
assert PRECEDENCE_SOURCES == (
    "request_variable",
    "static",
    "dynconf",
    "http_baseline",
    "default",
), f"unexpected precedence source order: {PRECEDENCE_SOURCES!r}"


# --- Model types ---


class Provenance(Enum):
    """Effective source provenance for a dynconf-mutable field."""
    BUILTIN_DEFAULT = auto()
    HTTP_BASELINE = auto()
    DYNCONF = auto()
    STATIC = auto()          # server/location explicit
    REQUEST_VARIABLE = auto()


# The five dynconf-mutable fields
DYNCONF_FIELDS = ("filter", "prune_noise", "log_verbosity",
                  "error_policy", "streaming_buffer")

# Built-in defaults (arbitrary model values for testing)
BUILTIN_DEFAULTS: dict[str, int] = {
    "filter": 1,
    "prune_noise": 0,
    "log_verbosity": 2,
    "error_policy": 0,
    "streaming_buffer": 2097152,
}


@dataclass
class ConfigLevel:
    """Configuration at one NGINX block level.

    A value of None means "not explicitly set at this level".
    """
    filter: Optional[int] = None
    prune_noise: Optional[int] = None
    log_verbosity: Optional[int] = None
    error_policy: Optional[int] = None
    streaming_buffer: Optional[int] = None

    def get(self, field_name: str) -> Optional[int]:
        return getattr(self, field_name)


@dataclass
class DynconfSnapshot:
    """Dynconf JSON snapshot.  None means the key is absent ("do not override")."""
    filter: Optional[int] = None
    prune_noise: Optional[int] = None
    log_verbosity: Optional[int] = None
    error_policy: Optional[int] = None
    streaming_buffer: Optional[int] = None

    def get(self, field_name: str) -> Optional[int]:
        return getattr(self, field_name)


@dataclass
class RequestVariables:
    """Request variable evaluation.  Only filter supports this."""
    filter: Optional[int] = None


@dataclass
class BlockMask:
    """Per-field block mask.  True means dynconf is blocked for that field."""
    filter: bool = False
    prune_noise: bool = False
    log_verbosity: bool = False
    error_policy: bool = False
    streaming_buffer: bool = False

    def is_blocked(self, field_name: str) -> bool:
        return getattr(self, field_name)

    def set_blocked(self, field_name: str) -> "BlockMask":
        import copy
        m = copy.copy(self)
        setattr(m, field_name, True)
        return m


@dataclass
class EffectiveResult:
    """Resolved effective value and provenance for one field."""
    value: int
    provenance: Provenance


# --- Precedence resolution model ---


def compute_block_mask(
    http_conf: ConfigLevel,
    server_conf: ConfigLevel,
    location_conf: ConfigLevel,
) -> BlockMask:
    """Compute the block mask for the final location.

    Rules:
    - http-level explicit settings do NOT set block bits
    - server-level explicit settings DO set block bits
    - Block bits propagate from server to child locations (merge semantics)
    - A location that explicitly configures the field keeps the bit set
      with the location's own value
    """
    mask = BlockMask()
    for f in DYNCONF_FIELDS:
        # Server explicit sets the bit
        if server_conf.get(f) is not None:
            mask = mask.set_blocked(f)
        # Location explicit also sets/keeps the bit
        if location_conf.get(f) is not None:
            mask = mask.set_blocked(f)
    return mask


def resolve_effective(
    http_conf: ConfigLevel,
    server_conf: ConfigLevel,
    location_conf: ConfigLevel,
    dynconf: Optional[DynconfSnapshot],
    request_vars: RequestVariables,
    field_name: str,
) -> EffectiveResult:
    """Resolve effective value and provenance for a single field.

    Implements the frozen five-tier precedence ladder:
      (1) request_variable (only filter)
      (2) server/location explicit (block bit set)
      (3) dynconf (only where block bit NOT set and key present)
      (4) http baseline
      (5) built-in default
    """
    # Step 1: Compute block mask
    mask = compute_block_mask(http_conf, server_conf, location_conf)

    # Step 2: Determine the static value and its base provenance.  The tier
    # order is loaded from the canonical contract above, not re-declared here.
    if PRECEDENCE_SOURCES[1] == "static" and location_conf.get(field_name) is not None:
        value = location_conf.get(field_name)
        provenance = Provenance.STATIC
    elif PRECEDENCE_SOURCES[1] == "static" and server_conf.get(field_name) is not None:
        value = server_conf.get(field_name)
        provenance = Provenance.STATIC
    elif PRECEDENCE_SOURCES[3] == "http_baseline" and http_conf.get(field_name) is not None:
        value = http_conf.get(field_name)
        provenance = Provenance.HTTP_BASELINE
    else:
        value = BUILTIN_DEFAULTS[field_name]
        provenance = Provenance.BUILTIN_DEFAULT

    # Step 3: Apply dynconf overlay (only where block bit NOT set and key present)
    if PRECEDENCE_SOURCES[2] == "dynconf" and not mask.is_blocked(field_name) and dynconf is not None:
        dynconf_value = dynconf.get(field_name)
        if dynconf_value is not None:
            value = dynconf_value
            provenance = Provenance.DYNCONF

    # Step 5: Apply request variable evaluation (only filter supports this)
    if (
        PRECEDENCE_SOURCES[0] == "request_variable"
        and field_name == "filter"
        and request_vars.filter is not None
    ):
        value = request_vars.filter
        provenance = Provenance.REQUEST_VARIABLE

    return EffectiveResult(value=value, provenance=provenance)


def provenance_to_source(provenance: Provenance) -> str:
    """Map provenance to the diagnostics effective_sources string."""
    if provenance == Provenance.REQUEST_VARIABLE:
        return "request_variable"
    elif provenance == Provenance.DYNCONF:
        return "dynconf"
    else:
        return "static"


# --- Hypothesis strategies ---


field_value_st = st.integers(min_value=1, max_value=100)
optional_value_st = st.one_of(st.none(), field_value_st)

config_level_st = st.builds(
    ConfigLevel,
    filter=optional_value_st,
    prune_noise=optional_value_st,
    log_verbosity=optional_value_st,
    error_policy=optional_value_st,
    streaming_buffer=optional_value_st,
)

dynconf_snapshot_st = st.one_of(
    st.none(),
    st.builds(
        DynconfSnapshot,
        filter=optional_value_st,
        prune_noise=optional_value_st,
        log_verbosity=optional_value_st,
        error_policy=optional_value_st,
        streaming_buffer=optional_value_st,
    ),
)

request_vars_st = st.builds(
    RequestVariables,
    filter=optional_value_st,
)


# --- Property tests ---


@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(
    http_conf=config_level_st,
    server_conf=config_level_st,
    location_conf=config_level_st,
    dynconf=dynconf_snapshot_st,
    request_vars=request_vars_st,
)
def test_precedence_ladder_correct(
    http_conf: ConfigLevel,
    server_conf: ConfigLevel,
    location_conf: ConfigLevel,
    dynconf: Optional[DynconfSnapshot],
    request_vars: RequestVariables,
):
    """For any configuration combination, the resolved value matches the
    frozen five-tier precedence ladder.

    **Validates: Requirements 3.13, 3.14, 3.15, 4.12**
    """
    for f in DYNCONF_FIELDS:
        result = resolve_effective(
            http_conf, server_conf, location_conf, dynconf, request_vars, f
        )
        mask = compute_block_mask(http_conf, server_conf, location_conf)

        # Verify the precedence ladder manually
        if (
            PRECEDENCE_SOURCES[0] == "request_variable"
            and f == "filter"
            and request_vars.filter is not None
        ):
            # Level 1: request variable always wins (only filter)
            assert result.value == request_vars.filter
            assert result.provenance == Provenance.REQUEST_VARIABLE
        elif PRECEDENCE_SOURCES[1] == "static" and mask.is_blocked(f):
            # Level 2: server/location explicit (block bit set)
            # Value comes from the most-specific explicit static config
            if location_conf.get(f) is not None:
                assert result.value == location_conf.get(f)
            else:
                assert result.value == server_conf.get(f)
            assert result.provenance == Provenance.STATIC
        elif (
            PRECEDENCE_SOURCES[2] == "dynconf"
            and dynconf is not None
            and dynconf.get(f) is not None
        ):
            # Level 3: dynconf overlay (block bit NOT set, key present)
            assert result.value == dynconf.get(f)
            assert result.provenance == Provenance.DYNCONF
        elif PRECEDENCE_SOURCES[3] == "http_baseline" and http_conf.get(f) is not None:
            # Level 4: http baseline
            assert result.value == http_conf.get(f)
            assert result.provenance == Provenance.HTTP_BASELINE
        else:
            # Level 5: built-in default
            assert result.value == BUILTIN_DEFAULTS[f]
            assert result.provenance == Provenance.BUILTIN_DEFAULT


@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(
    http_conf=config_level_st,
    server_conf=config_level_st,
    location_conf=config_level_st,
    dynconf=dynconf_snapshot_st,
    request_vars=request_vars_st,
)
def test_provenance_field_specific_legal_sources(
    http_conf: ConfigLevel,
    server_conf: ConfigLevel,
    location_conf: ConfigLevel,
    dynconf: Optional[DynconfSnapshot],
    request_vars: RequestVariables,
):
    """Field-specific provenance matches the winning precedence level.

    Only filter may report request_variable; the other four fields report
    static or dynconf only.

    **Validates: Requirements 3.13, 3.14, 3.15, 4.12**
    """
    for f in DYNCONF_FIELDS:
        result = resolve_effective(
            http_conf, server_conf, location_conf, dynconf, request_vars, f
        )
        source = provenance_to_source(result.provenance)

        if f == "filter":
            assert source in ("static", "dynconf", "request_variable"), (
                f"filter source must be static|dynconf|request_variable, "
                f"got {source}"
            )
        else:
            assert source in ("static", "dynconf"), (
                f"{f} source must be static|dynconf, got {source}"
            )
            # Non-filter fields must NEVER be request_variable
            assert result.provenance != Provenance.REQUEST_VARIABLE, (
                f"{f} must never have request_variable provenance"
            )


@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(
    http_conf=config_level_st,
    server_conf=config_level_st,
    location_conf=config_level_st,
    request_vars=request_vars_st,
)
def test_absent_dynconf_key_does_not_override(
    http_conf: ConfigLevel,
    server_conf: ConfigLevel,
    location_conf: ConfigLevel,
    request_vars: RequestVariables,
):
    """An absent dynconf key does not reset the field to a default and does
    not clear any block bit.

    With all dynconf keys absent (all None), the result must equal the static
    resolution (levels 2, 4, 5) plus any request variable.

    **Validates: Requirements 3.13, 3.15**
    """
    # Dynconf with all keys absent
    all_absent_dynconf = DynconfSnapshot()

    for f in DYNCONF_FIELDS:
        with_dynconf = resolve_effective(
            http_conf, server_conf, location_conf, all_absent_dynconf,
            request_vars, f
        )
        without_dynconf = resolve_effective(
            http_conf, server_conf, location_conf, None, request_vars, f
        )

        # Absent key means "do not override" — same as no dynconf at all
        assert with_dynconf.value == without_dynconf.value, (
            f"field={f}: absent dynconf key should not change value "
            f"(with={with_dynconf.value}, without={without_dynconf.value})"
        )
        # Provenance should also remain unchanged (stays static, not dynconf)
        assert with_dynconf.provenance == without_dynconf.provenance, (
            f"field={f}: absent dynconf key should not change provenance "
            f"(with={with_dynconf.provenance}, without={without_dynconf.provenance})"
        )


@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(
    http_conf=config_level_st,
    server_conf=config_level_st,
    location_conf=config_level_st,
    dynconf=dynconf_snapshot_st,
    request_vars=request_vars_st,
)
def test_block_mask_propagation(
    http_conf: ConfigLevel,
    server_conf: ConfigLevel,
    location_conf: ConfigLevel,
    dynconf: Optional[DynconfSnapshot],
    request_vars: RequestVariables,
):
    """Block mask propagation from server to child locations.

    - Server explicit sets the block bit, propagates to child
    - Location explicit keeps the bit set with the location's own value
    - Http-level explicit does NOT set block bits

    **Validates: Requirements 3.13, 3.15**
    """
    mask = compute_block_mask(http_conf, server_conf, location_conf)

    for f in DYNCONF_FIELDS:
        # Http-level explicit does NOT set block bits
        if (server_conf.get(f) is None and location_conf.get(f) is None):
            assert not mask.is_blocked(f), (
                f"field={f}: http-only explicit must NOT set block bit"
            )

        # Server explicit sets the block bit
        if server_conf.get(f) is not None:
            assert mask.is_blocked(f), (
                f"field={f}: server explicit must set block bit"
            )

        # Location explicit keeps the bit set
        if location_conf.get(f) is not None:
            assert mask.is_blocked(f), (
                f"field={f}: location explicit must keep block bit set"
            )


@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(
    http_conf=config_level_st,
    server_conf=config_level_st,
    location_conf=config_level_st,
    dynconf=dynconf_snapshot_st,
    request_vars=request_vars_st,
)
def test_http_explicit_does_not_block_dynconf(
    http_conf: ConfigLevel,
    server_conf: ConfigLevel,
    location_conf: ConfigLevel,
    dynconf: Optional[DynconfSnapshot],
    request_vars: RequestVariables,
):
    """An explicit setting in the http block does NOT block Dynconf.

    The http-level value is the baseline that Dynconf overrides.

    **Validates: Requirements 3.13**
    """
    for f in DYNCONF_FIELDS:
        # Skip if request_variable wins (only filter)
        if f == "filter" and request_vars.filter is not None:
            continue

        # Only consider cases where http is explicit but server/location are not
        if http_conf.get(f) is None:
            continue
        if server_conf.get(f) is not None or location_conf.get(f) is not None:
            continue

        # No server/location explicit => block bit not set
        mask = compute_block_mask(http_conf, server_conf, location_conf)
        assert not mask.is_blocked(f)

        # If dynconf has a value for this field, it should win over http
        if dynconf is not None and dynconf.get(f) is not None:
            result = resolve_effective(
                http_conf, server_conf, location_conf, dynconf, request_vars, f
            )
            assert result.value == dynconf.get(f), (
                f"field={f}: dynconf should override http-level explicit "
                f"(dynconf={dynconf.get(f)}, result={result.value})"
            )
            assert result.provenance == Provenance.DYNCONF


@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(
    http_conf=config_level_st,
    server_conf=config_level_st,
    dynconf=dynconf_snapshot_st,
    request_vars=request_vars_st,
)
def test_server_block_bit_propagates_to_child(
    http_conf: ConfigLevel,
    server_conf: ConfigLevel,
    dynconf: Optional[DynconfSnapshot],
    request_vars: RequestVariables,
):
    """A server block that explicitly sets a field blocks the Dynconf overlay
    for that field in every child location (unless the child explicitly
    configures the field).

    Model: server explicit, location with NO explicit = child inherits block.

    **Validates: Requirements 3.15**
    """
    # Location with no explicit settings (inherits from server)
    empty_location = ConfigLevel()

    for f in DYNCONF_FIELDS:
        if server_conf.get(f) is None:
            continue

        mask = compute_block_mask(http_conf, server_conf, empty_location)
        assert mask.is_blocked(f), (
            f"field={f}: server explicit should propagate block bit to child"
        )

        # Even if dynconf has a value, block bit prevents override
        if dynconf is not None and dynconf.get(f) is not None:
            result = resolve_effective(
                http_conf, server_conf, empty_location, dynconf, request_vars, f
            )
            # Skip if request_variable wins for filter
            if f == "filter" and request_vars.filter is not None:
                continue
            assert result.value == server_conf.get(f), (
                f"field={f}: blocked by server, dynconf must not override "
                f"(server={server_conf.get(f)}, result={result.value})"
            )
            assert result.provenance == Provenance.STATIC


@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(
    http_conf=config_level_st,
    server_conf=config_level_st,
    dynconf=dynconf_snapshot_st,
    request_vars=request_vars_st,
    location_value=field_value_st,
    field_name=st.sampled_from(DYNCONF_FIELDS),
)
def test_child_explicit_overrides_server_block_with_own_value(
    http_conf: ConfigLevel,
    server_conf: ConfigLevel,
    dynconf: Optional[DynconfSnapshot],
    request_vars: RequestVariables,
    location_value: int,
    field_name: str,
):
    """A child location that explicitly configures a field keeps the block bit
    set with the child's own value (not the server's value).

    **Validates: Requirements 3.15**
    """
    f = field_name

    # Ensure server has an explicit value for this field
    assume(server_conf.get(f) is not None)
    # Ensure the location value differs from server for clarity
    assume(location_value != server_conf.get(f))

    # Build a location with only this field explicitly set
    location_conf = ConfigLevel()
    setattr(location_conf, f, location_value)

    mask = compute_block_mask(http_conf, server_conf, location_conf)
    assert mask.is_blocked(f), (
        f"field={f}: child explicit should keep block bit set"
    )

    result = resolve_effective(
        http_conf, server_conf, location_conf, dynconf, request_vars, f
    )

    # Skip if request_variable wins for filter
    if f == "filter" and request_vars.filter is not None:
        assert result.provenance == Provenance.REQUEST_VARIABLE
    else:
        # Child's own value wins, not the server's
        assert result.value == location_value, (
            f"field={f}: child explicit should use own value "
            f"(child={location_value}, result={result.value})"
        )
        assert result.provenance == Provenance.STATIC
