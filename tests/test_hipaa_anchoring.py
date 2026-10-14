"""The HIPAA Privacy and Breach Notification Rules are anchorable (#420).

NIST publishes no 800-53 mapping for either rule, so before this a registry
could not own a requirement of theirs: a topic anchoring `164.502` was an
unknown anchor and `/coverage` reached 0 of 881 and 0 of 38. Each rule is one
control per section with every paragraph an enhancement of its section, so a
topic anchoring a section owns every paragraph under it, as AC-2 owns AC-2(1).

Written from the catalogs and from hand-built inputs, not from the code under
test: the section ids are read from the shipped `controls.json`, and the
vacated paragraphs from the manifest.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from policyforge.frameworks.vacated import VACATED, VACATED_REVISION, declared_vacated
from policyforge.ingest.schema import Control, load_controls
from policyforge.topics.coverage import (
    analyze_coverage,
    format_report,
    split_by_adoption,
    unadopted_note,
)
from policyforge.topics.registry import Topic, TopicRegistryError, parse_topics
from policyforge.topics.satisfies import (
    HIPAA_PART_164,
    catalog_scope,
    named_catalogs,
    split_citation,
)

ROOT = Path(__file__).resolve().parent.parent
FRAMEWORKS = ROOT / "data" / "frameworks"
PRIVACY = FRAMEWORKS / "hipaa-privacy-rule" / "controls.json"
BREACH = FRAMEWORKS / "hipaa-breach-notification-rule" / "controls.json"
SECURITY = FRAMEWORKS / "hipaa-security-rule" / "controls.json"
NIST = FRAMEWORKS / "nist-800-53-r5" / "controls.json"


def _ids(control: Control) -> list[str]:
    return [control.control_id, *(e.enhancement_id for e in control.enhancements)]


@pytest.fixture(scope="module")
def privacy():
    return load_controls(PRIVACY)


@pytest.fixture(scope="module")
def breach():
    return load_controls(BREACH)


@pytest.fixture(scope="module")
def security():
    return load_controls(SECURITY)


@pytest.fixture(scope="module")
def vacated():
    return declared_vacated({})


# ---- anchoring ----------------------------------------------------------------


def test_a_section_anchor_owns_every_paragraph_under_it(privacy):
    section = next(c for c in privacy if c.control_id == "164.502")
    topic = Topic(name="Privacy Practices", owner="Privacy", nist_controls=["164.502"])

    report = analyze_coverage([topic], privacy)

    assert set(_ids(section)) <= set(report.covered)
    assert {report.covered[i] for i in _ids(section)} == {"Privacy Practices"}
    # The other 17 sections are nobody's, and said so.
    assert "164.506" in report.orphaned and "164.506(a)" in report.orphaned


def test_a_paragraph_anchored_directly_beats_its_section(privacy):
    topics = [
        Topic(name="Whole", owner="A", nist_controls=["164.502"]),
        Topic(name="One paragraph", owner="B", nist_controls=["164.502(b)"]),
    ]

    report = analyze_coverage(topics, privacy)

    assert report.covered["164.502(b)"] == "One paragraph"
    assert report.covered["164.502(a)"] == "Whole"
    assert "164.502(b)" not in report.contested


def test_a_section_the_catalog_has_is_not_an_unknown_anchor(privacy, breach):
    topic = Topic(name="T", owner="O", nist_controls=["164.404", "164.999"])

    report = analyze_coverage([topic], [*privacy, *breach])

    assert report.unknown_anchors == {"T": ["164.999"]}


def test_a_registry_adopts_a_rule_by_anchoring_a_section_of_it(privacy, breach):
    nist = load_controls(NIST)
    topic = Topic(name="T", owner="O", nist_controls=["164.502"])

    in_scope, unadopted, _ = split_by_adoption([topic], [*nist, *privacy, *breach])

    frameworks = {c.framework for c in in_scope}
    assert "HIPAA Privacy Rule" in frameworks
    assert "HIPAA Breach Notification Rule" not in frameworks
    # Named, not silently absent: the rule this registry doesn't anchor, and
    # 800-53, which it doesn't anchor either.
    assert {c.framework for c in unadopted} == {"HIPAA Breach Notification Rule", "NIST 800-53"}
    assert any("HIPAA Breach Notification Rule" in line for line in unadopted_note(unadopted))


def test_a_registry_that_anchors_neither_rule_never_counts_them(privacy, breach):
    nist = load_controls(NIST)
    topic = Topic(name="T", owner="O", nist_controls=["AC-2"])

    in_scope, unadopted, _ = split_by_adoption([topic], [*nist, *privacy, *breach])

    assert {c.framework for c in in_scope} == {"NIST 800-53"}
    assert {c.framework for c in unadopted} == {
        "HIPAA Privacy Rule",
        "HIPAA Breach Notification Rule",
    }


def test_the_rules_are_not_a_crosswalk_target():
    """Anchorable is not mappable: no 800-53 mapping is published, and
    `crosswalk seed` must not generate one (the AI RMF's reason)."""
    from policyforge.mapping.crosswalk import NIST_ANCHOR, TOPIC_ANCHORS

    assert NIST_ANCHOR == "nist-800-53"
    assert {"hipaa-privacy", "hipaa-breach"} <= TOPIC_ANCHORS
    assert "hipaa" not in TOPIC_ANCHORS, "the Security Rule is reached through its crosswalk"


# ---- text a court vacated -----------------------------------------------------


def test_vacated_paragraphs_leave_the_denominator_and_are_named(privacy, vacated):
    status = vacated["hipaa-privacy"]
    every = [i for c in privacy for i in _ids(c)]
    gone = sorted(i for i in every if status.status(i) == VACATED)
    revised = sorted(i for i in every if status.status(i) == VACATED_REVISION)
    assert gone and revised, "the manifest marks both kinds; the population is wrong"
    topic = Topic(name="T", owner="O", nist_controls=["164.502"])

    report = analyze_coverage([topic], privacy, vacated=vacated)

    assert not set(gone) & set(report.in_scope)
    assert set(revised) <= set(report.in_scope), "a revised paragraph's pre-rule wording binds"
    assert len(report.in_scope) == len(every) - len(gone)
    aside = report.vacated["HIPAA Privacy Rule"]
    assert sorted(aside.ids) == gone
    assert aside.judgment == "Purl v. HHS"
    assert f"{len(gone)} vacated paragraph(s) of HIPAA Privacy Rule not counted (Purl v. HHS)" in (
        format_report(report)
    )


def test_no_vacated_source_sets_nothing_aside(privacy):
    topic = Topic(name="T", owner="O", nist_controls=["164.502"])

    report = analyze_coverage([topic], privacy)

    assert report.vacated == {}
    assert len(report.in_scope) == sum(len(_ids(c)) for c in privacy)


def test_generation_and_coverage_drop_the_same_paragraphs(privacy, vacated):
    """One source (80's ruling): what `coverage` sets aside is exactly what
    synthesis does not send. Two sessions of this report once disagreed."""
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.synthesis.merge import build_synthesis_topic

    report = analyze_coverage(
        [Topic(name="T", owner="O", nist_controls=["164.502"])], privacy, vacated=vacated
    )
    topic = build_synthesis_topic(
        "T", ["164.502"], privacy, build_crosswalk(privacy), vacated=vacated, scopes={}
    )
    sent = {e.enhancement_id for c in topic.controls for e in c.enhancements}

    assert sent, "synthesis retrieved nothing for an anchored section"
    assert not sent & set(report.vacated["HIPAA Privacy Rule"].ids)


def test_every_coverage_caller_in_src_hands_it_the_vacated_source():
    """Reading the manifests costs ~0.15 s, so the function takes them rather
    than reading them, and a caller that forgets gets a denominator that
    counts text a court vacated. Both callers today pass it; a third fails here."""
    src = ROOT / "src" / "policyforge"
    calls = []
    for path in src.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", "")) == "analyze_coverage"
            ):
                calls.append((path.relative_to(src).as_posix(), node))
    assert len(calls) >= 2, "the callers were not found"
    missing = [p for p, n in calls if "vacated" not in {k.arg for k in n.keywords}]
    assert not missing, f"analyze_coverage called without vacated=: {missing}"


