"""Fragment assembly, and the guard accepting a fragment.

The coupling is the part worth testing hardest. `scripts/changelog_guard.py`
demands a changelog entry; `changelog.d/` exists so people stop editing the
one shared section. If the guard did not accept a fragment it would push
every author back into the file the fragments were built to keep them out
of — a well-meant check defeating the change it sits beside.

The assembly cases come from what actually went wrong in one sitting: a
resolution that converted 1,768 lines to CRLF, one that left an entry as a
heading so five unrelated entries nested under it, and two merges that
shipped no entry at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import changelog_fragments as frag
import changelog_guard as guard


def write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


# ------------------------------------------------- the guard accepts fragments


def test_a_fragment_satisfies_the_guard() -> None:
    """Otherwise the guard defeats the fragments."""
    ok, message = guard.decide(
        ["src/policyforge/cli/etl.py", "changelog.d/1d-my-branch.md"], body=""
    )
    assert ok
    assert "changelog.d/1d-my-branch.md" in message


def test_the_fragment_readme_does_not_count_as_an_entry() -> None:
    """Editing the instructions is not writing an entry."""
    ok, _ = guard.decide(["src/policyforge/cli/etl.py", "changelog.d/README.md"], body="")
    assert not ok


@pytest.mark.parametrize(
    "paths",
    [
        ["changelog.d/README.md", "changelog.d/feat.md"],
        ["changelog.d/feat.md", "changelog.d/README.md"],
        ["changelog.d/README.md", "changelog.d/adds-thing.md", "changelog.d/feat.md"],
    ],
    ids=["readme-first", "fragment-first", "readme-first-two-fragments"],
)
def test_a_real_fragment_counts_whatever_order_the_readme_arrives_in(paths: list[str]) -> None:
    """The defect 9b found, in the likely case rather than a corner one.

    Written as `next(...)` then `if not ...README.md`, the clause took the
    first `.md` under changelog.d/ and only then asked whether it was the
    README — so a branch with a real fragment *and* a README touch was
    rejected. `git diff --name-only` sorts, and uppercase `R` sorts before
    every lowercase letter, so the README is first by default. The author
    most likely to hit it is whoever writes the first fragment and improves
    the instructions while they are there, and the guard would have told
    them to add the entry they were looking at.
    """
    ok, message = guard.decide(["src/policyforge/cli/etl.py", *paths], body="")
    assert ok, message
    assert "README.md" not in message


def test_editing_the_changelog_directly_still_satisfies_it() -> None:
    ok, _ = guard.decide(["src/policyforge/cli/etl.py", "CHANGELOG.md"], body="")
    assert ok


def test_the_failure_message_offers_the_fragment_first() -> None:
    _, message = guard.decide(["src/policyforge/cli/etl.py"], body="")
    assert "changelog.d/" in message
    assert message.index("changelog.d/") < message.index("CHANGELOG.md")


# ----------------------------------------------------------------- validation


def test_an_empty_fragment_is_a_problem(tmp_path: Path) -> None:
    """Someone opened the file and did not write the entry."""
    write(tmp_path, "a.md", "   \n\n")
    assert any("empty" in p for p in frag.check(tmp_path))


def test_a_fragment_with_crlf_is_a_problem(tmp_path: Path) -> None:
    """The 1,768-line conversion in miniature."""
    (tmp_path / "a.md").write_bytes(b"**A change.** It does a thing.\r\n")
    assert any("CR" in p for p in frag.check(tmp_path))


def test_a_fragment_that_starts_a_section_is_a_problem(tmp_path: Path) -> None:
    """A `## ` inside a fragment makes a second release section."""
    write(tmp_path, "a.md", "## 1.5.0\n\n**A change.**\n")
    assert any("heading" in p for p in frag.check(tmp_path))


def test_a_bold_lead_fragment_is_fine(tmp_path: Path) -> None:
    write(tmp_path, "a.md", "**A change.** What a user does differently.\n")
    assert frag.check(tmp_path) == []


def test_the_readme_is_never_a_fragment(tmp_path: Path) -> None:
    write(tmp_path, "README.md", "# Changelog fragments\n\nHow to write one.\n")
    write(tmp_path, "1d-thing.md", "**A change.**\n")
    assert [p.name for p in frag.fragments(tmp_path)] == ["1d-thing.md"]


# ------------------------------------------------------------------- assembly


def test_assembly_emits_filename_order_under_one_heading(tmp_path: Path) -> None:
    write(tmp_path, "b-second.md", "**Second.** Body.\n")
    write(tmp_path, "a-first.md", "**First.** Body.\n")
    section = frag.assemble(frag.fragments(tmp_path), "1.5.0")

    assert section.startswith("## 1.5.0\n\n")
    assert section.count("## ") == 1
    assert section.index("**First.**") < section.index("**Second.**")


def test_the_section_is_inserted_above_the_newest_release() -> None:
    changelog = "# Changelog\n\n## 1.4.0\n\nold entry\n\n## 1.3.0\n\nolder\n"
    out = frag.insert(changelog, "## 1.5.0\n\nnew entry\n\n")

    assert out.index("## 1.5.0") < out.index("## 1.4.0") < out.index("## 1.3.0")
    assert "old entry" in out and "older" in out


def test_insertion_without_a_release_heading_refuses(tmp_path: Path) -> None:
    """Better to stop than to guess where a section belongs."""
    with pytest.raises(SystemExit):
        frag.insert("# Changelog\n\nnothing released yet\n", "## 1.5.0\n\nx\n\n")


def test_assembly_does_not_reorder_or_categorise(tmp_path: Path) -> None:
    """The tool has no opinion about what belongs first, deliberately.

    This changelog's voice is bold-lead paragraphs in a narrative order.
    A tool emitting `### Added` / `### Fixed` would flatten that into
    something nobody here would have written, so ordering stays a person's
    judgement — made once, in one file, with no conflict to resolve.
    """
    write(tmp_path, "a.md", "**Trivial.** A small thing.\n")
    write(tmp_path, "b.md", "**Breaking.** Your pipeline fails.\n")
    section = frag.assemble(frag.fragments(tmp_path), "1.5.0")

    assert section.index("**Trivial.**") < section.index("**Breaking.**")
    assert "### " not in section


# ------------------------------------------------------- #217: shape and type
#
# Three defects, failing in opposite directions: a `# ` heading was accepted
# though it splits a section exactly as `## ` does; a fenced `## 1.6.1` was
# refused though it is an example rather than structure; and a non-`.md`
# file was neither accepted nor refused but invisible. Each is paired below
# with the case it must not break.


def test_an_h1_is_a_problem_for_the_same_reason_an_h2_is(tmp_path: Path) -> None:
    """**The level that was not observed.**

    The old rule was `startswith("## ")` — written from the `###` incident
    on #211, and blind one level up. `# ` sits *higher* than the release
    heading it would be nested under, so it splits the section at least as
    badly, and it was accepted.
    """
    write(tmp_path, "a.md", "# An H1\n\n**A change.**\n")
    assert any("heading" in p for p in frag.check(tmp_path))


def test_a_third_level_heading_is_still_fine(tmp_path: Path) -> None:
    """What the level rule must ALLOW.

    `###` and deeper are legitimate sub-structure inside a fragment. A rule
    that refused every heading would be satisfiable only by fragments with
    no structure, and would be edited out rather than obeyed.
    """
    write(tmp_path, "a.md", "**A change.**\n\n### Why it matters\n\nBecause.\n")
    assert frag.check(tmp_path) == []


def test_a_heading_inside_a_fence_is_an_example_not_a_section(tmp_path: Path) -> None:
    """**Over-refusal is a defect too, and this one made a legitimate
    fragment unwritable.**

    A fragment documenting the changelog format has to be able to show what
    an assembled section looks like. The old scan was line-oriented and
    could not tell an example from structure.
    """
    write(
        tmp_path,
        "a.md",
        "**A change.** The assembled section looks like:\n\n```\n## 1.6.1\n\n**A change.**\n```\n",
    )
    assert frag.check(tmp_path) == []


def test_a_fence_that_closes_does_not_hide_a_later_heading(tmp_path: Path) -> None:
    """The fence rule must not become a way to smuggle one past.

    Toggling on an opening fence and off on a closing one is the whole
    mechanism, so the case that matters is a real heading *after* a closed
    fence — which is where a naive "skip from the first fence" rule would
    go quiet.
    """
    write(tmp_path, "a.md", "**A change.**\n\n```\ncode\n```\n\n## 1.6.1\n")
    assert any("heading" in p for p in frag.check(tmp_path))


def test_a_non_markdown_file_is_reported_not_ignored(tmp_path: Path) -> None:
    """**The silent-drop half, and the worst of the three.**

    `changelog.d/my-fix.txt` is written, committed, passes the gate green
    and passes CI green, and the entry never reaches the changelog. Nothing
    anywhere said a file was ignored.
    """
    write(tmp_path, "my-fix.txt", "**A change nobody will ever read.**\n")
    problems = frag.check(tmp_path)
    assert any("my-fix.txt" in p for p in problems), problems
    assert [p.name for p in frag.strays(tmp_path)] == ["my-fix.txt"]


def test_the_readme_is_not_a_stray(tmp_path: Path) -> None:
    """What the stray rule must ALLOW.

    `README.md` is excluded as a *fragment* already; it must not reappear
    as a stray, or the rule would report the one file that is documented
    as belonging there.
    """
    write(tmp_path, "README.md", "# Changelog fragments\n\nHow to write one.\n")
    write(tmp_path, "1d-thing.md", "**A change.**\n")
    assert frag.strays(tmp_path) == []
    assert frag.check(tmp_path) == []


def test_check_derives_its_own_population(tmp_path: Path) -> None:
    """**The signature is the fix, not a matter of taste.**

    `check` used to take a list of paths, and every caller spelled it
    `check(fragments(...))` — so a stray was filtered out one call before
    the check could see it, and no test written that way could have caught
    it. Asserting the signature rather than the behaviour, because the
    behaviour above would come back the moment someone reintroduces a
    `paths` parameter and the call sites follow.
    """
    import inspect

    parameters = list(inspect.signature(frag.check).parameters)
    assert parameters == ["directory"], (
        f"check takes {parameters}; handing it a population lets a caller filter "
        "the thing it is supposed to guard"
    )


# ---- a pointer to another entry by position (#387) ----


@pytest.mark.parametrize(
    "sentence",
    [
        # The three written in one hour of 1.6.1's fixes, one per author.
        "The README says so (see the 800-171 entry above).",
        "Generated documents follow this rule (see below).",
        "A Standard for an AI topic is also regenerated or refused (see below).",
        # Shapes the 1.6.1 CHANGELOG itself used between entries.
        "Each is explained in full in its own entry below.",
        'Unaffected. "The call ledger hid four provider capabilities" below has the detail.',
        "It is how the harness change below found it.",
        "That is how the entry above went unmeasured.",
        "The same run, on top of the ledger fix above.",
        "As the previous entry says, it is refused.",
        "It installs with brew, and everything else below had accumulated on main.",
        "Documentation accuracy fixes found while writing the above, each overclaimed.",
        # "see" with an adverb, from 1d's domain sentences on #420.
        "See further below for the migration.",
        "The flag is gone (see just above).",
        "The rest is unchanged; see also below.",
        # A pointer wrapped across a line is still one pointer.
        "That is how the entry\nabove went unmeasured.",
    ],
)
def test_a_pointer_to_another_entry_by_position_is_a_problem(tmp_path: Path, sentence: str) -> None:
    """Assembly orders fragments by filename, which the writer cannot see:
    every one of the three real instances pointed the wrong way or by luck."""
    write(tmp_path, "a.md", f"**A change.** {sentence}\n")
    problems = frag.check(tmp_path)
    assert len(problems) == 1 and "by position" in problems[0], problems


@pytest.mark.parametrize(
    "sentence",
    [
        # ALL NINE sentences in the 1.6.1 CHANGELOG where "above" or "below"
        # points at no other entry. The issue's first pattern refused every
        # one (80's request on #387: pinned, so a widening cannot bring
        # them back). Derived by running that pattern over CHANGELOG.md and
        # reading each in context (1d on #420 found a tenth that was not
        # prose; it is the expected miss below).
        "A Procedure step carries out the Standard requirement above it, and that requirement "
        "holds the citation.",
        "The note above the rows no longer counts the kinds of zero; each row names its own.",
        "It covers both that shape and the one above, where an unquoted value adds arguments.",
        "The write is refused whenever any of the three counts would fall below the catalog "
        "being replaced.",
        "It reconciles, and is the same 814 and the same 200 you see below.",
        "Rewrite them to the forms above, and rewrite every tag in a document at once.",
        "All are equally invisible to the tag reader, and the pattern above catches any bracket.",
        "Two paragraphs above, the same page states that it cannot attest to any third party.",
        "**mcp is held below 2** (at 1.30.0), in the extra and in Dependabot's ignores.",
        # A comparison, not a pointer: the position has a complement (1d).
        "Any change below 5% is noise.",
        "A fix below 1.0 was never shipped.",
        # Within the fragment, which may use `###` (1d: `section` dropped).
        "The section below lists each flag.",
        # What the message tells a writer to do instead.
        "The README says so (see the NIST SP 800-171 entry in this release).",
        # Quoted, not said.
        "The check refuses `see below` in a fragment.",
        "```\nsee below\n```",
    ],
)
def test_above_or_below_that_is_not_a_pointer_passes(tmp_path: Path, sentence: str) -> None:
    write(tmp_path, "a.md", f"**A change.**\n\n{sentence}\n")
    assert frag.check(tmp_path) == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "known miss, named in _POSITIONAL's comment: a pointer whose subject is "
        "any other noun: the first is 1.5.0's CHANGELOG pointing 79 lines back at "
        "another entry, the second one of 1d's domain sentences (both on #420). "
        "Strict, so either goes red the day it is caught and has to move."
    ),
)
@pytest.mark.parametrize(
    "sentence",
    [
        "Seven entries do exactly that, including `2.16` and `2.19` in the Part 2 catalog above.",
        "As noted in the ledger bug above, the figure was wrong.",
    ],
)
def test_a_pointer_whose_subject_is_any_other_noun_is_not_yet_caught(
    tmp_path: Path, sentence: str
) -> None:
    write(tmp_path, "a.md", f"**A change.** {sentence}\n")
    assert frag.check(tmp_path), "caught: remove the xfail and move it to the refused tests"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "known miss, named in _POSITIONAL's comment: a pointer followed by it/the/a "
        "or a number reads as a comparison and passes, the price of letting "
        "'a change below 5%' through (policyforge-b5 on #420). Strict, so it goes "
        "red the day it is caught."
    ),
)
def test_a_pointer_followed_by_what_reads_as_a_complement_is_not_yet_caught(
    tmp_path: Path,
) -> None:
    write(tmp_path, "a.md", "**A change.** The fix above it also covers HIPAA.\n")
    assert frag.check(tmp_path), "caught: remove the xfail and move it to the refused tests"
