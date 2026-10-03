"""The ONC certification catalog: re-landed after #152's fabricated criteria.

**The review question these tests answer is 1d's, from #163: name an entry
that is NOT first in its group, and show it against the source.** #152's
tests pinned `(a)(1)`, `(b)(1)`, `(d)(1)`, `(g)(10)`, `(j)(20)` -- every one
correct, because the first criterion in each category took a different code
path. Id-shape, distinct-title, non-empty and count checks all pass on
fabricated criteria. So the central test here is EQUALITY with an
independently agreed set, and the rest aim at entries no rule gets for free.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
from pathlib import Path

import pytest

from policyforge.ingest.onc_loader import (
    AGREED_CRITERIA,
    FRAMEWORK,
    OncParseError,
    parse_onc_criteria,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "ecfr_45cfr170_315.xml"
CATALOG = ROOT / "data" / "frameworks" / "cfr-170-315-onc-certification"
AS_OF = dt.date(2026, 9, 22)  # the eCFR date the fixture is current to


@pytest.fixture(scope="module")
def xml() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parsed(xml):
    return parse_onc_criteria(xml, as_of=AS_OF)


def _by_id(parsed) -> dict:
    return {c.control_id: c for c in parsed[0]}


# ---- equality with the independent set --------------------------------------------


def test_the_live_criteria_equal_the_independently_agreed_set(parsed):
    assert {c.control_id for c in parsed[0]} == AGREED_CRITERIA


def test_reading_the_text_alone_is_refused(xml):
    """**#152's mechanism, as an input.** Strip the italics from the level-5
    and level-6 markers -- what `itertext()` did -- and a nested `(1)` is
    indistinguishable from a criterion. The tracker then fails loudly, or
    the equality refuses; either way nothing is written."""
    flattened = re.sub(r"\(<I>([0-9a-z]+)</I>\)", r"(\1)", xml)
    assert flattened != xml
    with pytest.raises(OncParseError):
        parse_onc_criteria(flattened, as_of=AS_OF)


def test_a_lost_criterion_is_refused_with_its_id(xml):
    """Drop `(e)(3)`'s paragraph: the parse is one short, and the refusal
    names the lost id rather than shipping a smaller catalog."""
    paragraph = re.search(r"<P>\(3\) <I>Patient health information capture\.</I>.*?</P>", xml, re.S)
    assert paragraph, "the fixture no longer holds (e)(3) as expected"
    with pytest.raises(OncParseError, match=r"lost: \['170\.315\(e\)\(3\)'\]"):
        parse_onc_criteria(xml.replace(paragraph.group(0), "", 1), as_of=AS_OF)


def test_a_marker_that_fits_no_level_is_an_error_not_a_guess(xml):
    """`(q)` directly after a criterion is no level's successor, first child
    or repeat. The tracker refuses instead of placing it somewhere."""
    first_criterion = re.search(r"<P>\(2\) <I>", xml)
    broken = (
        xml[: first_criterion.start()]
        + "<P>(q) Not a real marker.</P>"
        + xml[first_criterion.start() :]
    )
    with pytest.raises(OncParseError, match=r"marker \(q\) fits no level"):
        parse_onc_criteria(broken, as_of=AS_OF)


# ---- entries that are NOT first in their group -------------------------------------


@pytest.mark.parametrize(
    ("criterion", "title"),
    [
        ("170.315(b)(2)", "Clinical information reconciliation and incorporation"),
        ("170.315(b)(7)", "Security tags—summary of care—send"),
        ("170.315(d)(13)", "Multi-factor authentication"),
        ("170.315(e)(3)", "Patient health information capture"),
        ("170.315(g)(33)", "Provider prior authorization API—prior authorization support"),
        ("170.315(j)(21)", "Subscriptions—client"),
    ],
)
def test_a_non_first_criterion_is_the_one_the_regulation_names(parsed, xml, criterion, title):
    """Structurally chosen, not reputationally: none is first in its
    category, so none gets the first-criterion code path for free. Each
    title is checked against the fixture's own text as well."""
    control = _by_id(parsed)[criterion]
    assert control.title == title
    assert title in re.sub(r"<[^>]+>", "", xml)


def test_b_11_is_titled_though_the_regulation_does_not_italicise_it(parsed):
    """`(11) Decision support interventions—` has no italic heading, which
    defeated the successor-plus-italics rule. Titled from its text."""
    assert _by_id(parsed)["170.315(b)(11)"].title == "Decision support interventions"