# ---- `HIPAA <section>` resolves across the three catalogs (80, 6044) -----------


def _scope(*sets):
    controls = [c for one in sets for c in one]
    return catalog_scope(controls)


def test_hipaa_164_502_b_is_unknown_with_only_the_security_rule_loaded(security):
    assert _scope(security).standing_for("HIPAA", "164.502(b)")[1] == []


def test_hipaa_164_502_b_resolves_once_the_privacy_rule_is_loaded(security, privacy):
    catalog, standing = _scope(security, privacy).standing_for("HIPAA", "164.502(b)")

    assert (catalog, standing) == ("hipaa-privacy", ["164.502(b)"])


def test_the_breach_rule_resolves_the_same_way(security, privacy, breach):
    scope = _scope(security, privacy, breach)

    assert scope.standing_for("HIPAA", "164.404(a)(1)") == ("hipaa-breach", ["164.404(a)(1)"])
    assert scope.standing_for("HIPAA", "164.308(a)(1)")[0] == "hipaa"


def test_an_id_in_none_of_the_three_stays_reported(security, privacy, breach):
    assert _scope(security, privacy, breach).standing_for("HIPAA", "164.999")[1] == []


def test_a_declared_name_still_names_only_its_own_catalog(security, privacy):
    scope = _scope(security, privacy)

    assert scope.standing_for("HIPAA Security Rule", "164.502(b)")[1] == []
    assert scope.standing_for("HIPAA Privacy Rule", "164.502(b)")[1] == ["164.502(b)"]


