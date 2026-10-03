"""Block structure read from markdown-it (#376), and list numbers (#365).

The rebuild is held to conservation over the saved generated corpus (on the
issue); these pin the shapes that measurement found, and #365's three.
Expected answers come from today's behaviour or from markdown-it, never from
the code under test.
"""

from __future__ import annotations

from markdown_it import MarkdownIt

from policyforge.content.deontic import _line_kinds, analyze

AC2 = "[NIST 800-53 AC-2]"
CP2 = "*[NIST CP-2(7)]*"
AU2 = "[NIST 800-53 AU-2]"


def _texts(text: str) -> list[str]:
    return [s.text for s in analyze(text)]


# ---- #365: a list number is not a sentence -----------------------------------


def test_a_list_number_the_splitter_cut_off_is_not_a_statement():
    """`1. Open ...` read as `1.` and `Open ...`: 4,133 of the 45 Procedures'
    statements were such numbers."""
    text = "1. Open the console.\n1. Close the console.\n"
    assert _texts(text) == ["Open the console.", "Close the console."]
    # `1)` never was: the splitter breaks only at `.`, `!` or `?`, so the
    # number stays in its sentence, as it did before #365.
    assert _texts(text.replace("1.", "1)")) == ["1) Open the console.", "1) Close the console."]


def test_a_dotted_section_number_is_not_a_statement():
    """#365 named `6.2.` beside `1.`: 426 of the Standards' statements were
    dotted section numbers (ba on #421). Not list markers to CommonMark, so
    blanked where they open a paragraph and the splitter would cut them off."""
    assert _texts("6.2. The organization must retain logs.\n") == [
        "The organization must retain logs."
    ]
    assert _texts("4.1. Scope\n\n4.1.1. The owner shall act.\n") == [
        "Scope",
        "The owner shall act.",
    ]
    # Kept as they were: the splitter never cut these off, or it is no
    # section number at the start of a paragraph.
    for kept in (
        "6.2. the organization must retain logs.",
        "6.2 The organization must retain logs.",
        "6.2. **Retention.** The organization must retain logs.",
    ):
        assert _texts(kept + "\n") == [kept], kept
    assert _texts("See section 6.2. The owner must act.\n")[0] == "See section 6.2."


def test_a_number_under_a_sentence_is_not_glued_to_its_end():
    """Inside a colon list the number used to end the item above
    (`... broadcasting. 1.`)."""
    text = "Configure each of the following:\n1. Disable broadcasting.\n1. Enable encryption.\n"
    assert not any(t.endswith(" 1.") or t == "1." for t in _texts(text))
    assert _texts(text)[-1] == "Enable encryption."


def test_a_number_the_splitter_never_cut_off_keeps_its_text():
    """Conservation: `1. item two` and `1. **Scope.**` were one piece before
    #365 and still are."""
    assert _texts("1. item two shall log.") == ["1. item two shall log."]
    assert _texts("1. **Scope.** The owner must act.")[0].startswith("1. **Scope.**")


def test_line_numbers_and_citations_stay_on_their_sentence():
    text = f"Intro.\n\n1. Open the console {AC2}.\n1. Close the console.\n"
    statements = analyze(text)
    assert [(s.line, s.text, s.citations) for s in statements] == [
        (1, "Intro.", ()),
        (3, f"Open the console {AC2}.", (AC2,)),
        (4, "Close the console.", ()),
    ]


def test_a_numbered_colon_list_reads_like_a_bulleted_one():
    """#351/#354: the lead-in and its items are one statement for citation
    crediting. A bulleted list already read so; the number split a numbered
    one. The citation reaches the lead-in's obligation both ways."""
    for marker in ("- ", "1. "):
        text = f"The owner must:\n{marker}keep a register;\n{marker}Review it quarterly {AC2}.\n"
        first = analyze(text)[0]
        assert first.binds and first.citations == (AC2,), (marker, first)


# ---- citation-only lines the measurement found -------------------------------


