"""A command the product prints must be one a person can actually run.

Three ways a printed command fails, and this project has now produced all
three:

- **its context differs from the reader's** — `/coverage` printed
  `crosswalk seed --framework 'NIST 800-171'`, which worked in the shell
  that printed it and exited 1 from the CLI's own default catalogs;
- **it does not survive a shell** — the same fix then emitted Windows
  backslashes, which bash eats as escapes;
- **an interpolated value breaks the quoting** — the subject of #187.

A printed command is a promise. It is also the one kind of output nothing
here executes, so nothing here notices when it stops being true.
"""

from __future__ import annotations

import ast
import re
import shlex
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "policyforge"

#: A backtick-quoted `policyforge …` string carrying at least one flag.
#: Bare command names in prose — "see `policyforge generate`" — are
#: references rather than invocations and cannot fail to run.
_PRINTED = re.compile(r"`policyforge ([^`]*--[^`]*)`")

#: Markers that a string is illustrative rather than runnable.
#:
#: `zardoz/skills.py` prints ``policyforge satisfies --controls ...`` with a
#: literal ellipsis, and `zardoz/shell.py` prints `<your markdown tree>`.
#: **Both are doing their job.** A class test that cannot say "this one is
#: an example" fails on strings that are correct, which is the fastest way
#: to get a class test deleted.
_ILLUSTRATIVE = ("...", "<")