def test_the_part_164_catalogs_are_the_shipped_ones_and_their_ids_do_not_overlap(
    security, privacy, breach
):
    from policyforge.mapping.crosswalk import normalize_framework

    keys = {normalize_framework(c.framework) for one in (security, privacy, breach) for c in one}
    assert keys == set(HIPAA_PART_164)
    seen: dict[str, str] = {}
    for key, controls in zip(HIPAA_PART_164, (security, privacy, breach), strict=True):
        for control in controls:
            for i in _ids(control):
                assert seen.setdefault(i, key) == key, f"{i} is in {seen[i]} and {key}"


def test_named_catalogs_answers_for_the_two_rules_alone(privacy, breach):
    index = {"hipaa-privacy": set(), "hipaa-breach": set()}

    assert named_catalogs("HIPAA", index) == ["hipaa-privacy", "hipaa-breach"]
    assert named_catalogs("HIPAA Privacy Rule", index) == ["hipaa-privacy"]
    assert named_catalogs("HIPAA", {}) == []


def test_a_bare_hipaa_citation_splits_with_its_id_from_the_catalog_that_has_it(privacy, security):
    scope = _scope(security, privacy)

    framework, requirement_id, qualifier = split_citation(
        "HIPAA 164.502(b)(2)(iii) Required", ["HIPAA"], scope.index
    )

    assert (framework, requirement_id, qualifier) == ("HIPAA", "164.502(b)(2)(iii)", "Required")


def test_drift_reaches_a_document_citing_the_privacy_rule_by_the_bare_name(
    tmp_path, security, privacy
):
    from policyforge.frameworks.drift import documents_reached

    docs = tmp_path / "docs" / "standards"
    docs.mkdir(parents=True)
    (docs / "privacy.md").write_text(
        "# P\n\nDisclose the minimum necessary. [HIPAA 164.502(b)]\n", encoding="utf-8"
    )
    index = {"hipaa": {i for c in security for i in _ids(c)}}
    index["hipaa-privacy"] = {i for c in privacy for i in _ids(c)}

    hits = documents_reached(
        {"164.502(b)"},
        tmp_path / "docs",
        framework="HIPAA Privacy Rule",
        catalog_ids=index["hipaa-privacy"],
        catalogs=index,
    )

    assert list(hits["164.502(b)"]) == ["standards/privacy.md"]


# ---- the starter registry and the registry loader -----------------------------


def test_the_starters_phi_topics_anchor_every_section_of_their_rule_and_are_marked(privacy, breach):
    import yaml

    text = (ROOT / "config" / "topics.example.yaml").read_text(encoding="utf-8")
    topics = {
        t["name"]: t
        for t in yaml.safe_load(text)["topics"]
        if t["name"]
        in {
            "HIPAA Privacy Practices",
            "Breach Notification",
        }
    }
    assert set(topics) == {"HIPAA Privacy Practices", "Breach Notification"}
    # Every section but the two a court vacated whole (see the next test).
    assert topics["HIPAA Privacy Practices"]["nist_controls"] == [
        c.control_id for c in privacy if c.control_id not in {"164.509", "164.535"}
    ]
    assert topics["Breach Notification"]["nist_controls"] == [c.control_id for c in breach]
    assert {t["owner"] for t in topics.values()} == {"Privacy"}
    # 22 part 3: each is a condition of `programme.personal_data: phi`.
    assert text.count("# On when `programme.personal_data` is `phi` (22, part 3)") == 2