def test_a_citation_under_a_heading_with_a_list_below_stays_its_own():
    """Measured on a generated Procedure: the section's scoping citation, a
    blank line, then the steps. It was a statement of its own before #376,
    and the first step does not borrow it."""
    text = f"### Coordinate\n\n{CP2}\n\n1. Site Reliability Engineering coordinates the plan.\n"
    statements = analyze(text)
    assert statements[0].text == CP2 and statements[0].cited
    assert statements[1].text == "Site Reliability Engineering coordinates the plan."
    assert statements[1].citations == ()


def test_a_citation_alone_in_its_section_does_not_cross_the_heading():
    """With no sentence of its own section to credit, it credits nothing, as
    before #376, and never the next section's sentence (#349)."""
    text = f"### One\n\n{AC2}\n\n### Two\n\nThe owner must act.\n"
    (statement,) = analyze(text)
    assert statement.text == "The owner must act." and statement.citations == ()


def test_a_citation_under_a_heading_opens_the_paragraph_below():
    """With a paragraph (not a list) below, the citation goes to it, as before."""
    text = f"### One\n\n{AC2}\n\nThe owner must act.\n"
    (statement,) = analyze(text)
    assert statement.citations == (AC2,) and statement.binds


def test_a_citation_indented_under_a_colon_list_is_the_whole_lists():
    """Inside the last item by CommonMark's indentation, after a blank line:
    still the list's own, shared by every item (#354), as at 74779c4. A
    mutation giving it to the last item alone survived every other test."""
    from policyforge.content.deontic import _colon_units

    text = f"Acme Health must:\n\n- maintain a register;\n- review access quarterly.\n\n  {AC2}\n"
    units, _ = _colon_units(text, analyze(text))
    assert [(u.line, u.citations) for u in units] == [(3, (AC2,)), (4, (AC2,))]


# ---- three shapes #376 changed that the corpus does not contain (9b on #421) --


def test_a_fence_after_a_paragraph_is_a_block_of_its_own():
    """Before #376 the paragraph, the fence and the sentence after it read as
    one statement carrying the paragraph's citation. The fence now ends it,
    as a blank line would."""
    text = f"The owner must log changes {AU2}:\n```\nlogger --all\n```\nReview it weekly.\n"
    statements = analyze(text)
    assert [(s.line, s.citations) for s in statements] == [(1, (AU2,)), (2, ()), (5, ())]
    assert statements[-1].text == "Review it weekly."


def test_a_hash_line_inside_a_fence_is_code_not_a_heading():
    """Before #376 it was a heading and a section start, which split the
    section the fence sits in: a defect, now gone."""
    from policyforge.content.grounding import _section_starts

    text = "## Scope\n\nThe owner must act.\n\n```\n# not a heading\nrun it\n```\n\nMore text.\n"
    kinds = _line_kinds(text.split("\n"))
    assert [i + 1 for i, kind in enumerate(kinds) if kind] == [1]
    assert _section_starts(text) == [1]


def test_a_heading_inside_a_list_item_is_a_heading_with_its_text_clean():
    """CommonMark reads `- ## x` as a heading inside a list item, and since
    #376 so does `deontic`. Its text is the heading's, not "- ## x"."""
    from policyforge.content.deontic import heading_statements

    text = f"Intro text.\n\n- ## Access must be reviewed\n- The owner shall log {AU2}.\n"
    (heading,) = heading_statements(text)
    assert (heading.line, heading.text, heading.modality) == (
        3,
        "Access must be reviewed",
        "obligation",
    )
    assert [s.text for s in analyze(text)] == ["Intro text.", f"- The owner shall log {AU2}."]
    for marker in ("* ", "+ ", "1. ", "> - "):
        (h,) = heading_statements(f"{marker}## Access must be reviewed\n")
        assert h.text == "Access must be reviewed", marker


