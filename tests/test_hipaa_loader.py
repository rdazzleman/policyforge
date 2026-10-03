"""ingest/hipaa_loader.py tests, against a real eCFR fixture (45 CFR 164
Subpart C, tests/fixtures/ecfr_45cfr164_subpart_c.xml — the actual
regulation text as fetched from eCFR's versioner API, not synthetic data).
Expected values here were hand-verified against that fixture's text."""

from __future__ import annotations

from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "ecfr_45cfr164_subpart_c.xml"


def _load():
    from policyforge.ingest.hipaa_loader import parse_hipaa_security_rule

    xml = FIXTURE.read_text(encoding="utf-8")
    controls = parse_hipaa_security_rule(xml)
    by_id = {}
    for c in controls:
        by_id[c.control_id] = c
        for e in c.enhancements:
            by_id[e.enhancement_id] = e
    return controls, by_id


def test_parses_expected_number_of_controls():
    controls, _ = _load()
    # 34 top-level items across §164.306-318 (Definitions excluded).
    assert len(controls) == 34


def test_excludes_definitions_section():
    controls, _ = _load()
    assert not any(c.control_id.startswith("164.304") for c in controls)
    assert not any(c.title == "Access" for c in controls)  # a defined term, not a control


def test_security_management_process_standard_and_specs():
    _, by_id = _load()

    standard = by_id["164.308(a)(1)(i)"]
    assert standard.title == "Security management process"
    assert standard.framework == "HIPAA Security Rule"
    assert "prevent, detect, contain, and correct" in standard.control_statement
    assert [e.enhancement_id for e in standard.enhancements] == [
        "164.308(a)(1)(ii)(A)",
        "164.308(a)(1)(ii)(B)",
        "164.308(a)(1)(ii)(C)",
        "164.308(a)(1)(ii)(D)",
    ]

    risk_analysis = by_id["164.308(a)(1)(ii)(A)"]
    assert risk_analysis.title == "Risk analysis"
    assert risk_analysis.baseline == "Required"
    assert "potential risks and vulnerabilities" in risk_analysis.description


def test_workforce_security_specs_are_addressable():
    _, by_id = _load()
    spec = by_id["164.308(a)(3)(ii)(A)"]
    assert spec.title == "Authorization and/or supervision"
    assert spec.baseline == "Addressable"


def test_implementation_specs_marked_required_at_roman_level_not_just_capital():
    """§164.310 tags Required/Addressable at the roman-numeral level
    directly (no capital-letter sub-level), unlike §164.308 — the loader
    must not assume a fixed citation depth for specs."""
    _, by_id = _load()
    spec = by_id["164.310(d)(2)(i)"]
    assert spec.title == "Disposal"
    assert spec.baseline == "Required"


def test_ambiguous_letter_after_number_resolves_to_new_top_level_paragraph():
    """§164.312(d) ('Person or entity authentication') follows §164.312(c)(2)
    — a lone lowercase 'd' here must resolve to a new top-level paragraph,
    not a stray roman/capital numeral nested under (c)(2)."""
    _, by_id = _load()
    control = by_id["164.312(d)"]
    assert control.title == "Person or entity authentication"
    assert control.control_id == "164.312(d)"


def test_compound_paragraph_with_embedded_em_dash_citation_splits_correctly():
    """§164.314(a)(2) reads '(2) Implementation specifications (Required)
    —(i) Business associate contracts...' as a single <P> — the embedded
    '(i)' citation must still be recognized and tracked."""
    _, by_id = _load()
    assert "164.314(a)(2)(i)" in by_id
    contract = by_id["164.314(a)(2)(i)"]
    assert contract.title == "Business associate contracts"


def test_uncaptioned_list_items_are_appended_not_dropped():
    """The bare, un-italicized (A)/(B)/(C) list items under
    §164.314(a)(2)(i) carry real requirement text and must not be
    silently discarded just because they lack their own <I> lead-in."""
    _, by_id = _load()
    text = by_id["164.314(a)(2)(i)"].control_statement
    assert "Comply with the applicable requirements of this subpart" in text
    assert "Report to the covered entity any security incident" in text


def test_alternative_compliance_paths_are_separate_controls():
    """§164.314(a)(1) says the contract 'must meet the requirements of
    paragraph (a)(2)(i), (a)(2)(ii), or (a)(2)(iii) ... as applicable' —
    these are alternative ways to satisfy one standard, not a stacked list
    of required items, so each is correctly its own Control rather than
    an enhancement of a single one."""
    _, by_id = _load()
    assert "164.314(a)(2)(i)" in by_id
    assert "164.314(a)(2)(ii)" in by_id
    assert "164.314(a)(2)(iii)" in by_id


def test_special_characters_decoded_correctly():
    _, by_id = _load()
    text = by_id["164.314(a)(2)(i)"].control_statement
    assert "—" in text  # em dash, not the literal XML entity
    assert "�" not in text


