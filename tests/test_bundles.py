"""The team bundle and the assessor's reverse view.

Both are set arithmetic over the registry, the catalogs and the crosswalk,
so what these tests are mostly about is the part that could be wrong in a
way that matters: whether a claim says honestly how it was reached. A topic
that anchored AC-2 must not appear to have deliberately addressed a HITRUST
requirement nobody has read.
"""

from __future__ import annotations

from policyforge.ingest.schema import Control, ControlEnhancement
from policyforge.mapping.crosswalk import NIST_ANCHOR
from policyforge.topics.bundles import (
    CROSSWALKED,
    DIRECT,
    INHERITED,
    requirement_view,
    team_bundle,
)
from policyforge.topics.registry import Topic


def control(control_id, *enhancement_ids, framework="NIST SP 800-53 Rev 5"):
    return Control(
        control_id=control_id,
        title=f"{control_id} title",
        family="Access Control",
        family_abbr="AC",
        framework=framework,
        framework_version="Rev 5",
        control_statement="Do the thing.",
        enhancements=[
            ControlEnhancement(
                enhancement_id=e, title=f"{e} title", baseline="Moderate", description="More."
            )
            for e in enhancement_ids
        ],
    )


NIST = [control("AC-2", "AC-2(1)", "AC-2(3)"), control("AC-6"), control("RA-1")]

HIPAA = [control("164.308(a)(1)(i)", framework="HIPAA Security Rule")]

CROSSWALK = {"RA-1": {"HIPAA Security Rule": ["164.308(a)(1)(i)"]}}

ACCESS = Topic(
    name="Access Review",
    owner="IT Asset Management",
    nist_controls=["AC-2"],
    cadence="quarterly",
    confluence={"space": "ENG", "pages": {"standard": "Access Review Standard"}},
)
LEAST_PRIV = Topic(
    name="Least Privilege",
    owner="IT Asset Management",
    nist_controls=["AC-6"],
    confluence={},
)
RISK = Topic(
    name="Risk Assessment",
    owner="Security",
    nist_controls=["RA-1"],
    confluence={"space": "ENG", "pages": {"policy": "Risk Policy"}},
)
TOPICS = [ACCESS, LEAST_PRIV, RISK]


# ---- the team bundle ----------------------------------------------------


def test_a_bundle_collects_every_topic_an_owner_has():
    bundle = team_bundle(TOPICS, NIST, "IT Asset Management")
    assert [t.name for t in bundle.topics] == ["Access Review", "Least Privilege"]
    assert bundle.exists


def test_owner_matching_is_case_and_whitespace_tolerant():
    assert team_bundle(TOPICS, NIST, "  it asset management ").exists


def test_owner_matching_is_not_fuzzy():
    """Quietly matching a prefix would hand somebody another team's work."""
    assert not team_bundle(TOPICS, NIST, "IT").exists
    assert not team_bundle(TOPICS, NIST, "Asset").exists


def test_an_unknown_owner_says_so_rather_than_returning_nothing():
    rendered = team_bundle(TOPICS, NIST, "Nobody").render()
    assert "No topic in the registry is owned by" in rendered
    assert "UNASSIGNED" in rendered


def test_anchoring_a_control_claims_its_enhancements():
    bundle = team_bundle(TOPICS, NIST, "IT Asset Management")
    owned = {r.requirement_id: r for r in bundle.requirements}
    assert owned["AC-2"].route == DIRECT
    assert owned["AC-2(1)"].route == INHERITED
    assert owned["AC-2(3)"].route == INHERITED


def test_the_bundle_separates_direct_from_inherited_in_its_summary():
    rendered = team_bundle(TOPICS, NIST, "IT Asset Management").render()
    assert (
        "2 anchored directly, 2 inherited from an anchored parent "
        "(a control, or an AI RMF category)." in rendered
    )


