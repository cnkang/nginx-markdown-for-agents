"""Unit tests for the wire-compression sniffing guard in benchmark_validation.

The zlib probe accepts any header satisfying the RFC 1950 CMF/FLG invariant
(CM == 8 and the 16-bit header divisible by 31), not only the default 0x78
CMF values, while a header with an invalid FCHECK must not count.
"""

from tools.perf.benchmark_validation import _is_wire_compressed


def test_zlib_check_invariant_accepts_valid_headers():
    """Any FCHECK-valid zlib header counts, not just the default 0x78 CMF."""
    assert _is_wire_compressed(b"\x08\x1d" + b"\x00" * 8)
    assert _is_wire_compressed(b"\x78\x9c" + b"\x00" * 8)


def test_zlib_header_with_invalid_fcheck_is_not_compressed():
    """A 0x78 CMF with an invalid FCHECK is not a zlib stream."""
    assert not _is_wire_compressed(b"\x78\x1d" + b"\x00" * 8)


def test_gzip_magic_is_still_detected():
    assert _is_wire_compressed(b"\x1f\x8b" + b"\x00" * 8)