def test_an_unquoted_section_is_refused_rather_than_read_as_a_number():
    """YAML reads `164.510` as 164.51 and `164.500` as 164.5, ids no catalog
    has; the loader says so by name instead of reporting a mystery anchor."""
    import yaml

    data = yaml.safe_load("topics:\n  - name: T\n    owner: O\n    nist_controls: [164.510]\n")

    with pytest.raises(TopicRegistryError, match=r"164\.51 as a number.*in quotes"):
        parse_topics(data)


def test_a_quoted_section_is_kept_as_written():
    import yaml

    data = yaml.safe_load('topics:\n  - name: T\n    owner: O\n    nist_controls: ["164.510"]\n')

    assert parse_topics(data)[0].nist_controls == ["164.510"]


def test_the_decision_table_and_the_anchor_set_agree_on_the_two_rules():
    from policyforge.mapping.crosswalk import ANCHOR_DECISIONS, ANCHORS

    assert ANCHOR_DECISIONS["hipaa-privacy-rule"] == ANCHORS
    assert ANCHOR_DECISIONS["hipaa-breach-notification-rule"] == ANCHORS
    assert ANCHOR_DECISIONS["hipaa-security-rule"] != ANCHORS


def test_the_json_report_carries_what_was_set_aside(privacy, vacated):
    import dataclasses

    report = analyze_coverage(
        [Topic(name="T", owner="O", nist_controls=["164.502"])], privacy, vacated=vacated
    )

    data = json.loads(json.dumps(dataclasses.asdict(report)))

    assert data["vacated"]["HIPAA Privacy Rule"]["judgment"] == "Purl v. HHS"
    assert data["vacated"]["HIPAA Privacy Rule"]["ids"]


def test_no_starter_anchor_is_text_a_court_vacated(vacated):
    """1d on #420: the starter anchored 164.509 and 164.535, whole sections
    Purl v. HHS vacated, so a new user's first `coverage` called them out of
    scope. Derived from the manifest, not from a list kept here."""
    import yaml

    from policyforge.mapping.crosswalk import normalize_framework

    topics = yaml.safe_load((ROOT / "config" / "topics.example.yaml").read_text("utf-8"))["topics"]
    anchors = [(t["name"], str(a)) for t in topics for a in t["nist_controls"]]
    status = vacated["hipaa-privacy"]
    assert normalize_framework("HIPAA Privacy Rule") == "hipaa-privacy"
    assert status.sections, "the manifest declares vacated sections; the population is wrong"
    bad = [(name, a) for name, a in anchors if status.status(a) == VACATED]
    assert not bad, f"the starter anchors vacated text: {bad}"


def test_a_vacated_anchor_is_named_as_vacated_not_as_out_of_scope(privacy, vacated):
    topic = Topic(name="T", owner="O", nist_controls=["164.509", "164.502"])

    report = analyze_coverage([topic], privacy, vacated=vacated)

    assert report.vacated_anchors == {"T": ["164.509"]}
    assert report.out_of_scope_anchors == {}
    text = format_report(report)
    assert "Anchored but vacated" in text and "T: 164.509" in text
    assert "out of scope" not in text


def test_the_starter_headers_coverage_command_loads_what_the_starter_needs():
    """80's 6167: a command that loads Privacy and Breach without the Security
    Rule is a partial HIPAA that looks complete. Compared with the catalogs
    `init` writes, which are what the starter registry's anchors need."""
    import re

    from policyforge.scaffold import STARTER_CATALOGS

    header = (ROOT / "config" / "topics.example.yaml").read_text(encoding="utf-8")
    command = next(line for line in header.splitlines() if "policyforge coverage" in line)
    loaded = re.findall(r"data/frameworks/([^/]+)/controls\.json", command)

    assert sorted(loaded) == sorted(STARTER_CATALOGS)