def test_a_bundle_lists_the_documents_the_team_must_keep_current():
    bundle = team_bundle(TOPICS, NIST, "IT Asset Management")
    assert ("Access Review", "standard", "Access Review Standard") in bundle.documents


def test_a_topic_with_no_document_is_named_as_such():
    """Invisible in a coverage report, which counts controls rather than pages."""
    bundle = team_bundle(TOPICS, NIST, "IT Asset Management")
    assert bundle.undocumented_topics == ["Least Privilege"]
    assert "nobody can read" in bundle.render()


def test_crosswalked_frameworks_are_reported_as_leads_not_coverage():
    """Framework names are normalised, as `coverage.py` normalises them.

    One canonical key per framework, so "HIPAA Security Rule" from a
    crosswalk and "HIPAA" from a catalog do not become two rows describing
    the same thing.
    """
    bundle = team_bundle(TOPICS, NIST, "Security", crosswalk=CROSSWALK)
    assert bundle.frameworks == {"hipaa": ["164.308(a)(1)(i)"]}
    assert "Read them as leads, not as coverage." in bundle.render()


def test_a_bundle_without_a_crosswalk_claims_no_frameworks():
    assert team_bundle(TOPICS, NIST, "Security").frameworks == {}


# ---- the assessor's reverse view ---------------------------------------


def test_a_directly_anchored_requirement_names_its_topic_and_owner():
    view = requirement_view(TOPICS, NIST, "AC-2")
    assert view.owned
    assert view.claims[0].topic == "Access Review"
    assert view.claims[0].owner == "IT Asset Management"
    assert view.claims[0].route == DIRECT


def test_an_enhancement_resolves_through_its_parent_and_says_so():
    view = requirement_view(TOPICS, NIST, "AC-2(1)")
    assert view.claims[0].route == INHERITED
    assert "inherited" in view.render()
    assert "a control's enhancements" in view.render()


def test_the_view_points_at_the_document_to_show_an_assessor():
    view = requirement_view(TOPICS, NIST, "AC-2")
    assert view.documents == [("Access Review", "standard", "Access Review Standard")]
    assert "Access Review Standard" in view.render()


def test_an_owned_requirement_with_no_document_says_the_harder_thing():
    view = requirement_view(TOPICS, NIST, "AC-6")
    assert view.owned
    assert not view.documents
    assert "nothing an assessor can be shown" in view.render()


def test_a_non_nist_requirement_resolves_through_the_crosswalk():
    """The assessor's actual question: they name a HIPAA citation."""
    view = requirement_view(
        TOPICS, NIST, "164.308(a)(1)(i)", other_controls=HIPAA, crosswalk=CROSSWALK
    )
    assert view.owned
    claim = view.claims[0]
    assert claim.route == CROSSWALKED
    assert claim.via == "RA-1"
    assert claim.owner == "Security"


def test_a_crosswalked_claim_says_nobody_wrote_that_requirement_down():
    """The distinction that stops a mapping from reading as a commitment."""
    view = requirement_view(
        TOPICS, NIST, "164.308(a)(1)(i)", other_controls=HIPAA, crosswalk=CROSSWALK
    )
    rendered = view.render()
    assert "via RA-1" in rendered
    assert "not written down as this requirement" in rendered


def test_an_unowned_requirement_is_a_gap_not_a_lookup_failure():
    view = requirement_view(TOPICS, NIST, "AC-2", other_controls=HIPAA)
    orphan = requirement_view([RISK], NIST, "AC-2")
    assert view.owned and not orphan.owned
    assert "a real gap, not a lookup failure" in orphan.render()


def test_an_id_in_no_catalog_is_distinguished_from_an_unowned_one():
    """A typo and a genuine gap need different answers."""
    view = requirement_view(TOPICS, NIST, "XX-99")
    assert view.unknown
    assert not view.owned
    assert "not in any loaded catalog" in view.render()


