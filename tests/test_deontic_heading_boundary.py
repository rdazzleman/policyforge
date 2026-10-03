"""A heading ends a sentence, and nothing is credited across one (#349).

`analyze` blanked headings and read straight through them. Measured over 33
generated Standards, 18 of 2,501 statements ran across a heading. One was
glm's `... [Playbook Map 1.6 Action 8].` running into the next section's
`- **CAT-01:** ... shall ensure ... [NIST AI RMF Map 2]`, so a check read
CAT-01's obligation and Core citation as NIST's suggestion. One test per
heading shape (1d's conditions), and the two joins that must survive.
"""

from __future__ import annotations

import pytest

from policyforge.content.deontic import analyze

TAG_A = "[NIST 800-53 AC-2]"
TAG_B = "[NIST 800-53 AU-6]"


def _by_start(text: str) -> dict[str, tuple[int, tuple[str, ...]]]:
    """Each statement's first word -> (line, citations)."""
    return {s.text.split()[0]: (s.line, s.citations) for s in analyze(text)}


@pytest.mark.parametrize(
    "heading",
    [
        "## Next section",
        "### 3.1 Requirements",
        "Next section\n============",
        "Next section\n------------",
    ],
    ids=["atx-h2", "atx-h3", "setext-equals", "setext-dashes"],
)
def test_a_sentence_does_not_run_across_a_heading(heading):
    """The glm shape: a sentence ending in its own tag and full stop, a
    heading, then a bullet whose tag is its own."""
    text = (
        f"Alpha keeps a register {TAG_A}.\n\n{heading}\n\n"
        f"- **B-01:** Bravo shall review logs. {TAG_B}\n"
    )

    statements = analyze(text)

    alpha = next(s for s in statements if s.text.startswith("Alpha"))
    assert alpha.citations == (TAG_A,), "Bravo's tag was credited across the heading"
    assert "shall" not in alpha.text
    assert any("Bravo shall review logs" in s.text and TAG_B in s.citations for s in statements)
    assert not any("Next section" in s.text for s in statements), "heading text is not a sentence"


@pytest.mark.parametrize(
    "boundary",
    [
        "***",
        "___",
        "- - -",
        "* * *",
        "Next section\n=",
        "Next section\n==",
        "Next section\n--",
        "> ## Quoted heading",
        "##",
    ],
    ids=["break-stars", "break-underscores", "break-spaced-dashes", "break-spaced-stars",
         "setext-one-equals", "setext-two-equals", "setext-two-dashes", "quoted-atx", "bare-atx"],
)  # fmt: skip
def test_the_colon_lead_in_does_not_take_the_next_sections_citation(boundary):
    """1d's shapes on #350, none of which the 33-Standard corpus contains:
    the lead-in "must identify:" took the next section's citation across
    each of them."""
    text = f"Alpha must identify:\n\n{boundary}\n\nBravo shall review logs. {TAG_B}\n"

    statements = analyze(text)

    alpha = next(s for s in statements if s.text.startswith("Alpha"))
    assert alpha.citations == (), f"the citation crossed {boundary!r}"
    assert "Bravo" not in alpha.text
    assert [s.citations for s in statements if s.text.startswith("Bravo")] == [(TAG_B,)]


def test_a_paragraph_above_a_thematic_break_stays_text():
    """`***` is never a setext underline, so the line above it is not a heading."""
    texts = [s.text for s in analyze("Charlie must act.\n***\nDelta must act.\n")]
    assert texts == ["Charlie must act.", "Delta must act."]


def test_two_dashes_with_no_paragraph_above_are_neither_heading_nor_break():
    """`--` is a setext underline only under a paragraph line; alone, it is
    text: nothing is blanked. Asked of the block split directly.

    It used to assert the text stays ONE block. Since #358 a blank line ends
    a paragraph, so `--` after one is a paragraph of its own, as in
    CommonMark: two blocks, and still no heading."""
    from policyforge.content.deontic import _heading_blocks

    text = "Echo must act.\n\n--\n"
    blanked, blocks = _heading_blocks(text)
    assert blanked == text
    assert blocks == [(0, "Echo must act.\n"), (16, "--\n")]


