"""Exercise the lifecycle script's real path resolver without root or APT.

Only the production helper is extracted. No path-resolution logic is copied,
and the script's container and package-mutation guards remain intact.
"""

from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[4] / "packaging/tests/test_ubuntu_native_upgrade.sh"


def _resolve(cwd: Path, package: str) -> subprocess.CompletedProcess[str]:
    """Run the production helper with an argument passed through Bash argv."""
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("resolve_package_file() {")
    end = source.index("\n}", start) + 2
    helper = source[start:end]
    return subprocess.run(
        ["bash", "-c", helper + '\nresolve_package_file "$1"', "resolver", package],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("kind", ["relative", "dot_relative", "absolute", "symlink"])
def test_local_deb_paths_become_absolute_files(tmp_path: Path, kind: str) -> None:
    """APT must receive the actual file for every supported path spelling."""
    package = tmp_path / "dist" / "module package.deb"
    package.parent.mkdir()
    package.write_bytes(b"fixture")
    link = tmp_path / "linked.deb"
    link.symlink_to(package)
    arguments = {
        "relative": "dist/module package.deb",
        "dot_relative": "./dist/module package.deb",
        "absolute": str(package),
        "symlink": str(link),
    }
    result = _resolve(tmp_path, arguments[kind])
    assert result.returncode == 0, result.stderr
    resolved = Path(result.stdout.strip())
    assert resolved.is_absolute()
    assert resolved.samefile(package)


def test_missing_deb_fails_before_package_operations(tmp_path: Path) -> None:
    """A missing local artifact must not become an APT package argument."""
    result = _resolve(tmp_path, "dist/missing.deb")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "DEB file does not exist" in result.stderr
