"""The 45 CFR 171 parse, against real eCFR XML.

The fixture is seven whole sections lifted from eCFR's published Part 171,
not written by hand, because every defect this parser has actually had came
from the regulation's shape rather than from an imagined one. Each section
earns its place by carrying a hazard: `171.102` and `171.402` are the two
kinds of section that must be dropped, `171.1001` is the *third* kind and
was missing until a defect proved it was — a live section with a reserved
paragraph inside it, which is not the same shape as a reserved section and
was not caught by the check for one. `171.1101` is four-digit,
`171.203` and `171.303` are where lettered conditions, digits and roman
numerals interleave, and `171.201` carries the italic condition headings
— while `171.203`, which has none, is what keeps that from being read
into every section.

These hold the *parse*. Nothing here reaches the network — what the fetch
does is `test_ecfr_fetch.py`'s question, and a parser test that needed
eCFR to be up would be muted the first week it was not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from policyforge.ingest.info_blocking import (
    CONDITION_ID_RE,
    SECTION_ID_RE,
    parse_information_blocking,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ecfr_45cfr171_excerpt.xml"


@pytest.fixture(scope="module")
def controls():
    return parse_information_blocking(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def by_id(controls):
    return {c.control_id: c for c in controls}


def test_a_section_the_pattern_no_longer_recognises_raises(controls):
    """**External extent, which every other check here is blind to.**

    The tests around this one ask whether the entries are well-formed.
    None of them asks *how many there should be*, and those are different
    questions — a pattern that stops matching yields a smaller catalog,
    not an error, and a smaller catalog is internally consistent in every
    way a reader or a test can see.

    This is the general form of what cost the ONC catalog twelve
    fabricated criteria: the loader asserted hard about the shape of what
    it found and nothing at all about whether it had found everything.

    Asked against the **document** rather than against a count. eCFR
    emits a node per section, so renumbering the part leaves those nodes
    in place and unrecognised — which is exactly the signal. A
    hand-written expected total would have to be maintained and could
    only ever be checked against itself.
    """
    xml = FIXTURE.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match=r"not recognised as sections"):
        parse_information_blocking(xml.replace("171.", "171A."))

    # And the unmutated fixture must still parse, or the guard is just
    # refusing everything — a pinned extent is one typo from that.
    assert controls


def test_reserved_and_definitions_are_excluded_not_unaccounted(by_id):
    """A recognised section that is deliberately dropped must not trip the
    extent check. The distinction the guard exists to preserve is between
    a **decision** and an **omission**, and a guard that cannot tell them
    apart forces the next person to delete it."""
    assert "171.402" not in by_id, "reserved, recognised and excluded"
    assert "171.102" not in by_id, "definitions, recognised and excluded"
    assert "171.201" in by_id


def test_reserved_sections_are_absent(by_id):
    """`171.402` is `[Reserved]`, and absence is the whole assertion.

    Asserting only that the real sections are present would pass just as
    happily on a parser that also emitted `171.402` — a control with a
    number, a title and no obligations. That is the failure worth testing
    for, because it survives every other check: it counts, it renders, it
    crosswalks, and it means nothing.
    """
    assert "171.402" not in by_id


def test_definitions_sections_are_absent(by_id):
    """A defined term is not a requirement; `hipaa_loader` drops these too."""
    assert "171.102" not in by_id


def test_four_digit_sections_are_parsed(by_id):
    """Subparts J and K number in four digits, and dropping them is silent.

    A section pattern written from reading the first few sections — all of
    them three-digit — would have excluded five of the part's twenty-four
    without failing anything.
    """
    assert "171.1101" in by_id
    assert SECTION_ID_RE.match("171.1101")
    assert SECTION_ID_RE.match("171.203")


def test_roman_numerals_are_not_conditions(by_id):
    """`171.303` has three conditions, not nine.

    eCFR's XML puts `(a)`, `(1)` and `(i)` in flat sibling `<P>` elements,
    so nesting has to be inferred. Matching any single letter read each
    `(i)` under this section's digits as a lettered condition, giving nine
    — five of them sharing the id `171.303(i)`.
    """
    ids = [e.enhancement_id for e in by_id["171.303"].enhancements]
    assert ids == ["171.303(a)", "171.303(b)", "171.303(c)"]


def test_condition_ids_are_unique(controls):
    """Duplicate ids are how the roman-numeral defect showed up in data."""
    ids = [e.enhancement_id for c in controls for e in c.enhancements]
    assert len(ids) == len(set(ids))


def test_lettered_conditions_run_in_order(controls):
    """Conditions are a,b,c… with no gaps — a gap means one was misread."""
    for control in controls:
        letters = [e.enhancement_id.split("(")[1][0] for e in control.enhancements]
        assert letters == [chr(ord("a") + i) for i in range(len(letters))]


def test_deeper_nesting_stays_in_its_condition(by_id):
    """`171.203(d)`'s (1)-(4) are the contents of one condition, not four.

    They say what a written security policy must contain. Split out, each
    would read as a free-standing duty that the regulation never imposes
    on its own.
    """
    condition = next(e for e in by_id["171.203"].enhancements if e.enhancement_id == "171.203(d)")
    assert "(1) Be in writing" in condition.description
    assert "(4) Provide objective timeframes" in condition.description


def test_every_id_matches_its_pattern(controls):
    for control in controls:
        assert SECTION_ID_RE.match(control.control_id)
        for enhancement in control.enhancements:
            assert CONDITION_ID_RE.match(enhancement.enhancement_id)


def test_no_condition_is_empty(controls):
    """An empty condition is the reserved-section failure one level down."""
    for control in controls:
        for enhancement in control.enhancements:
            assert enhancement.description.strip()


def test_no_condition_is_merely_reserved(controls):
    """**This test existed and checked the wrong shape.**

    The one above names the hazard exactly right — "the reserved-section
    failure one level down" — and then tests for an *empty* description.
    `171.1001(b)`'s description is not empty: it is the eight characters
    `[Reserved]`, so it passed, shipped, and sat in the catalog as a
    condition whose entire obligation is a word meaning there isn't one.

    Emptiness was the shape the defect took in `hipaa_loader`. Assuming a
    hazard keeps the shape it had in the last catalog is what let this one
    through, so this asserts the *property* — a condition carries an
    obligation — rather than the spelling of one failure to.
    """
    reserved = [
        e.enhancement_id
        for c in controls
        for e in c.enhancements
        if "reserved" in e.description.lower()
    ]

    assert reserved == []


def test_a_reserved_paragraph_does_not_take_its_live_neighbours_with_it(by_id):
    """The drop has to be exact, and `171.1001` is where that is visible:
    it has two conditions and only one of them is reserved. A rule that
    dropped the section, or stopped the parse at `(b)`, would also pass
    the test above while losing the disincentives CMS may actually apply.
    """
    disincentives = by_id["171.1001"]
    ids = [e.enhancement_id for e in disincentives.enhancements]

    assert ids == ["171.1001(a)"]
    assert "Medicare Shared Savings Program" in disincentives.enhancements[0].description


# There is deliberately no test here that the drop runs *after* the section
# is parsed, though `_drop_reserved_enhancements` is written that way. I
# wrote one, then implemented the drop the wrong way on purpose — refusing
# to open the condition, so its body would land on the previous one — and
# all nineteen tests still passed. `171.1001(b)` is the last paragraph in
# its section, so nothing follows it to be misplaced, and the test could
# not tell the two implementations apart. Asserting the ordering here would
# have been a check that cannot fail, which is worse than no check: it
# reads as coverage. The ordering is precautionary, and the docstring on
# the helper says so rather than claiming this data proves it.


def test_inline_emphasis_does_not_truncate_text(by_id):
    """A condition's text survives the italic run that opens it.

    Part 171 writes conditions as `(a) <I>Reasonable belief.</I> The actor
    must…`. Reading `<P>.text` rather than all descendant text stops at the
    `<I>`, so the marker is all that survives and the condition arrives
    empty — short, well-formed and wrong.
    """
    condition = next(e for e in by_id["171.201"].enhancements if e.enhancement_id == "171.201(a)")
    assert condition.description.startswith("The actor engaging in the practice")


def test_condition_headings_are_captured_as_titles(by_id):
    """The italic names are content: "Practice breadth" is how the preamble
    and the case law refer to `171.201(b)`, so dropping it into the body
    text would lose the only short handle the condition has.
    """
    titles = {e.enhancement_id: e.title for e in by_id["171.201"].enhancements}
    assert titles["171.201(a)"] == "Reasonable belief"
    assert titles["171.201(b)"] == "Practice breadth"


def test_a_heading_is_not_repeated_in_the_body(by_id):
    """Title and description are two fields, not the same words twice."""
    for control in by_id.values():
        for enhancement in control.enhancements:
            if enhancement.title:
                assert not enhancement.description.startswith(enhancement.title)


def test_mid_sentence_italics_are_not_mistaken_for_headings(by_id):
    """Only an italic run opening the paragraph is a heading.

    `171.203` italicizes nothing at the front of its conditions, so every
    one of them must come back untitled rather than borrowing a defined
    term from the middle of its own sentence.
    """
    assert all(e.title == "" for e in by_id["171.203"].enhancements)


def test_provenance_points_at_the_section_it_came_from(by_id):
    assert by_id["171.203"].source_path == "https://www.ecfr.gov/current/title-45/section-171.203"


def test_the_xml_parser_suppression_premise_still_holds():
    """The bandit/semgrep suppression in `info_blocking` rests on a premise.

    That premise is that the XML cannot come from anywhere an attacker
    chooses: `ecfr._API` is a constant, and no caller can point the fetch
    at another host. Python's stdlib parser refuses undefined entities and
    ignores external DTDs, so with a fixed host the residual exposure is
    entity expansion on the operator's own machine — accepted, and argued
    in a comment beside the import.

    A comment is not a check. A change that parameterised the URL would
    invalidate that reasoning without anyone rereading it, which is the
    failure this project keeps finding: a true claim that quietly stops
    being true. So the premise is asserted here, and this test failing
    means the suppression needs revisiting rather than repairing.
    """
    import inspect

    from policyforge.ingest import ecfr

    assert ecfr._API.startswith("https://www.ecfr.gov/")

    # No parameter through which a caller could supply a different host.
    for func in (ecfr.fetch_part_xml, ecfr.source_url, ecfr.current_date):
        params = set(inspect.signature(func).parameters)
        assert not params & {"url", "api", "base_url", "host", "endpoint"}


def test_the_framework_is_named_so_a_document_can_cite_it(controls):
    """**This test used to assert the defect.** It was called
    `test_framework_is_named_as_the_regulation` and pinned `45 CFR 171` —
    which reads as obviously right and made the catalog uncitable, because
    `SOURCE_TAG_RE` builds a framework name from capital-initial words and
    a digit-initial name is not a tag at all. The regulation is still named,
    in `framework_version` and in the catalog README; what the `framework`
    field carries is the thing a person types inside a citation.
    """
    assert {c.framework for c in controls} == {"Information Blocking"}
    assert {c.framework_version for c in controls} == {"45 CFR Part 171"}
