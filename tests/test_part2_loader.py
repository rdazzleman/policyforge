"""42 CFR Part 2, read into sections.

The fixture is six real sections cut from the 2026-09-17 revision, chosen
for the cases a parser has to get right rather than for being the first
six: the security hub (2.16), a control-shaped section with a conduct
heading (2.19), a section whose security duty is a cross-reference (2.52),
the definitions section that must be dropped (2.11), the statutory
authority section that must also be dropped (2.1), and one pure conduct
section (2.13).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from policyforge.ingest.part2_loader import (
    CONTROL_SECTIONS,
    EXPECTED_SECTIONS,
    SECTION_ID_RE,
    Section,
    _opens_requirement,
    _require_unique_ids,
    parse_part2,
    sections,
)

FIXTURE = Path(__file__).parent / "fixtures" / "ecfr_42cfr2_excerpt.xml"


@pytest.fixture(scope="module")
def excerpt() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parsed(excerpt) -> list[Section]:
    return sections(excerpt, strict=False)


# --------------------------------------------------------------------------
# The id pattern, which is where a copied parser fails silently
# --------------------------------------------------------------------------


def test_the_section_pattern_matches_how_this_part_actually_numbers():
    """`2.1` through `2.68`: one or two digits after the dot."""
    for number in ("2.1", "2.4", "2.11", "2.16", "2.19", "2.31", "2.52", "2.68"):
        assert SECTION_ID_RE.match(number), number


def test_part_171_s_pattern_matches_nothing_here():
    """The near miss this parser exists downstream of.

    Part 171 numbers its sections `171.203`, `171.1000` — three or four
    digits. Copied across, the pattern does not merely look wrong, it
    matches **zero** of Part 2's sections, and a catalog built on it comes
    out empty while every check that asks whether its entries are
    well-formed passes on the nothing.
    """
    borrowed = re.compile(r"^171\.\d{3,4}$")

    matched = [n for n in ("2.1", "2.11", "2.16", "2.19", "2.52", "2.68") if borrowed.match(n)]

    assert matched == []


def test_a_deeper_number_is_not_a_section(parsed):
    """`2.16(a)` is a paragraph within a section, not a section."""
    assert not SECTION_ID_RE.match("2.16(a)")
    assert not SECTION_ID_RE.match("2.311")


# --------------------------------------------------------------------------
# An empty or short parse is loud
# --------------------------------------------------------------------------


def test_a_parse_that_finds_nothing_refuses_rather_than_returning_empty():
    """An empty catalog is the failure mode that passes every other check,
    so it is the one checked explicitly."""
    with pytest.raises(ValueError) as caught:
        sections("<DIV5><DIV8 N='171.203'><HEAD>Security exception.</HEAD></DIV8></DIV5>")

    message = str(caught.value)
    assert "matched 0 of the 1 sections" in message
    assert "parser fault" in message, "an empty parse must be named a parser fault, not a change"
    assert "171.203" in message, "the message must name the section the pattern missed"


def test_the_primary_count_check_asks_the_document_not_the_constant():
    """**The check that catches a wrong pattern uses no hand-written
    number.** Both sides come from the XML: what eCFR says is there, and
    what the pattern matched. A constant nobody can re-derive is the same
    shape as a hand-written prefix list — right the day it is written and
    unverifiable after.

    Pinned with a document whose section count is *not* `EXPECTED_SECTIONS`
    and not zero, so passing it cannot be an accident of either number.
    """
    three_unmatched = (
        "<DIV5>"
        + "".join(f"<DIV8 N='171.{n}'><HEAD>x</HEAD></DIV8>" for n in (203, 204, 205))
        + "</DIV5>"
    )

    with pytest.raises(ValueError) as caught:
        sections(three_unmatched)

    message = str(caught.value)
    assert "matched 0 of the 3 sections" in message
    assert str(EXPECTED_SECTIONS) not in message, "the pattern check must not cite the constant"


def test_a_document_the_pattern_fully_matches_is_still_held_to_the_pinned_count(excerpt):
    """The second check, for the failure the first cannot see: a response
    that is internally consistent and is not the whole part. Six sections
    all match the pattern, so only the pinned count can catch it."""
    with pytest.raises(ValueError) as caught:
        sections(excerpt)

    message = str(caught.value)
    assert f"expected {EXPECTED_SECTIONS}" in message
    assert "the pattern is fine" in message
    assert "truncated" in message
    assert "2.16" in message, "the message should name what it did find"


def test_the_count_check_is_the_only_thing_strict_turns_off(excerpt, parsed):
    """`strict=False` parses an excerpt. It must not also relax the id
    pattern, or a fixture would prove less than it appears to."""
    assert len(parsed) == 6
    assert all(SECTION_ID_RE.match(s.number) for s in parsed)


# --------------------------------------------------------------------------
# What a section carries
# --------------------------------------------------------------------------


def test_the_heading_loses_its_section_symbol_and_number(parsed):
    by_number = {s.number: s for s in parsed}

    assert by_number["2.16"].title == "Security for records and notification of breaches."
    assert by_number["2.19"].title == "Disposition of records by discontinued programs."


def test_italic_runs_inside_a_paragraph_are_kept(parsed):
    """eCFR names paragraphs in `<I>` runs — "Requirements for formal
    policies and procedures" is one — and reading `.text` alone truncates
    at the first, which loses content while still producing a section."""
    security = next(s for s in parsed if s.number == "2.16")

    assert "Requirements for formal policies and procedures" in security.text


def test_the_security_section_carries_its_actual_obligations(parsed):
    """Verified against the fetched text rather than the heading. If this
    section ever stops saying these things, the product judgement that
    Part 2 contains one crosswalkable control stops being true."""
    security = next(s for s in parsed if s.number == "2.16")

    for duty in (
        "formal policies and procedures",
        "sanitizing the hard copy media",
        "secure room, locked file cabinet",
        "45 CFR 164.514(b)",
        "subpart D of 45 CFR part 164",
    ):
        assert duty in security.text, duty


# --------------------------------------------------------------------------
# Vocabulary is not a requirement
# --------------------------------------------------------------------------


def test_definitions_and_statutory_authority_state_no_obligation(parsed):
    """Dropped for `hipaa_loader`'s reason: a defined term is not a duty."""
    by_number = {s.number: s for s in parsed}

    assert not by_number["2.11"].states_an_obligation
    assert not by_number["2.1"].states_an_obligation


