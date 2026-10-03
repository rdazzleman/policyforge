"""topics/ tests — registry parsing/validation and coverage analysis.

Unit tests build small synthetic control sets so each ownership rule can be
asserted in isolation. The final section runs the *shipped*
config/topics.example.yaml against the real 800-53 catalog, because a starter
registry that leaves controls unowned — or lets two topics claim the same one
— would ship the exact failure this tool exists to detect.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from policyforge.mapping.crosswalk import NIST_ANCHOR

EXAMPLE_REGISTRY = Path(__file__).parent.parent / "config" / "topics.example.yaml"
NIST_DATA = (
    Path(__file__).parent.parent / "data" / "frameworks" / "nist-800-53-r5" / "controls.json"
)
HIPAA_DATA = (
    Path(__file__).parent.parent / "data" / "frameworks" / "hipaa-security-rule" / "controls.json"
)


def _control(control_id, *enhancement_ids, framework="NIST 800-53", baseline="Low"):
    from policyforge.ingest.schema import Control, ControlEnhancement

    return Control(
        control_id=control_id,
        title=control_id,
        framework=framework,
        framework_version="Rev 5",
        baseline=baseline,
        enhancements=[
            ControlEnhancement(enhancement_id=e, title=e, baseline=baseline, description="")
            for e in enhancement_ids
        ],
    )


def _topic(name, owner, controls):
    from policyforge.topics.registry import Topic

    return Topic(name=name, owner=owner, nist_controls=controls)


# --------------------------------------------------------------------------
# Registry parsing and validation
# --------------------------------------------------------------------------


def test_parses_the_shipped_example_registry():
    from policyforge.topics.registry import parse_topics

    topics = parse_topics(yaml.safe_load(EXAMPLE_REGISTRY.read_text(encoding="utf-8")))
    assert topics
    # README documents ~25 as the practical ceiling.
    assert len(topics) <= 25
    assert all(t.owner for t in topics), "every topic needs an accountable owner"
    assert all(t.nist_controls for t in topics)


def test_every_topic_must_declare_an_owner():
    from policyforge.topics.registry import TopicRegistryError, parse_topics

    with pytest.raises(TopicRegistryError, match="missing `owner`"):
        parse_topics({"topics": [{"name": "Orphan process", "nist_controls": ["AC-2"]}]})


def test_duplicate_topic_names_are_rejected():
    from policyforge.topics.registry import TopicRegistryError, parse_topics

    with pytest.raises(TopicRegistryError, match="Duplicate topic name"):
        parse_topics(
            {
                "topics": [
                    {"name": "Access Review", "owner": "IAM"},
                    {"name": "access review", "owner": "SecOps"},
                ]
            }
        )


def test_unknown_keys_are_rejected_rather_than_silently_ignored():
    """A typo'd key would otherwise drop real data — `nist_control` instead of
    `nist_controls` would silently produce a topic that claims nothing."""
    from policyforge.topics.registry import TopicRegistryError, parse_topics

    with pytest.raises(TopicRegistryError, match="unknown key"):
        parse_topics({"topics": [{"name": "T", "owner": "O", "nist_control": ["AC-2"]}]})


def test_registry_must_have_a_topics_list():
    from policyforge.topics.registry import TopicRegistryError, parse_topics

    with pytest.raises(TopicRegistryError, match="top-level `topics:` list"):
        parse_topics({"not_topics": []})
    with pytest.raises(TopicRegistryError, match="non-empty list"):
        parse_topics({"topics": []})


def test_missing_registry_file_says_how_to_create_one(tmp_path):
    from policyforge.topics.registry import load_topics

    with pytest.raises(FileNotFoundError, match=r"topics\.example\.yaml"):
        load_topics(tmp_path / "nope.yaml")


# --------------------------------------------------------------------------
# Coverage: orphaned, contested, and claim resolution
# --------------------------------------------------------------------------


def test_control_claimed_by_no_topic_is_orphaned():
    from policyforge.topics.coverage import analyze_coverage

    controls = [_control("AC-2"), _control("AU-6")]
    report = analyze_coverage([_topic("Access", "IAM", ["AC-2"])], controls)

    assert report.covered == {"AC-2": "Access"}
    assert report.orphaned == ["AU-6"]
    assert not report.is_clean


def test_control_claimed_by_two_topics_is_contested():
    """The worse failure of the two: it looks covered while each owner
    assumes the other has it."""
    from policyforge.topics.coverage import analyze_coverage

    report = analyze_coverage(
        [_topic("Access", "IAM", ["AC-2"]), _topic("Joiners", "HR", ["AC-2"])],
        [_control("AC-2")],
    )

    assert report.contested == {"AC-2": ["Access", "Joiners"]}
    assert report.covered == {}
    assert not report.is_clean


def test_anchoring_a_control_also_claims_its_enhancements():
    """So a topic needn't enumerate AC-2(1)..AC-2(13) to own AC-2."""
    from policyforge.topics.coverage import analyze_coverage

    report = analyze_coverage(
        [_topic("Access", "IAM", ["AC-2"])],
        [_control("AC-2", "AC-2(1)", "AC-2(2)")],
    )

    assert report.orphaned == []
    assert report.covered == {"AC-2": "Access", "AC-2(1)": "Access", "AC-2(2)": "Access"}