@pytest.mark.parametrize("underline", ["---", "--", "==="])
def test_a_playbook_obligation_underlined_as_a_heading_is_still_an_error(tmp_path, underline):
    """9b on #350: a setext underline directly under an obligation makes the
    line heading text, so `analyze` blanks it. `playbook_tagged_headings`
    must then see it as a tagged heading, through the same classification,
    or the obligation is caught by neither. Through `check_tree`, as a user
    running `policyforge check` meets it."""
    from policyforge.content.check import ERROR, check_tree

    body = (
        "# Standard\n\nAcme Health must maintain an AI legal register. "
        f"[NIST AI RMF Playbook Govern 1.1 Action 1]\n{underline}\n\nMore text.\n"
    )
    (tmp_path / "standards").mkdir()
    (tmp_path / "standards" / "ai.md").write_text(body, encoding="utf-8")

    errors = [
        f for f in check_tree(tmp_path, org_actors=("Acme Health",)).findings if f.severity == ERROR
    ]

    assert any("Playbook" in f.message and "line 3" in f.message for f in errors), errors


PB_TAG = "[NIST AI RMF Playbook Govern 1.1 Action 1]"
#: Every shape a line can become heading text in, each with a Playbook tag
#: on its heading text. The population for the conservation test below.
HEADING_SHAPES = {
    "atx": f"## Legal {PB_TAG}\n\nBody.\n",
    "atx-bare-then-text": f"##\nAcme must act. {PB_TAG}\n",
    "atx-quoted": f"> ## Legal {PB_TAG}\n\nBody.\n",
    "setext-equals": f"Legal {PB_TAG}\n===\n\nBody.\n",
    "setext-one-equals": f"Legal {PB_TAG}\n=\n\nBody.\n",
    "setext-dashes": f"Legal {PB_TAG}\n---\n\nBody.\n",
    "setext-two-dashes": f"Legal {PB_TAG}\n--\n\nBody.\n",
    "setext-multiline-first": f"Legal {PB_TAG}\nand more\n---\n\nBody.\n",
    "setext-multiline-last": f"Legal\nand more {PB_TAG}\n===\n\nBody.\n",
}


@pytest.mark.parametrize("shape", sorted(HEADING_SHAPES))
def test_every_blanked_line_with_a_playbook_tag_is_seen_by_the_heading_check(shape):
    """**Conservation** (1d on #350): a line `analyze` removes as heading text
    must be one `playbook_tagged_headings` reports, or a Playbook tag on it
    is judged by neither. Derived from the shared classification: every
    heading-text line carrying a Playbook tag, in every shape."""
    from policyforge.content.deontic import _HEADING_TEXT, _line_kinds, playbook_tagged_headings

    text = HEADING_SHAPES[shape]
    lines = text.split("\n")
    kinds = _line_kinds(lines)
    tagged_heading_text = {
        n for n, (line, kind) in enumerate(zip(lines, kinds, strict=True), 1)
        if kind in _HEADING_TEXT and "Playbook" in line
    }  # fmt: skip
    seen = {n for n, _ in playbook_tagged_headings(text)}

    if shape == "atx-bare-then-text":
        assert tagged_heading_text == set(), "the premise: `##` alone, the tagged line is text"
        assert len(playbook_obligations_for(text)) == 1
    else:
        assert tagged_heading_text, "the premise: this shape puts the tag on heading text"
    assert tagged_heading_text <= seen


def playbook_obligations_for(text: str):
    from policyforge.content.deontic import playbook_obligations

    return playbook_obligations(text, ("Acme Health", "Acme"))