def test_a_conduct_section_still_states_an_obligation(parsed):
    """The test is vocabulary versus duty, **not** conduct versus control.
    § 2.13 is conduct and is not crosswalkable, but it plainly states
    duties, and a parser that dropped it here would be pre-empting the
    open shape question instead of answering this one."""
    conduct = next(s for s in parsed if s.number == "2.13")

    assert conduct.states_an_obligation


def test_a_control_section_with_a_conduct_heading_is_not_dropped(parsed):
    """§ 2.19 reads administrative and its body is media sanitization.
    Nothing here may classify on the heading alone."""
    disposition = next(s for s in parsed if s.number == "2.19")

    assert disposition.states_an_obligation
    assert "sanitizing any associated hard copy or electronic media" in disposition.text


def test_a_reserved_section_states_no_obligation():
    """Not in Part 2 today, which is why it is constructed rather than
    drawn from the fixture: a `[Reserved]` section is countable, plausible
    and empty, and every check except "is this real" passes on it."""
    reserved = Section(number="2.99", title="[Reserved]", text="")

    assert not reserved.states_an_obligation


# --------------------------------------------------------------------------
# The catalog: two sections of thirty-eight
# --------------------------------------------------------------------------


def test_the_catalog_is_the_two_sections_and_nothing_else(excerpt):
    """Thinness is the correct answer here, so it is asserted rather than
    left as an outcome a later change could quietly inflate."""
    catalog = parse_part2(excerpt, strict=False)

    assert [c.control_id for c in catalog] == ["2.16", "2.19"]
    assert CONTROL_SECTIONS == ("2.16", "2.19")


def test_conduct_sections_are_not_controls(excerpt):
    """A section can state obligations and still not be a control. 2.13
    says what may be disclosed, not what must be implemented, and citing
    it as a control would assert a safeguard exists where the regulation
    says a disclosure was lawful."""
    catalog = parse_part2(excerpt, strict=False)

    assert "2.13" not in {c.control_id for c in catalog}
    assert "2.52" not in {c.control_id for c in catalog}


#: Security machinery an implementer would have to build. Beside the test
#: that uses it, so the evidence and the claim move together.
_MACHINERY = (
    "encrypt",
    "decrypt",
    "backup",
    "sealed",
    "envelope",
    "label",
    "climate",
    "retention period",
    "portable electronic device",
    "access control",
)


