"""The registry `init` writes from the pool (22, part 3): what a merge moves,
what a role anchors, and that no anchor is lost or claimed twice.

Expected anchors are written out by hand from the shipped pool and 5925, not
read back from the builder's own constants.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
import yaml

from policyforge.topics.build import PoolError, build_registry, header, load_pool, render
from policyforge.topics.registry import parse_registry
from policyforge.topics.rule import select
from policyforge.topics.scope_facts import ScopeFacts

ROOT = Path(__file__).resolve().parent.parent
HEALTHCARE = {
    "builds_software": True,
    "own_facilities": False,
    "personal_data": "phi",
    "baseline": "800-53",
}


@pytest.fixture(scope="module")
def pool() -> dict:
    return yaml.safe_load((ROOT / "config" / "topics.example.yaml").read_text(encoding="utf-8"))


def _built(pool, **facts):
    scope = ScopeFacts(**facts)
    selection = select(scope)
    return build_registry(pool, selection), selection, scope


def _topic(registry, name):
    return next(t for t in registry["topics"] if t["name"] == name)


def test_the_pool_is_the_shipped_example_and_load_pool_reads_it(pool):
    assert load_pool() == pool
    assert len(pool["topics"]) == 33


def test_empty_facts_fold_the_four_below_the_bar_and_move_their_anchors():
    pool = load_pool()
    registry, _, _ = _built(pool)

    assert len(registry["topics"]) == 16
    identity = _topic(registry, "Identity Lifecycle & Access Review")
    # Remote & Third-Party Access, whole: AC-17, 18, 19, 20 and 22.
    assert {"AC-17", "AC-18", "AC-19", "AC-20", "AC-22"} <= set(identity["nist_controls"])
    assert "Third-party access register" in identity["evidence"]
    # The receiver's owner stays accountable; the donor's is named.
    assert identity["owner"] == "IAM Engineering"
    assert "Remote & Third-Party Access" in identity["description"]
    assert "Network Security contributes" in identity["description"]
    # Personnel Security's controls land in Workforce Security Training.
    training = _topic(registry, "Workforce Security Training")
    assert {"PS-1", "PS-2", "PS-9"} <= set(training["nist_controls"])
    # Absent, not folded: nothing says Secure Development's controls moved.
    names = {t["name"] for t in registry["topics"]}
    assert "Secure Development Lifecycle" not in names
    assert "Privacy" not in names


def test_a_policy_no_remaining_topic_names_is_left_out_and_the_rest_are_kept():
    pool = load_pool()
    registry, _, _ = _built(pool)

    declared = {p["name"] for p in registry["policies"]}
    used = {t["policy"] for t in registry["topics"]}
    assert declared == used
    assert "AI Governance" not in declared
    assert "Secure Development" not in declared
    assert "Physical" not in declared


def test_a_user_of_ai_has_responsible_use_hold_the_categories_it_replaces():
    pool = load_pool()
    registry, _, _ = _built(pool, ai="user")

    use = _topic(registry, "Responsible Use of AI")
    assert use["nist_controls"] == [
        # AI System Inventory & Impact Assessment, as the pool has it ...
        "Map 1", "Map 2", "Map 3", "Map 5",
        # ... AI Evaluation & Measurement ...
        "Measure 1", "Measure 2", "Measure 3", "Measure 4",
        # ... AI Risk Treatment & Incident Response.
        "Manage 1", "Manage 2", "Manage 4",
    ]  # fmt: skip
    third_party = _topic(registry, "Third-Party AI & Data Supply Chain")
    assert third_party["nist_controls"] == ["Govern 6", "Map 4", "Manage 3"]
    names = {t["name"] for t in registry["topics"]}
    assert "AI Evaluation & Measurement" not in names


def test_a_builder_with_room_has_each_extra_take_one_category_from_its_parent():
    pool = load_pool()
    registry, selection, _ = _built(pool, ai="builder")

    assert selection.dropped_extras == []
    assert _topic(registry, "Data for AI Systems")["nist_controls"] == ["Map 4"]
    assert _topic(registry, "Transparency to Affected People")["nist_controls"] == ["Map 5"]
    assert _topic(registry, "Third-Party AI & Data Supply Chain")["nist_controls"] == [
        "Govern 6",
        "Manage 3",
    ]
    assert _topic(registry, "AI System Inventory & Impact Assessment")["nist_controls"] == [
        "Map 1",
        "Map 2",
        "Map 3",
    ]


def test_a_builder_at_the_ceiling_keeps_the_categories_with_the_parents():
    pool = load_pool()
    registry, selection, _ = _built(pool, **HEALTHCARE, ai="builder")

    assert sorted(selection.dropped_extras) == [
        "Data for AI Systems",
        "Transparency to Affected People",
    ]
    assert "Map 4" in _topic(registry, "Third-Party AI & Data Supply Chain")["nist_controls"]
    assert "Map 5" in _topic(registry, "AI System Inventory & Impact Assessment")["nist_controls"]
    names = {t["name"] for t in registry["topics"]}
    assert not names & {"Data for AI Systems", "Transparency to Affected People"}


def test_every_built_registry_parses_and_no_topic_is_unanchored():
    pool = load_pool()
    for facts in (
        {},
        HEALTHCARE,
        {**HEALTHCARE, "ai": "user"},
        {**HEALTHCARE, "ai": "builder"},
        {"ai": "builder"},
    ):
        scope = ScopeFacts(**facts)
        text = render(pool, scope, select(scope))
        registry = parse_registry(yaml.safe_load(text))
        assert registry.undeclared_policies() == {}, facts
        assert all(t.nist_controls for t in registry.topics), facts


def test_across_every_combination_no_anchor_is_claimed_by_two_topics_and_none_is_lost():
    """The pool claims each anchor once, so a built registry that claims one
    twice moved it wrong, and one that lost it dropped a control silently.
    The lost set is checked against the topics the rule left absent, written
    here by hand."""
    pool = load_pool()
    pool_anchors = {t["name"]: list(t.get("nist_controls") or []) for t in pool["topics"]}
    absent_when_off = {
        "builds_software": ["Secure Development Lifecycle"],
        "own_facilities": ["Physical & Environmental Security"],
        "personal_data": ["Privacy", "HIPAA Privacy Practices", "Breach Notification"],
    }
    flags = (None, True, False)
    for software, facilities, data, baseline, ai in itertools.product(
        flags, flags, (None, "none", "pii", "phi"), (None, "800-53"), (None, "user", "builder")
    ):
        scope = ScopeFacts(software, facilities, data, baseline, ai)
        registry, _, _ = _built(pool, **scope.declared())
        seen: dict[str, str] = {}
        for topic in registry["topics"]:
            for anchor in topic["nist_controls"]:
                assert anchor not in seen, (scope, anchor, seen[anchor], topic["name"])
                seen[anchor] = topic["name"]
        expected_absent: set[str] = set()
        if not software:
            expected_absent |= set(absent_when_off["builds_software"])
        if not facilities:
            expected_absent |= set(absent_when_off["own_facilities"][:1])
        if data not in ("pii", "phi"):
            expected_absent |= {"Privacy"}
        if data != "phi":
            expected_absent |= {"HIPAA Privacy Practices", "Breach Notification"}
        if ai not in ("user", "builder"):
            expected_absent |= {
                "AI Governance & Accountability",
                "AI System Inventory & Impact Assessment",
                "AI Evaluation & Measurement",
                "AI Risk Treatment & Incident Response",
                "Third-Party AI & Data Supply Chain",
            }
        lost = {
            anchor
            for name, anchors in pool_anchors.items()
            for anchor in anchors
            if anchor not in seen
        }
        lost_expected = {a for name in expected_absent for a in pool_anchors[name]}
        assert lost == lost_expected, (scope, sorted(lost ^ lost_expected))


def test_a_pool_missing_a_topic_the_rule_names_refuses_by_name(pool):
    broken = {
        **pool,
        "topics": [t for t in pool["topics"] if t["name"] != "Encryption & Key Management"],
    }

    with pytest.raises(PoolError, match="Encryption & Key Management"):
        build_registry(broken, select(ScopeFacts()))


def test_the_header_names_the_facts_the_merges_and_what_nobody_declared():
    pool = load_pool()
    scope = ScopeFacts(personal_data="phi", ai="builder", builds_software=True)

    text = header(scope, select(scope))

    assert "builds_software=true" in text
    assert "personal_data=phi" in text
    assert "Not declared, so treated as off: own_facilities, baseline" in " ".join(
        line.lstrip("# ") for line in text.splitlines()
    )
    assert "Encryption & Key Management folds into Data Protection" in " ".join(
        line.lstrip("# ") for line in text.splitlines()
    )
    assert pool  # the pool fixture is the same file `init` reads


def test_a_written_registry_is_all_comments_then_one_mapping():
    pool = load_pool()
    scope = ScopeFacts()

    text = render(pool, scope, select(scope))

    head, _, body = text.partition("\n\ntopics:\n")
    assert all(line.startswith("#") for line in head.splitlines())
    assert set(yaml.safe_load("topics:\n" + body)) == {"topics", "policies"}


def test_the_header_names_the_areas_that_are_on_only_by_the_unverified_columns():
    """Hand-written from 5921: Endpoint, Governance, Risk, Asset, Vulnerability
    and Media reach the bar of 3 only with the HITRUST or ISO column counted."""
    pool = load_pool()
    scope = ScopeFacts()

    text = " ".join(line.lstrip("# ") for line in header(scope, select(scope)).splitlines())

    assert "Only on if the unverified columns of the table are counted (hitrust, iso):" in text
    for area in (
        "Endpoint and malware",
        "Governance and programme",
        "Risk management",
        "Asset management",
        "Vulnerability management",
        "Media handling",
    ):
        assert area in text
    assert "Access control" not in text.split("Only on if")[1]
    assert pool
