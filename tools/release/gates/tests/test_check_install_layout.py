"""Tests for the release package install-layout checker."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CHECK_INSTALL_LAYOUT = (
    REPOSITORY_ROOT / "tools" / "release" / "gates" / "check_install_layout.sh"
)


def _run_layout_check(
    tmp_path: Path,
    package_name: str,
    package_paths: list[str],
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    contents_path = tmp_path / "package-contents.txt"
    contents_path.write_text(
        "".join(f"./{package_path}\n" for package_path in package_paths),
        encoding="utf-8",
    )
    dpkg_deb = fake_bin / "dpkg-deb"
    dpkg_deb.write_text(
        "#!/bin/sh\n"
        "[ \"$1\" = \"--contents\" ] || exit 2\n"
        "cat \"$PACKAGE_CONTENTS_FILE\"\n",
        encoding="utf-8",
    )
    dpkg_deb.chmod(0o755)
    package_path = tmp_path / package_name
    package_path.touch()
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["PACKAGE_CONTENTS_FILE"] = str(contents_path)

    return subprocess.run(
        ["bash", str(CHECK_INSTALL_LAYOUT), str(package_path)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_ubuntu_native_deb_uses_its_documentation_directory(
    tmp_path: Path,
) -> None:
    package_paths = [
        "usr/lib/nginx/modules/ngx_http_markdown_filter_module.so",
        "usr/share/nginx/modules-available/mod-markdown.conf",
        "usr/share/doc/nginx-markdown-for-agents-ubuntu/LICENSE",
        "usr/share/doc/nginx-markdown-for-agents-ubuntu/README.md",
        "usr/share/doc/nginx-markdown-for-agents-ubuntu/UBUNTU-LTS-NGINX-COMPATIBILITY.md",
    ]
    result = _run_layout_check(
        tmp_path,
        "nginx-module-markdown-for-agents-ubuntu_0.9.3_ubuntu-24.04"
        "_nginx-1.24.0-2ubuntu7.19_amd64.deb",
        package_paths,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_nginx_org_deb_keeps_the_shared_documentation_layout(
    tmp_path: Path,
) -> None:
    package_paths = [
        "usr/lib/nginx/modules/ngx_http_markdown_filter_module.so",
        "usr/share/nginx/modules-available/mod-markdown.conf",
        "usr/share/doc/nginx-markdown-for-agents/README.md",
        "usr/share/doc/nginx-markdown-for-agents/INSTALL.md",
        "usr/share/doc/nginx-markdown-for-agents/PACKAGE_INSTALLATION.md",
        "usr/share/doc/nginx-markdown-for-agents/PACKAGE_COMPATIBILITY.md",
        "usr/share/licenses/nginx-markdown-for-agents/LICENSE",
    ]
    result = _run_layout_check(
        tmp_path,
        "nginx-module-markdown-for-agents_0.9.3_nginx-1.30.6_amd64.deb",
        package_paths,
    )

    assert result.returncode == 0, result.stdout + result.stderr
