"""Resolve the repository's literal Make calls without executing build code.

Only direct commands and literal Make variable lists establish edges. Shell
control flow, subdirectory makes and dynamic target expressions do not provide
positive evidence. An unresolved path therefore cannot certify a rule.
"""
from __future__ import annotations

import re
import shlex


VARIABLE = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)")
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[:?+]?=\s*(.*)$")
TARGET = re.compile(r"^([A-Za-z0-9_.-]+):\s*([^=]*)$")


def command_words(line: str) -> list[str]:
    """Tokenize one direct command, ignoring comments and Make recipe prefixes."""
    try:
        words = shlex.split(line.lstrip("@-+"), comments=True)
    except ValueError:
        return []
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
        words.pop(0)
    if any(word in {";", "&&", "||", "|", "&", ">", ">>", "<"} for word in words):
        return []
    return words


def literal_script_lines(script: str) -> list[str]:
    """Refuse compound scripts rather than treating dormant bodies as calls."""
    lines = script.replace("\\\n", " ").splitlines()
    for line in lines:
        words = command_words(line)
        if "<<" in line or (words and words[0] in {
            "if", "for", "while", "until", "case", "function", "exit", "return",
        }):
            return []
    return lines


def make_targets(line: str) -> list[str]:
    """Read plain root Make targets; uncertain options never establish edges."""
    words = command_words(line)
    if not words or words[0] not in {"make", "$(MAKE)"}:
        return []
    targets = words[1:]
    if not targets or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", x) for x in targets):
        return []
    return targets


def _expand(text: str, variables: dict[str, str]) -> str:
    """Expand only finite, literal variable lists, retaining unknown expressions."""
    seen: set[str] = set()
    for _ in range(32):
        if text in seen or len(text) > 65536:
            return ""
        seen.add(text)
        expanded = VARIABLE.sub(lambda m: variables.get(m[1], m[0]), text)
        if expanded == text:
            return text
        text = expanded
    return ""


def _make_nodes(text: str) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Collect target dependencies and recipes from the root Makefile."""
    nodes: dict[str, list[str]] = {}
    variables = {"MAKE": "make"}
    current: str | None = None
    for line in text.replace("\\\n", " ").splitlines():
        if line.startswith("\t"):
            if current is not None:
                nodes[current].append(line.strip().lstrip("@-+"))
            continue
        assignment = ASSIGNMENT.fullmatch(line)
        if assignment:
            variables[assignment[1]] = assignment[2]
            current = None
            continue
        target = TARGET.fullmatch(line)
        if target:
            current = target[1]
            nodes.setdefault(current, [])
            nodes[current].append("make " + target[2].strip())
        elif line.strip() and not line.startswith("#"):
            current = None
    return nodes, variables


def reachable_commands(makefile: str, entries: list[str], profile: str,
                       gates: list[str]) -> str:
    """Follow only targets called by entries, including the push profile gates."""
    nodes, variables = _make_nodes(makefile)
    pending = list(entries)
    visited: set[str] = set()
    reached: list[str] = []
    profile_seen = False
    while pending:
        line = _expand(pending.pop(), variables)
        words = command_words(line)
        if not words:
            continue
        # Canonical quoting preserves comments/arguments when the caller parses.
        reached.append(shlex.join(words))
        if words[:2] == ["python3", profile] and not profile_seen:
            profile_seen = True
            pending.extend(gates)
        for target in make_targets(line):
            if target not in visited:
                visited.add(target)
                pending.extend(literal_script_lines("\n".join(nodes.get(target, []))))
    return "\n".join(reached)