def test_two_topics_claiming_one_requirement_is_reported():
    rival = Topic(name="Rival", owner="Other Team", nist_controls=["AC-2"])
    view = requirement_view([ACCESS, rival], NIST, "AC-2")
    assert view.contested
    assert "two teams each believing the other has it" in view.render()


def test_a_direct_claim_beats_an_inherited_one():
    """Matching coverage.py: a specifically-anchored enhancement can sit
    with a different team than its parent without being contested."""
    specific = Topic(name="Specific", owner="Other Team", nist_controls=["AC-2(1)"])
    view = requirement_view([ACCESS, specific], NIST, "AC-2(1)")
    assert not view.contested
    assert view.claims[0].topic == "Specific"
    assert view.claims[0].route == DIRECT


def test_an_unknown_anchor_in_the_registry_does_not_grant_ownership():
    """A typo'd anchor must not silently claim a real requirement."""
    typo = Topic(name="Typo", owner="Team", nist_controls=["AC-2)"])
    assert not requirement_view([typo], NIST, "AC-2").owned


# ---- reachable from the command line -----------------------------------


def write_fixtures(tmp_path):
    """A registry and two catalogs on disk, as the commands expect them."""
    import dataclasses
    import json

    import yaml

    topics_path = tmp_path / "topics.yaml"
    topics_path.write_text(
        yaml.safe_dump(
            {
                "topics": [
                    {
                        "name": "Access Review",
                        "owner": "IT Asset Management",
                        "nist_controls": ["AC-2"],
                        "confluence": {
                            "space": "ENG",
                            "pages": {"standard": "Access Review Standard"},
                        },
                    },
                    {"name": "Risk Assessment", "owner": "Security", "nist_controls": ["RA-1"]},
                ]
            }
        ),
        encoding="utf-8",
    )

    nist_path = tmp_path / "nist.json"
    nist_path.write_text(
        json.dumps([dataclasses.asdict(c) for c in NIST], indent=2), encoding="utf-8"
    )
    hipaa_path = tmp_path / "hipaa.json"
    hipaa = [
        dataclasses.asdict(
            Control(
                control_id="164.308(a)(1)(i)",
                title="Security management process",
                framework="HIPAA Security Rule",
                framework_version="2013",
                control_statement="Implement policies.",
                source_crosswalk={NIST_ANCHOR: "RA-1"},
            )
        )
    ]
    hipaa_path.write_text(json.dumps(hipaa, indent=2), encoding="utf-8")
    return topics_path, nist_path, hipaa_path


def test_bundle_command_reports_a_team(tmp_path):
    from click.testing import CliRunner

    from policyforge.cli import cli

    topics_path, nist_path, _ = write_fixtures(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "bundle",
            "IT Asset Management",
            "--topics",
            str(topics_path),
            "--controls",
            str(nist_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Access Review" in result.output
    assert "Access Review Standard" in result.output


def test_bundle_command_on_an_unknown_team_exits_cleanly(tmp_path):
    """Not an error — a misspelled team is a normal thing to type."""
    from click.testing import CliRunner

    from policyforge.cli import cli

    topics_path, nist_path, _ = write_fixtures(tmp_path)
    result = CliRunner().invoke(
        cli, ["bundle", "Nobody", "--topics", str(topics_path), "--controls", str(nist_path)]
    )
    assert result.exit_code == 0
    assert "No topic in the registry is owned by" in result.output


def test_addresses_command_resolves_a_hipaa_citation(tmp_path):
    """The assessor's question, end to end through the crosswalk."""
    from click.testing import CliRunner

    from policyforge.cli import cli

    topics_path, nist_path, hipaa_path = write_fixtures(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "addresses",
            "164.308(a)(1)(i)",
            "--topics",
            str(topics_path),
            "--controls",
            str(nist_path),
            "--controls",
            str(hipaa_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Risk Assessment" in result.output
    assert "via RA-1" in result.output
    assert "not written down as this requirement" in result.output