def test_the_catalog_still_matches_the_test_that_produced_it(excerpt):
    """**Sweeps every parsed section**, rather than spot-checking the two
    the enumeration was argued from.

    The earlier version asserted only that 2.19 still qualifies and 2.52
    still does not. That catches a kept section ceasing to qualify, and a
    *named* rejected one starting to -- and misses any section nobody
    thought to name, which is the whole population the enumeration exists
    to decide about. Checking the two cases you already believe is how a
    test comes to agree with you rather than with the source.

    **What this does not cover, stated rather than implied:** a
    thirty-ninth section arriving in a future revision is not in the
    fixture and cannot be. That case is caught by `_require_sections`,
    which fails when the document carries a different number of sections
    than `EXPECTED_SECTIONS` records -- deliberately forcing a person to
    read the diff. The two guards are complementary: this one asks whether
    the sections we have still sort the way we said, that one asks whether
    the sections we have are still the sections there are.
    """
    parsed = {s.number: s.text.lower() for s in sections(excerpt, strict=False)}
    hub = parsed["2.16"]

    def independent_of_the_hub(number):
        return [t for t in _MACHINERY if t in parsed[number] and t not in hub]

    qualifying = {n for n in parsed if n != "2.16" and independent_of_the_hub(n)}

    assert len(parsed) > 2, "a sweep over one section proves nothing"
    assert qualifying | {"2.16"} == set(CONTROL_SECTIONS), (
        f"the independence test no longer sorts these sections the way "
        f"CONTROL_SECTIONS says it does. Qualifying now: {sorted(qualifying)}; "
        f"CONTROL_SECTIONS: {sorted(CONTROL_SECTIONS)}. Adding or removing a "
        f"control is a decision for a person -- read the sections, then "
        f"change the enumeration, not this test."
    )

    # Named for the reader, after the sweep rather than instead of it:
    # 2.19 imposes duties 2.16 does not -- encryption at rest, separated
    # decryption tools, a backup copy, labelled sealed containers. 2.52
    # mentions sanitization and uses no term 2.16 lacks, because it says
    # "apply 2.16 to researchers", which is a requirement of 2.16.
    assert independent_of_the_hub("2.19")
    assert independent_of_the_hub("2.52") == []


# --------------------------------------------------------------------------
# Requirements
# --------------------------------------------------------------------------


def test_a_roman_numeral_does_not_open_a_requirement(excerpt):
    """`(i)` is roman one here, not the ninth letter, and eCFR gives no
    structural hint -- every marker is a flat sibling `<P>`.

    Read as a letter it gave 2.16 a phantom `2.16(i)` that stole the
    paper-records list out of `2.16(a)`, and gave 2.19 **two** requirements
    numbered `2.19(i)` plus a `2.19(v)`.
    """
    catalog = parse_part2(excerpt, strict=False)

    ids = {e.enhancement_id for c in catalog for e in c.enhancements}
    assert ids == {"2.16(a)", "2.16(b)", "2.19(a)", "2.19(b)"}


def test_two_requirements_may_not_answer_to_one_citation(excerpt):
    """The guard that would have caught the roman-numeral bug without a
    person reading the output. Duplicate ids are well-formed, plausible,
    and wrong only to a reader who follows the citation."""
    catalog = parse_part2(excerpt, strict=False)
    catalog[1].enhancements.append(catalog[1].enhancements[0])

    with pytest.raises(ValueError) as caught:
        _require_unique_ids(catalog)

    assert "more than one requirement under the same id" in str(caught.value)
    assert "2.19(a)" in str(caught.value)


def test_nesting_stays_inside_the_requirement_it_qualifies(excerpt):
    """`(a)(1)(i)(A)` qualifies `(a)`; it is not a sibling of it. The whole
    paper-and-electronic-records list belongs to `2.16(a)`."""
    catalog = {c.control_id: c for c in parse_part2(excerpt, strict=False)}
    a = next(e for e in catalog["2.16"].enhancements if e.enhancement_id == "2.16(a)")

    for nested in (
        "Transferring and removing such records",
        "sanitizing the hard copy media",
        "Creating, receiving, maintaining, and transmitting such records",
        "45 CFR 164.514(b)",
    ):
        assert nested in a.description, nested


def test_a_requirement_keeps_the_italic_run_as_its_title(excerpt):
    """eCFR names the paragraph in an `<I>`, and that name is the
    requirement's title rather than the first words of its body."""
    catalog = {c.control_id: c for c in parse_part2(excerpt, strict=False)}
    b = next(e for e in catalog["2.19"].enhancements if e.enhancement_id == "2.19(b)")

    assert b.title == "Special procedure where retention period required by law"
    assert not b.description.startswith("Special procedure")