def test_raises_on_missing_subpart_c():
    from policyforge.ingest.hipaa_loader import parse_hipaa_security_rule

    try:
        parse_hipaa_security_rule("<DIV5>no subpart c here</DIV5>")
    except ValueError as exc:
        assert "Subpart C" in str(exc)
    else:
        raise AssertionError("expected ValueError when Subpart C is absent")


# --------------------------------------------------------------------------
# Nothing emitted is empty
# --------------------------------------------------------------------------


def test_the_source_carries_164_314_a_2_as_a_run_in_heading():
    """First half of the two-sided assertion: the paragraph really is a
    heading that delegates to its children.

    If eCFR ever gives `(a)(2)` text of its own, this fails and the
    omission below has to be revisited rather than left quietly dropping
    a real requirement.
    """
    xml = FIXTURE.read_text(encoding="utf-8")

    # The em dash is the entity `&#x2014;` in the raw fixture, not a decoded
    # character: this reads the file as published rather than as parsed, so
    # it must match what is actually on disk.
    assert (
        "<P>(2) <I>Implementation specifications (Required)</I>"
        "&#x2014;(i) <I>Business associate contracts.</I>" in xml
    ), "164.314(a)(2) is no longer the run-in heading this omission assumes"


def test_the_catalog_has_no_entry_for_164_314_a_2():
    """Second half. It carried a title and no text: an entry that counts,
    renders and crosswalks while saying nothing.

    Worse than a blank row -- a user citing `164.314(a)(2)` cited a title,
    and an assessor following that citation found nothing, while the
    obligation they wanted sat at `(a)(2)(i)`.
    """
    _, by_id = _load()

    assert "164.314(a)(2)" not in by_id


def test_dropping_it_loses_no_obligation():
    """Its entire content is its children, and all three are emitted as
    controls in their own right."""
    _, by_id = _load()

    for citation, opening in (
        ("164.314(a)(2)(i)", "The contract must provide"),
        ("164.314(a)(2)(ii)", "arrangement"),
        ("164.314(a)(2)(iii)", "subcontractor"),
    ):
        assert citation in by_id, citation
        assert opening.lower() in by_id[citation].control_statement.lower(), citation


def test_the_identically_titled_specification_that_does_state_something_is_kept():
    """`164.314(b)(2)` has the same title as the dropped entry and a real
    description, because there the text follows the label instead of
    delegating. The contrast is what shows this is not the phrase being
    mishandled."""
    _, by_id = _load()

    kept = by_id["164.314(b)(2)"]

    assert kept.title == "Implementation specifications"
    assert "plan documents of the group health plan" in kept.description
    assert len(kept.description) > 500


def test_a_section_the_pattern_no_longer_matches_raises():
    """**External extent — the question no other test in this file asks.**

    Everything else here asks whether an entry is right. None asks
    whether they are *all here*. `_SECTION_RE` pins the identifier as
    `164\\.\\d+`, so a renumbering makes it match fewer sections and
    return a shorter catalog — internally consistent, well-formed, and
    missing a Standard.

    Checked against the document rather than a count: the comparison
    population is gathered by a pattern that does not know what a section
    identifier looks like, so neither side can go stale.
    """
    import pytest

    from policyforge.ingest.hipaa_loader import parse_hipaa_security_rule

    xml = FIXTURE.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match=r"were recognised"):
        parse_hipaa_security_rule(xml.replace('N="164.312"', 'N="164.312a"'))

    # The guard must still ALLOW the real document, or it is just
    # refusing everything — which looks identical to a broken parser.
    assert parse_hipaa_security_rule(xml)


def test_no_implementation_specification_is_empty():
    """The general rule rather than the one instance, so the next
    run-in heading is caught without anyone noticing it."""
    controls, _ = _load()

    empty = [
        e.enhancement_id for c in controls for e in c.enhancements if not e.description.strip()
    ]

    assert empty == []


def test_a_heading_shaped_control_may_still_have_no_statement():
    """Deliberately **not** covered by the rule above. A control that is
    purely structural legitimately carries no statement of its own, and
    "fix" it and you would start dropping real sections."""
    controls, _ = _load()

    assert controls, "no controls parsed"
    # The rule is about enhancements only; assert the parser still emits
    # controls regardless of whether they carry a statement.
    assert any(c.control_statement.strip() for c in controls)


# --------------------------------------------------------------------------
# The dropped lead-in's relationship is carried (#268, code half)
# --------------------------------------------------------------------------

_LEAD_IN = "must, in accordance with &#xA7; 164.306:</P>"
#: Hand-verified against the fixture: the four framed sections' standards.
_FRAMED = {
    "164.308(a)": 9,
    "164.310": 4,
    "164.312": 5,
    "164.316": 2,
}


def _framed_by(control_id: str) -> str | None:
    for prefix in _FRAMED:
        if control_id.startswith(prefix + "("):
            return prefix
    return None


