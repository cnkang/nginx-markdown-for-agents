"""Unit tests for the wire-compression sniffing guard in benchmark_validation.

Classification requires more than a header: a zlib body is only reported as
compressed when the CMF/FLG invariant holds AND the stream decodes and reaches
its end marker, so plain Markdown that starts with zlib-like bytes, a truncated
capture, or a non-zlib body is rejected.
"""

import zlib

from tools.perf.benchmark_validation import _is_wire_compressed


def test_complete_zlib_streams_are_detected():
    """Real zlib streams count, including a non-default window size."""
    assert _is_wire_compressed(zlib.compress(b"markdown body " * 64))
    custom = zlib.compressobj(6, zlib.DEFLATED, 9)
    stream = custom.compress(b"markdown body " * 64) + custom.flush()
    assert _is_wire_compressed(stream)


def test_zlib_header_alone_is_not_compressed():
    """A valid header with a non-decodable payload is not a zlib stream:
    plain text can start with the same bytes."""
    assert not _is_wire_compressed(b"\x78\x9c" + b"\x00" * 8)
    assert not _is_wire_compressed(b"\x08\x1d" + b"\x00" * 8)


def test_zlib_header_with_invalid_fcheck_is_not_compressed():
    """A 0x78 CMF with an invalid FCHECK is not a zlib stream."""
    assert not _is_wire_compressed(b"\x78\x1d" + b"\x00" * 8)


def test_zlib_header_with_reserved_cinfo_is_not_compressed():
    """CINFO above 7 is reserved; such a header is not a valid zlib stream."""
    assert not _is_wire_compressed(b"\x88\x1c" + b"\x00" * 8)


def test_truncated_zlib_stream_is_not_compressed():
    """A truncated capture of a valid stream never completes."""
    assert not _is_wire_compressed(zlib.compress(b"markdown body " * 64)[:8])


def test_gzip_magic_is_still_detected():
    assert _is_wire_compressed(b"\x1f\x8b" + b"\x00" * 8)

def test_stream_expanding_past_the_output_budget_is_compressed():
    """A body whose decode exceeds the output budget is compressed data;
    stream completion is not required for the classification."""
    assert _is_wire_compressed(zlib.compress(b"A" * (33 << 20)))