def test_a_direct_enhancement_claim_beats_an_inherited_one():
    """AC-2(1) can sit with a different team than AC-2 without either being
    reported as contested — the more specific claim wins."""
    from policyforge.topics.coverage import analyze_coverage

    report = analyze_coverage(
        [
            _topic("Access", "IAM", ["AC-2"]),
            _topic("Automation", "Platform", ["AC-2(1)"]),
        ],
        [_control("AC-2", "AC-2(1)", "AC-2(2)")],
    )

    assert report.contested == {}
    assert report.covered["AC-2"] == "Access"
    assert report.covered["AC-2(1)"] == "Automation"
    assert report.covered["AC-2(2)"] == "Access"  # still inherited
    assert report.is_clean


def test_owner_rollup_counts_requirements_not_topics():
    from policyforge.topics.coverage import analyze_coverage

    report = analyze_coverage(
        [_topic("Access", "IAM", ["AC-2"]), _topic("Auth", "IAM", ["IA-5"])],
        [_control("AC-2", "AC-2(1)"), _control("IA-5")],
    )

    assert report.by_owner == {"IAM": 3}
    assert report.by_topic == {"Access": 2, "Auth": 1}


# --------------------------------------------------------------------------
# Coverage: anchors that don't resolve
# --------------------------------------------------------------------------


def test_a_typod_anchor_is_reported_as_unknown():
    from policyforge.topics.coverage import analyze_coverage

    report = analyze_coverage([_topic("Access", "IAM", ["AC-9999"])], [_control("AC-2")])

    assert report.unknown_anchors == {"Access": ["AC-9999"]}
    assert not report.is_clean


def test_an_out_of_scope_anchor_is_not_treated_as_a_typo():
    """A real control that simply isn't in the baseline being analyzed — the
    PM and PT families sit in no baseline at all. Reporting those as bad IDs
    made the report cry wolf about nine valid anchors."""
    from policyforge.topics.coverage import analyze_coverage

    catalog = [_control("AC-2"), _control("PM-1", baseline="")]
    in_scope = [_control("AC-2")]

    report = analyze_coverage(
        [_topic("Access", "IAM", ["AC-2", "PM-1"])],
        in_scope,
        catalog=catalog,
        scope="Low baseline",
    )

    assert report.unknown_anchors == {}
    assert report.out_of_scope_anchors == {"Access": ["PM-1"]}
    # Out-of-scope anchors are informational, not a failure.
    assert report.is_clean


def test_without_a_catalog_every_anchor_is_judged_against_the_scope():
    """Back-compat: `catalog` is optional, and omitting it means scope is all
    the parser has to go on."""
    from policyforge.topics.coverage import analyze_coverage

    report = analyze_coverage([_topic("Access", "IAM", ["AC-2", "PM-1"])], [_control("AC-2")])
    assert report.unknown_anchors == {"Access": ["PM-1"]}


