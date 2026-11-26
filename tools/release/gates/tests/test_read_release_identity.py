from pathlib import Path

import pytest

from tools.release.read_release_identity import read_release_identity

REPO_ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("quote", ['"', "'"])
def test_reads_valid_toml_string_spellings(tmp_path: Path, quote: str) -> None:
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        f"[package]\nname = 'example'\nversion = {quote}1.0.0-rc.1{quote}\n",
        encoding="utf-8",
    )

    assert read_release_identity(manifest) == "1.0.0-rc.1"


def test_rejects_identity_outside_shared_semver_grammar(tmp_path: Path) -> None:
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(
        "[package]\nname = 'example'\nversion = '1.0.0-rc.01'\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid release identity"):
        read_release_identity(manifest)


def test_release_build_scripts_use_the_shared_reader() -> None:
    scripts = (
        REPO_ROOT / "tools/build_release.sh",
        REPO_ROOT / "packaging/scripts/build-ubuntu-native-module.sh",
    )

    assert all(
        "read_release_identity.py" in script.read_text(encoding="utf-8")
        for script in scripts
    )
