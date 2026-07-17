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
