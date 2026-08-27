#!/usr/bin/env python3
"""Regression tests for the release-matrix normalization entry point
(release-matrix normalization contract).

Run:
    python3 -m pytest tools/release/matrix/tests/test_normalize_matrix.py -q
"""

import json
import pathlib
import sys

import pytest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import normalize_matrix  # noqa: E402

MatrixNormalizationError = normalize_matrix.MatrixNormalizationError


def canonical_entry(**overrides):
    entry = {
        "nginx_version": "1.26.3",
        "os": "linux",
        "libc": "glibc",
        "target": "x86_64-unknown-linux-gnu",
        "artifact_type": "deb",
        "feature_manifest_digest": "sha256:" + "0" * 64,
        "abi_version": 2,
    }
    entry.update(overrides)
    return entry


class TestCanonicalDocument:
    def test_canonical_entries_pass(self):
        doc = {"schema_version": 1, "entries": [canonical_entry()]}
        normalized = normalize_matrix.normalize_document(doc)
        assert normalized["schema_version"] == 1
        assert len(normalized["entries"]) == 1

    def test_compatibility_metadata_is_preserved(self):
        doc = {
            "schema_version": 1,
            "updated_at": "2026-08-05T00:00:00Z",
            "support_tiers": {"supported": "published artifact"},
            "tier_mapping": {"full": "supported"},
            "entries": [canonical_entry()],
        }
        normalized = normalize_matrix.normalize_document(doc)
        assert normalized["updated_at"] == doc["updated_at"]
        assert normalized["support_tiers"] == doc["support_tiers"]
        assert normalized["tier_mapping"] == doc["tier_mapping"]

    def test_optional_image_metadata_is_preserved(self):
        image_digest = "sha256:" + "a" * 64
        doc = {
            "schema_version": 1,
            "entries": [
                canonical_entry(
                    image_ref="nginx:1.31.4@" + image_digest,
                    image_digest=image_digest,
                )
            ],
        }

        normalized = normalize_matrix.normalize_document(doc)
        entry = normalized["entries"][0]
        assert entry["image_ref"] == doc["entries"][0]["image_ref"]
        assert entry["image_digest"] == image_digest

    def test_legacy_metadata_keys_dropped(self):
        doc = {"schema_version": 1, "entries": [canonical_entry(nginx_channel="stable")]}
        normalized = normalize_matrix.normalize_document(doc)
        assert "nginx_channel" not in normalized["entries"][0]


class TestLegacyAliases:
    def test_top_level_matrix_alias(self):
        doc = {
            "schema_version": 1,
            "matrix": [
                {
                    "nginx": "1.26.3",
                    "os_type": "linux",
                    "libc": "glibc",
                    "arch": "x86_64-unknown-linux-gnu",
                    "artifact_type": "rpm",
                    "feature_manifest_digest": "sha256:" + "0" * 64,
                    "abi_version": 2,
                }
            ],
        }
        normalized = normalize_matrix.normalize_document(doc)
        entry = normalized["entries"][0]
        assert entry["nginx_version"] == "1.26.3"
        assert entry["os"] == "linux"
        assert entry["target"] == "x86_64-unknown-linux-gnu"

    def test_mixed_canonical_and_alias_agree(self):
        doc = {
            "schema_version": 1,
            "entries": [canonical_entry(os="linux", os_type="linux")],
        }
        normalized = normalize_matrix.normalize_document(doc)
        assert normalized["entries"][0]["os"] == "linux"


class TestFailClosed:
    def test_simultaneous_entries_and_matrix(self):
        doc = {
            "schema_version": 1,
            "entries": [canonical_entry()],
            "matrix": [canonical_entry()],
        }
        with pytest.raises(MatrixNormalizationError):
            normalize_matrix.normalize_document(doc)

    def test_unknown_top_level_key(self):
        doc = {"schema_version": 1, "entries": [canonical_entry()], "bogus": 1}
        with pytest.raises(MatrixNormalizationError):
            normalize_matrix.normalize_document(doc)

    def test_unknown_entry_key(self):
        doc = {"schema_version": 1, "entries": [canonical_entry(surprise=1)]}
        with pytest.raises(MatrixNormalizationError):
            normalize_matrix.normalize_document(doc)

    def test_alias_canonical_disagreement(self):
        doc = {"schema_version": 1, "entries": [canonical_entry(nginx="1.28.0")]}
        with pytest.raises(MatrixNormalizationError):
            normalize_matrix.normalize_document(doc)

    def test_missing_identity(self):
        doc = {"schema_version": 1, "entries": [{"artifact_type": "deb"}]}
        with pytest.raises(MatrixNormalizationError):
            normalize_matrix.normalize_document(doc)

    def test_non_object_document(self):
        with pytest.raises(MatrixNormalizationError):
            normalize_matrix.normalize_document([])

    def test_entries_not_array(self):
        doc = {"schema_version": 1, "entries": canonical_entry()}
        with pytest.raises(MatrixNormalizationError):
            normalize_matrix.normalize_document(doc)


class TestSchemaContract:
    def test_schema_declares_canonical_contract(self):
        schema_path = pathlib.Path(__file__).resolve().parents[4] / "schemas" / "release-matrix.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["properties"]["schema_version"]["const"] == 1
        assert "entries" in schema["properties"]
        entry_props = schema["$defs"]["entry"]["properties"]
        for key in normalize_matrix.CANONICAL_ENTRY_KEYS:
            assert key in entry_props, f"canonical key {key!r} missing from schema"
        assert "matrix" in schema["properties"]
        assert schema["required"] == ["schema_version"]
        assert len(schema["oneOf"]) == 2
        assert schema["oneOf"][0]["required"] == ["entries"]
        assert schema["oneOf"][1]["required"] == ["matrix"]


class TestCompatibilityDocument:
    def test_compatibility_aliases_share_target_and_tier(self):
        doc = {
            "schema_version": "1.0",
            "entries": [
                {
                    "nginx": "1.26.3",
                    "os": "linux",
                    "os_type": "glibc",
                    "arch": "amd64",
                    "artifact_type": "dynamic-module",
                    "support_tier": "full",
                }
            ],
        }

        normalized = normalize_matrix.normalize_compatibility_document(doc)
        entry = normalized["entries"][0]
        assert entry["nginx_version"] == "1.26.3"
        assert entry["libc"] == "glibc"
        # arch: amd64 canonicalizes to x86_64 so equivalent rows
        # (arch: amd64 vs target: x86_64) share one identity.
        assert entry["target"] == "x86_64"
        assert entry["support_tier"] == "supported"

    def test_compatibility_alias_disagreement_fails_closed(self):
        doc = {
            "entries": [
                {
                    "nginx_version": "1.26.3",
                    "nginx": "1.28.0",
                    "libc": "glibc",
                    "target": "amd64",
                    "support_tier": "supported",
                }
            ]
        }
        with pytest.raises(MatrixNormalizationError):
            normalize_matrix.normalize_compatibility_document(doc)

    def test_compatibility_image_metadata_is_preserved(self):
        image_digest = "sha256:" + "b" * 64
        doc = {
            "entries": [
                {
                    "nginx_version": "1.31.4",
                    "libc": "glibc",
                    "target": "amd64",
                    "artifact_type": "docker-image",
                    "support_tier": "supported",
                    "image_ref": "nginx:1.31.4@" + image_digest,
                    "image_digest": image_digest,
                }
            ]
        }

        normalized = normalize_matrix.normalize_compatibility_document(doc)
        entry = normalized["entries"][0]
        assert entry["image_ref"] == doc["entries"][0]["image_ref"]
        assert entry["image_digest"] == image_digest
