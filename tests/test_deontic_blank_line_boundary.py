"""A blank line ends a paragraph, and a sentence with it (#358).

`analyze` ended a sentence only at a stop followed by a capital, so a
paragraph ending in a backticked citation, or one followed by `3.2 The team
must ...`, ran on into the next. Since #341 the joined statement could read
as Playbook-only: sonnet's Risk Treatment draft from #301's rate run gave 7
false ERRORs, and 0 with each paragraph judged alone (b5). It also hid a real
one: a Playbook-tagged obligation joined to a colon list with an 800-53 item
read as mixed.

The oracle is b5's instrument: the text judged whole must give what its
paragraphs give judged alone, the paragraphs read by markdown-it (the
project's own CommonMark parser), not by the rule under test.

**Measured over the 33 generated Standards and the two saved rate drafts:**
every citation stays attached (2,568 before and after); the corpus has one
citation-only line after a blank line, and it keeps its sentence; the
Playbook gate goes 8 -> 1 (the rate draft's 7 -> 0); and of 159 `check`
findings one moves, from line 289 to 291, where the obligation is.
"""

from __future__ import annotations

import pytest

from policyforge.content.deontic import analyze, playbook_obligations

CORE_1 = "`[NIST AI RMF Manage 1.1]`"
CORE_2 = "`[NIST AI RMF Manage 1.2]`"
PB_1 = "`[NIST AI RMF Playbook Manage 1.1 Action 1]`"
PB_2 = "`[NIST AI RMF Playbook Manage 1.2 Action 1]`"

#: The rate draft's shape (lines 13-19): the team's requirements cited to the
#: Core, each ending in a backticked tag, then NIST's suggestions.
RATE_SHAPE = (
    f"3.1 The AI Risk team must make a determination on each risk {CORE_1}\n\n"
    f"3.2 The AI Risk team must prioritize treatment by impact {CORE_2}\n\n"
    f"NIST suggests, among its 5 actions for Manage 1.1, that teams document it {PB_1}\n\n"
    f"NIST suggests, among its 3 actions for Manage 1.2, that teams rank risks {PB_2}"
)


def _paragraphs(text: str) -> list[str]:
    """The paragraphs markdown-it reads, each as its own text."""
    from markdown_it import MarkdownIt

    lines = text.split("\n")
    return [
        "\n".join(lines[t.map[0] : t.map[1]])
        for t in MarkdownIt("commonmark").parse(text)
        if t.type == "paragraph_open" and t.map
    ]


def _alone(text: str) -> list[tuple[str, tuple[str, ...]]]:
    return [(s.text, s.citations) for p in _paragraphs(text) for s in analyze(p)]


@pytest.mark.parametrize(
    "text",
    [
        RATE_SHAPE,
        f"Acme Health must keep a register {PB_1}\n\nAcme Health must retain logs.",
        "Acme Health must review logs\n\nthe team must approve changes.",
        f"Acme Health must review logs {CORE_1}\n\n\n\nAcme Health must rotate keys.",
    ],
    ids=["rate-shape", "backticked-tag", "lowercase-next", "several-blanks"],
)
def test_the_text_judged_whole_gives_what_its_paragraphs_give_alone(text):
    assert len(_paragraphs(text)) > 1  # the premise: more than one paragraph
    assert [(s.text, s.citations) for s in analyze(text)] == _alone(text)


def test_the_rate_runs_false_errors_are_gone():
    """b5's measurement, as a test: 0 ERRORs, as each paragraph gives alone."""
    assert playbook_obligations(RATE_SHAPE, ("The AI Risk team",)) == []
    assert sum(len(playbook_obligations(p, ())) for p in _paragraphs(RATE_SHAPE)) == 0


def test_a_playbook_obligation_before_a_colon_list_is_no_longer_hidden():
    """b5's addition on #358: joined to the list's 800-53 item it read as
    mixed and was silent. It is an ERROR, at its own line."""
    text = (
        f"Acme Health must keep a register {PB_1}\n\n"
        "Acme Health must:\n- review access [NIST 800-53 AC-2]"
    )
    assert [s.line for s in playbook_obligations(text, ("Acme Health",))] == [1]


def test_a_citation_after_a_blank_line_still_belongs_to_the_sentence_above():
    """The measurement #349 named: a citation-only line never starts a
    paragraph of its own here, blank line or not (#353's rule), so it is
    credited backwards as before."""
    (statement,) = analyze("Acme Health must review logs.\n\n[NIST 800-53 AU-6]")
    assert statement.citations == ("[NIST 800-53 AU-6]",)


def test_an_indented_paragraph_in_a_list_item_stays_with_the_item():
    """CommonMark: an indented paragraph after a blank line inside an item is
    the item's; unindented, it ends the list (#351)."""
    kept = analyze("- Acme Health must review logs\n\n  weekly [NIST 800-53 AU-6]")
    assert [s.citations for s in kept] == [("[NIST 800-53 AU-6]",)]
    ended = analyze("- Acme Health must review logs\n\nweekly [NIST 800-53 AU-6]")
    assert [s.citations for s in ended] == [(), ("[NIST 800-53 AU-6]",)]


def test_a_colon_lists_loose_items_still_read_as_one():
    text = "Acme Health must identify:\n\n- the logs [NIST 800-53 AU-6]\n\n- the reviewers"
    (statement,) = analyze(text)
    assert statement.citations == ("[NIST 800-53 AU-6]",)


def test_a_wrapped_sentence_with_no_blank_line_still_joins():
    (statement,) = analyze("Acme Health must review\nlogs weekly [NIST 800-53 AU-6].")
    assert statement.citations == ("[NIST 800-53 AU-6]",)


# 1d on #362: "citation-only" read literally lost `[AU-6].` after a blank line
# and made a backticked tag a statement of its own; and a heading's citation
# line, blank, then the sentence, lost the carried tag. Each keeps its tag.
_TAG = "[NIST 800-53 AU-6]"


@pytest.mark.parametrize(
    "text",
    [
        f"Acme Health must review logs.\n\n{_TAG}",
        f"Acme Health must review logs\n\n{_TAG}.",
        f"Acme Health must review logs.\n\n`{_TAG}`",
        f"Acme Health must review logs\n\n{_TAG};",
        f"## Access\n\n{_TAG}\n\nAcme Health must review logs.",
    ],
    ids=[
        "plain",
        "tag-then-stop",
        "backticked",
        "tag-then-semicolon",
        "heading-tag-blank-sentence",
    ],
)
def test_a_citation_line_after_a_blank_line_keeps_its_tag(text):
    """Conserved: the tag is on exactly one statement, and it is the
    obligation's, not a statement made of the tag itself."""
    (statement,) = analyze(text)
    assert statement.citations == (_TAG,) and statement.binds


def test_a_citation_after_a_word_is_prose_and_splits_off():
    """Named, not a defect (1d on #362): `per [AU-6].` has a word, so after a
    blank line it is a paragraph of its own and keeps its own tag."""
    first, second = analyze(f"Acme Health must review logs\n\nper {_TAG}.")
    assert first.citations == () and second.citations == (_TAG,)