@pytest.mark.parametrize(
    "line",
    ["#hashtag Acme must keep a register.", "#1 priority: Acme must keep a register."],
    ids=["hashtag", "numbered-hash"],
)
def test_a_hash_not_followed_by_a_space_is_not_a_heading(line):
    """The destructive direction (1d on #350): read as a heading, this line
    would be blanked out of every gate. It stays a sentence, and the
    Playbook gate reads it."""
    from policyforge.content.deontic import playbook_tagged_headings

    text = f"{line} {PB_TAG}\n"

    assert playbook_tagged_headings(text) == []
    assert len(playbook_obligations_for(text)) == 1


def test_a_lone_dash_is_not_an_underline_by_choice():
    """markdown-it would make the line above a setext heading; excluding it
    is a stated choice, so the line stays in sentence analysis."""
    from policyforge.content.deontic import playbook_tagged_headings

    text = f"Acme must keep a register. {PB_TAG}\n-\n"

    assert playbook_tagged_headings(text) == []
    assert len(playbook_obligations_for(text)) == 1


def test_with_a_blank_line_before_the_rule_it_is_still_a_sentence():
    """The other arm: a `---` after a blank line is a thematic break, the
    line above stays a sentence, and the Playbook gate reads it."""
    from policyforge.content.deontic import playbook_obligations, playbook_tagged_headings

    body = (
        "Acme Health must maintain an AI legal register. "
        "[NIST AI RMF Playbook Govern 1.1 Action 1]\n\n---\n"
    )
    assert playbook_tagged_headings(body) == []
    assert len(playbook_obligations(body, ("Acme Health",))) == 1


def test_a_heading_right_after_a_list_item_ends_it():
    text = f"- Charlie reviews access\n## Section\nDelta must log changes. {TAG_B}\n"

    statements = analyze(text)

    charlie = [s for s in statements if "Charlie" in s.text]
    assert len(charlie) == 1
    assert "Delta" not in charlie[0].text and charlie[0].citations == ()
    assert [(s.line, s.citations) for s in statements if s.text.startswith("Delta")] == [
        (3, (TAG_B,))
    ]


def test_a_heading_right_after_a_table_row_ends_it():
    text = f"| Echo | monthly |\n### Section\nFoxtrot must rotate keys. {TAG_B}\n"

    statements = analyze(text)

    assert [s.citations for s in statements if "Echo" in s.text] == [()]
    assert [s.line for s in statements if s.text.startswith("Foxtrot")] == [3]


def test_a_rule_under_a_list_item_is_a_break_not_a_heading():
    """`---` under a list item is a thematic break: it ends the block, but
    the list item above it is still text, not heading text to blank."""
    text = "- Golf must be kept\n---\nHotel follows.\n"

    texts = [s.text for s in analyze(text)]

    assert any("Golf must be kept" in t for t in texts)
    assert not any("Golf" in t and "Hotel" in t for t in texts)


def test_a_sentence_wrapped_across_two_ordinary_lines_still_joins():
    text = f"India must review every account\nat least quarterly. {TAG_A}\n"

    (statement,) = analyze(text)

    assert statement.text == "India must review every account at least quarterly."
    assert statement.citations == (TAG_A,)
    assert statement.line == 1


def test_a_citation_on_the_line_below_still_attaches_to_its_sentence():
    """The shape `analyze`'s docstring was written for, inside one block."""
    text = f"## Access\n\nJuliet must disable stale accounts.\n{TAG_A}\n\nKilo is next.\n"

    starts = _by_start(text)

    assert starts["Juliet"] == (3, (TAG_A,))
    assert starts["Kilo"][1] == ()


def test_a_citation_opening_a_block_stays_with_the_sentence_it_opens():
    """Nothing above it in the block: it must not cross the heading back."""
    text = f"Lima is done.\n\n## Next\n\n{TAG_A} Mike must act.\n"

    starts = _by_start(text)

    assert starts["Lima"][1] == ()
    assert starts["Mike"][1] == (TAG_A,)


def test_line_numbers_still_point_at_the_document():
    text = "# Title\n\nNovember must act.\n\nSubtitle\n--------\n\nOscar must act.\n"

    starts = _by_start(text)

    assert starts["November"][0] == 3
    assert starts["Oscar"][0] == 8
