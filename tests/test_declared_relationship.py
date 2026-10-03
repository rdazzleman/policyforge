"""A catalog's declared crosswalk relationship (#408, 80's ruling (y)).

NIST publishes CSF 2.0's mapping to 800-53 (OLIR 186) with no relationship
per pair and marks it `comprehensive: No`. So the CSF catalog declares
`crosswalk_relationship: source-untyped` in its manifest, and coverage reads
every CSF pair as partial: a CSF subcategory is never "covered" on the
strength of NIST's untyped link alone.

Three properties, each tested both with and without an overlay:

- a declaring catalog's pairs read `source-untyped`, so coverage is partial;
- seeding that catalog writes the declared relationship, so seeding changes
  nothing (the contract `seed_overlay` states);
- a catalog that declares nothing reads exactly as before.
"""

from __future__ import annotations

import json

import pytest

from policyforge.crosswalk.overlay import (
    ACCEPTED,
    MappingRow,
    Overlay,
    relationships_for,
    seed_overlay,
)
from policyforge.frameworks.registry import declared_crosswalk_relationships
from policyforge.ingest.schema import Control
from policyforge.mapping.crosswalk import build_crosswalk
from policyforge.topics.coverage import PARTIAL_RELATIONSHIPS, analyze_coverage
from policyforge.topics.registry import Topic

CSF = "NIST CSF 2.0"
HIPAA = "HIPAA Security Rule"


def _controls():
    """AC-2 in 800-53; one CSF subcategory and one HIPAA standard mapped to it."""
    return [
        Control(control_id="AC-2", title="t", framework="NIST 800-53", framework_version="Rev 5"),
        Control(
            control_id="PR.AA-01",
            title="t",
            framework=CSF,
            framework_version="2.0",
            source_crosswalk={"nist-800-53": "AC-2"},
        ),
        Control(
            control_id="164.308(a)(3)(i)",
            title="t",
            framework=HIPAA,
            framework_version="45 CFR 164",
            source_crosswalk={"nist-800-53": "AC-2"},
        ),
    ]


def _catalog(root, directory, name, relationship=None):
    path = root / directory
    path.mkdir(parents=True)
    manifest = f"name: {name}\nlicence: public-domain\n"
    if relationship is not None:
        manifest += f"crosswalk_relationship: {relationship}\n"
    (path / "framework.yaml").write_text(manifest, encoding="utf-8")
    (path / "controls.json").write_text(
        json.dumps([{"control_id": "X", "framework": name}]), encoding="utf-8"
    )


@pytest.fixture
def declared(tmp_path):
    """Read from manifests on disk, as the CLI reads them: CSF declares, HIPAA does not."""
    _catalog(tmp_path, "nist-csf-2-0", CSF, "source-untyped")
    _catalog(tmp_path, "hipaa", HIPAA)
    return declared_crosswalk_relationships(roots=[tmp_path])


def _covered(controls, relationships):
    topics = [Topic(name="T", owner="O", nist_controls=["AC-2"])]
    nist = [c for c in controls if c.framework == "NIST 800-53"]
    other = [c for c in controls if c.framework != "NIST 800-53"]
    report = analyze_coverage(
        topics,
        nist,
        other_controls=other,
        crosswalk=build_crosswalk(controls),
        relationships=relationships,
    )
    return {f.framework: len(f.covered) for f in report.framework_coverage}


def test_the_manifest_declaration_is_read_and_keyed_as_coverage_keys_it(declared):
    assert declared.get("nist-csf") == "source-untyped"
    assert "hipaa" not in declared, "a catalog that declares nothing must contribute nothing"
    assert "source-untyped" in PARTIAL_RELATIONSHIPS


def test_a_misspelled_declaration_is_refused_rather_than_read_as_full(tmp_path):
    """Anything outside PARTIAL_RELATIONSHIPS reads as full coverage, so a
    typo would count every CSF pair covered. It must stop, naming the file."""
    _catalog(tmp_path, "nist-csf-2-0", CSF, "source-untypd")
    with pytest.raises(ValueError, match=r"source-untypd.*not one of"):
        declared_crosswalk_relationships(roots=[tmp_path])


def test_without_an_overlay_a_csf_pair_is_partial_and_hipaa_is_unchanged(declared):
    controls = _controls()
    table = relationships_for(controls, [], declared=declared)

    assert table == {("nist-csf", "PR.AA-01", "AC-2"): "source-untyped"}
    covered = _covered(controls, table)
    assert covered.get("nist-csf") == 0, "NIST's untyped link alone reads as full coverage"
    assert covered.get("hipaa") == 1, "a catalog declaring nothing moved"
    assert _covered(controls, relationships_for(controls, [], declared={})) == {
        "nist-csf": 1,
        "hipaa": 1,
    }, "without the declaration CSF would read full; the declaration is what moves it"


