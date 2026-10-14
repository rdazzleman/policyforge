"""Every population `scripts/check.py` derives must be asserted non-empty.

**The defect this file guards is the gate reporting PASS having examined
nothing.** Measured before the fix, in-process, with the gate's own `run`:

    check.run("mdformat --check", [mdformat, "--check"])   -> True  -> PASS
    check.run("mdformat --check", [mdformat, "--check", *5 real files]) -> True

Identical values. Every tool this gate drives treats "no input" as success,
so a derivation that stops matching is indistinguishable in the summary from
a clean tree — and `check.py`'s exit code is what the charge tells everyone
to condition their push on.

**#224 reported one population. This file asserts the class**, because a
guard written for the instance that was reported covers the instance that
was reported. The list below is derived from the source rather than typed,
so a fourth population added later is covered by the test that exists rather
than by somebody remembering.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "scripts" / "check.py"

sys.path.insert(0, str(ROOT / "scripts"))

import check  # noqa: E402


def test_the_guard_refuses_an_empty_population():
    with pytest.raises(check.EmptyDerivation) as excinfo:
        check.derived("some check", [], "markdown files")
    message = str(excinfo.value)
    assert "some check" in message
    assert "markdown files" in message, "the message must name what was not found"


def test_the_guard_returns_a_real_population_untouched():
    """**What it must ALLOW.**

    A guard that also transforms its input is a guard people route around.
    """
    items = ["a.md", "b.md"]
    assert check.derived("x", items, "y") is items


#: Assignments the parse finds that are NOT a population to guard, each with
#: the reason zero is acceptable there. **One list, used by both tests
#: below** — the first draft had it twice and they would have drifted, which
#: is the defect these tests exist to catch, in the tests themselves.
NOT_A_POPULATION = {
    "dirty": "the FINDINGS of `git status --porcelain`; zero is the good case",
    "hits": "the FINDINGS of the conflict scan, not its corpus; zero is the good case",
    "others": (
        "in `_tree_identity`, where it is one number in a banner string "
        "('N untracked (not ignored)') and gates nothing. Zero untracked "
        "files is the normal state of a clean checkout"
    ),
}


def _population_expressions() -> list[str]:
    """Every list-producing expression in `check.py` that feeds a check.

    Derived by parsing rather than by grepping: a comment mentioning
    `rglob` is not a population, and this file has already been burned once
    today by a rule that matched prose.
    """
    tree = ast.parse(GATE.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            source = ast.unparse(node.value)
            if re.search(r"\brglob\b|splitlines\(\)", source):
                found.append(target.id)
    return found


def _names_reaching_derived() -> set[str]:
    """Every name whose value reaches a `derived(...)` call.

    Two ways to reach it, and both count because both mean the value was
    asserted non-empty before use:

      corpus = derived(label, tracked + other, ...)   the RESULT is guarded
      derived(label, tracked_files + other_files, …)  the INPUT is guarded

    Walks the call's arguments for `ast.Name`, so a list built inline from
    two named halves guards both halves.
    """
    tree = ast.parse(GATE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "derived"):
            continue
        for arg in node.args:
            for inner in ast.walk(arg):
                if isinstance(inner, ast.Name):
                    names.add(inner.id)
    # and anything assigned from the call's result
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", "") == "derived"
        ):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def test_every_derived_population_passes_through_the_guard():
    """**The class assertion, and the reason this file exists.**

    `md_targets` was the one #224 reported. Parsing found three: markdown
    targets from `rglob`, tracked files from `ls-files --eol`, and the
    conflict-marker corpus. All three had the same property and only one
    had been noticed.

    A name here that is genuinely not a population belongs in
    `NOT_A_POPULATION` with a reason, not removed from the derivation —
    the same rule this repository settled on for exemptions: enumerate them
    by hand, derive the set they are drawn from.
    """
    populations = _population_expressions()
    assert populations, "parsed zero populations -- the parse is broken, not the file clean"

    # **The property is that the value FLOWS INTO the guard**, and this
    # test asked for one spelling twice before getting there -- first
    # `name = derived(...)`, then a looser regex for the same thing. Both
    # called a guarded corpus unguarded, which is the enumerate-the-shape
    # error these tests exist to catch, committed inside them.
    guarded = _names_reaching_derived()
    unguarded = [n for n in populations if n not in NOT_A_POPULATION and n not in guarded]

    assert not unguarded, (
        f"these populations are derived and never asserted non-empty: {unguarded}. "
        f"Wrap each in `derived(label, ..., what)`, or name it in "
        f"NOT_A_POPULATION with a reason saying why zero is acceptable there."
    )


def test_the_reasons_are_not_stale():
    """An exemption for a name that no longer exists is a stale exemption,
    and it hides the day the thing it excused came back under a new shape."""
    populations = set(_population_expressions())
    stale = set(NOT_A_POPULATION) - populations
    assert not stale, (
        f"NOT_A_POPULATION names things that are no longer derived: {stale}. "
        f"Remove them, or the next population to appear under one of those "
        f"names is exempt before anyone looks at it."
    )


def test_an_empty_derivation_exits_2_not_1():
    """**A distinct exit code, because the two facts are distinct.**

    `1` means a check ran and failed. `2` means nothing was checked. A
    reader who sees `1` goes looking for the failing check; a reader who
    sees `1` for an empty derivation goes looking for a check that does not
    exist.
    """
    source = GATE.read_text(encoding="utf-8")
    assert "except EmptyDerivation" in source, "an uncaught raise is a traceback, not a gate result"
    match = re.search(r"except EmptyDerivation.*?return (\d)", source, re.S)
    assert match and match.group(1) == "2", "an empty derivation must exit 2, distinct from 1"


def test_the_guard_cannot_be_silenced_by_allow_skip():
    """`--allow-skip` acknowledges a tool that is ABSENT. This is a tool
    that ran and examined nothing, which is never acceptable — and letting
    one flag cover both would make the acknowledgement meaningless."""
    assert "EmptyDerivation" not in str(check.SKIP_FLAGS)
    for label in check.SKIP_FLAGS:
        assert "derivation" not in label.lower()


def test_a_gate_with_no_checks_is_not_a_passing_gate():
    """**The same failure one level up, in the function that produces the
    answer everyone conditions their push on.**

    `summarise({}, set())` returned 0 and printed `0 ran, 0 failed, 0
    skipped`. Found by policyforge-ba, who argued it was categorically
    different because `results` is a dict literal whose keys cannot shrink
    without a visible diff.

    **That is true of the code as it stands and it is an argument from the
    current shape rather than from a guard.** It stops holding the moment
    anyone builds `results` conditionally, which is a two-line change
    nobody would flag in review. And this file's own title is *refuse a
    gate result derived from nothing* — a gate with no checks is one.
    """
    assert check.summarise({}, set()) == 2


def test_a_gate_with_checks_still_summarises_normally():
    """What it must ALLOW, beside what it refuses."""
    assert check.summarise({"ruff (lint)": True}, set()) == 0
    assert check.summarise({"ruff (lint)": False}, set()) == 1


def test_no_assertion_in_the_gate_restates_its_own_construction():
    """**policyforge-ba's other finding, and it was in this PR.**

    `assert len(corpus) == tracked + others` where `corpus` *is*
    `tracked_files + other_files` and the two numbers are `len()` of those
    same lists. No input can make it differ — **a line that reads as a
    check and is not one**, in the change whose whole subject is that
    shape. It was also a bare `assert`, which `python -O` strips.

    Pinned by the specific text rather than by banning `assert`, which has
    legitimate uses: what is forbidden is this one coming back.
    """
    source = GATE.read_text(encoding="utf-8")
    assert "assert len(corpus) == tracked + others" not in source


def test_no_function_here_has_an_orphaned_docstring_block():
    """**A no-op string expression that reads as documentation.**

    Inserting a docstring above an existing one leaves the original as a
    bare string expression in the body: still there when you read the file,
    gone from `__doc__`, from `help()` and from any doc build. `ruff` does
    not flag it — measured.

    It happened in this very PR. `summarise` ended with two triple-quoted
    blocks and the sentence *a check that did not run is not a check that
    passed* — the rule the function encodes — left the API silently.
    Found by policyforge-ba, who blocked on it for the right reason: **a
    no-op line that reads as documentation, in the change whose subject is
    a no-op line that reads as a check.**
    """
    tree = ast.parse(GATE.read_text(encoding="utf-8"))
    orphans = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            continue
        for index, statement in enumerate(node.body):
            is_string = (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            )
            if is_string and index > 0:
                name = getattr(node, "name", "<module>")
                orphans.append(f"{name} line {statement.lineno}")
    assert not orphans, (
        f"string expressions that are not docstrings and reach nobody: {orphans}. "
        f"Merge them into the real docstring above, or delete them."
    )
