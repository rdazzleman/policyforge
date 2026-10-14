"""Every reader of tags accepts the citation form the user confirmed (#17).

80's comment 5928 on #17, the user confirming `[NIST IR-1, IR-8 |
ARC-AMPE]`: 800-53's ids appear once, under `NIST`; a tailoring of 800-53 is
named as a baseline with no ids, unless it adds an enhancement 800-53
doesn't select (`ARC-AMPE RA-5(11)`); other frameworks keep their own ids.
80's rulings 6042 and 6044: `NIST` alone names 800-53 and never another NIST
catalog; a bare framework name is a baseline reference only inside a tag
that also cites something, and a bracket that is nothing but a name stays
reported.

Expected values are written out by hand from those rulings, not derived
from the readers under test.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from policyforge.content.tags import (
    is_citation_tag,
    is_unresolved_citation,
    source_tags,
    tag_parts,
)
from policyforge.frameworks.drift import documents_citing
from policyforge.ingest.schema import load_controls
from policyforge.topics.satisfies import catalog_scope, document_evidence, parse_citations

FRAMEWORKS = Path(__file__).resolve().parent.parent / "data" / "frameworks"
FORM = "[HIPAA Security Rule 164.308(a)(1) | NIST IR-1, IR-8 | ARC-AMPE]"


def _controls(*directories: str) -> list:
    return [c for d in directories for c in load_controls(FRAMEWORKS / d / "controls.json")]


@pytest.fixture(scope="module")
def run():
    """A run drawn from 800-53, a tailoring of it, HIPAA and a second NIST
    catalog, so `NIST` is not the only NIST name in scope."""
    controls = _controls("nist-800-53-r5", "arc-ampe", "hipaa-security-rule", "nist-800-171-r3")
    return SimpleNamespace(controls=controls, scope=catalog_scope(controls))


def _evidence(body: str, run):
    document = SimpleNamespace(relative_path="standards/ir.md", title="IR", body=body)
    return document_evidence(document, controls=run.controls, crosswalk={})


# ---- the splitter ------------------------------------------------------------


def test_a_part_listing_ids_after_one_name_is_one_citation_per_id():
    parts = tag_parts("[NIST IR-1, IR-8]", ["NIST 800-53"])
    assert [(p.framework, p.rest) for p in parts] == [("NIST", "IR-1"), ("NIST", "IR-8")]


def test_a_level_list_splits_like_any_other():
    parts = tag_parts("[HITRUST 11.a, 11.c Level 2]", ["HITRUST"])
    assert [p.citation for p in parts] == ["HITRUST 11.a", "HITRUST 11.c Level 2"]


@pytest.mark.parametrize(
    "tag, citation",
    [
        ("[GovRAMP IR-4(1) Moderate, High]", "GovRAMP IR-4(1) Moderate, High"),
        ("[HIPAA 164.310(a)(2)(i) Addressable, Required]", None),
    ],
)
def test_a_qualifier_list_stays_one_citation(tag, citation):
    """A comma starts a new citation only before an id-shaped item."""
    parts = tag_parts(tag, ["GovRAMP", "HIPAA"])
    assert len(parts) == 1, parts
    assert parts[0].citation == (citation or tag.strip("[]"))


def test_a_framework_named_alone_is_a_baseline_part():
    parts = tag_parts(FORM, ["HIPAA Security Rule", "NIST 800-53", "ARC-AMPE"])
    assert [(p.citation, p.baseline) for p in parts] == [
        ("HIPAA Security Rule 164.308(a)(1)", False),
        ("NIST IR-1", False),
        ("NIST IR-8", False),
        ("ARC-AMPE", True),
    ]


def test_a_multi_word_name_alone_is_a_baseline_part_too(run):
    """Not split into the abbreviation `HIPAA` and an id `Security Rule`."""
    tag = "[NIST IR-1 | HIPAA Security Rule]"
    parts = tag_parts(tag, ["NIST 800-53", "HIPAA Security Rule"])
    assert [(p.citation, p.baseline) for p in parts] == [
        ("NIST IR-1", False),
        ("HIPAA Security Rule", True),
    ]
    evidence = _evidence(f"## X\n\nText. {tag}\n", run)
    assert {c.key for c in evidence.cited} == {("nist-800-53", "IR-1")}
    assert evidence.unknown == []


def test_an_abbreviation_alone_is_a_baseline_part_too(run):
    """`ARC` for ARC-AMPE, as tags already abbreviate it: not inherited as
    the citation `NIST ARC`."""
    tag = "[NIST IR-1 | ARC]"
    evidence = _evidence(f"## X\n\nText. {tag}\n", run)
    assert {c.key for c in evidence.cited} == {("nist-800-53", "IR-1")}
    assert evidence.unknown == []


def test_a_later_part_opening_with_nist_names_nist_not_the_part_before():
    """`NIST` is the start of a known name, so it isn't a shorthand part
    inheriting HIPAA (`HIPAA Security Rule NIST IR-1`)."""
    names = ["HIPAA Security Rule", "NIST 800-53"]
    parts = tag_parts("[HIPAA Security Rule 164.308(a)(1) | NIST IR-1]", names)
    assert parts[1].framework == "NIST" and not parts[1].inherited


@pytest.mark.parametrize(
    "tag, first",
    [
        ("[NIST 800-53 CP-9(1), (2), (3)]", "CP-9(1), (2), (3)"),
        ("[NIST 800-53 AC-2(1), (3)]", "AC-2(1), (3)"),
        ("[NIST 800-53 SA-11(1)-(2), (8)-(9)]", "SA-11(1)-(2), (8)-(9)"),
    ],
)
def test_an_enhancement_list_stays_one_citation(tag, first):
    """b5 on PR 429: `(2)` carries a digit but names nothing alone, so an
    item opening with `(` continues the citation before it."""
    parts = tag_parts(tag, ["NIST 800-53"])
    assert [p.rest for p in parts] == [first]


@pytest.mark.parametrize(
    ("tag", "members"),
    [
        ("[NIST 800-53 CP-9(1), (2), (3)]", {"CP-9(1)", "CP-9(2)", "CP-9(3)"}),
        ("[NIST AC-2(1), (3)]", {"AC-2(1)", "AC-2(3)"}),
        ("[NIST AC-2(1), (3) | NIST IR-1]", {"AC-2(1)", "AC-2(3)", "IR-1"}),
    ],
)
def test_an_enhancement_list_cites_each_member_and_no_fragment(tag, members, run):
    """b5 on PR 429: no fragment such as `NIST 800-53 (2)`. 1d on PR 429: nor
    may `(2)` and `(3)` go silent as a qualifier on `CP-9(1)`. 80 on 421
    (6058): a bare `(2)` takes the stem before it, so every member is cited
    when every member exists (a missing one: `test_range_citations`)."""
    evidence = _evidence(f"## X\n\nText. {tag}\n", run)
    assert evidence.unknown == []
    assert {c.requirement_id for c in evidence.cited} == members
    assert all(c.qualifier == "" for c in evidence.cited)


def test_an_item_naming_its_own_framework_is_its_own_citation():
    """In the committed corpus: `[NIST AI RMF Map 1, NIST AI RMF Map 1.1]`.
    The second item is no qualifier of the first; read as one, `Map 1.1`
    was neither cited nor reported."""
    parts = tag_parts("[NIST AI RMF Map 1, NIST AI RMF Map 1.1]", ["NIST AI RMF"])
    assert [(p.framework, p.rest) for p in parts] == [
        ("NIST AI RMF", "Map 1"),
        ("NIST AI RMF", "Map 1.1"),
    ]


def test_a_list_under_a_framework_with_no_catalog_reports_every_id(run):
    """1d on PR 429: with no HITRUST catalog loaded, `HITRUST` names no known
    framework, and `11.c` was dropped rather than reported."""
    evidence = _evidence("## X\n\nText. [HITRUST 11.a, 11.c | NIST IR-1]\n", run)
    assert [c.key for c in evidence.cited] == [("nist-800-53", "IR-1")]
    assert sorted(evidence.unknown) == ["HITRUST 11.a", "HITRUST 11.c"]


# ---- the Playbook gate -------------------------------------------------------

_PLAYBOOK_SENTENCE = "Acme will review the register."


@pytest.mark.parametrize(
    "tail",
    [" | ARC-AMPE", " | NIST", ""],
)
def test_a_baseline_part_does_not_lift_a_playbook_sentence_out_of_the_gate(tail):
    """b5 on PR 429: a baseline reference cites no requirement, so a
    Playbook-only sentence with one beside it is still Playbook-only."""
    from policyforge.content.deontic import playbook_obligations

    tag = f"[NIST AI RMF Playbook Govern 1.1 Action 1{tail}]"
    assert len(playbook_obligations(f"{_PLAYBOOK_SENTENCE} {tag}\n")) == 1


def test_a_binding_id_beside_the_playbook_still_lifts_it():
    """A cited 800-53 id carries its own obligation (80 on #300), so the
    sentence is governed by that source's rule, not the Playbook gate's."""
    from policyforge.content.deontic import playbook_obligations

    tag = "[NIST AI RMF Playbook Govern 1.1 Action 1 | NIST IR-1]"
    assert playbook_obligations(f"{_PLAYBOOK_SENTENCE} {tag}\n") == []


# ---- satisfies, check's citation steps, and the placeholder reader ---------


def test_the_form_resolves_every_id_and_reports_nothing(run):
    cited = {
        (run.scope.standing_for(f, i)[0], i)
        for f, i, _, _ in parse_citations(FORM, run.scope.names, run.scope.index)
    }
    assert cited == {
        ("hipaa", "164.308(a)(1)"),
        ("nist-800-53", "IR-1"),
        ("nist-800-53", "IR-8"),
    }
    evidence = _evidence(f"## Plan\n\nKeep a plan. {FORM}\n", run)
    # The HIPAA standard is read as the paragraph the catalog carries for it
    # (80's ruling on #423), as before this change.
    assert {c.key for c in evidence.cited} == {
        ("hipaa", "164.308(a)(1)(i)"),
        ("nist-800-53", "IR-1"),
        ("nist-800-53", "IR-8"),
    }
    assert evidence.unknown == []
    assert is_citation_tag(FORM, run.scope)
    assert not is_unresolved_citation(FORM, run.scope)


def test_a_tailorings_own_enhancement_is_cited_under_its_name(run):
    tag = "[NIST RA-5 | ARC-AMPE RA-5(11)]"
    cites = parse_citations(tag, run.scope.names, run.scope.index)
    assert [(f, i) for f, i, _, _ in cites] == [("NIST", "RA-5"), ("ARC-AMPE", "RA-5(11)")]
    assert run.scope.cites("ARC-AMPE", "RA-5(11)")


@pytest.mark.parametrize("span", ["[NIST]", "[ARC-AMPE]", "[NIST 800-53]"])
def test_a_bracket_that_is_only_a_framework_name_stays_reported(span, run):
    """6042 point 2: a name alone is an incomplete citation (3512 on #214)."""
    assert not is_citation_tag(span, run.scope)
    assert is_unresolved_citation(span, run.scope)


def test_a_tag_of_only_a_framework_name_is_still_an_unknown_in_satisfies(run):
    evidence = _evidence("## X\n\nText. [NIST 800-53]\n", run)
    assert evidence.cited == []
    assert evidence.unknown == ["NIST 800-53"]


def test_nist_alone_never_reaches_another_nist_catalog(run):
    """6044: 800-171's id under the bare name stays unknown with 800-171 in
    the run, so loading a catalog can't turn an unknown into evidence."""
    evidence = _evidence("## X\n\nText. [NIST 03.01.01 | ARC-AMPE]\n", run)
    assert evidence.cited == []
    assert evidence.unknown == ["NIST 03.01.01"]


# ---- check and drift ----------------------------------------------------------


def test_check_reads_the_form_as_one_source_tag():
    """`check` compares a document's tags with its synthesis's as written."""
    assert source_tags(f"Keep a plan. {FORM} Then test it.") == [FORM]


def test_drift_reaches_a_document_citing_a_listed_id(tmp_path):
    """IR-8 is the second id of the part, after a comma."""
    root = tmp_path / "docs"
    (root / "standards").mkdir(parents=True)
    (root / "standards" / "ir.md").write_text(f"# IR\n\nKeep a plan. {FORM}\n", encoding="utf-8")
    hits = documents_citing(
        {"IR-8"}, root, framework="NIST 800-53", catalog_ids={"IR-1", "IR-8", "RA-5"}
    )
    assert hits == {"IR-8": ["standards/ir.md"]}