# --------------------------------------------------------------------------
# Cross-framework reachability
# --------------------------------------------------------------------------


def test_owned_nist_controls_pull_other_frameworks_in_via_the_crosswalk():
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.coverage import analyze_coverage

    nist = [_control("AC-2"), _control("AU-6")]
    hipaa = _control("164.308(a)(3)(i)", framework="HIPAA Security Rule", baseline="")
    hipaa.source_crosswalk = {NIST_ANCHOR: "AC-2"}
    unmapped = _control("164.318(a)", framework="HIPAA Security Rule", baseline="")

    report = analyze_coverage(
        [_topic("Access", "IAM", ["AC-2"])],
        nist,
        other_controls=[hipaa, unmapped],
        crosswalk=build_crosswalk(nist + [hipaa, unmapped]),
    )

    (hipaa_coverage,) = report.framework_coverage
    assert hipaa_coverage.framework == "hipaa"
    assert hipaa_coverage.covered == ["164.308(a)(3)(i)"]
    assert hipaa_coverage.uncovered == ["164.318(a)"]


def _reach(relationships, owned=("AC-2", "AU-6")):
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.coverage import analyze_coverage

    nist = [_control("AC-2"), _control("AU-6")]
    hipaa = _control("164.308(a)(3)(i)", framework="HIPAA Security Rule", baseline="")
    hipaa.source_crosswalk = {NIST_ANCHOR: "AC-2, AU-6"}
    report = analyze_coverage(
        [_topic("Access", "IAM", list(owned))],
        nist,
        other_controls=[hipaa],
        crosswalk=build_crosswalk(nist + [hipaa]),
        relationships=relationships,
    )
    (coverage,) = report.framework_coverage
    return coverage


def test_a_requirement_reached_only_in_part_is_reported_apart():
    """Two controls that each cover part of a requirement are not one that covers it."""
    coverage = _reach(
        {
            ("hipaa", "164.308(a)(3)(i)", "AC-2"): "superset",
            ("hipaa", "164.308(a)(3)(i)", "AU-6"): "intersects",
        }
    )
    assert coverage.covered == []
    assert coverage.partial == ["164.308(a)(3)(i)"]
    assert coverage.uncovered == []
    assert coverage.total == 1


def test_one_owned_control_recorded_as_covering_it_is_enough():
    coverage = _reach(
        {
            ("hipaa", "164.308(a)(3)(i)", "AC-2"): "superset",
            ("hipaa", "164.308(a)(3)(i)", "AU-6"): "subset",
        }
    )
    assert coverage.covered == ["164.308(a)(3)(i)"]
    assert coverage.partial == []


def test_a_pair_with_no_recorded_relationship_counts_as_it_always_did():
    assert _reach({}).covered == ["164.308(a)(3)(i)"]
    unspecified = {("hipaa", "164.308(a)(3)(i)", "AC-2"): "unspecified"}
    assert _reach(unspecified, owned=("AC-2",)).covered == ["164.308(a)(3)(i)"]


def test_only_owned_controls_decide_it():
    """An unowned control recorded as covering the requirement reaches nothing."""
    coverage = _reach(
        {
            ("hipaa", "164.308(a)(3)(i)", "AC-2"): "intersects",
            ("hipaa", "164.308(a)(3)(i)", "AU-6"): "equal",
        },
        owned=("AC-2",),
    )
    assert coverage.partial == ["164.308(a)(3)(i)"]


def test_the_report_names_the_partly_reached_requirements():
    from policyforge.topics.coverage import CoverageReport, FrameworkCoverage, format_report

    report = CoverageReport(scope="all controls")
    report.framework_coverage = [
        FrameworkCoverage("hipaa", covered=["a"], partial=["164.308(a)(3)(i)"], uncovered=["b"])
    ]

    text = format_report(report)

    assert "1 of 3 requirements map to an owned NIST control" in text
    assert "1 more are reached only in part" in text
    assert "164.308(a)(3)(i)" in text


