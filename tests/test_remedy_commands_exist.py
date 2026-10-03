"""A message that tells you to run a command must name one that exists.

Three of these shipped. `_refuse_crosswalk_loss` refused a write and told
the reader to run `etl-hipaa` — the command that had just refused (#119,
fixed in #140). `NOT_CROSSWALK_ANCHORABLE` refuses to seed a crosswalk for
45 CFR 171 and tells the reader to cite it instead, which no tag can do
because the catalog's declared name begins with a digit (#115, open).
`bundles.render` sent a reader to `policyforge topics`, which has never
existed in any release — `/topics` is a shell skill, a different interface.

All three were reviewed and merged. Two were guards and one was an
ordinary not-found message, so **this is a property of any text telling
someone what to do next**, not of guards.

**Only half of that surface is checkable here, and the split is the
point.** A remedy that names a *command* has a membership test with no
judgement in it: the command is in the CLI or it is not. A remedy that
names an *action* — "cite it", "fix the frontmatter" — cannot be checked
mechanically and stays the reviewer's job. A green run here means every
named command exists. It does not mean every remedy is performable, and
reading it that way would be the skip-reporting defect in a new place.

**Why the population is built this way, and why the number is printed.**
A verb sweep cannot define this set: scanning literals inside `raise` for
capitalised verbs finds 55 messages and misses *both* known instances —
one because its text lives in a module-level dict rather than in the
`raise`, the other because it says `re-run` and the pattern said `Re-run`.
Widening to every literal, case-insensitively, reaches 700 and is mostly
docstrings. So this check drops the verb entirely and keys on the only
unambiguous marker available: the literal string `policyforge <word>`.
The counts go in the failure message because two people running two
versions of this sweep got different totals from the same tree, and a
total with no method attached looks like a change in the code.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "policyforge"

#: `policyforge <word>` wherever it appears in a string literal. Not
#: restricted to backticked spellings: requiring backticks drops two real
#: references (`policyforge drift`, `policyforge etl-hitrust`, both
#: written plain) to spare one prose false positive, and the prose was
#: rewritten instead — narrowing the net to accommodate one sentence
#: would have cost more coverage than it bought.
COMMAND_RE = re.compile(r"policyforge ([a-z][a-z0-9-]*)")


def registered_commands() -> set[str]:
    """Every command name the CLI answers to, groups and subcommands."""
    from policyforge.cli import cli

    def walk(group, prefix: str = "") -> set[str]:
        found: set[str] = set()
        for name, command in getattr(group, "commands", {}).items():
            found.add((prefix + name).strip())
            found |= walk(command, prefix + name + " ")
        return found

    full = walk(cli)
    # Both spellings: a message may name `crosswalk` or `crosswalk seed`,
    # and the first word of a group is a real command either way.
    return full | {name.split()[0] for name in full}


def referenced_commands() -> dict[str, list[str]]:
    """`{command: [file:line, ...]}` for every string literal in the package.

    Read from the AST rather than by grepping the file text, so a name
    inside a comment is not mistaken for one a user will be shown.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        relative = path.relative_to(SRC.parent.parent).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                flat = " ".join(node.value.split())
                for match in COMMAND_RE.finditer(flat):
                    found.setdefault(match.group(1), []).append(f"{relative}:{node.lineno}")
    return found


def test_every_command_named_in_a_message_exists():
    have = registered_commands()
    referenced = referenced_commands()
    missing = {name: where for name, where in referenced.items() if name not in have}

    assert not missing, (
        "a message names a command that does not exist:\n"
        + "\n".join(
            f"  `policyforge {name}` — " + ", ".join(sorted(set(where)))
            for name, where in sorted(missing.items())
        )
        + f"\n\nmethod: every `policyforge <word>` in a string literal under "
        f"{SRC.as_posix()}, compared against the CLI's registered commands.\n"
        f"  commands registered : {len(have)}\n"
        f"  commands referenced : {len(referenced)}\n"
        f"  naming nothing      : {len(missing)}\n"
        "If the command was renamed, update the message. If the remedy belongs "
        "to another interface — a zardoz shell skill, say — name the file or "
        "list the answer instead of naming a command the reader cannot run."
    )


def test_the_check_can_fail():
    """The population is real, so a fabricated name is caught.

    Without this, a regex that silently matched nothing would pass
    forever and read as "every remedy verified".
    """
    referenced = referenced_commands()
    assert referenced, "no `policyforge <word>` strings found at all — the scan is broken"

    have = registered_commands()
    assert "etl-oscal" in referenced, "expected a real command reference in the package"
    assert "etl-oscal" in have
    assert "not-a-real-command" not in have


@pytest.mark.parametrize("owner", ["Nobody At All", "security"])
def test_an_unknown_owner_is_answered_rather_than_redirected(owner):
    """The message this check was written for: it named `policyforge
    topics`, which exists in neither the CLI nor anywhere else.

    It is rendered by both `policyforge bundle` and the shell's `/team`,
    so any command it named would have been wrong in one of them. It now
    lists the owners the registry does name, which is the answer the
    reader wanted and needs no remedy at all.
    """
    from policyforge.topics.bundles import team_bundle
    from policyforge.topics.registry import load_topics

    root = Path(__file__).resolve().parent.parent
    topics = load_topics(root / "config" / "topics.example.yaml")
    rendered = team_bundle(topics, [], owner).render()

    assert "policyforge topics" not in rendered
    assert "The registry names these owners:" in rendered
    for name in {t.owner.strip() for t in topics if t.owner.strip()}:
        assert name in rendered


def test_an_empty_registry_says_so_instead_of_listing_nothing():
    """A heading followed by no owners reads as a rendering bug. The
    two states — wrong name, and nothing to be right about — are
    different facts and get different sentences."""
    from policyforge.topics.bundles import team_bundle

    rendered = team_bundle([], [], "Anyone").render()

    assert "names no owners at all" in rendered
    assert "The registry names these owners:" not in rendered