def test_every_standard_a_lead_in_frames_names_164_306_and_no_other_does():
    """80's ruling on #268: the statements stay; each control in the framed
    sections names `164.306` as related, as the source cites it."""
    controls, _ = _load()
    framed = [c for c in controls if _framed_by(c.control_id)]
    assert {p: sum(_framed_by(c.control_id) == p for c in framed) for p in _FRAMED} == _FRAMED
    assert len(framed) + sum(len(c.enhancements) for c in framed) == 58  # requirements
    assert all(c.related_controls == ["164.306"] for c in framed)
    rest = [c for c in controls if not _framed_by(c.control_id)]
    assert rest and all(c.related_controls == [] for c in rest)
    # 164.308(b) sits outside the (a) lead-in, and 164.306 does not relate to itself.
    assert any(c.control_id.startswith("164.308(b)") for c in rest)


def test_the_statements_do_not_carry_the_lead_in():
    controls, _ = _load()
    assert not any("in accordance with § 164.306" in c.control_statement for c in controls)


def test_the_relationship_is_read_from_the_lead_in_not_typed():
    """Change the section the lead-in cites and the relation follows it.
    § 164.308 is chosen because it yields controls, so the parse is allowed."""
    from policyforge.ingest.hipaa_loader import parse_hipaa_security_rule

    xml = FIXTURE.read_text(encoding="utf-8")
    one = xml.replace(
        f"<P>A covered entity or business associate {_LEAD_IN}",
        "<P>A covered entity or business associate must, in accordance with &#xA7; 164.308:</P>",
        1,
    )
    assert one != xml
    by_id = {c.control_id: c for c in parse_hipaa_security_rule(one)}
    assert by_id["164.310(a)(1)"].related_controls == ["164.308"]
    assert by_id["164.312(a)(1)"].related_controls == ["164.306"]


def test_a_lead_in_citing_two_sections_carries_both():
    from policyforge.ingest.hipaa_loader import parse_hipaa_security_rule

    xml = FIXTURE.read_text(encoding="utf-8").replace(
        f"<P>A covered entity or business associate {_LEAD_IN}",
        "<P>A covered entity or business associate must, in accordance with "
        "&#xA7;&#xA7; 164.306 and 164.314:</P>",
        1,
    )
    by_id = {c.control_id: c for c in parse_hipaa_security_rule(xml)}
    assert by_id["164.310(a)(1)"].related_controls == ["164.306", "164.314"]


def test_a_cited_section_with_no_control_here_is_refused():
    import pytest

    from policyforge.ingest.hipaa_loader import parse_hipaa_security_rule

    xml = FIXTURE.read_text(encoding="utf-8").replace(_LEAD_IN, _LEAD_IN.replace("306", "399"), 1)
    with pytest.raises(ValueError, match=r"no control here: \['164.399'\]"):
        parse_hipaa_security_rule(xml)


def test_a_lead_in_that_frames_nothing_is_refused_not_dropped():
    """A lead-in at (a) with no control under (a): the relationship would
    vanish with nothing saying so. (Moving the fixture's `(a)` token does not
    build this: it renumbers every control after it, which stay framed.)"""
    import pytest

    from policyforge.ingest.hipaa_loader import parse_hipaa_security_rule

    def subpart(lead_in_at: str) -> str:
        return (
            '<DIV6 N="C" TYPE="SUBPART">'
            '<DIV8 N="164.306" TYPE="SECTION"><HEAD>&#xA7; 164.306 General rules.</HEAD>'
            "<P>(a) <I>General requirements.</I> Ensure the confidentiality.</P></DIV8>"
            '<DIV8 N="164.310" TYPE="SECTION"><HEAD>&#xA7; 164.310 Physical safeguards.</HEAD>'
            f"<P>{lead_in_at} A covered entity must, in accordance with &#xA7; 164.306:</P>"
            "<P>(b) <I>Standard: Workstation use.</I> Implement policies.</P></DIV8></DIV6>"
        )

    with pytest.raises(ValueError, match=r"frames 0 control"):
        parse_hipaa_security_rule(subpart("(a)"))
    # The control: the same lead-in with no token frames the section, and passes.
    (_, workstation) = parse_hipaa_security_rule(subpart(""))
    assert workstation.control_id == "164.310(b)" and workstation.related_controls == ["164.306"]


def test_a_lead_in_is_judged_by_what_it_cites_not_by_its_section_sign():
    """1d on #355: `section 164.306` with no `§` is still carried, and a `§`
    citing something this loader cannot read is refused, never skipped."""
    import pytest

    from policyforge.ingest.hipaa_loader import parse_hipaa_security_rule

    xml = FIXTURE.read_text(encoding="utf-8")
    target = f"<P>A covered entity or business associate {_LEAD_IN}"
    no_sign = xml.replace(target, target.replace("&#xA7; 164.306", "section 164.306"), 1)
    assert no_sign != xml
    by_id = {c.control_id: c for c in parse_hipaa_security_rule(no_sign)}
    assert by_id["164.310(a)(1)"].related_controls == ["164.306"]

    elsewhere = xml.replace(target, target.replace("164.306", "160.103"), 1)
    with pytest.raises(ValueError, match=r"cites no section this loader can read"):
        parse_hipaa_security_rule(elsewhere)