def test_a_table_or_list_item_over_an_underline_stays_prose():
    """markdown-it reads a table (no tables in `commonmark`) or a list item's
    first line over `---`/`===` as a setext heading, which is blanked: every
    obligation in it left analysis (ba and 9b on #421). As at 74779c4, it
    stays prose; `---` is a break."""
    must = "The admin must review access."
    table = f"| Role | Duty |\n|---|---|\n| Admin | {must} |\n"
    assert _texts(f"Intro. {AC2}\n\n{table}---\n") == [
        "Intro.",
        f"| Role | Duty | |---|---| | Admin | {must} |",
    ]
    assert _texts(f"| Admin | {must} |\n===\n") == [f"| Admin | {must} | ==="]
    assert _texts(f"- {must}\n  ---\nAfter.\n") == [f"- {must}", "After."]
    assert _texts(f"- {must}\n  ===\n") == [f"- {must} ==="]
    # The number goes (#365); the obligation stays.
    assert _texts(f"1. {must}\n   ---\nAfter.\n") == [must, "After."]
    # Where the text line sits right on the underline, 74779c4 made it a
    # heading and dropped its sentence; it now stays prose too.
    statements = analyze(f"| Admin | {must} |\nStaff shall log it.\n---\n")
    assert [s.text for s in statements] == [f"| Admin | {must} | Staff shall log it."]
    # A paragraph over `---` is still a heading.
    assert _texts("Access must be reviewed\n---\nText.\n") == ["Text."]


def test_an_atx_line_indented_four_spaces_is_code_not_a_heading():
    """Changed by #376, as CommonMark reads it: indented code, so prose, not
    a heading (9b on #421). Before, it was a heading."""
    from policyforge.content.deontic import heading_statements

    text = "Intro.\n\n    ## Access must be reviewed\n\nAfter.\n"
    assert heading_statements(text) == []
    assert _texts(text) == ["Intro.", "## Access must be reviewed", "After."]


# ---- HTML and indented code are read as prose (80's ruling on #376; 1d) -------


def test_an_obligation_inside_an_html_block_is_still_analysed():
    """80's ruling: an HTML block is read as prose, as at 74779c4, whose
    answers these are. Without it among the leaf blocks, the obligation
    vanished from `check` with no warning (1d's arm on #421)."""
    div = f"<div>\nStaff must report incidents {AU2}.\n</div>\n"
    expected = (1, f"<div> Staff must report incidents {AU2}. </div>", "obligation", (AU2,))
    for text in (div, div + "\nThe owner may act.\n"):
        first = analyze(text)[0]
        assert (first.line, first.text, first.modality, first.citations) == expected
    (comment,) = analyze(f"<!--\nStaff must report incidents {AU2}.\n-->\n")
    assert comment.modality == "obligation" and comment.citations == (AU2,)


def test_an_obligation_in_indented_code_is_still_analysed():
    """Indented code is read as prose too, as at 74779c4. Alone, or straight
    under a heading, it is no paragraph's continuation, so without it among
    the leaf blocks the sentence vanished."""
    for text, line in (
        ("    Staff must log changes.\n", 1),
        ("## Scope\n\n    Staff must log changes.\n", 3),
    ):
        (statement,) = analyze(text)
        assert (statement.line, statement.text, statement.modality) == (
            line,
            "Staff must log changes.",
            "obligation",
        )


# ---- the structure is markdown-it's ------------------------------------------


def test_headings_are_markdown_its_except_the_lone_dash():
    """Every heading line markdown-it reports is a heading line here, and
    nothing else is, except the lone `-` underline kept by choice (1d on
    #350). Inputs from shapes the regexes each missed once."""
    text = (
        "# A\nText\n===\n> ## Quoted\n##\nPara\n-\n\n***\n- - -\nPlain\n---\n"
        "- item\n---\n1. one\n\n    indented code\n"
    )
    lines = text.split("\n")
    expected = set()
    for token in MarkdownIt("commonmark").parse(text):
        if token.type == "heading_open" and not (
            token.markup == "-" and lines[token.map[1] - 1].strip() == "-"
        ):
            expected.update(range(*token.map))
        if token.type == "hr":
            expected.add(token.map[0])
    got = {i for i, kind in enumerate(_line_kinds(lines)) if kind}
    assert got == expected
    assert 6 not in got, "the lone `-` under 'Para' is not an underline"
