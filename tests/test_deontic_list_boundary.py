"""A list item starts a new block; a colon lead-in keeps its items (#351).

`analyze` joined a sentence ending in a full stop to the list item under it,
so an uncited obligation borrowed the item's citation and "binds but cites
nothing" went silent on it. The cause is the sentence boundary rule
(`entail/base._BOUNDARY_RE`): a stop ends a sentence only before a capital,
a quote, `[` or `(`, so a list marker never begins one, blank line or not.

80's ruling: a list item starts a new block, as in CommonMark, and a
sentence ending in `.` takes no citation from the item after it. **A colon
lead-in is different** -- `The owner must identify:` is carried by its items
-- and must still read as one statement with them.

The 33 generated Standards contain no instance of the joined shape once
#349's heading boundary is in (their citations sit on the line below, which
already splits), so these hand-written cases are what carry the fix.
"""

from __future__ import annotations

import pytest

from policyforge.content.deontic import analyze

CITE = "[NIST 800-53 AU-6]"


def _read(text: str) -> list[tuple[str, tuple[str, ...]]]:
    return [(s.text, s.citations) for s in analyze(text)]


@pytest.mark.parametrize(
    "between, marker",
    [
        ("\n", "- "),
        ("\n\n", "- "),
        ("\n", "* "),
        ("\n", "+ "),
        ("\n", "1. "),
        ("\n", "1) "),
        ("\n  ", "- "),
    ],
    ids=["dash", "blank-line", "star", "plus", "numbered", "paren", "indented"],
)
def test_a_full_stop_sentence_does_not_borrow_the_next_items_citation(between, marker):
    text = f"The owner must review logs.{between}{marker}item two shall log {CITE}."
    statements = analyze(text)
    assert [s.text for s in statements] == [
        "The owner must review logs.",
        f"{marker}item two shall log {CITE}.".strip(),
    ]
    first = statements[0]
    assert first.binds and not first.cited and first.citations == ()


def test_one_list_item_does_not_borrow_the_next_items_citation():
    """The same defect between two items, each ending in a full stop."""
    statements = analyze(f"- The owner must review logs.\n- The owner shall log {CITE}.")
    assert len(statements) == 2
    assert statements[0].citations == () and statements[1].citations == (CITE,)


def test_a_question_or_exclamation_ends_the_sentence_too():
    for stop in ("?", "!"):
        statements = analyze(f"Who must review logs{stop}\n- the owner shall {CITE}.")
        assert len(statements) == 2, _read(f"Who must review logs{stop}\n- x")


@pytest.mark.parametrize("between", ["\n", "\n\n"], ids=["tight", "loose"])
def test_a_colon_lead_in_still_reads_as_one_with_its_items(between):
    """80's ruling: the lead-in's obligation is carried by its items. Every
    item stays with the lead-in, not only the first."""
    text = (
        f"The owner must identify:{between}- the logs {CITE}{between}"
        "- the reviewers [NIST 800-53 AU-2]"
    )
    (statement,) = analyze(text)
    assert statement.binds
    assert statement.citations == (CITE, "[NIST 800-53 AU-2]")


def test_a_list_after_a_colon_list_ends_at_the_next_full_stop_sentence():
    """The colon's list is the lead-in's; a new paragraph after it is not."""
    text = (
        f"The owner must identify:\n- the logs {CITE}\n\n"
        "The team must review logs.\n- item two shall log [NIST 800-53 AU-2]."
    )
    texts = [s.text for s in analyze(text)]
    assert texts[-2] == "The team must review logs."
    assert analyze(text)[-2].citations == ()


def test_what_already_split_or_joined_is_unchanged():
    # A citation line between them already split; the backward credit stays.
    split = analyze(f"The owner must review logs.\n{CITE}\n- item two shall log.")
    assert [s.citations for s in split] == [(CITE,), ()]
    # A sentence wrapped across two ordinary lines is one sentence.
    (wrapped,) = analyze(f"The owner must review\nlogs weekly {CITE}.")
    assert wrapped.citations == (CITE,)
    # A hyphen inside a line is not a list marker.
    (inline,) = analyze(f"The owner must review logs - weekly {CITE}.")
    assert inline.citations == (CITE,)


def test_line_numbers_still_point_at_the_item():
    statements = analyze(
        f"Intro line.\n\nThe owner must review logs.\n- item two shall log {CITE}."
    )
    assert [s.line for s in statements] == [1, 3, 4]


