"""Regression tests for the 0.9.2 release gate validator."""

from __future__ import annotations

import json
from pathlib import Path

from tools.release.gates import validate_release_gates_092 as validator


def _write_reason_fixture(tmp_path: Path, *, all_names=None, source_edit=None) -> None:
    """Create a complete frozen reason registry fixture."""
    count = validator.EXPECTED_REASON_CODE_COUNT
    names = [f"Code{index}" for index in range(count)]
    strings = [f"code_{index}" for index in range(count)]
    metrics = ["markdown_errors_total" for _ in range(count)]
    all_names = all_names or names
    enum = "\n".join(
        f"    {name} = {index}," for index, name in enumerate(names))
    all_entries = "\n".join(f"    ReasonCode::{name}," for name in all_names)
    as_str = "\n".join(
        f"            ReasonCode::{name} => \"{strings[index]}\","
        for index, name in enumerate(names))
    metric = "\n".join(
        f"            ReasonCode::{name} => \"{metrics[index]}\","
        for index, name in enumerate(names))
    source = f"""pub const REASON_CODE_COUNT: usize = {count};
pub enum ReasonCode {{
{enum}
}}
pub const ALL: [ReasonCode; REASON_CODE_COUNT] = [
{all_entries}
];
impl ReasonCode {{
    pub fn as_str(self) -> &'static str {{
        match self {{
{as_str}
        }}
    }}
    pub fn metric_key(self) -> &'static str {{
        match self {{
{metric}
        }}
    }}
    pub fn log_callsite(self) -> &'static str {{ "test" }}
}}
"""
    if source_edit is not None:
        source = source_edit(source)
    reason_path = tmp_path / validator.REASON_CODE_RELATIVE_PATH
    reason_path.parent.mkdir(parents=True)
    reason_path.write_text(source, encoding="utf-8")
    c_path = tmp_path / validator.REASON_C_RELATIVE_PATH
    c_path.parent.mkdir(parents=True)
    c_path.write_text(
        "\n".join(f"static ngx_str_t reason_str_code_{index};"
                  for index in range(count)),
        encoding="utf-8",
    )
    inventory_path = tmp_path / validator.REASON_INVENTORY_RELATIVE_PATH
    inventory_path.parent.mkdir(parents=True)
    inventory_path.write_text(json.dumps({
        "registry_count": count,
        "reason_codes": [
            {
                "discriminant": index,
                "name": name,
                "string": strings[index],
                "metric_key": metrics[index],
                "c_accessor": f"reason_str_code_{index}",
            }
            for index, name in enumerate(names)
        ],
    }), encoding="utf-8")


def test_find_repo_root_returns_checkout_root() -> None:
    """The validator must resolve paths from the checkout, not the cwd."""
    repo = validator.find_repo_root()

    assert repo == Path(validator.__file__).resolve().parents[3]
    assert (repo / "CHANGELOG.md").is_file()


def test_version_consistency_fails_when_sources_are_missing(tmp_path: Path) -> None:
    """Version consistency must fail closed when either source is absent."""
    result = validator.check_version_consistency(tmp_path)

    assert result["status"] == "fail"
    assert "Cargo.toml: file not found" in result["message"]
    assert "CHANGELOG.md: file not found" in result["message"]


def test_version_consistency_reads_the_package_table(tmp_path: Path) -> None:
    """A workspace version must not hide a mismatching package version."""
    cargo_path = tmp_path / "components" / "rust-converter" / "Cargo.toml"
    cargo_path.parent.mkdir(parents=True)
    cargo_path.write_text(
        "[workspace.package]\nversion = \"0.9.2\"\n\n"
        "[package]\nname = \"fixture\"\nversion = \"0.9.1\"\n",
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(
        "## [0.9.2]\n", encoding="utf-8"
    )

    result = validator.check_version_consistency(tmp_path)

    assert result["status"] == "fail"
    assert "Cargo.toml: 0.9.1" in result["message"]


def test_version_consistency_rejects_invalid_utf8_cargo_file(
    tmp_path: Path,
) -> None:
    """Invalid Cargo.toml bytes must remain a reported gate mismatch."""
    cargo_path = tmp_path / "components" / "rust-converter" / "Cargo.toml"
    cargo_path.parent.mkdir(parents=True)
    cargo_path.write_bytes(b"[package]\nversion = \xff\n")
    (tmp_path / "CHANGELOG.md").write_text(
        "## [0.9.2]\n", encoding="utf-8"
    )

    result = validator.check_version_consistency(tmp_path)

    assert result["status"] == "fail"
    assert "Cargo.toml: invalid TOML" in result["message"]


def test_reason_code_registry_accepts_complete_fixture(tmp_path: Path) -> None:
    """A complete Rust/C/inventory registry must pass with its full count."""
    _write_reason_fixture(tmp_path)

    result = validator.check_reason_code_registry(tmp_path)

    assert result["status"] == "pass"
    assert result["details"]["count"] == validator.EXPECTED_REASON_CODE_COUNT


def test_reason_code_registry_accepts_reindented_metric_match(
    tmp_path: Path,
) -> None:
    """Metric match parsing must not depend on Rust brace indentation."""
    _write_reason_fixture(
        tmp_path,
        source_edit=lambda source: source.replace(
            "\n        }\n    }\n    pub fn log_callsite",
            "\n      }\n    }\n    pub fn log_callsite",
        ),
    )

    result = validator.check_reason_code_registry(tmp_path)

    assert result["status"] == "pass"


def test_reason_code_registry_rejects_unterminated_metric_match(
    tmp_path: Path,
) -> None:
    """An unterminated metric match must fail before log_callsite parsing."""
    _write_reason_fixture(
        tmp_path,
        source_edit=lambda source: source.replace(
            "\n        }\n    }\n    pub fn log_callsite",
            "\n    }\n    pub fn log_callsite",
        ),
    )

    result = validator.check_reason_code_registry(tmp_path)

    assert result["status"] == "fail"
    assert "unterminated" in result["message"]


def test_reason_code_registry_rejects_malformed_source(tmp_path: Path) -> None:
    """A missing Rust metadata registry must fail closed."""
    _write_reason_fixture(
        tmp_path,
        source_edit=lambda source: source.replace(
            "pub fn metric_key", "pub fn missing_metric_key"),
    )

    result = validator.check_reason_code_registry(tmp_path)

    assert result["status"] == "fail"
    assert "registr" in result["message"]


def test_reason_code_registry_rejects_duplicate_discriminants(tmp_path: Path) -> None:
    """Duplicate Rust discriminants must not be hidden by dictionary parsing."""
    last_index = validator.EXPECTED_REASON_CODE_COUNT - 1
    _write_reason_fixture(
        tmp_path,
        source_edit=lambda source: source.replace(
            f"Code{last_index} = {last_index},",
            f"Code{last_index} = {last_index - 1},",
        ),
    )

    result = validator.check_reason_code_registry(tmp_path)

    assert result["status"] == "fail"
    assert "duplicate discriminant" in result["message"]


def test_reason_code_registry_rejects_all_array_ordering(tmp_path: Path) -> None:
    """The exhaustive ALL array must preserve enum declaration order."""
    result_path = tmp_path
    _write_reason_fixture(
        result_path,
        all_names=["Code1", "Code0"] + [
            f"Code{index}"
            for index in range(2, validator.EXPECTED_REASON_CODE_COUNT)
        ],
    )

    result = validator.check_reason_code_registry(result_path)

    assert result["status"] == "fail"
    assert "order or membership" in result["message"]
