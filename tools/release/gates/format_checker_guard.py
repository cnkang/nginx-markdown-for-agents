"""Fail-closed jsonschema format-checker provisioning for release gates.

Release-gate schemas declare ``format: "date-time"`` constraints, and
``jsonschema`` only enforces a declared format when the corresponding
checker is registered on the ``FormatChecker`` passed to validation.  Those
registrations come from the optional ``[format]`` extras: a bare
``jsonschema`` install imports and reports a version just fine, but registers
no ``date-time`` checker, and validation then SILENTLY skips every declared
format - a fail-open.

Every release-gate call site must therefore obtain its checker from
``require_format_checker`` below, which verifies the registration and raises
a clear, actionable error instead of silently validating less than the
schema declares.  ``release-packages.yml`` carries the same assertion as a
workflow preflight; this module gives the same guarantee to every other
entry point (local ``make`` targets, observation workflows, and tests).
"""

from __future__ import annotations

from typing import Any

# The format the release-gate schemas actually rely on.  Keep this list
# explicit so extending schema usage forces a deliberate edit here.
REQUIRED_FORMATS: tuple[str, ...] = ("date-time",)

_MISSING_EXTRAS_MESSAGE = (
    "jsonschema [format] extras are not installed; the {formats} format "
    "checker(s) are missing, so declared format constraints would be "
    "silently skipped. Install the pinned requirements "
    "(pip install -r requirements-release.txt) or requirements-dev.txt."
)


def require_format_checker() -> Any:
    """Return a ``FormatChecker`` with every required format registered.

    Returns:
        jsonschema.FormatChecker: Checker safe to pass to ``validate()`` /
        ``iter_errors()`` for the release-gate schemas.

    Raises:
        ValueError: When the required formats are not registered (the
        ``[format]`` extras are missing), so the caller fails closed
        instead of validating without format checking.
    """
    try:
        from jsonschema import FormatChecker
    except ImportError as exc:  # pragma: no cover - exercised via callers
        raise ValueError(
            "jsonschema is required for release-gate schema validation"
        ) from exc

    checker = FormatChecker()
    missing = tuple(
        name for name in REQUIRED_FORMATS if name not in checker.checkers
    )
    if missing:
        raise ValueError(
            _MISSING_EXTRAS_MESSAGE.format(formats=", ".join(missing))
        )
    return checker
