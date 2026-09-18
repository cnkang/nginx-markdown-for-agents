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


def test_space_padded_dotted_receiver_is_reported(tmp_path):
    """`receiver . open()` must not evade the path sink check."""
    source_path = tmp_path / "fixture.py"
    source_path.write_text(
        "def load(args):\n"
        "    with user_path . open(encoding='utf-8') as stream:\n"
        "        return stream.read()\n",
        encoding="utf-8",
    )

    errors, warnings = detector.check_file(source_path, strict=True)

    assert len(errors) == 1
    assert "user_path" in errors[0]
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


def test_fstring_open_argument_is_flagged_unaudited(tmp_path):
    """open() fed an f-string must be reported as an unparsed sink,
    not silently skipped."""
    source_path = tmp_path / "fixture.py"
    source_path.write_text(
        "def load(base):\n"
        "    with open(f\"{base}/file.txt\") as stream:\n"
        "        return stream.read()\n",
        encoding="utf-8",
    )

    errors, warnings = detector.check_file(source_path, strict=True)

    assert errors == []
    assert len(warnings) == 1
    assert "dynamic expression" in warnings[0]


def test_open_string_literal_inside_fixture_is_not_flagged(tmp_path):
    """open() text embedded in a string literal is not a call site."""
    source_path = tmp_path / "fixture.py"
    source_path.write_text(
        "content = 'with open(\"tools/release-matrix.json\") as f:'\n"
        "assert content\n",
        encoding="utf-8",
    )

    errors, warnings = detector.check_file(source_path, strict=True)

    assert errors == []
    assert warnings == []


def test_identifier_with_attribute_access_is_unaudited(tmp_path):
    """open(args.filename) is a dynamic member, not a safe bare variable."""
    source_path = tmp_path / "fixture.py"
    source_path.write_text(
        "def load(args):\n"
        "    with open(args.filename, encoding='utf-8') as stream:\n"
        "        return stream.read()\n",
        encoding="utf-8",
    )

    errors, warnings = detector.check_file(source_path, strict=True)

    assert len(warnings) == 1
    assert "dynamic expression" in warnings[0]


def test_trailing_comment_open_does_not_warn(tmp_path):
    """open() text inside a trailing comment is not a live call site."""
    source_path = tmp_path / "fixture.py"
    source_path.write_text(
        "def load():\n"
        "    value = calculate()  # open(path) documented here\n"
        "    return value\n",
        encoding="utf-8",
    )

    errors, warnings = detector.check_file(source_path, strict=True)

    assert errors == []
    assert warnings == []


def test_two_open_calls_on_one_line_both_reported(tmp_path):
    """A later live open() after an earlier quoted one is still a sink."""
    source_path = tmp_path / "fixture.py"
    source_path.write_text(
        "def load(a, b):\n"
        "    with open(a) as f1, open(b) as f2:\n"
        "        return f1.read() + f2.read()\n",
        encoding="utf-8",
    )

    errors, warnings = detector.check_file(source_path, strict=True)

    assert len(errors) == 2
    assert any("open(a)" in e for e in errors)
    assert any("open(b)" in e for e in errors)
    assert warnings == []


def test_os_open_fstring_argument_is_unaudited(tmp_path):
    """os.open() fed an f-string is an unparsed sink, not a skip."""
    source_path = tmp_path / "fixture.py"
    source_path.write_text(
        "import os\n"
        "def open_base(base):\n"
        "    fd = os.open(f\"{base}/data\", os.O_RDONLY)\n"
        "    return fd\n",
        encoding="utf-8",
    )

    errors, warnings = detector.check_file(source_path, strict=True)

    assert errors == []
    assert len(warnings) == 1
    assert "dynamic expression" in warnings[0]


def test_os_open_concatenated_argument_still_classified(tmp_path):
    """os.open() with a concatenated root is treated like builtin open():
    the root identifier is classified, so an unvalidated base is an
    error rather than silently skipped."""
    source_path = tmp_path / "fixture.py"
    source_path.write_text(
        "import os\n"
        "def open_base(base):\n"
        "    fd = os.open(base + '/data', os.O_RDONLY)\n"
        "    return fd\n",
        encoding="utf-8",
    )

    errors, warnings = detector.check_file(source_path, strict=True)

    assert len(errors) == 1
    assert "base" in errors[0]
    assert warnings == []


def test_a_longer_identifier_ending_in_open_is_not_a_sink(tmp_path):
    """popen/fdopen/reopen/Popen end in `open` but are not builtin
    open()/os.open() calls, so they must not be reported."""
    source_path = tmp_path / "fixture.py"
    source_path.write_text(
        "import os\n"
        "import subprocess\n"
        "def run(cmd, fd, p, mode):\n"
        "    stream = os.popen(cmd)\n"
        "    handle = os.fdopen(fd, mode)\n"
        "    again = reopen(p, mode)\n"
        "    proc = subprocess.Popen(cmd)\n"
        "    return stream, handle, again, proc\n",
        encoding="utf-8",
    )

    errors, warnings = detector.check_file(source_path, strict=True)

    assert errors == []
    assert warnings == []


def test_a_receiver_ending_in_os_is_classified_as_a_method_call(tmp_path):
    """A receiver name that merely ends in `os` (myos.open, someos.open)
    must be classified through the method-call path instead of being
    skipped as a longer identifier."""
    source_path = tmp_path / "fixture.py"
    source_path.write_text(
        "def load(myos, someos, path):\n"
        "    first = myos.open(path, 'r')\n"
        "    second = someos.open(path, 'r')\n"
        "    return first, second\n",
        encoding="utf-8",
    )

    errors, warnings = detector.check_file(source_path, strict=True)

    assert len(errors) == 2
    assert any("myos" in e for e in errors)
    assert any("someos" in e for e in errors)
    assert warnings == []