def test_a_colon_lists_citation_under_the_list_stays_with_the_lead_in():
    """The generated Standards' own shape: the lead-in, a blank line, the
    items, a blank line, then the citation alone on its line. A draft of
    this fix read that citation as a new paragraph and orphaned it."""
    text = (
        "Scientific integrity considerations shall be documented, including:\n\n"
        "- Experimental design\n- Construct validation\n\n"
        "[NIST AI RMF Map 2.3]\n\nThe owner must review logs."
    )
    statements = analyze(text)
    assert statements[0].binds and statements[0].citations == ("[NIST AI RMF Map 2.3]",)
    assert statements[-1].text == "The owner must review logs."
    assert statements[-1].citations == ()


def test_an_indented_continuation_stays_with_its_item():
    """A loose item's second paragraph, indented under it, is the item's."""
    text = f"The owner must identify:\n\n- the logs\n\n  kept for a year {CITE}\n\n- the reviewers"
    (statement,) = analyze(text)
    assert statement.citations == (CITE,)


def test_a_lead_in_whose_colon_carries_a_citation_is_still_a_lead_in():
    (statement,) = analyze(f"The owner must identify: {CITE}\n- the logs\n- the reviewers")
    assert statement.binds and statement.citations == (CITE,)


# Second line of each: does it open a list? Expected answers come from
# markdown-it (the project's own CommonMark parser), never from the rule under
# test (1d on #353: only an ordered list starting at 1 interrupts a paragraph).
_SHAPES = [
    "Logs must be retained for no fewer than\n90) days [NIST 800-53 AU-11].",
    "Access is reviewed each quarter, and\n2. rounds are recorded [NIST 800-53 AC-2].",
    "Access is reviewed each quarter, and\n1. rounds are recorded [NIST 800-53 AC-2].",
    "Access is reviewed each quarter.\n- rounds are recorded [NIST 800-53 AC-2].",
    "Access is reviewed each quarter.\n\n2. rounds are recorded [NIST 800-53 AC-2].",
    "Access is reviewed each quarter.\n\n90) rounds are recorded [NIST 800-53 AC-2].",
]


def _commonmark_opens_a_list(text: str) -> bool:
    from markdown_it import MarkdownIt

    tokens = MarkdownIt("commonmark").parse(text)
    return any(t.type in ("bullet_list_open", "ordered_list_open") for t in tokens)


@pytest.mark.parametrize("text", _SHAPES, ids=range(len(_SHAPES)))
def test_a_line_splits_off_exactly_when_commonmark_opens_a_list_there(text):
    split = len(analyze(text)) == 2
    assert split == _commonmark_opens_a_list(text), _read(text)


def test_the_shapes_include_both_answers():
    """Otherwise the comparison above could agree by being constant."""
    answers = {_commonmark_opens_a_list(t) for t in _SHAPES}
    assert answers == {True, False}


def test_a_lead_ins_own_citation_line_is_not_the_lead_in():
    """1d on #353: the lead-in is the last line that is not only citations,
    so a citation under the colon line does not hide the colon."""
    text = "The owner must identify:\n[NIST 800-53 RA-3]\n\n- a\n- b [NIST 800-53 RA-5]"
    (statement,) = analyze(text)
    assert statement.citations == ("[NIST 800-53 RA-3]", "[NIST 800-53 RA-5]")


@pytest.mark.parametrize("first, second", [("1. ", "2. "), ("1) ", "2) ")], ids=["dot", "paren"])
def test_numbered_items_do_not_borrow_each_others_citation(first, second):
    """1d on #353: inside a list, `2.` is the next item, not a paragraph line
    it cannot interrupt. Lowercase on purpose: a capital after `2.` is cut by
    the sentence splitter anyway and hides the defect. The expected count of
    items comes from markdown-it."""
    from markdown_it import MarkdownIt

    text = (
        f"{first}the owner must review logs weekly\n"
        f"{second}the owner must rotate keys yearly [NIST 800-53 SC-12]"
    )
    items = sum(t.type == "list_item_open" for t in MarkdownIt("commonmark").parse(text))
    statements = analyze(text)
    assert len(statements) == items == 2
    assert statements[0].citations == () and statements[1].citations == ("[NIST 800-53 SC-12]",)
