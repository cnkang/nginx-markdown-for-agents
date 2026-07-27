#!/usr/bin/env python3
"""Compare a downloaded source archive with a resolved Git commit archive.

Archive container metadata and the top-level directory name may differ. The
comparison therefore uses normalized tracked-file content, executable bits,
and symbolic-link targets without extracting either archive.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from lib.path_validation import validate_read_path  # noqa: E402


@dataclass(frozen=True)
class ArchiveEntry:
    """Security-relevant identity of one tracked archive entry."""

    kind: str
    executable: bool
    identity: str


def _relative_name(name: str, root: str | None) -> tuple[str, str]:
    """Return a safe path with the archive's single root directory removed."""
    if name.startswith("/"):
        raise ValueError(f"archive contains absolute path: {name!r}")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise ValueError(f"archive contains invalid path: {name!r}")
    if root is not None and parts[0] != root:
        raise ValueError("archive contains more than one top-level directory")
    return parts[0], "/".join(parts[1:])


def _process_file_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> ArchiveEntry:
    """Process a regular file member and return its archive entry."""
    source = archive.extractfile(member)
    if source is None:
        raise ValueError(f"cannot read archive member: {member.name}")
    digest = hashlib.sha256(source.read()).hexdigest()
    return ArchiveEntry(
        kind="file",
        executable=bool(member.mode & 0o111),
        identity=digest,
    )


def _process_symlink_member(member: tarfile.TarInfo) -> ArchiveEntry:
    """Process a symlink member and return its archive entry."""
    return ArchiveEntry(
        kind="symlink",
        executable=False,
        identity=member.linkname,
    )


def _process_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    relative: str,
    manifest: dict[str, ArchiveEntry],
) -> None:
    """Process a single archive member and add to manifest."""
    if member.isdir():
        return
    if not relative:
        raise ValueError("archive file entry has no path below its root")
    if relative in manifest:
        raise ValueError(f"archive contains duplicate path: {relative}")
    if member.isfile():
        manifest[relative] = _process_file_member(archive, member)
    elif member.issym():
        manifest[relative] = _process_symlink_member(member)
    else:
        raise ValueError(f"archive contains unsupported entry type: {relative}")


def archive_manifest(path: Path) -> dict[str, ArchiveEntry]:
    """Build a normalized content manifest without extracting the archive."""
    manifest: dict[str, ArchiveEntry] = {}
    root: str | None = None
    with tarfile.open(path, mode="r:*") as archive:
        for member in archive:
            member_root, relative = _relative_name(member.name, root)
            if root is None:
                root = member_root
            _process_member(archive, member, relative, manifest)
    if not manifest:
        raise ValueError("archive contains no tracked files")
    return manifest


def compare_archives(candidate: Path, reference: Path) -> None:
    """Raise ``ValueError`` unless both archives represent the same Git tree."""
    candidate_manifest = archive_manifest(candidate)
    reference_manifest = archive_manifest(reference)
    if candidate_manifest == reference_manifest:
        return

    differing = sorted(
        path
        for path in candidate_manifest.keys() | reference_manifest.keys()
        if candidate_manifest.get(path) != reference_manifest.get(path)
    )
    preview = ", ".join(differing[:5])
    raise ValueError(
        "downloaded archive does not match resolved Git commit "
        f"({len(differing)} differing path(s): {preview})"
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, help="downloaded archive")
    parser.add_argument("--reference", required=True, help="git archive")
    return parser.parse_args()


def main() -> int:
    """Validate both paths and compare their normalized manifests."""
    args = parse_args()
    try:
        candidate = validate_read_path(
            args.candidate,
            purpose="downloaded release archive",
        )
        reference = validate_read_path(
            args.reference,
            purpose="resolved Git commit archive",
        )
        compare_archives(candidate, reference)
    except (OSError, tarfile.TarError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Downloaded archive matches resolved Git commit", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())