"""Regression contract for privileged installer examples, without executing them."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class InstallerBootstrapExamplesTest(unittest.TestCase):
    def test_header_requires_authenticated_private_path(self):
        header = (ROOT / "tools/install.sh").read_text().split("REPO=")[0]
        self.assertIn("docs/guides/INSTALLATION.md#2-shortest-success-path", header)
        self.assertIn("signed SHA256SUMS", header)
        self.assertNotIn("/tmp/", header)
        self.assertNotIn("curl -", header)

    def test_canonical_download_uses_private_staging_before_sudo(self):
        guide = (ROOT / "docs/guides/INSTALLATION.md").read_text()
        recipe = guide.split("# Step 1: Download", 1)[1].split("```", 1)[0]
        staging = recipe.index('WORKDIR="$(mktemp -d)"')
        download = recipe.index("curl --proto")
        verify = recipe.index("--verify")
        privileged = recipe.index("sudo env VERSION=")
        self.assertLess(staging, download)
        self.assertLess(download, verify)
        self.assertLess(verify, privileged)
        self.assertIn("set -euo pipefail", recipe[:download])
        self.assertIn("sha256sum -c -", recipe[:privileged])


if __name__ == "__main__":
    unittest.main()