def test_no_title_has_a_sub_item_s_shape(parsed):
    """9b's tell from #163: the fabricated criteria were titled like
    sub-items ("Vital Signs;"). Nothing here ends in a semicolon."""
    assert not [c.control_id for c in parsed[0] if c.title.rstrip().endswith(";")]


def test_a_sub_item_shaped_title_is_refused_even_when_the_set_is_right(xml):
    """The `;` guard, reached: real data never produces one, so without this
    the guard could be deleted and nothing would notice. Changing one real
    heading keeps the criterion SET equal, so only this guard can refuse it."""
    changed = xml.replace(
        "<I>Patient health information capture.</I>",
        "<I>Patient health information capture;</I>",
        1,
    )
    assert changed != xml
    with pytest.raises(OncParseError, match="ending in ';'"):
        parse_onc_criteria(changed, as_of=AS_OF)


def test_both_dated_versions_of_b_2_iv_are_in_its_statement(parsed):
    statement = _by_id(parsed)["170.315(b)(2)"].control_statement
    # eCFR carries two dated versions of (b)(2)(iv); both belong to (b)(2).
    assert "on and after December 31, 2022." in statement
    assert "for the time period up to and including December 31, 2025" in statement
    assert statement.count("System verification") == 2


# ---- what is left out, and why ------------------------------------------------------


def test_a_criterion_whose_first_sub_paragraph_is_reserved_is_live(parsed):
    """The `is_reserved` defect from #163: `(b)(3)` opens with a reserved
    first sub-paragraph and is a real criterion. Reserved-ness is decided
    by what the marker governs at the criterion's OWN level."""
    _, excluded = parsed
    assert "170.315(b)(3)" in _by_id(parsed)
    assert "170.315(b)(3)" not in excluded["reserved"]
    assert "[Reserved]" in _by_id(parsed)["170.315(b)(3)"].control_statement


def test_reserved_criteria_are_reported_including_both_ranges(parsed):
    reserved = set(parsed[1]["reserved"])
    assert {"170.315(a)(6)", "170.315(b)(5)", "170.315(e)(2)", "170.315(g)(8)"} <= reserved
    assert {f"170.315(g)({n})" for n in range(11, 31)} <= reserved
    assert {f"170.315(j)({n})" for n in range(1, 20)} <= reserved
    assert not reserved & AGREED_CRITERIA


def test_an_expired_criterion_is_left_out_as_of_its_date(xml, parsed):
    """`(a)(9)`'s own text says it expired on 1 January 2025. As of the
    fixture's eCFR date it is excluded; parsed as of 2024 it is live, and
    that parse is then REFUSED against the agreed set -- which is how a
    date error cannot quietly re-add it."""
    assert parsed[1]["expired"] == ["170.315(a)(9)"]
    with pytest.raises(OncParseError, match=r"invented: \['170\.315\(a\)\(9\)'\]"):
        parse_onc_criteria(xml, as_of=dt.date(2024, 12, 31))


# ---- the catalog, keys, citations, README --------------------------------------------


def test_the_shipped_catalog_is_the_parse_of_the_fixture(parsed):
    shipped = json.loads((CATALOG / "controls.json").read_text(encoding="utf-8"))
    assert shipped == [dataclasses.asdict(c) for c in parsed[0]]


def test_the_catalog_keys_to_itself_and_its_siblings_do_not():
    from policyforge.mapping.crosswalk import normalize_framework

    assert normalize_framework(FRAMEWORK) == "cfr-170-315-onc-certification"
    assert normalize_framework("ONC Health IT Certification Program") == "onc"


def test_a_criterion_citation_resolves_to_this_catalog(parsed):
    from policyforge.content.tags import source_tags
    from policyforge.mapping.crosswalk import normalize_framework
    from policyforge.topics.satisfies import parse_citations

    tag = "[ONC Certification Criteria 170.315(g)(10)]"
    assert source_tags(tag) == [tag]
    ids = {normalize_framework(FRAMEWORK): {c.control_id for c in parsed[0]}}
    found = parse_citations(f"- Offer a patient API. {tag}", [FRAMEWORK], ids)
    assert [(f, r) for f, r, _, _ in found] == [(FRAMEWORK, "170.315(g)(10)")]


