"""Resolve the repository's literal Make calls without executing build code.

Only direct commands and literal Make variable lists establish edges. Shell
control flow, subdirectory makes and dynamic target expressions do not provide
positive evidence. An unresolved path therefore cannot certify a rule.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path


VARIABLE = re.compile(r"\$\((\w+)\)")

# The Make directive that disables error checking for its prerequisites.
IGNORE_TARGET = ".IGNORE"


CONDITIONAL_START = re.compile(r"^(?:ifeq|ifneq|ifdef|ifndef)(?:\s|$)")


def command_words(line: str) -> list[str]:
    """Tokenize one direct command, ignoring comments and Make recipe prefixes."""
    try:
        words = shlex.split(line.lstrip("@-+"), comments=True)
    except ValueError:
        return []
    while words and re.fullmatch(r"\w+=.*", words[0]):
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
    return bool(re.fullmatch(r"\w+\(\)", words[0])) if len(words) > 1 else False


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
        stripped_line = line.strip()
        if not words and stripped_line and not stripped_line.startswith("#"):
            # A line that carries no plain command is not evidence either; an
            # indented comment is a comment and does not condemn its script.
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
    if line.startswith("\t"):
        # A recipe line is never a directive, whatever it spells.
        return None
    stripped = line.strip()
    if CONDITIONAL_START.match(stripped):
        return 1
    if re.match(r"endif(?:\s|$)", stripped):
        return -1
    if re.match(r"else(?:\s|$)", stripped):
        return 0
    return None


ASSIGNMENT_OPERATORS = (":=", "?=", "+=", "=")

ASSIGNMENT_PREFIX = re.compile(r"(?:override|export)\s+")


def _strip_assignment_prefixes(line: str) -> str:
    """Drop Make's `override`/`export` prefixes so the assignment is read.

    Both are modifiers: `override LIST := other` still assigns LIST here, and
    the value must win over an earlier assignment the way Make applies it.
    """
    stripped = line
    while True:
        match = ASSIGNMENT_PREFIX.match(stripped)
        if match is None:
            return stripped
        stripped = stripped[match.end():]


def _assignment(line: str) -> tuple[str, str, str] | None:
    """Split `NAME op value` without a pattern that could backtrack."""
    name = re.match(r"\w+", line)
    if name is None:
        return None
    rest = line[name.end():].lstrip()
    for operator in ASSIGNMENT_OPERATORS:
        if rest.startswith(operator):
            return name.group(), operator, rest[len(operator):].lstrip()
    return None


def _target(line: str) -> tuple[str, str] | None:
    """Split `NAME: prerequisites`, refusing a line that carries an `=`."""
    name = re.match(r"[\w.-]+", line)
    if name is None or len(name.group()) == len(line):
        return None
    rest = line[name.end():]
    if not rest.startswith(":"):
        return None
    prerequisites = rest[1:].lstrip()
    return None if "=" in prerequisites else (name.group(), prerequisites)


def _consume_recipe(
    line: str,
    recipes: dict[str, list[tuple[int, str | None]]],
    generation: dict[str, int],
    current: str | None,
) -> str | None:
    """Record a recipe line, dropping one whose exit status Make would ignore."""
    if current is None:
        return current
    recipe = line.strip()
    dropped = recipe.lstrip("@-+")
    ignored = "-" in recipe[: len(recipe) - len(dropped)]
    # The definition is recorded either way: Make has replaced the earlier
    # recipe, so nothing from it may be used.  A recipe whose status Make
    # ignores is stored without a command, because it cannot be the evidence
    # that fails a build.
    recipes.setdefault(current, []).append(
        (generation.get(current, 0), None if ignored else dropped)
    )
    return current


def _drop(name: str, variables: dict[str, str], simple: set[str]) -> None:
    """Forget a variable whose value cannot be known from here."""
    variables.pop(name, None)
    simple.discard(name)


def _apply_assignment(
    assignment: tuple[str, str, str], variables: dict[str, str], simple: set[str]
) -> None:
    """Apply one assignment the way Make would, flavor included."""
    name, operator, value = assignment
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
    target = _target(line)
    if target is None:
        return None
    name = target[0]
    # Every definition contributes prerequisites, while a later recipe replaces
    # an earlier one, so the two are kept apart.
    generation[name] = generation.get(name, 0) + 1
    dependencies.setdefault(name, [])
    expanded = _expand(target[1].strip(), variables)
    if name == IGNORE_TARGET:
        # An unresolved scope here could ignore more targets than the file
        # shows, so it keeps its fail-closed treatment (raised downstream).
        dependencies[name].append(expanded)
        return name
    # Make expands a prerequisite list while it reads the line, so a reference
    # still open here is undefined at read time and expands to empty; a later
    # assignment must not fill it in retroactively.  The order-only separator
    # (`|`) is not a prerequisite name: drop it before storing, while keeping
    # both the normal and the order-only prerequisite groups traversable.
    dependencies[name].append(VARIABLE.sub("", expanded).replace("|", " "))
    return name


def _existing_leaf(token: str, root: Path | None) -> bool:
    """Return whether a prerequisite token names an existing file under root.

    Make treats an on-disk file as a satisfied leaf prerequisite, so such a
    token is not missing; only tokens that are neither declared targets nor
    existing files leave a target uncertifiable.  Tokens resolving outside
    the root are refused: certification must not follow a path out of the
    tree under verification.
    """
    if root is None:
        return False
    try:
        base = root.resolve()
        target = (root / token).resolve()
    except OSError:
        return False
    if not target.is_relative_to(base):
        return False
    return target.is_file() or target.is_dir()


def _names_broken_target(entry: str, broken: set[str]) -> bool:
    """Return whether a prerequisite entry names an already-broken target."""
    return any(token in broken for token in entry.split())


def _directly_broken_targets(
    dependencies: dict[str, list[str]],
    root: Path | None,
    declared: set[str],
) -> set[str]:
    """Return targets naming a prerequisite outside the declared graph."""
    return {
        name
        for name, entries in dependencies.items()
        if any(
            any(
                token not in declared and not _existing_leaf(token, root)
                for token in entry.split()
            )
            for entry in entries
        )
    }


def _broken_targets(
    dependencies: dict[str, list[str]], recipes: dict[str, list[tuple[int, str | None]]],
    root: Path | None = None,
) -> set[str]:
    """Targets whose prerequisites leave the declared graph, transitively.

    Make refuses to build a target with an undeclared prerequisite (or one
    reachable only through such a target), so none of that subtree is
    certifiable.  A prerequisite naming an existing file under *root* is a
    satisfied leaf instead: Make accepts it without a rule.  The resolver
    under-certifies here instead of over-certifying: a subtree reachable only
    through a broken prerequisite stays unreached.
    """
    declared = set(dependencies) | set(recipes)
    broken = _directly_broken_targets(dependencies, root, declared)
    changed = True
    while changed:
        changed = False
        for name, entries in dependencies.items():
            if name in broken:
                continue
            if any(_names_broken_target(entry, broken) for entry in entries):
                broken.add(name)
                changed = True
    return broken


def _target_script(
    name: str,
    dependencies: dict[str, list[str]],
    recipes: dict[str, list[tuple[int, str | None]]],
    ignored: set[str],
    broken: set[str],
) -> str:
    """Lines a target contributes: its prerequisites, then its last recipe."""
    if name in broken:
        return ""
    lines = [f"make {deps}" for deps in dependencies.get(name, []) if deps.strip()]
    entries = recipes.get(name, [])
    if entries and name not in ignored and "*" not in ignored:
        last = max(index for index, _ in entries)
        lines.extend(
            line for index, line in entries if index == last and line is not None
        )
    return "\n".join(lines)


def _consume_make_line(
    line: str,
    dependencies: dict[str, list[str]],
    recipes: dict[str, list[tuple[int, str | None]]],
    generation: dict[str, int],
    variables: dict[str, str],
    simple: set[str],
    current: str | None,
) -> str | None:
    """Read one recipe, assignment or target line; return its target."""
    if line.startswith("\t"):
        return _consume_recipe(line, recipes, generation, current)
    assignment = _assignment(_strip_assignment_prefixes(line))
    if assignment is not None:
        _apply_assignment(assignment, variables, simple)
        return None
    recorded = _record_target(line, dependencies, generation, variables)
    if recorded is not None:
        return recorded
    return None if line.strip() and not line.startswith("#") else current


def _make_include_line(line: str) -> bool:
    """Return whether a top-level line delegates parsing to another file."""
    if line.startswith("\t"):
        return False
    return re.match(r"^(?:-?include|sinclude)(?:\s|$)", line.strip()) is not None


def _update_conditional_depth(delta: int, depth: int) -> int:
    """Apply one Make conditional directive, rejecting unmatched closers."""
    if delta < 0 and depth == 0:
        raise ValueError("unmatched make conditional endif")
    if delta == 0 and depth == 0:
        raise ValueError("unmatched make conditional else")
    return depth + delta


def _poison_conditional_line(
    line: str,
    variables: dict[str, str],
    simple: set[str],
    generation: dict[str, int],
    recipes: dict[str, list[tuple[int, str | None]]],
) -> None:
    """Discard uncertain assignments and recipes from a conditional branch."""
    poisoned = _assignment(_strip_assignment_prefixes(line.strip()))
    if poisoned is not None:
        variables.pop(poisoned[0], None)
        simple.discard(poisoned[0])
    redefined = _target(line.strip())
    if redefined is None:
        return
    if redefined[0] == IGNORE_TARGET:
        raise ValueError("cannot verify conditional .IGNORE scope")
    generation[redefined[0]] = generation.get(redefined[0], 0) + 1
    recipes.setdefault(redefined[0], []).append(
        (generation[redefined[0]], None)
    )


def _make_nodes(
    text: str,
) -> tuple[dict[str, list[str]], dict[str, list[tuple[int, str | None]]], dict[str, str]]:
    """Collect target prerequisites and recipes from the root Makefile."""
    dependencies: dict[str, list[str]] = {}
    recipes: dict[str, list[tuple[int, str | None]]] = {}
    generation: dict[str, int] = {}
    variables = {"MAKE": "make"}
    current: str | None = None
    conditionals = 0
    simple: set[str] = set()
    for line in text.replace("\\\n", " ").splitlines():
        if _make_include_line(line):
            raise ValueError("cannot verify included makefile")
        delta = _conditional_delta(line)
        if delta is not None:
            conditionals = _update_conditional_depth(delta, conditionals)
            current = None
            continue
        if conditionals:
            # The branch cannot be evaluated, so a variable it assigns may hold
            # either value and a target it defines may replace the one outside.
            # Both are dropped here, before a later declaration could rely on
            # the value or the recipe from outside the branch.
            _poison_conditional_line(
                line, variables, simple, generation, recipes
            )
            continue
        current = _consume_make_line(
            line, dependencies, recipes, generation, variables, simple, current
        )
    if conditionals:
        raise ValueError("unclosed make conditional")
    return dependencies, recipes, variables


def _ignored_targets(dependencies: dict[str, list[str]]) -> set[str]:
    """Resolve .IGNORE at read time; unknown scopes cannot prove blocking calls."""
    ignored: set[str] = set()
    for declaration in dependencies.get(IGNORE_TARGET, []):
        try:
            names = shlex.split(declaration, comments=True)
        except ValueError as exc:
            raise ValueError("cannot verify .IGNORE scope") from exc
        if not names:
            ignored.add("*")
        for name in names:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                raise ValueError("cannot verify dynamic .IGNORE scope")
            ignored.add(name)
    return ignored


def _runs_profile(words: list[str], profile: str) -> bool:
    """True only for the invocation that runs gates, not a listing mode."""
    if words[:2] != ["python3", profile]:
        return False
    return all(not word.startswith("-") for word in words[2:])


def reachable_commands(makefile: str, entries: list[str], profile: str,
                       gates: list[str], root: Path | None = None) -> str:
    """Follow only targets called by entries, including the push profile gates.

    When *root* is given, prerequisites that name existing files under it are
    satisfied leaves; without it, only declared targets satisfy prerequisites.
    """
    dependencies, recipes, variables = _make_nodes(makefile)
    ignored = _ignored_targets(dependencies)
    broken = _broken_targets(dependencies, recipes, root)
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
                    literal_script_lines(
                        _target_script(target, dependencies, recipes, ignored, broken)
                    )
                )
    return "\n".join(reached)