def test_the_special_procedure_carries_what_makes_2_19_a_control(excerpt):
    """These are the duties absent from 2.16 -- the reason 2.19 is a
    control rather than a requirement of it. If they stop appearing here,
    CONTROL_SECTIONS needs re-deciding, not repairing."""
    catalog = {c.control_id: c for c in parse_part2(excerpt, strict=False)}
    b = next(e for e in catalog["2.19"].enhancements if e.enhancement_id == "2.19(b)")

    for duty in (
        "encryption to encrypt the data at rest",
        "backup copy",
        "Within one year",
        "climate-controlled",
        "decryption tools",
    ):
        assert duty in b.description, duty


# --------------------------------------------------------------------------
# Nothing emitted is empty, at any level
# --------------------------------------------------------------------------


def test_the_reserved_paragraph_is_in_the_source(excerpt):
    """The first half of the two-sided assertion. If the CFR ever fills
    `(b)(1)(i)(B)` in, this fails and the omission below must be revisited
    rather than left quietly dropping real text."""
    source = {s.number: s.text for s in sections(excerpt, strict=False)}

    assert "(B) [Reserved]" in source["2.19"]


def test_the_reserved_paragraph_is_absent_from_the_requirements(excerpt):
    """The second half. It sits four levels down, so it would never appear
    as an entry -- it would be folded into `2.19(b)`'s prose, where an
    empty statement inherits the credibility of the real requirements
    around it. Which is why this is checked on the text, not on the ids."""
    catalog = {c.control_id: c for c in parse_part2(excerpt, strict=False)}
    b = next(e for e in catalog["2.19"].enhancements if e.enhancement_id == "2.19(b)")

    assert "Reserved" not in b.description


def test_no_requirement_is_empty(excerpt):
    """80's ruling generalised: nothing emitted is empty, at any level."""
    for control in parse_part2(excerpt, strict=False):
        assert control.title.strip()
        for requirement in control.enhancements:
            assert requirement.description.strip(), requirement.enhancement_id


def test_a_missing_control_section_refuses_rather_than_shipping_one(excerpt):
    """A catalog of two that silently becomes a catalog of one is the same
    failure as an empty parse, one level up."""
    without = excerpt.replace('N="2.19"', 'N="2.199"')

    with pytest.raises(ValueError) as caught:
        parse_part2(without)

    assert "2.19" in str(caught.value)


# --------------------------------------------------------------------------
# Guards that Part 2's own text never exercises
# --------------------------------------------------------------------------


def test_a_list_that_descended_to_digits_does_not_step_back_up_to_a_letter():
    """`(h)`, `(1)`, `(2)`, `(i)` -- the case both readings fit.

    **Constructed, because Part 2 never produces it.** Its sections stop
    well before a ninth lettered paragraph, so `(i)` here is always roman
    and the successor rule alone is enough. That is exactly why this test
    exists: a mutation removing the `saw_digit` guard passed the entire
    suite against real text, which means the guard was being trusted and
    not held.

    A section that did reach `(h)` and then descended to digits would, with
    the guard gone, read the roman `(i)` beneath it as a ninth sibling --
    silently moving a nested obligation up a level.
    """
    assert _opens_requirement("i", previous="h", saw_digit=False) is True
    assert _opens_requirement("i", previous="h", saw_digit=True) is False
    # Not a successor at all: roman one under (c), which is the real shape
    # everywhere in Part 2.
    assert _opens_requirement("i", previous="c", saw_digit=True) is False
    # An ordinary letter is unaffected by the digit guard -- only i, v and
    # x are ambiguous, and treating (d) as roman cost Part 171 three
    # conditions when it was tried there.
    assert _opens_requirement("d", previous="c", saw_digit=True) is True


def test_the_duplicate_id_guard_is_wired_into_the_parse(excerpt, monkeypatch):
    """The guard is unreachable by data and that is not a reason to leave
    it unheld.

    With the successor rule in place, `_requirements` cannot emit two
    paragraphs under one id -- each letter increments. So the guard is
    defence against the successor rule regressing, and the only honest way
    to test it is to regress the successor rule.

    Testing `_require_unique_ids` directly, which is what this suite did
    first, passes happily when the call is deleted from `parse_part2`. A
    guard nothing calls is a comment.
    """
    monkeypatch.setattr(
        "policyforge.ingest.part2_loader._opens_requirement",
        lambda letter, previous, saw_digit: True,
    )

    with pytest.raises(ValueError) as caught:
        parse_part2(excerpt, strict=False)

    assert "more than one requirement under the same id" in str(caught.value)
