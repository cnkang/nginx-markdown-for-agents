"""Exercise the lifecycle script's real path resolver without root or APT.

The production helper and capture statements are extracted. No logic is copied,
and the script's container and package-mutation guards remain intact.
"""

from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[4] / "packaging/tests/test_ubuntu_native_upgrade.sh"


def _resolve(cwd: Path, package: str) -> subprocess.CompletedProcess[str]:
    """Run the production resolver and capture with a Bash argv argument."""
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("resolve_package_file() {")
    end = source.index("\n}", start) + 2
    helper = source[start:end]
    capture_start = source.index('package_file="$(resolve_package_file ')
    capture_end = source.index('\nservice_policy_status=', capture_start)
    capture = source[capture_start:capture_end]
    return subprocess.run(
        ["bash", "-ec", helper + '\npackage_file="$1"\n' + capture
         + '\nprintf "%s" "$package_file"', "resolver", package],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("kind", ["relative", "dot_relative", "absolute", "symlink",
                                 "trailing_newline", "trailing_newlines"])
def test_local_deb_paths_become_absolute_files(tmp_path: Path, kind: str) -> None:
    """APT must receive the actual file for every supported path spelling."""
    suffix = {"trailing_newline": "\n", "trailing_newlines": "\n\n"}.get(kind, "")
    package = tmp_path / "dist" / ("module package.deb" + suffix)
    package.parent.mkdir()
    package.write_bytes(b"fixture")
    link = tmp_path / "linked.deb"
    link.symlink_to(package)
    arguments = {
        "relative": "dist/module package.deb",
        "dot_relative": "./dist/module package.deb",
        "absolute": str(package),
        "symlink": str(link),
        "trailing_newline": str(package),
        "trailing_newlines": str(package),
    }
    result = _resolve(tmp_path, arguments[kind])
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(package.resolve())
    resolved = Path(result.stdout)
    assert resolved.is_absolute()
    assert resolved.samefile(package)


def test_missing_deb_fails_before_package_operations(tmp_path: Path) -> None:
    """The production resolver must reject a missing local DEB artifact."""
    result = _resolve(tmp_path, "dist/missing.deb")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "DEB file does not exist" in result.stderr


def test_package_resolution_precedes_all_apt_commands() -> None:
    """A package transaction must not start before path resolution succeeds."""
    lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    resolution = lines.index('package_file="$(resolve_package_file "$package_file" && printf x)"')
    apt_lines = [
        index for index, line in enumerate(lines)
        if "apt-get" in line and not line.lstrip().startswith("#")
    ]
    assert apt_lines
    assert all(index > resolution for index in apt_lines)
