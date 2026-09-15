#!/usr/bin/env python3
"""validate-release-manifest.py — Validate release-manifest.json.

Checks schema, package integrity, SHA256SUMS inclusion, and absence of
placeholder values.

Usage:
    validate-release-manifest.py -m MANIFEST -d ARTIFACT_DIR [--sha256sums SHA256SUMS] [--version VERSION] [--require-bootstrap-assets]

Exit codes:
    0  All validations passed
    1  Validation failure
"""

from __future__ import annotations

import argparse
import hashlib
import os
import json
import re
import sys
from pathlib import Path

PLACEHOLDER_PATTERNS = [
    re.compile(r"PLACEHOLDER", re.IGNORECASE),
    re.compile(r"<[^>]+>"),  # <...> angle-bracket placeholders
    re.compile(r"TODO", re.IGNORECASE),
    re.compile(r"FIXME", re.IGNORECASE),
]

SEMVER_TAG_RE = re.compile(
    r"v?(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_no_placeholders(value: str, field_path: str, errors: list[str]) -> None:
    """Check that a string value contains no placeholder patterns."""
    for pat in PLACEHOLDER_PATTERNS:
        if pat.search(value):
            errors.append(f"{field_path} contains placeholder: {value!r}")
            return


def parse_sha256sums(path: Path, errors: list[str]) -> dict[str, str]:
    """Parse SHA256SUMS as 'digest  filename' records."""
    entries: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as e:
        errors.append(f"Cannot read SHA256SUMS: {e}")
        return entries

    for line_no, line in enumerate(lines, start=1):
        if not line:
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2:
            errors.append(f"SHA256SUMS line {line_no} has invalid format")
            continue
        digest, filename = parts
        if not re.match(r"^[0-9a-f]{64}$", digest):
            errors.append(
                f"SHA256SUMS line {line_no} digest is not a 64-char hex string"
            )
            continue
        if not filename:
            errors.append(f"SHA256SUMS line {line_no} filename is empty")
            continue
        if filename in entries:
            errors.append(f"Duplicate file in SHA256SUMS: {filename}")
            continue
        entries[filename] = digest

    return entries


def sha256_no_follow(path: Path) -> str:
    """Hash a file without following a symlink at the final component.

    The containment check resolves the path, so re-opening by name leaves a
    window in which the entry could be replaced; opening the descriptor once
    closes it.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    digest = hashlib.sha256()
    fd = os.open(path, flags)
    try:
        handle = os.fdopen(fd, "rb")
    except OSError:
        os.close(fd)
        raise
    with handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_bundle_name(tag: str) -> str:
    """Name of the source bundle the release workflow publishes for a tag."""
    return f"nginx-markdown-for-agents-source-{tag}.tar.gz"


def _check_source_bundle(
    source,
    bundle_name: str,
    expected_url: str | None,
    artifact_dir: Path,
    sha256_entries: dict[str, str],
    errors: list[str],
) -> None:
    """Check the published source bundle and the digests that describe it.

    Allowing the name in SHA256SUMS is not enough: the bundle is the provenance
    artifact, so its on-disk digest must match both the signed checksum file and
    the manifest.  A manifest that records a digest without publishing the
    artifact is exactly the gap this check exists to close.
    """
    bundle_path = artifact_dir / bundle_name
    recorded = source.get("sha256") if isinstance(source, dict) else None

    # The name comes from the manifest, so the resolved path has to stay inside
    # the artifact directory: a traversal or an escaping symlink must not make
    # these checks read something else.
    try:
        root = artifact_dir.resolve()
        resolved = bundle_path.resolve()
    except (OSError, RuntimeError) as exc:
        errors.append(f"{bundle_name} cannot be resolved ({exc})")
        return
    if not resolved.is_relative_to(root):
        errors.append(
            f"{bundle_name} resolves outside the artifact directory and is not "
            "inspected"
        )
        return

    # The URL is part of the provenance claim: a link to a differently named or
    # differently tagged artifact would describe something other than the bundle
    # whose digest the manifest records.
    if expected_url is not None:
        actual_url = source.get("archive_url") if isinstance(source, dict) else None
        if actual_url != expected_url:
            errors.append(
                "source.archive_url does not point at the published bundle: "
                f"expected={expected_url}, actual={actual_url}"
            )

    if not bundle_path.is_file():
        if recorded:
            errors.append(
                f"{bundle_name} is missing from the artifact directory while "
                "the manifest records source.sha256"
            )
        return

    try:
        actual = sha256_no_follow(bundle_path)
    except OSError as exc:
        errors.append(f"{bundle_name} cannot be read safely ({exc})")
        return
    # This comparison needs the checksum data; the presence and URL checks above
    # do not, which is why they run for every tag release.
    if sha256_entries and sha256_entries.get(bundle_name) != actual:
        errors.append(
            f"SHA256SUMS digest mismatch for {bundle_name}: "
            f"sha256sums={sha256_entries.get(bundle_name)}, actual={actual}"
        )
    if recorded != actual:
        errors.append(
            f"source.sha256 does not match the published bundle: "
            f"manifest={recorded}, actual={actual}"
        )


def validate_manifest(
    manifest_path: Path,
    artifact_dir: Path,
    sha256sums_path: Path | None,
    expected_version: str | None,
    require_bootstrap_assets: bool = False,
) -> list[str]:
    """
    Validate a release manifest and its associated artifacts.

    Parameters:
        manifest_path (Path): Path to the release manifest.
        artifact_dir (Path): Directory containing the release artifacts.
        sha256sums_path (Path | None): Optional path to the SHA256SUMS file.
        expected_version (str | None): Optional version that the manifest must declare.
        require_bootstrap_assets (bool): Whether semantic-version tag releases must include bootstrap assets.

    Returns:
        list[str]: Validation error messages; an empty list indicates a valid manifest.
    """
    errors: list[str] = []

    # Load manifest
    try:
        text = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(text)
    except json.JSONDecodeError as e:
        return [f"Invalid JSON: {e}"]
    except (OSError, UnicodeError) as e:
        return [f"Cannot read manifest: {e}"]

    # Basic schema checks
    if not isinstance(manifest, dict):
        return ["Manifest must be a JSON object"]

    for key in ("schema_version", "project", "version", "git", "packages", "integrity", "workflow"):
        if key not in manifest:
            errors.append(f"Missing required top-level key: {key}")

    if errors:
        return errors  # Can't continue without basic keys

    # schema_version
    if manifest["schema_version"] != 1:
        errors.append(f"Unexpected schema_version: {manifest['schema_version']}")

    # project
    if manifest["project"] != "nginx-markdown-for-agents":
        errors.append(f"Unexpected project: {manifest['project']}")

    # version
    version = manifest["version"]
    check_no_placeholders(version, "version", errors)
    if expected_version and version != expected_version:
        errors.append(f"Version mismatch: manifest={version}, expected={expected_version}")

    # git
    git = manifest["git"]
    is_tag_release = (
        manifest.get("workflow", {}).get("ref_type") == "tag"
        or (isinstance(git, dict) and bool(git.get("tag")))
    )
    if not isinstance(git, dict):
        errors.append("git must be an object")
    else:
        for key in ("repository", "commit"):
            if key not in git:
                errors.append(f"Missing git.{key}")
            elif not git[key]:
                errors.append(f"git.{key} is empty")
            else:
                check_no_placeholders(git[key], f"git.{key}", errors)
        if "commit" in git and not re.match(r"^[0-9a-f]{7,40}$", git["commit"]):
            errors.append(f"git.commit does not look like a SHA: {git['commit']}")

        # git.tag is required for tag releases, optional for workflow_dispatch
        if is_tag_release:
            if "tag" not in git:
                errors.append("Missing git.tag (required for tag releases)")
            elif not git["tag"]:
                errors.append("git.tag is empty (required for tag releases)")
            else:
                check_no_placeholders(git["tag"], "git.tag", errors)

    # packages
    manifest_filenames: set[str] = set()
    package_entries: list[dict[str, object]] = []
    packages = manifest.get("packages", [])
    if not isinstance(packages, list) or len(packages) == 0:
        errors.append("packages must be a non-empty list")
    else:
        for i, pkg in enumerate(packages):
            prefix = f"packages[{i}]"
            if not isinstance(pkg, dict):
                errors.append(f"{prefix}: package must be an object")
                continue
            malformed = [
                key
                for key in (
                    "filename", "format", "version", "nginx_version",
                    "libc", "arch", "sha256",
                )
                if key in pkg and not isinstance(pkg[key], str)
            ]
            if malformed:
                for key in malformed:
                    errors.append(
                        f"{prefix}: {key} must be a string, got: {type(pkg[key]).__name__}"
                    )
                continue
            package_entries.append(pkg)
            # dynamic-module tarballs carry nginx_version/libc/arch instead of
            # a project version (their name encodes the NGINX version, not the
            # release version).  Require the version key for deb/rpm only, and
            # the full identity set for dynamic-module entries.
            if pkg.get("format") in ("deb", "rpm"):
                required_keys = ("filename", "format", "version", "sha256")
            elif pkg.get("format") == "dynamic-module":
                required_keys = (
                    "filename", "format", "nginx_version", "libc", "arch", "sha256",
                )
            else:
                required_keys = ("filename", "format", "sha256")
            for key in required_keys:
                if key not in pkg:
                    errors.append(f"{prefix}: missing {key}")

            if "filename" in pkg:
                fname = pkg["filename"]
                check_no_placeholders(fname, f"{prefix}.filename", errors)
                manifest_filenames.add(fname)

                # Reject path traversal — filename must resolve inside artifact_dir
                resolved_artifact = artifact_dir.resolve()
                try:
                    fpath = (resolved_artifact / fname).resolve()
                    fpath.relative_to(resolved_artifact)
                except (ValueError, RuntimeError):
                    errors.append(
                        f"{prefix}: filename escapes artifact directory: {fname}"
                    )
                    continue

                if not fpath.exists():
                    errors.append(f"{prefix}: file not found in artifacts: {fname}")
                elif "sha256" in pkg:
                    # Same single-open reader as the bundle: resolving and then
                    # reopening by name leaves a window for a swap.
                    try:
                        actual_sha = sha256_no_follow(fpath)
                    except OSError as exc:
                        errors.append(f"{prefix}: cannot read {fname}: {exc}")
                    else:
                        if actual_sha != pkg["sha256"]:
                            errors.append(
                                f"{prefix}: SHA256 mismatch for {fname}: "
                                f"manifest={pkg['sha256']}, actual={actual_sha}"
                            )

            if "format" in pkg and pkg["format"] not in ("deb", "rpm", "dynamic-module"):
                errors.append(f"{prefix}: unexpected format: {pkg['format']}")

            if "sha256" in pkg:
                check_no_placeholders(pkg["sha256"], f"{prefix}.sha256", errors)
                if not re.match(r"^[0-9a-f]{64}$", pkg["sha256"]):
                    errors.append(f"{prefix}: sha256 is not a 64-char hex string")

    # source — required for tag releases, optional for workflow_dispatch
    source = manifest.get("source")
    if is_tag_release:
        if not source or not isinstance(source, dict):
            errors.append("source is required for tag releases")
        elif not source.get("available", False):
            errors.append("source.available must be true for tag releases")
        else:
            if "archive_url" not in source or not source["archive_url"]:
                errors.append("source.archive_url is required for tag releases")
            else:
                check_no_placeholders(source["archive_url"], "source.archive_url", errors)
            # The digest is required for tag releases.  The release workflow
            # builds the source bundle from the released commit and records its
            # digest, so provenance is self-contained and no longer depends on a
            # registry entry that can only be written after the tag exists.
            if "sha256" not in source:
                errors.append(
                    "source.sha256 is required for tag releases: the release "
                    "workflow builds the source bundle from the released commit"
                )
            else:
                digest = source["sha256"]
                if not isinstance(digest, str) or not digest:
                    errors.append(
                        "source.sha256 must be a non-empty string for tag releases"
                    )
                else:
                    check_no_placeholders(digest, "source.sha256", errors)
                    if not re.match(r"^[0-9a-f]{64}$", digest):
                        errors.append("source.sha256 is not a 64-char hex string")
    elif source and isinstance(source, dict):
        # Non-tag: source is optional; if present and available, validate fields
        if source.get("available", False):
            if "archive_url" in source:
                check_no_placeholders(source["archive_url"], "source.archive_url", errors)
            if "sha256" in source:
                check_no_placeholders(source["sha256"], "source.sha256", errors)
                if not re.match(r"^[0-9a-f]{64}$", source["sha256"]):
                    errors.append("source.sha256 is not a 64-char hex string")

    # integrity
    integrity = manifest.get("integrity", {})
    if isinstance(integrity, dict):
        if integrity.get("checksums") != "SHA256SUMS":
            errors.append(f"integrity.checksums expected SHA256SUMS, got: {integrity.get('checksums')}")
        if is_tag_release:
            if integrity.get("signature") != "SHA256SUMS.asc":
                errors.append(f"integrity.signature expected SHA256SUMS.asc, got: {integrity.get('signature')}")
            if integrity.get("signature_available") is not True:
                errors.append("integrity.signature_available must be true for tag releases")
            if integrity.get("signature_type") != "gpg-detached-ascii-armored":
                errors.append(
                    "integrity.signature_type expected gpg-detached-ascii-armored, "
                    f"got: {integrity.get('signature_type')}"
                )
            if integrity.get("signed_file") != "SHA256SUMS":
                errors.append(f"integrity.signed_file expected SHA256SUMS, got: {integrity.get('signed_file')}")
        else:
            if integrity.get("signature") is not None:
                errors.append(
                    f"integrity.signature expected null for non-tag runs, got: {integrity.get('signature')}"
                )
            if integrity.get("signature_available") is not False:
                errors.append("integrity.signature_available must be false for non-tag runs")
            if integrity.get("signature_type") is not None:
                errors.append(
                    f"integrity.signature_type expected null for non-tag runs, got: {integrity.get('signature_type')}"
                )
            if integrity.get("signed_file") is not None:
                errors.append(
                    f"integrity.signed_file expected null for non-tag runs, got: {integrity.get('signed_file')}"
                )

    manifest_artifact = artifact_dir.resolve() / "release-manifest.json"
    if manifest_artifact.exists():
        try:
            if manifest_path.resolve() != manifest_artifact.resolve():
                artifact_text = manifest_artifact.read_text(encoding="utf-8")
                if artifact_text != text:
                    errors.append(
                        "CLI manifest differs from artifact_dir/release-manifest.json"
                    )
        except (OSError, UnicodeError) as e:
            errors.append(
                "Cannot compare CLI manifest with artifact_dir/release-manifest.json: "
                f"{e}"
            )

    # SHA256SUMS inclusion and digest consistency
    # Parse the checksum file first: the source-bundle check below compares
    # against it when it is available, and the rest of the checksum validation
    # reads the same mapping.
    sha256_entries: dict[str, str] = {}
    if sha256sums_path and sha256sums_path.exists():
        sha256_entries = parse_sha256sums(sha256sums_path, errors)

    # A tag that is not a semantic release tag cannot name the bootstrap assets
    # at all, so a strict run has to refuse instead of finding nothing to check.
    # This stands outside the checksum-file branch: the requirement is about the
    # tag, not about which files happen to be present.
    if require_bootstrap_assets and is_tag_release and isinstance(git, dict):
        required_tag = git.get("tag", "")
        if not (
            isinstance(required_tag, str) and SEMVER_TAG_RE.fullmatch(required_tag)
        ):
            errors.append(
                "git.tag must be a semantic release tag to validate bootstrap assets"
            )

    # The bundle is the provenance artifact for a tag release, so its
    # presence, its recorded digest and the URL that points at it are
    # checked for every tag release, not only when a checksum file happens
    # to be available.  Only the comparison against SHA256SUMS needs the
    # checksum data.
    if is_tag_release and isinstance(git, dict):
        release_tag = git.get("tag", "")
        if isinstance(release_tag, str) and release_tag:
            bundle_name = _source_bundle_name(release_tag)
            repository = git.get("repository")
            expected_url = (
                f"https://github.com/{repository}/releases/download/"
                f"{release_tag}/{bundle_name}"
                if repository
                else None
            )
            _check_source_bundle(
                source,
                bundle_name,
                expected_url,
                artifact_dir,
                sha256_entries,
                errors,
            )

    if sha256sums_path and sha256sums_path.exists():
        if "release-manifest.json" not in sha256_entries:
            errors.append("release-manifest.json not found in SHA256SUMS")

        if "release-manifest.json" in sha256_entries:
            if not manifest_artifact.exists():
                errors.append(
                    "release-manifest.json listed in SHA256SUMS but not found in artifacts"
                )
            else:
                actual_manifest_sha = sha256_file(manifest_artifact)
                if sha256_entries["release-manifest.json"] != actual_manifest_sha:
                    errors.append(
                        "SHA256SUMS digest mismatch for release-manifest.json: "
                        f"sha256sums={sha256_entries['release-manifest.json']}, "
                        f"actual={actual_manifest_sha}"
                    )

        allowed_sha256_names = set(manifest_filenames)
        allowed_sha256_names.add("release-manifest.json")

        bootstrap_filenames: set[str] = set()
        if is_tag_release and isinstance(git, dict):
            tag = git.get("tag", "")
            if isinstance(tag, str) and tag:
                # The bundle is published for every tag, so the reverse scan
                # must allow its name whatever the tag looks like.
                allowed_sha256_names.add(_source_bundle_name(tag))
            if isinstance(tag, str) and SEMVER_TAG_RE.fullmatch(tag):
                bootstrap_filenames = {
                    f"nginx-markdown-for-agents-installer-{tag}.sh",
                    "nginx-markdown-for-agents-release.asc",
                }
                # The release workflow builds this bundle from the released
                # commit and publishes it, so the signed checksum file covers
                # it.  Without this entry every tag release fails the reverse
                # scan below with "Unexpected file in SHA256SUMS".

        allowed_sha256_names.update(bootstrap_filenames)

        for pkg in package_entries:
            if "filename" not in pkg:
                continue
            fname = pkg["filename"]
            sums_sha = sha256_entries.get(fname)
            if sums_sha is None:
                errors.append(f"Package {fname} not found in SHA256SUMS")
                continue
            if "sha256" in pkg and sums_sha != pkg["sha256"]:
                errors.append(
                    f"SHA256SUMS digest mismatch for {fname}: "
                    f"sha256sums={sums_sha}, manifest={pkg['sha256']}"
                )

        for fname in sorted(sha256_entries):
            if fname not in allowed_sha256_names:
                errors.append(f"Unexpected file in SHA256SUMS: {fname}")

        # Every regular artifact file must be covered by the signed checksum
        # manifest.  The manifest and its detached signature are the only
        # metadata files intentionally exempt from this reverse check.
        unhashed_metadata = {"SHA256SUMS", "SHA256SUMS.asc"}
        try:
            artifacts = sorted(artifact_dir.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            errors.append(f"Cannot scan artifact directory: {exc}")
        else:
            for artifact in artifacts:
                if (
                    artifact.is_file()
                    and artifact.name not in unhashed_metadata
                    and artifact.name not in sha256_entries
                ):
                    errors.append(
                        f"Artifact {artifact.name} is missing from SHA256SUMS"
                    )

        for fname in sorted(bootstrap_filenames):
            fpath = artifact_dir / fname
            sums_sha = sha256_entries.get(fname)
            if sums_sha is None:
                if require_bootstrap_assets:
                    errors.append(f"Bootstrap asset {fname} not found in SHA256SUMS")
                continue
            if not fpath.is_file():
                errors.append(f"Bootstrap asset {fname} listed in SHA256SUMS but not found in artifacts")
                continue
            actual_sha = sha256_file(fpath)
            if sums_sha != actual_sha:
                errors.append(
                    f"SHA256SUMS digest mismatch for bootstrap asset {fname}: "
                    f"sha256sums={sums_sha}, actual={actual_sha}"
                )
    elif sha256sums_path:
        errors.append(f"SHA256SUMS file not found: {sha256sums_path}")
    elif require_bootstrap_assets and is_tag_release:
        errors.append(
            "SHA256SUMS is required when bootstrap assets are explicitly required"
        )

    # Check packages are sorted deterministically
    if package_entries:
        filenames = [p.get("filename", "") for p in package_entries]
        if filenames != sorted(filenames):
            errors.append("Packages are not sorted by filename")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate release-manifest.json")
    parser.add_argument("-m", "--manifest", required=True, help="Path to release-manifest.json")
    parser.add_argument("-d", "--artifact-dir", required=True, help="Artifact directory")
    parser.add_argument("--sha256sums", default=None, help="Path to SHA256SUMS file")
    parser.add_argument("--version", default=None, help="Expected version")
    parser.add_argument(
        "--require-bootstrap-assets",
        action="store_true",
        help="Require the installer and release-signature bootstrap assets",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    artifact_dir = Path(args.artifact_dir)
    sha256sums_path = Path(args.sha256sums) if args.sha256sums else None

    errors = validate_manifest(
        manifest_path,
        artifact_dir,
        sha256sums_path,
        args.version,
        args.require_bootstrap_assets,
    )

    if errors:
        print("VALIDATION FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        raise SystemExit(1)

    print("release-manifest.json: all validations passed ✓")


if __name__ == "__main__":
    main()
