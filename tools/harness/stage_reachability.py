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
CONDITIONAL_START = re.compile(r"^(?:ifeq|ifneq|ifdef|ifndef)\b")


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


def _defines_or_braces(words: list[str]) -> bool:
    """Recognise a shell function definition, whose body never runs on its own."""
    if not words:
        return False
    if words[0].endswith("()") or "{" in words or "}" in words:
        return True
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\(\)", words[0])) if len(words) > 1 else False


def _leaves_quote_open(line: str) -> bool:
    """True when the line ends inside a quote, so its text spans lines."""
    single = double = False
    escaped = False
    for char in line:
        if escaped:
            escaped = False
        elif char == "\\" and not single:
            escaped = True
        elif char == "'" and not double:
            single = not single
        elif char == '"' and not single:
            double = not double
    return single or double


def literal_script_lines(script: str) -> list[str]:
    """Refuse compound scripts rather than treating dormant bodies as calls."""
    lines = script.replace("\\\n", " ").splitlines()
    for line in lines:
        words = command_words(line)
        if _leaves_quote_open(line):
            # Quoted text is data; a call written inside it never runs.
            return []
        if "<<" in line or _defines_or_braces(words) or (words and words[0] in {
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
    if not targets:
        return []
    if any(word.startswith("-") for word in targets):
        # -n and -q print or probe without running a recipe: not evidence.
        return []
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", x) for x in targets):
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


def _conditional_delta(line: str) -> int | None:
    """Depth change a Make conditional makes, or None for an ordinary line."""
    stripped = line.strip()
    if CONDITIONAL_START.match(stripped):
        return 1
    if stripped.startswith("endif"):
        return -1
    if stripped.startswith("else"):
        return 0
    return None


def _consume_make_line(
    line: str,
    nodes: dict[str, list[str]],
    variables: dict[str, str],
    current: str | None,
) -> str | None:
    """Read one recipe, assignment or target line; return its target."""
    if line.startswith("\t"):
        if current is not None:
            nodes[current].append(line.strip().lstrip("@-+"))
        return current
    assignment = ASSIGNMENT.fullmatch(line)
    if assignment:
        variables[assignment[1]] = assignment[2]
        return None
    target = TARGET.fullmatch(line)
    if target:
        nodes.setdefault(target[1], [])
        nodes[target[1]].append("make " + target[2].strip())
        return target[1]
    if line.strip() and not line.startswith("#"):
        return None
    return current


def _make_nodes(text: str) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Collect target dependencies and recipes from the root Makefile."""
    nodes: dict[str, list[str]] = {}
    variables = {"MAKE": "make"}
    current: str | None = None
    conditionals = 0
    unknown: set[str] = set()
    for line in text.replace("\\\n", " ").splitlines():
        delta = _conditional_delta(line)
        if delta is not None:
            conditionals = max(0, conditionals + delta)
            current = None
            continue
        if conditionals:
            # The branch cannot be evaluated, so a variable it assigns may hold
            # either value; drop it rather than letting the outside value decide.
            poisoned = ASSIGNMENT.fullmatch(line.strip())
            if poisoned:
                unknown.add(poisoned[1])
            continue
        current = _consume_make_line(line, nodes, variables, current)
    for name in unknown:
        variables.pop(name, None)
    return nodes, variables


def _runs_profile(words: list[str], profile: str) -> bool:
    """True only for the invocation that runs gates, not a listing mode."""
    if words[:2] != ["python3", profile]:
        return False
    return all(not word.startswith("-") for word in words[2:])


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
        if _runs_profile(words, profile) and not profile_seen:
            profile_seen = True
            pending.extend(gates)
        for target in make_targets(line):
            if target not in visited:
                visited.add(target)
                pending.extend(literal_script_lines("\n".join(nodes.get(target, []))))
    return "\n".join(reached)
