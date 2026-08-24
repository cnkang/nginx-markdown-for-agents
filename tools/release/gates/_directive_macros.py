"""Shared expansion for canonical NGINX directive-name macros."""

from __future__ import annotations

import re


def expand_directive_macros(content: str, names: str) -> str:
    """Expand one-line and continued directive-name macros in source text."""
    definitions = dict(
        re.findall(
            r"^#define\s+(NGX_HTTP_MARKDOWN_DIRECTIVE_[A-Z0-9_]+)\s+"
            r"(?:\\\s*)?\"([^\"]+)\"",
            names,
            flags=re.MULTILINE,
        )
    )
    return re.sub(
        r"ngx_string\((NGX_HTTP_MARKDOWN_DIRECTIVE_[A-Z0-9_]+)\)",
        lambda match: (
            f'ngx_string("{definitions[match.group(1)]}")'
            if match.group(1) in definitions
            else match.group(0)
        ),
        content,
    )
