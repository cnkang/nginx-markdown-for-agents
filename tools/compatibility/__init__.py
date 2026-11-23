"""Cross-release compatibility contract anchors for the 1.0 LTS line.

The package freezes the public contract that a released tag published and
compares a candidate tree against it.  It is deliberately separate from
``tools/harness/detect_public_surface_drift.py``: that gate proves the current
implementation agrees with the current inventory, which one change can satisfy
by editing both.  This package compares against an immutable historical tag.
"""

EXTRACTOR_NAME = "compatibility-contract"
EXTRACTOR_VERSION = "1"