# --------------------------------------------------------------------------
# The shipped starter registry, against the real catalog
# --------------------------------------------------------------------------


def _anchorable_catalogs():
    """Every catalog a topic may anchor, derived from `TOPIC_ANCHORS`.

    **Derived, not listed.** This used to load only 800-53. When the AI RMF
    became anchorable and the example registry gained topics anchoring it,
    those anchors were not out of scope and were not typos — they were
    identifiers from a catalog this fixture had never heard of, and
    `analyze_coverage` reported them as `unknown_anchors`, which is its word
    for a typo. Four tests went red claiming the shipped registry pointed at
    non-existent controls.

    That is the fixture's fault and it is the failure the `catalog`
    parameter exists to prevent: *"pass the full, unfiltered catalog so an
    anchor that's merely out of scope can be told apart from one that's a
    typo"*. A fixture narrower than the thing under test turns one into the
    other.
    """
    from policyforge.ingest.schema import load_controls
    from policyforge.mapping.crosswalk import anchors_a_topic

    controls = []
    for path in sorted(NIST_DATA.parent.parent.glob("*/controls.json")):
        loaded = load_controls(path)
        if loaded and anchors_a_topic(loaded[0].framework):
            controls.extend(loaded)
    return controls


def _real():
    from policyforge.topics.registry import parse_topics

    topics = parse_topics(yaml.safe_load(EXAMPLE_REGISTRY.read_text(encoding="utf-8")))
    return topics, _anchorable_catalogs()


def test_example_registry_anchors_are_all_real_controls():
    topics, catalog = _real()
    known = {c.control_id for c in catalog} | {
        e.enhancement_id for c in catalog for e in c.enhancements
    }
    for topic in topics:
        unknown = [a for a in topic.nist_controls if a not in known]
        assert not unknown, f"{topic.name} anchors non-existent control(s): {unknown}"


def test_no_two_example_topics_anchor_the_same_control():
    topics, _ = _real()
    seen: dict[str, str] = {}
    for topic in topics:
        for anchor in topic.nist_controls:
            assert anchor not in seen, f"{anchor} anchored by both {seen[anchor]} and {topic.name}"
            seen[anchor] = topic.name


@pytest.mark.parametrize("baseline", ["Low", "Moderate", "High"])
def test_example_registry_fully_owns_every_baseline(baseline):
    """The starter registry should ship clean: every control in every
    baseline owned by exactly one topic. Shipping orphans would demonstrate
    the failure this tool exists to detect."""
    from policyforge.ssp.workbook import select_for_baseline
    from policyforge.topics.coverage import analyze_coverage

    topics, catalog = _real()
    report = analyze_coverage(
        topics,
        select_for_baseline(catalog, baseline),
        catalog=catalog,
        scope=f"{baseline} baseline",
    )

    assert report.orphaned == []
    assert report.contested == {}
    assert report.unknown_anchors == {}
    assert report.is_clean


def test_example_registry_reaches_every_crosswalked_hipaa_requirement():
    """65 of the 74 HIPAA requirements map to a NIST control; all 65 should be
    reachable from an owned topic.

    74 rather than 75 since `164.314(a)(2)` was dropped: it had a title and
    no description, so it counted toward the total while stating nothing
    for a topic to cover.
    """
    from policyforge.ingest.schema import load_controls
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.ssp.workbook import select_for_baseline
    from policyforge.topics.coverage import analyze_coverage

    topics, catalog = _real()
    hipaa = load_controls(HIPAA_DATA)
    report = analyze_coverage(
        topics,
        select_for_baseline(catalog, "Moderate"),
        catalog=catalog,
        other_controls=hipaa,
        crosswalk=build_crosswalk(catalog + hipaa),
    )

    (hipaa_coverage,) = report.framework_coverage
    assert len(hipaa_coverage.covered) == 65
    assert hipaa_coverage.total == 74


