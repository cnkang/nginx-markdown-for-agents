"""Resolve the repository's literal Make calls without executing build code.

Only direct commands and literal Make variable lists establish edges. Shell
control flow, subdirectory makes and dynamic target expressions do not provide
positive evidence. An unresolved path therefore cannot certify a rule.
"""
from __future__ import annotations

import re
import shlex


VARIABLE = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)")
ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*([:?+]?=)\s*(.*)$")
TARGET = re.compile(r"^([A-Za-z0-9_.-]+):\s*([^=]*)$")
CONDITIONAL_START = re.compile(r"^(?:ifeq|ifneq|ifdef|ifndef)\b")


def command_words(line: str) -> list[str]:
    """Tokenize one direct command, ignoring comments and Make recipe prefixes."""
    try:
        words = shlex.split(line.lstrip("@-+"), comments=True)
    except ValueError:
        return []
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
        name = words[0].split("=", 1)[0]
        if name in {"MAKEFLAGS", "GNUMAKEFLAGS", "MFLAGS"}:
            # Those carry options such as -n that decide whether a recipe runs.
            return []
        words.pop(0)
    if any(word in {";", "&&", "||", "|", "&", ">", ">>", "<"} for word in words):
        return []
    for word in words:
        if re.fullmatch(r"[;&|<>]+", word):
            continue
        if re.search(r"[;&|<>]", word):
            # An operator glued to an operand (`||true`) never runs alone, so
            # the line is not a plain command.  Standalone operators are already
            # refused above.
            return []
    return words


def _defines_or_braces(words: list[str]) -> bool:
    """Recognise a shell function definition, whose body never runs on its own."""
    if not words:
        return False
    if words[0].endswith("()") or "{" in words or "}" in words:
        return True
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\(\)", words[0])) if len(words) > 1 else False


def _quote_spans_lines(line: str) -> bool:
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
        if not words and line.strip() and not line.startswith("#"):
            # A line that carries no plain command is not evidence either.
            return []
        if _quote_spans_lines(line):
            # Quoted text is data; a call written inside it never runs.
            return []
        if "<<" in line or (words and words[0] in {"cd", "pushd"}) or _defines_or_braces(words) or (words and words[0] in {
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


def _consume_recipe(
    line: str,
    recipes: dict[str, list[tuple[int, str]]],
    generation: dict[str, int],
    current: str | None,
) -> str | None:
    """Record a recipe line, dropping one whose exit status Make would ignore."""
    if current is None:
        return current
    recipe = line.strip()
    dropped = recipe.lstrip("@-+")
    if "-" in recipe[: len(recipe) - len(dropped)]:
        # Make's prefixes come in any order; a `-` among them means the status
        # is ignored, so the command cannot be blocking evidence.
        return current
    recipes.setdefault(current, []).append((generation.get(current, 0), dropped))
    return current


def _drop(name: str, variables: dict[str, str], simple: set[str]) -> None:
    """Forget a variable whose value cannot be known from here."""
    variables.pop(name, None)
    simple.discard(name)


def _apply_assignment(
    assignment: "re.Match[str]", variables: dict[str, str], simple: set[str]
) -> None:
    """Apply one assignment the way Make would, flavor included."""
    name, operator, value = assignment[1], assignment[2], assignment[3]
    if operator == "?=":
        if name not in variables:
            variables[name] = value
            simple.discard(name)
        return
    if operator == "=":
        variables[name] = value
        simple.discard(name)
        return
    expanded = _expand(value, variables)
    if VARIABLE.search(expanded):
        # A later assignment must not fill a reference that is open here.
        _drop(name, variables, simple)
        return
    if operator == ":=":
        variables[name] = expanded
        simple.add(name)
        return
    if name in simple:
        # Appending to a simple variable expands the tail right here.
        variables[name] = variables.get(name, "") + " " + expanded
        return
    _drop(name, variables, simple)


def _record_target(
    line: str,
    dependencies: dict[str, list[str]],
    generation: dict[str, int],
    variables: dict[str, str],
) -> str | None:
    """Record a target declaration, expanding its prerequisites where it is read."""
    target = TARGET.fullmatch(line)
    if target is None:
        return None
    name = target[1]
    # Every definition contributes prerequisites, while a later recipe replaces
    # an earlier one, so the two are kept apart.
    generation[name] = generation.get(name, 0) + 1
    dependencies.setdefault(name, [])
    dependencies[name].append(_expand(target[2].strip(), variables))
    return name


def _target_script(
    name: str,
    dependencies: dict[str, list[str]],
    recipes: dict[str, list[tuple[int, str]]],
) -> str:
    """Lines a target contributes: its prerequisites, then its last recipe."""
    lines = [f"make {deps}" for deps in dependencies.get(name, []) if deps.strip()]
    entries = recipes.get(name, [])
    if entries:
        last = max(index for index, _ in entries)
        lines.extend(line for index, line in entries if index == last)
    return "\n".join(lines)


def _consume_make_line(
    line: str,
    dependencies: dict[str, list[str]],
    recipes: dict[str, list[tuple[int, str]]],
    generation: dict[str, int],
    variables: dict[str, str],
    simple: set[str],
    current: str | None,
) -> str | None:
    """Read one recipe, assignment or target line; return its target."""
    if line.startswith("\t"):
        return _consume_recipe(line, recipes, generation, current)
    assignment = ASSIGNMENT.fullmatch(line)
    if assignment:
        _apply_assignment(assignment, variables, simple)
        return None
    recorded = _record_target(line, dependencies, generation, variables)
    if recorded is not None:
        return recorded
    return None if line.strip() and not line.startswith("#") else current

def _make_nodes(
    text: str,
) -> tuple[dict[str, list[str]], dict[str, list[tuple[int, str]]], dict[str, str]]:
    """Collect target prerequisites and recipes from the root Makefile."""
    dependencies: dict[str, list[str]] = {}
    recipes: dict[str, list[tuple[int, str]]] = {}
    generation: dict[str, int] = {}
    variables = {"MAKE": "make"}
    current: str | None = None
    conditionals = 0
    simple: set[str] = set()
    for line in text.replace("\\\n", " ").splitlines():
        delta = _conditional_delta(line)
        if delta is not None:
            conditionals = max(0, conditionals + delta)
            current = None
            continue
        if conditionals:
            # The branch cannot be evaluated, so a variable it assigns may hold
            # either value.  It is dropped here, before any later declaration
            # could expand a dependency with the value from outside the branch.
            poisoned = ASSIGNMENT.fullmatch(line.strip())
            if poisoned:
                variables.pop(poisoned[1], None)
                simple.discard(poisoned[1])
            continue
        current = _consume_make_line(
            line, dependencies, recipes, generation, variables, simple, current
        )
    return dependencies, recipes, variables


def _runs_profile(words: list[str], profile: str) -> bool:
    """True only for the invocation that runs gates, not a listing mode."""
    if words[:2] != ["python3", profile]:
        return False
    return all(not word.startswith("-") for word in words[2:])


def reachable_commands(makefile: str, entries: list[str], profile: str,
                       gates: list[str]) -> str:
    """Follow only targets called by entries, including the push profile gates."""
    dependencies, recipes, variables = _make_nodes(makefile)
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
                pending.extend(
                    literal_script_lines(_target_script(target, dependencies, recipes))
                )
    return "\n".join(reached)
