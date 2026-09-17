#!/usr/bin/env python3
"""Unit tests for render_homebrew_formula.py stanza validation.

The renderer only rewrites the class-level url/version/sha256 stanzas, and
must refuse a snapshot whose stanza lines carry trailing content instead of
silently discarding it.

Run: python3 packaging/scripts/test_render_homebrew_formula.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import render_homebrew_formula as rhf  # noqa: E402 - direct test import

URL = "https://example.com/nginx-markdown-for-agents-0.9.2.tar.gz"
SHA = "0" * 64
VERSION = "0.9.2"

FORMULA = (
    "class NginxMarkdown < Formula\n"
    '  desc "Convert HTML to Markdown for AI agents"\n'
    '  url "https://example.com/old-0.9.1.tar.gz"\n'
    '  version "0.9.1"\n'
    '  sha256 "' + "1" * 64 + '"\n'
    "end\n"
)


class RenderFormulaTests(unittest.TestCase):
    def test_render_binds_release_identity(self):
        rendered = rhf.render_formula(FORMULA, URL, SHA, VERSION)
        self.assertIn(f'  url "{URL}"', rendered)
        self.assertIn(f'  version "{VERSION}"', rendered)
        self.assertIn(f'  sha256 "{SHA}"', rendered)

    def test_trailing_content_on_url_stanza_is_rejected(self):
        text = FORMULA.replace(
            '  url "https://example.com/old-0.9.1.tar.gz"',
            '  url "https://example.com/old-0.9.1.tar.gz" # bump me',
        )
        with self.assertRaises(ValueError):
            rhf.render_formula(text, URL, SHA, VERSION)

    def test_trailing_content_on_version_stanza_is_rejected(self):
        text = FORMULA.replace(
            '  version "0.9.1"', '  version "0.9.1" # stale'
        )
        with self.assertRaises(ValueError):
            rhf.render_formula(text, URL, SHA, VERSION)

    def test_trailing_content_on_sha_stanza_is_rejected(self):
        text = FORMULA.replace(
            '  sha256 "' + "1" * 64 + '"',
            '  sha256 "' + "1" * 64 + '" # stale',
        )
        with self.assertRaises(ValueError):
            rhf.render_formula(text, URL, SHA, VERSION)


if __name__ == "__main__":
    unittest.main()