def test_a_registry_that_is_not_valid_yaml_raises_the_registry_error(tmp_path):
    """Not the YAML library's own exception.

    Every caller that tolerates a bad registry catches TopicRegistryError.
    A syntax error raised yaml's ParserError instead, which none of them
    catch — so `policyforge zardoz`, whose launch code says a broken registry
    "is not fatal", crashed on a topics.yaml with an unclosed bracket.
    """
    import pytest

    from policyforge.topics.registry import TopicRegistryError, load_topics

    path = tmp_path / "topics.yaml"
    path.write_text("topics:\n  - name: [unclosed\n", encoding="utf-8")

    with pytest.raises(TopicRegistryError) as caught:
        load_topics(path)
    message = str(caught.value)
    assert str(path) in message
    assert "not valid YAML" in message
    assert "line 2" in message


def test_zardoz_opens_on_a_registry_with_a_syntax_error(tmp_path, monkeypatch):
    """What the fix is for, end to end: the shell starts and says what is wrong."""
    from click.testing import CliRunner

    import policyforge.cli as cli_mod

    topics = tmp_path / "topics.yaml"
    topics.write_text("topics:\n  - name: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(cli_mod, "load_config", lambda: {})
    monkeypatch.setattr(
        cli_mod, "get_provider", lambda config: (_ for _ in ()).throw(RuntimeError("no key"))
    )

    result = CliRunner().invoke(
        cli_mod.cli,
        [
            "zardoz",
            "--topics",
            str(topics),
            "--corpus-dir",
            str(tmp_path / "corpus"),
            "--no-art",
            "--plain",
        ],
        input="/quit\n",
    )

    assert result.exit_code == 0, result.output
    assert "could not be read" in result.output
    assert "not valid YAML" in result.output


def test_the_ai_block_names_topics_that_exist_and_ai_topics_anchor_only_outcomes():
    """**#188.** The AI block's comment says an AI topic generates an
    outcomes document because it anchors only AI RMF ids, and names the
    security topics whose 800-53 controls carry the obligations instead
    (every name in its "for example" list must be a topic).
    Both are claims about this file, so both are checked against it: a
    renamed security topic, or an 800-53 id added to an AI topic, would
    otherwise leave the comment describing a registry that no longer
    exists."""
    import re

    raw = yaml.safe_load(EXAMPLE_REGISTRY.read_text(encoding="utf-8"))
    topics = raw["topics"] if isinstance(raw, dict) else raw
    names = {t["name"] for t in topics}
    # The AI block's comment only: from its banner to the first AI topic.
    # The whole file will not do -- every name below is also its own
    # topic's `name:` line, so a search of the file could never fail.
    source = EXAMPLE_REGISTRY.read_text(encoding="utf-8")
    start = source.index("# ---- AI risk management")
    end = source.index("- name: AI Governance & Accountability", start)
    text = " ".join(line.strip().lstrip("#") for line in source[start:end].splitlines())
    text = " ".join(text.split())

    # The names are READ from the block, not listed here, so a topic added
    # to the comment is checked too (9b, on #289: a fixed list passed a
    # made-up name). Bound, stated: the names sit in one sentence of the form
    # "for example A, B, ... and Z." -- if that sentence is reworded, the
    # index() below raises, loudly, rather than checking nothing.
    listed = text[text.index("for example ") + len("for example ") :]
    listed = listed[: listed.index(".")]
    named = [n.strip() for n in re.split(r",| and (?=[A-Z])", listed) if n.strip()]
    assert len(named) >= 2, f"parsed no topic list from the AI block: {listed!r}"
    for name in named:
        assert name in names, f"the AI block names {name!r}, which is not a topic"

    outcome = re.compile(r"^(Govern|Map|Measure|Manage) \d")
    ai = [t for t in topics if " AI " in f" {t['name']} "]
    assert len(ai) == 5, [t["name"] for t in ai]
    for topic in ai:
        stray = [c for c in topic["nist_controls"] if not outcome.match(c)]
        assert not stray, f"{topic['name']} anchors non-outcome ids {stray}; the #188 text is false"