def _printed_commands() -> list[tuple[str, str]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _PRINTED.finditer(line):
                # `as_posix`, so the site string is the same on every
                # platform. `relative_to` gives backslashes on Windows, and
                # anything comparing these — the exemption set below, a
                # failure message someone pastes into an issue — would then
                # hold on one runner and not the other.
                where = f"{path.relative_to(SRC.parent).as_posix()}:{line_no}"
                found.append((where, match.group(1)))
    return found


def test_the_population_is_not_empty():
    """Guard the population, or every loop below passes vacuously."""
    assert _printed_commands(), (
        "no printed command with a flag was found anywhere in src/. Either the "
        "pattern stopped matching or they all went away; check which."
    )


#: The printed commands that are examples rather than invocations, named
#: rather than counted. Both are doing their job — `skills.py` prints a
#: literal ellipsis and `shell.py` a `<placeholder>`.
#:
#: **Keyed by the command, not by `path:line`.** The first version keyed
#: on the site, which is not an identity: the train moved `skills.py`'s
#: line from 305 to 321 and this went red on a merge that touched
#: nothing it checks. A guard that fires on unrelated edits, in a file
#: nobody working on the feature would open, gets muted by the third
#: person who hits it. The command string is what is actually being
#: exempted and is stable under every reformatting.
ILLUSTRATIVE_COMMANDS = {
    "zardoz sync --content-dir <your markdown tree>",
    "satisfies --controls ...",
}


def test_exactly_the_known_commands_are_exempt():
    """**A skip is how a site leaves this check, so the set of skips is a
    decision and not a side effect.**

    `_ILLUSTRATIVE` is a marker rule: any printed command containing
    `...` or `<` is skipped. That is right for the two sites it was
    written for, and it means a *third* one added later would be excluded
    silently — the check would keep passing while covering less.

    Pinned by the command string rather than by count, so the failure
    message names which string stopped being checked. Adding a genuinely
    illustrative command should turn this red once and be resolved by
    editing this set, which is a person deciding; a real command that
    happens to contain `<` should be caught here rather than skipped
    forever.

    Same shape as the catalog-key guard: derive the population from the
    code, enumerate the exemptions by hand.
    """
    # The path is kept out of the comparison and put in the message, so a
    # failure still locates the command without a line number deciding
    # whether the test passes.
    located = {
        printed: where
        for where, printed in _printed_commands()
        if any(marker in printed for marker in _ILLUSTRATIVE)
    }
    exempt = set(located)

    assert exempt == ILLUSTRATIVE_COMMANDS, (
        "the set of printed commands treated as examples has changed. Anything "
        "added here stops being checked against the CLI, so it wants a decision "
        "rather than a silent skip. Found:\n  "
        + "\n  ".join(f"{located[c]}: {c!r}" for c in sorted(exempt))
    )


def _cli_help(*command: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "policyforge.cli", *command, "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return result.stdout + result.stderr


@pytest.mark.parametrize("where,printed", _printed_commands())
def test_every_flag_in_a_printed_command_exists(where: str, printed: str):
    """**Checked against the live CLI, not against a list written here.**

    A list would be a second copy of the interface, correct on the day it
    was typed. Asking the CLI costs a subprocess and cannot go stale.
    """
    if any(marker in printed for marker in _ILLUSTRATIVE):
        pytest.skip(f"{where} is illustrative, not an invocation")

    words = printed.split()
    stop = next((i for i, w in enumerate(words) if w.startswith("-")), len(words))
    command = list(words[:stop])
    flags = {w.split("=")[0] for w in words if w.startswith("--")}
    if not command:
        pytest.skip(f"{where} names no subcommand")

    help_text = _cli_help(*command)
    for flag in sorted(flags):
        assert flag in help_text, (
            f"{where} prints `policyforge {printed}`, but {flag!r} is not on "
            f"`policyforge {' '.join(command)} --help`. The product is telling a "
            f"user to run something it does not accept."
        )


#: Every site that interpolates a value into a printed command, with a
#: hostile value for each. Derived by hand and listed because **there is no
#: way to discover an interpolation's runtime value statically** — the
#: population of *sites* is checkable, and that is what the next test does.
_INTERPOLATED = (
    'Vendor "Bring Your Own" Policy',
    "Ryan's Access Policy",
    "Policy; rm -rf /tmp/x",
    "a title with\ttabs",
)


def _publishers():
    """Every concrete `Publisher`, derived rather than listed.

    **The population is the point.** The first version of the test below
    named `ConfluencePublisher` and covered one of the three sites this
    PR quotes; reverting `shlex.quote` in `github_wiki.py` left the suite
    green. A fourth publisher must be covered by existing, not by
    somebody remembering to add a case.
    """
    import policyforge.export.github_wiki  # noqa: F401  — registers the subclass
    from policyforge.export.publisher import Publisher

    return sorted(Publisher.__subclasses__(), key=lambda c: c.__name__)


@pytest.mark.parametrize("publisher", _publishers(), ids=lambda c: c.__name__)
@pytest.mark.parametrize("title", _INTERPOLATED)
def test_a_reconcile_command_survives_a_hostile_title(publisher, title: str):
    """**The defect: a document title is user-authored prose.**

    `--title "Vendor "Bring Your Own" Policy"` parses as
    `--title "Vendor Bring"` — a valid command naming the wrong document,
    with nothing to indicate it went wrong. Titles containing quotes are
    not exotic; a policy set has them.

    **Across every publisher, because the guard that shipped with the fix
    covered only the site being fixed.** policyforge-80 reverted each
    `shlex.quote` in turn, confirming each edit landed, and found
    `publisher.py` caught and `github_wiki.py` not — a guard written
    while fixing one case covering only that case, in the PR closing the
    issue about exactly that.
    """

    # A distinct hostile value per field, so a field that is dropped or
    # merged into its neighbour is visible rather than masked by another
    # field carrying the same string.
    fields = {
        "space": f"space {title}",
        "page_title": f"page_title {title}",
        "tier": f"tier {title}",
    }

    class _Doc:
        space = fields["space"]
        page_title = fields["page_title"]
        tier = fields["tier"]

    class _Self:
        """Enough of a publisher for `reconcile_command`.

        `ConfluencePublisher` reads only `doc`; `GitHubWikiPublisher`
        calls `self.title(doc)`. Called unbound with this stub so the
        test needs no network config and no constructor arguments.
        """

        def title(self, doc):
            return doc.page_title

    class _Benign:
        space = "space benign"
        page_title = "page_title benign"
        tier = "tier benign"

    benign = publisher.reconcile_command(_Self(), _Benign())
    printed = publisher.reconcile_command(_Self(), _Doc())
    parsed = shlex.split(printed)

    # **Every field the document supplies, not the one the author was
    # thinking about.** The first version set only `page_title` hostile
    # and asserted only `--title`. Reverting
    # `shlex.quote(doc.tier or "standard")` on both publishers then left
    # the whole suite green — 2913 passed — and `tier` is declared
    # `tier: str` with no enum and no validator, so
    # `standard --apply --force /etc` injects flags into a command
    # printed for a person to copy. policyforge-80 found it: the fix had
    # derived *which sites* to walk and still hand-fixed *which field*
    # was hostile, which is the original finding one axis over.
    #
    # Asserted as "each value survives as one argv element" rather than
    # "each flag's next word matches", because a publisher may print a
    # constant (`--target`) or a valueless flag (`--apply`), and indexing
    # past the last one raises rather than failing.
    #
    # Which fields a publisher uses is derived from a BENIGN run, not
    # from `value in printed` — `shlex.quote` escapes the value, so a
    # title containing an apostrophe never appears literally in the
    # printed string. Asking the benign run also means a field that the
    # hostile run *drops entirely* is a failure rather than silently
    # dropping out of the population.
    expected = {
        name: marker
        for name, marker in (("space", "space "), ("page_title", "page_title "), ("tier", "tier "))
        if any(word.startswith(marker) for word in shlex.split(benign))
    }
    assert expected, f"{publisher.__name__} interpolated no document field: {benign}"
    for name in expected:
        assert parsed.count(fields[name]) == 1, (
            f"{publisher.__name__} does not round-trip {name} as one argument "
            f"— a shell would see {parsed}: {printed}"
        )


@pytest.mark.parametrize("name", _INTERPOLATED)
def test_the_history_command_survives_a_hostile_document_name(name: str):
    """The third quoting site, and the one that is not a publisher.

    `cli/content.py` prints a `history` invocation naming a document.
    Reverting its `shlex.quote` also left the suite green, because the
    test above reaches publishers and this is a `click.echo` in a CLI
    function — a different shape, so the derived population cannot
    include it and it gets its own case, stated rather than assumed.
    """
    from policyforge.cli import content

    # **Both interpolated fields, not the one in the test's name.** The
    # first version set only `name` hostile, and reverting
    # `shlex.quote(tier)` left the suite green — the same axis error as
    # the publishers, committed one file over in the commit fixing it.
    values = {"--tier": f"tier {name}", "--name": f"name {name}"}
    printed = content.history_hint(
        tier=values["--tier"], name=values["--name"], previous="v1", current="v2"
    )
    parsed = shlex.split(printed)

    for flag, value in values.items():
        assert parsed[parsed.index(flag) + 1] == value, (
            f"the history hint does not round-trip {flag} {value!r} — a shell "
            f"would see {parsed}: {printed}"
        )


#: A printed command runs from `policyforge ` to the first backtick,
#: newline, or end of the string. Prose on either side is not the command.
_COMMAND_SPAN = re.compile(r"policyforge [^`\n]*")

#: Interpolated expressions that are safe without `shlex.quote`, pinned by
#: the expression's own text and each with a reason. **Not by `path:line`**
#: — that moved under a merge once already and turned this file red for an
#: edit it does not check.
#:
#: A reason is required: an exemption with no reason cannot be told from an
#: oversight, and the next reader cannot know whether removing it is safe.
SAFE_INTERPOLATIONS = {
    "CLI_TARGET": "a module constant naming the wiki backend, never user data",
    "quoted_flags": (
        "a pre-assembled `--controls <path>` fragment whose every value is "
        "shlex.quote'd where it is appended. It cannot be quoted again here "
        "without collapsing the whole fragment into one argument. **This is "
        "the one place the provenance rule does not reach** — that the parts "
        "were quoted upstream is not visible at this site, so it is held by "
        "test_the_crosswalk_seed_hint_survives_a_path_with_a_space instead."
    ),
}


def _quotes_its_value(expression: str) -> bool:
    """Is this interpolated expression quoted for a shell?

    **The property, not a list of shapes.** The previous version of this
    guard enumerated *punctuation*: an interpolation inside hand-written
    `"{x}"` or `'{x}'`. That is one shape of the defect, and a bare `{x}`
    — the shape that injects argv tokens rather than truncating a title —
    was not matched. Adding bare would have made it enumerate three
    shapes, and the fourth would have arrived unannounced. It is the same
    trap as a list of tool names standing in for "treats no input as
    success".

    So the question asked here is about the value's **provenance**: did it
    go through `shlex.quote`? That covers every punctuation shape
    including ones nobody has written yet.
    """
    try:
        node = ast.parse(expression, mode="eval").body
    except SyntaxError:  # pragma: no cover - unparseable expressions don't occur
        return False
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "quote"
    )


def _interpolations_in_printed_commands() -> list[tuple[str, str, str]]:
    """Every value interpolated **inside** a printed command: (site, expr, command).

    **Derived by parsing, because the population is not lines.** Every
    real printed command in this repo is built from adjacent f-string
    literals across several lines, and the dangerous ones are on the
    continuation lines:

        f"policyforge pull --space {shlex.quote(doc.space)} "
        f"--title {shlex.quote(doc.page_title)} "      <- no `policyforge`
        f"--tier {shlex.quote(doc.tier or 'standard')} --apply"

    A line-scoped scan cannot see lines two and three at all. Measured
    before this was written: of the eight lines in `src/` that a
    line-scoped rule matches, **five interpolate outside any command** —
    `f"No overlay at {path}. Run \\`policyforge crosswalk propose\\` first."`
    is prose with a command in it, not a command with a value in it — and
    the continuation lines that carry the real risk match nothing.

    Python concatenates adjacent f-strings into one `JoinedStr`, so the
    whole command is one node. Each interpolation is replaced by a marker,
    the command spans are located in the reconstructed text, and an
    interpolation counts only if its marker lands inside one.
    """
    found = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            text, exprs = "", []
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    text += part.value
                elif isinstance(part, ast.FormattedValue):
                    text += f"\x00{len(exprs)}\x00"
                    # `!r` is recorded, not treated as quoting: Python repr
                    # survives spaces and both quote characters and then
                    # fails on a backslash — `C:\\path` comes back doubled —
                    # so a Windows path formatted with `!r` is wrong.
                    suffix = f"!{chr(part.conversion)}" if part.conversion > 0 else ""
                    exprs.append(ast.unparse(part.value) + suffix)
            for match in _COMMAND_SPAN.finditer(text):
                command = match.group(0)
                if "--" not in command:
                    # A bare name in prose — "see `policyforge generate`" —
                    # is a reference, and `policyforge mcp:{note}` is a log
                    # prefix. Neither is an invocation. Same rule the
                    # population above uses.
                    continue
                site = f"{path.relative_to(SRC.parent).as_posix()}:{node.lineno}"
                # Put the expressions back before anything prints this. The
                # markers are NUL bytes, and a failure message carrying them
                # is unreadable in a terminal and makes `grep` report CI's
                # log as a binary file. Found by running the mutation
                # battery, where the harness could not say which arms fired.
                readable = command
                for index, expression in enumerate(exprs):
                    readable = readable.replace(f"\x00{index}\x00", "{" + expression + "}")
                for index, expression in enumerate(exprs):
                    if f"\x00{index}\x00" in command:
                        found.append((site, expression, readable))
    return found


#: Every interpolation the scan must find, by module and expression, with
#: how many times. **Members, not a count** — see the test below for why.
EXPECTED_INTERPOLATIONS = {
    ("policyforge/cli/content.py", "shlex.quote(current)"): 1,
    ("policyforge/cli/content.py", "shlex.quote(name)"): 1,
    ("policyforge/cli/content.py", "shlex.quote(previous)"): 1,
    ("policyforge/cli/content.py", "shlex.quote(tier)"): 1,
    # #32: the two licensed commands' hints became one, in `_run_licensed`,
    # which now names the framework too; and the resolver's absence message.
    ("policyforge/cli/etl.py", "shlex.quote(framework)"): 1,
    ("policyforge/cli/etl.py", "shlex.quote(str(sample))"): 1,
    ("policyforge/plugins.py", "shlex.quote(framework)"): 1,
    ("policyforge/export/github_wiki.py", "CLI_TARGET"): 1,
    ("policyforge/export/github_wiki.py", "shlex.quote(doc.tier or 'standard')"): 1,
    ("policyforge/export/github_wiki.py", "shlex.quote(self.title(doc))"): 1,
    ("policyforge/export/publisher.py", "shlex.quote(doc.page_title)"): 1,
    ("policyforge/export/publisher.py", "shlex.quote(doc.space)"): 1,
    ("policyforge/export/publisher.py", "shlex.quote(doc.tier or 'standard')"): 1,
    ("policyforge/zardoz/skills.py", "quoted_flags"): 1,
    ("policyforge/zardoz/skills.py", "shlex.quote(name)"): 1,
}


def test_the_scan_finds_exactly_the_interpolations_that_are_there():
    """**Non-empty accepts a shrink; this does not.**

    The first version of this asserted only that the population was not
    empty. Measured on the merged code: narrowing `_COMMAND_SPAN` to
    `[^`\\n]{0,40}` takes the population from 14 to 11, **the suite stays
    green, and a real bare interpolation placed beyond the narrowed span
    is not caught.** A guard reporting `clean` over a population it
    quietly halved is worse than no guard, because it is the sentence
    that stops the next person looking. #228.

    **Pinned by members rather than by cardinality, and that is the part
    that matters.** `assert len(found) == 14` is edited to `== 11` in one
    keystroke and the diff shows a number changing, which reviews as
    nothing. A wrong edit *here* deletes a line naming a module and an
    expression, so the diff says which command stopped being checked —
    the same reason `ILLUSTRATIVE_COMMANDS` above is keyed by the command
    and not by `path:line`.

    **Why a pin at all, when `shell_status.py` needs none.** That guard
    has a second derivation — files signalled by a cruder scan — in the
    *same unit* as its parser, so the two can be compared directly and
    the population may grow freely. Here there is no such second
    derivation: 9b measured that a text census finds 3 modules where the
    AST finds 5, **missing exactly the two whose commands span
    continuation lines** — the two the AST exists for. A cruder second
    derivation in the same dimension is not independent, it is just
    wrong in the direction that hides the shrink. So this is pinned, and
    the cost is that a legitimate new printed command turns it red once.

    **Module *and* expression, because the module alone is not enough.**
    Measured: dropping one interpolation from `cli/content.py`, which has
    four, takes the population 14 -> 13 and leaves the set of modules at
    five. A module-level check misses that; this names
    `policyforge/cli/content.py: shlex.quote(tier)`.

    The module is used rather than `path:line`: a line number moves under
    an edit above it, and this file has already gone red once on a merge
    that touched nothing it checks.
    """
    found = Counter(
        (site.rsplit(":", 1)[0], expression)
        for site, expression, _command in _interpolations_in_printed_commands()
    )
    expected = Counter(EXPECTED_INTERPOLATIONS)

    gone = expected - found
    extra = found - expected
    assert not gone and not extra, (
        "the set of interpolations the scan finds has changed. Every one of these "
        "is a value going into a command printed for a person to copy, so one that "
        "stops being found stops being checked:\n"
        + "".join(f"  NO LONGER FOUND  {m}: {e}  (x{n})\n" for (m, e), n in sorted(gone.items()))
        + "".join(f"  NEWLY FOUND      {m}: {e}  (x{n})\n" for (m, e), n in sorted(extra.items()))
        + "If the change is intended, edit EXPECTED_INTERPOLATIONS — which is a "
        "person deciding, and leaves a diff naming what changed."
    )


def test_every_value_interpolated_into_a_printed_command_is_quoted():
    """**The class, by provenance rather than by punctuation.**

    A value interpolated into a command printed for a person to copy must
    go through `shlex.quote`, or be named in `SAFE_INTERPOLATIONS` with a
    reason. Both failure directions are real and they differ:

        --title "{title}"   a quote in the value TRUNCATES the command,
                            naming the wrong document, silently
        --tier {tier}       a space in the value ADDS ARGV TOKENS, so
                            `standard --apply --force /etc` injects flags

    Writing this guard found two live instances of the second, neither of
    which the punctuation-shaped predecessor could match — both on
    continuation lines:

        etl.py     --sample {export_path}     a HITRUST export is normally
                                              named "MyCSF Assessment
                                              Export.xlsx"; the shell saw
                                              three arguments
        skills.py  --controls {path}          and `--framework {name!r}`,
                                              where repr doubles the
                                              backslashes in a Windows path
    """
    offenders = [
        f"{site}: {expression}  in  {command.strip()}"
        for site, expression, command in _interpolations_in_printed_commands()
        if not _quotes_its_value(expression) and expression not in SAFE_INTERPOLATIONS
    ]
    assert not offenders, (
        "a value is interpolated into a printed command without `shlex.quote`. A "
        "space in it adds arguments and a quote character truncates it, and either "
        "way the command a person copies is not the one that was meant. Quote it, "
        "or add the expression to SAFE_INTERPOLATIONS with a reason:\n  " + "\n  ".join(offenders)
    )


def test_the_safe_list_names_only_expressions_that_are_really_there():
    """An exemption for an expression nobody writes any more is a stale
    permission: it stops describing the code and starts describing its
    history, and the next reader cannot tell which."""
    live = {expression for _site, expression, _cmd in _interpolations_in_printed_commands()}
    stale = sorted(set(SAFE_INTERPOLATIONS) - live)
    assert not stale, (
        f"SAFE_INTERPOLATIONS exempts {stale}, which no printed command "
        "interpolates any more. Remove the entry rather than leaving a "
        "permission nothing uses."
    )


def test_the_crosswalk_seed_hint_survives_a_path_with_a_space():
    """**The behavioural half of `quoted_flags`' exemption.**

    The source scan cannot see that a pre-assembled fragment was quoted
    where its parts were added, so that claim is held here instead — by
    running the real function and putting the command it prints through a
    shell parser.

    Both hostile values at once and each distinct, so a value that is
    dropped or merged into its neighbour is visible rather than masked:
    a framework name with a space, which is ordinary, and a catalog path
    with one, which on Windows is ordinary too.

    **This used `NIST 800-171` until #260**, and #260 is why it cannot
    now. 800-171 no longer receives a `crosswalk seed` hint at all: NIST
    publishes its mapping, so the report says so instead of telling users
    to rebuild it. The test was about quoting and only borrowed 800-171 as
    a name with a space; it now uses the framework that still prints this
    hint in production. The premise is asserted first, so the next time a
    framework leaves this branch the failure names that, rather than
    reading "no printed command" as it did on #260.
    """
    from policyforge.crosswalk.overlay import _refusal_reason, _upstream_reason
    from policyforge.ingest.schema import Control
    from policyforge.zardoz import skills

    # 42 CFR Part 2's declared name: it has spaces, and it is the framework
    # that still reaches the seed-hint branch.
    framework = "Substance Use Disorder Records"
    catalog = Path("data/my catalogs/part2.json")
    assert _refusal_reason(framework) is None and _upstream_reason(framework) is None, (
        f"{framework!r} no longer reaches the seed-hint branch, so this test no "
        "longer exercises the quoting it exists for. Pick a framework that does."
    )

    class _Coverage:
        def __init__(self, name):
            self.framework = name
            self.covered = 0
            self.partial = []  # FrameworkCoverage has it; #270 reads it

    class _Report:
        framework_coverage = [_Coverage(framework)]

    controls = [
        Control(
            control_id="2.11",
            title="t",
            framework=framework,
            framework_version="2024",
            control_statement="s",
        ),
        Control(
            control_id="AC-1",
            title="t",
            framework="NIST 800-53",
            framework_version="Rev 5",
            control_statement="s",
        ),
    ]
    monkeyed = skills._paths_by_framework
    try:
        skills._paths_by_framework = lambda _paths: {
            skills._framework_key(framework): catalog,
            skills._framework_key("NIST 800-53"): Path("data/nist 800-53/controls.json"),
        }
        notes = skills._zero_row_reasons(controls, _Report(), [catalog])
    finally:
        skills._paths_by_framework = monkeyed

    printed = "\n".join(notes)
    command = re.search(r"`(policyforge [^`]*)`", printed)
    assert command, f"no printed command in the hint: {printed!r}"

    parsed = shlex.split(command.group(1))
    assert framework in parsed, (
        f"the framework name did not survive as one argument — a shell would see "
        f"{parsed}: {command.group(1)}"
    )
    assert str(catalog) in parsed, (
        f"the catalog path did not survive as one argument — a shell would see "
        f"{parsed}: {command.group(1)}"
    )
