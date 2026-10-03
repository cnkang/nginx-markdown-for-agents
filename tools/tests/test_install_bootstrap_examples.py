"""Regression contract for privileged installer examples, without executing them."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class InstallerBootstrapExamplesTest(unittest.TestCase):
    def test_header_requires_authenticated_private_path(self):
        header = (ROOT / "tools/install.sh").read_text().split('REPO=')[0]
        recipes = [line for line in header.splitlines() if line.startswith("#   ")]
        self.assertTrue(recipes)
        self.assertFalse(any("/tmp/" in line for line in recipes))
        self.assertFalse(any("curl " in line for line in recipes))
        self.assertIn("Download and authenticate", header)
        self.assertIn("docs/guides/INSTALLATION.md", header)

    def test_canonical_download_uses_private_staging_before_sudo(self):
        guide = (ROOT / "docs/guides/INSTALLATION.md").read_text()
        recipe = guide.split("# Step 1: Download", 1)[1].split("```", 1)[0]
        staging = recipe.index('INSTALL_DIR="$(mktemp -d)"')
        enter = recipe.index('cd "${INSTALL_DIR}"')
        download = recipe.index("curl -fsSL")
        verify = recipe.index("--verify SHA256SUMS.asc SHA256SUMS")
        privileged = recipe.index("sudo env VERSION=")
        self.assertLess(staging, enter)
        self.assertLess(enter, download)
        self.assertLess(download, verify)
        self.assertLess(verify, privileged)
        self.assertIn("set -euo pipefail", recipe[:download])


if __name__ == "__main__":
    unittest.main()