def test_the_pending_proposal_is_described_as_a_subset_and_its_examples_are_live(parsed):
    """The README records ONC's pending proposed rule (90 FR 60970) (#179, 80).
    **Its first version said ONC proposed removing (a)(14), (h)(1) and
    (h)(2)** -- the three the FY2027 IPPS rule names for the Base EHR
    definition -- which read as the whole proposal; the rule marks many more
    (policyforge-9b, on #307). So the paragraph must cite the rule, call the
    three a subset, and give no count nobody has measured; and the three it
    names must still be live, so a re-pin applying the final rule fails here
    and the paragraph is rewritten in the same change."""
    import re

    readme = " ".join((CATALOG / "README.md").read_text(encoding="utf-8").split())
    start = readme.index("Pending, and not applied:")
    paragraph = readme[start : readme.index("absorbing it.", start)]
    assert "90 FR 60970" in paragraph, paragraph
    # Cited by its own title, not "HTI-5". 90 FR 60970 never names itself
    # so (0 hits in its text; 80, on #307). The label does appear elsewhere:
    # 91 FR 49570, the FY2027 IPPS rule, calls it "the HTI-5 proposed rule"
    # (policyforge-9b, correcting 80). So it is a sourced nickname, but it is
    # not the rule's own identifier, and the rule's title and FR page are.
    assert "HTI-5" not in paragraph, paragraph
    assert "subset, not the whole proposal" in paragraph, paragraph
    assert "No count of the proposed removals is given" in paragraph, paragraph
    named = {f"170.315{n}" for n in re.findall(r"\([a-z]\)\(\d+\)", paragraph)}
    live = {c.control_id for c in parsed[0]}
    assert named == {"170.315(a)(14)", "170.315(h)(1)", "170.315(h)(2)"}, named
    assert named <= live, f"no longer live, so rewrite the pending paragraph: {named - live}"


def test_the_readme_s_counts_are_the_catalog_s(parsed):
    readme = " ".join((CATALOG / "README.md").read_text(encoding="utf-8").split())
    controls = parsed[0]
    families = {}
    for c in controls:
        families.setdefault((c.family_abbr, c.family), 0)
        families[(c.family_abbr, c.family)] += 1
    assert f"{len(controls)} live criteria across {len(families)} categories" in readme
    for (abbr, family), count in families.items():
        assert f"| `{abbr}` | {family} | {count} |" in readme, (abbr, family, count)


# ---- the ETL, on the user's path ------------------------------------------------------
#
# The real command, with only the network fetch replaced by the fixture: the
# command takes no saved-XML option (see the last test), so this is the one
# seam, and everything past it -- parse, refusal, write, provenance -- runs.


def _etl(monkeypatch, tmp_path, xml_text: str):
    from click.testing import CliRunner

    import policyforge.ingest.onc_loader as onc
    from policyforge import cli as cli_mod

    monkeypatch.setattr(onc, "fetch_part_xml", lambda *, date=None: xml_text)
    out = tmp_path / "out" / "controls.json"
    result = CliRunner().invoke(cli_mod.cli, ["etl-onc", "--date", "2026-09-22", "--out", str(out)])
    return result, out


def test_etl_writes_the_shipped_catalog(monkeypatch, tmp_path, xml):
    result, out = _etl(monkeypatch, tmp_path, xml)
    assert result.exit_code == 0, result.output
    assert "Excluded 1 expired: 170.315(a)(9)" in result.output
    shipped = (CATALOG / "controls.json").read_bytes().replace(b"\r\n", b"\n")
    assert out.read_bytes() == shipped


def test_etl_refuses_a_changed_criterion_set_cleanly(monkeypatch, tmp_path, xml):
    paragraph = re.search(r"<P>\(3\) <I>Patient health information capture\.</I>.*?</P>", xml, re.S)
    result, out = _etl(monkeypatch, tmp_path, xml.replace(paragraph.group(0), "", 1))
    assert result.exit_code == 1, result.output
    assert "Error: the parsed criteria differ from the independently agreed set" in result.output
    assert "Traceback" not in result.output
    assert not out.exists(), "a refused parse wrote a catalog"


def test_etl_takes_no_saved_xml_option():
    """**The XML parser's safety premise, pinned.** `onc_loader` uses the
    stdlib parser because eCFR is the only source (see its import comment
    and `info_blocking.py`'s measurement). An option that read a local XML
    file would point it at user-supplied XML -- #179's first draft had one,
    and semgrep's use-defused-xml finding is what caught it. Adding one back
    needs `defusedxml`, and this test says so."""
    from policyforge import cli as cli_mod

    options = {param.name for param in cli_mod.cli.commands["etl-onc"].params}
    assert options == {"date", "out"}, f"etl-onc gained an input: {options}"