def test_seeding_csf_writes_the_declared_relationship_and_changes_nothing(declared):
    controls = _controls()
    seeded = seed_overlay(controls, CSF, declared=declared)

    rows = [row for rows in seeded.requirements.values() for row in rows]
    assert [(r.control, r.relationship) for r in rows] == [("AC-2", "source-untyped")]

    before = relationships_for(controls, [], declared=declared)
    after = relationships_for(controls, [seeded], declared=declared)
    assert after == before
    assert _covered(controls, after) == _covered(controls, before)


def test_seeding_a_catalog_that_declares_nothing_still_writes_unspecified(declared):
    controls = _controls()
    seeded = seed_overlay(controls, HIPAA, declared=declared)

    rows = [row for rows in seeded.requirements.values() for row in rows]
    assert [(r.control, r.relationship) for r in rows] == [("AC-2", "unspecified")]
    assert _covered(controls, relationships_for(controls, [seeded], declared=declared)) == {
        "nist-csf": 0,
        "hipaa": 1,
    }


def test_an_organisations_reviewed_row_overrides_the_declaration(declared):
    """The overlay is an organisation's reviewed decision about its own
    reading of the pair; the declaration is only the source's default."""
    controls = _controls()
    reviewed = Overlay(framework=CSF)
    reviewed.requirements["PR.AA-01"] = [
        MappingRow(control="AC-2", relationship="equal", status=ACCEPTED, sources=["reviewed"])
    ]
    table = relationships_for(controls, [reviewed], declared=declared)
    assert table[("nist-csf", "PR.AA-01", "AC-2")] == "equal"
    assert _covered(controls, table).get("nist-csf") == 1


@pytest.mark.parametrize(
    ("sources", "named"),
    [
        ({"nist-csf": "NIST OLIR 186"}, "NIST OLIR 186 (the source of this crosswalk)"),
        ({}, "the source of this crosswalk"),
    ],
    ids=["source-declared", "source-undeclared"],
)
def test_coverage_names_what_the_source_left_unmapped(declared, sources, named):
    """80 on #408 and #449: an unmapped CSF id is "not mapped by" its source,
    named from the manifest, never "no 800-53 equivalent". Only a
    `source-untyped` framework gets the note."""
    from policyforge.topics.coverage import format_report

    controls = [
        *_controls(),
        Control(control_id="GV.OC", title="t", framework=CSF, framework_version="2.0"),
    ]
    topics = [Topic(name="T", owner="O", nist_controls=["AC-2"])]
    report = analyze_coverage(
        topics,
        [c for c in controls if c.framework == "NIST 800-53"],
        other_controls=[c for c in controls if c.framework != "NIST 800-53"],
        crosswalk=build_crosswalk(controls),
        relationships=relationships_for(controls, [], declared=declared),
        crosswalk_sources=sources,
    )
    by_name = {f.framework: f for f in report.framework_coverage}
    assert by_name["nist-csf"].unmapped_by_source == ["GV.OC"]
    assert by_name["hipaa"].unmapped_by_source is None
    text = format_report(report)
    assert text.count(f"are not mapped by {named}, which is not the same") == 1
    assert f"{named[0].upper()}{named[1:]} publishes this mapping untyped" in text


class _Document:
    def __init__(self, body):
        self.body = body
        self.title = "D"
        self.relative_path = "d.md"
        self.tier = "standard"
        self.topic = ""


@pytest.mark.parametrize("with_overlay", [False, True], ids=["unseeded", "seeded"])
def test_satisfies_reports_a_csf_pair_in_part_and_hipaa_satisfied(declared, with_overlay):
    """`satisfies` reads the one partial set and the declared relationship, as
    `coverage` does: it printed NIST's untyped CSF links as "satisfied",
    through a private copy of the partial set that lacked `source-untyped`."""
    from policyforge.crosswalk.overlay import accepted_rows, apply_overlays
    from policyforge.topics.satisfies import document_evidence

    controls = _controls()
    overlays = [seed_overlay(controls, CSF, declared=declared)] if with_overlay else []
    apply_overlays(controls, overlays)
    evidence = document_evidence(
        _Document("Acme must manage accounts. [NIST 800-53 AC-2]\n"),
        controls=controls,
        crosswalk=build_crosswalk(controls),
        provenance=accepted_rows(overlays),
        declared_relationships=declared,
    )
    reached = {(r.framework, r.requirement_id): r for r in evidence.reached}
    assert reached[("nist-csf", "PR.AA-01")].in_part
    assert reached[("nist-csf", "PR.AA-01")].relationship == "source-untyped"
    assert not reached[("hipaa", "164.308(a)(3)(i)")].in_part
