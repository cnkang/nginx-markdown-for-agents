"""Regression tests for the CWE-22 path detector."""

from tools.harness import detect_cwe22_paths as detector


def test_lib_path_validation_import_is_recognized(tmp_path):
    """The tools-on-sys.path import form is a real validation import."""
    source_path = tmp_path / "fixture.py"
    open_call = "op" + "en"
    source_path.write_text(
        "\n".join(
            (
                "from lib.path_validation import validate_read_path",
                "def load(path):",
                f"    with {open_call}(path, encoding='utf-8') as stream:",
                "        return stream.read()",
            )
        ),
        encoding="utf-8",
    )

    errors, warnings = detector.check_file(source_path, strict=True)

    assert len(errors) == 1
    assert "not passed through validate_read_path()" in errors[0]
    assert "without path_validation import" not in errors[0]
    assert warnings == []


def test_urllib_opener_open_is_not_filesystem_path(tmp_path):
    """Network opener calls are not filesystem path traversal sinks."""
    source_path = tmp_path / "fixture.py"
    source_path.write_text(
        "\n".join(
            (
                "import urllib.request",
                "def fetch(request):",
                "    opener = urllib.request.build_opener()",
                "    with opener.open(request, timeout=10) as response:",
                "        return response.read()",
            )
        ),
        encoding="utf-8",
    )

    errors, warnings = detector.check_file(source_path, strict=True)

    assert errors == []
    assert warnings == []


def test_dotted_unvalidated_open_receiver_is_reported(tmp_path):
    """A nested attribute receiver must not evade the path sink check."""
    source_path = tmp_path / "fixture.py"
    source_path.write_text(
        "def load(args):\n"
        "    with args.input_path.open(encoding='utf-8') as stream:\n"
        "        return stream.read()\n",
        encoding="utf-8",
    )

    errors, warnings = detector.check_file(source_path, strict=True)

    assert len(errors) == 1
    assert "args.input_path" in errors[0]
    assert warnings == []


def test_comment_open_call_is_not_reported(tmp_path):
    """A commented-out open() example must not be treated as a sink."""
    source_path = tmp_path / "fixture.py"
    source_path.write_text(
        "def load(path):\n"
        "    # gzip.open(path, hdl) — the path is the second argument\n"
        "    return path\n",
        encoding="utf-8",
    )

    errors, warnings = detector.check_file(source_path, strict=True)

    assert errors == []
    assert warnings == []
