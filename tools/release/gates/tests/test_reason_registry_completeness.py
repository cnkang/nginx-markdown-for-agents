"""
Property 11: Reason registry dimension completeness — exhaustive tests.

For each reason code in reason_registry.toml, verify:
- Valid default Stage (one of 8 values)
- Allowed ErrorOrigins subset of canonical set
- Unique numeric discriminant (u8: 0-255)
- lowercase_snake_case key matching ^[a-z][a-z0-9_]*$
- operator_visible is a boolean
- No duplicate discriminants
- No duplicate keys

Reads the TOML source directly from components/rust-converter/reason_registry.toml.

**Validates: Requirements 6.1, 6.2, 6.4**
"""

import re
import sys
import tomllib
from pathlib import Path

import pytest

# Ensure the tools package is importable
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent.parent.parent)
)

# --- Constants from the spec ---

VALID_STAGES = frozenset([
    "eligibility",
    "decompression",
    "parsing",
    "conversion",
    "precommit",
    "postcommit",
    "delivery",
    "dynconf",
])

VALID_ERROR_ORIGINS = frozenset([
    "allocation",
    "downstream",
    "invariant",
    "format",
    "truncated",
    "timeout",
    "memory_budget",
    "internal",
])

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


# --- Load the registry ---

def load_registry() -> list[dict]:
    """Load reason_registry.toml and return the list of reason entries."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    registry_path = repo_root / "components" / "rust-converter" / "reason_registry.toml"
    assert registry_path.exists(), (
        f"reason_registry.toml not found at {registry_path}"
    )
    data = tomllib.loads(registry_path.read_text(encoding="utf-8"))
    reasons = data.get("reasons")
    assert reasons is not None, "No [[reasons]] entries in registry"
    assert isinstance(reasons, list), "reasons must be a list"
    assert len(reasons) > 0, "reasons list must not be empty"
    return reasons


# Cache the loaded registry for all tests in this module
REASONS = load_registry()


# Stable ids make every registry entry independently visible in pytest output.
REASON_IDS = [
    entry.get("key", f"index-{index}")
    for index, entry in enumerate(REASONS)
]


# --- Exhaustive per-entry tests ---

@pytest.mark.parametrize("entry", REASONS, ids=REASON_IDS)
def test_discriminant_is_valid_u8(entry):
    """
    Property 11a: Each discriminant is a valid u8 value (0-255).

    **Validates: Requirements 6.1, 6.2**
    """
    disc = entry.get("discriminant")
    assert disc is not None, f"Entry missing discriminant: {entry}"
    assert isinstance(disc, int), (
        f"Discriminant must be int, got {type(disc).__name__}: {entry}"
    )
    assert 0 <= disc <= 255, (
        f"Discriminant {disc} out of u8 range [0, 255]: {entry}"
    )


@pytest.mark.parametrize("entry", REASONS, ids=REASON_IDS)
def test_key_matches_snake_case_pattern(entry):
    """
    Property 11b: Each key matches ^[a-z][a-z0-9_]*$ (lowercase_snake_case).

    **Validates: Requirements 6.4**
    """
    key = entry.get("key")
    assert key is not None, f"Entry missing key: {entry}"
    assert isinstance(key, str), (
        f"Key must be string, got {type(key).__name__}: {entry}"
    )
    assert KEY_PATTERN.match(key), (
        f"Key '{key}' does not match ^[a-z][a-z0-9_]*$"
    )


@pytest.mark.parametrize("entry", REASONS, ids=REASON_IDS)
def test_default_stage_is_valid(entry):
    """
    Property 11c: Each default_stage is one of the 8 canonical stages.

    **Validates: Requirements 6.1**
    """
    stage = entry.get("default_stage")
    assert stage is not None, f"Entry missing default_stage: {entry}"
    assert isinstance(stage, str), (
        f"default_stage must be string, got {type(stage).__name__}: {entry}"
    )
    assert stage in VALID_STAGES, (
        f"default_stage '{stage}' not in valid stages {sorted(VALID_STAGES)}: "
        f"entry key='{entry.get('key')}'"
    )


@pytest.mark.parametrize("entry", REASONS, ids=REASON_IDS)
def test_allowed_origins_are_valid(entry):
    """
    Property 11d: Each allowed_origins entry is from the canonical ErrorOrigin set.

    **Validates: Requirements 6.1**
    """
    origins = entry.get("allowed_origins")
    assert origins is not None, f"Entry missing allowed_origins: {entry}"
    assert isinstance(origins, list), (
        f"allowed_origins must be a list, got {type(origins).__name__}: {entry}"
    )
    for origin in origins:
        assert isinstance(origin, str), (
            f"Origin must be string, got {type(origin).__name__}: {entry}"
        )
        assert origin in VALID_ERROR_ORIGINS, (
            f"Origin '{origin}' not in valid origins "
            f"{sorted(VALID_ERROR_ORIGINS)}: entry key='{entry.get('key')}'"
        )


@pytest.mark.parametrize("entry", REASONS, ids=REASON_IDS)
def test_operator_visible_is_boolean(entry):
    """
    Property 11e: Each operator_visible is a boolean.

    **Validates: Requirements 6.2**
    """
    visible = entry.get("operator_visible")
    assert visible is not None, f"Entry missing operator_visible: {entry}"
    assert isinstance(visible, bool), (
        f"operator_visible must be bool, got {type(visible).__name__} "
        f"(value={visible!r}): entry key='{entry.get('key')}'"
    )


# --- Uniqueness tests (exhaustive, not sampled) ---

def test_no_duplicate_discriminants():
    """
    Property 11f: No duplicate discriminants across the entire registry.

    **Validates: Requirements 6.1, 6.2**
    """
    seen = {}
    for entry in REASONS:
        disc = entry["discriminant"]
        key = entry["key"]
        if disc in seen:
            pytest.fail(
                f"Duplicate discriminant {disc}: "
                f"'{key}' conflicts with '{seen[disc]}'"
            )
        seen[disc] = key


def test_no_duplicate_keys():
    """
    Property 11g: No duplicate keys across the entire registry.

    **Validates: Requirements 6.2, 6.4**
    """
    seen = {}
    for entry in REASONS:
        disc = entry["discriminant"]
        key = entry["key"]
        if key in seen:
            pytest.fail(
                f"Duplicate key '{key}': "
                f"discriminant {disc} conflicts with discriminant {seen[key]}"
            )
        seen[key] = disc


# --- Structural completeness (exhaustive) ---

def test_all_entries_have_required_fields():
    """
    Every registry entry must have all five required fields:
    discriminant, key, default_stage, allowed_origins, operator_visible.

    **Validates: Requirements 6.1, 6.2**
    """
    required_fields = {"discriminant", "key", "default_stage", "allowed_origins", "operator_visible"}
    for i, entry in enumerate(REASONS):
        missing = required_fields - set(entry.keys())
        assert not missing, (
            f"Entry {i} (key='{entry.get('key', '<missing>')}') "
            f"missing required fields: {sorted(missing)}"
        )


def test_registry_is_nonempty():
    """
    The registry must contain at least one reason entry.

    **Validates: Requirements 6.2**
    """
    assert len(REASONS) > 0, "Registry contains no reason entries"
