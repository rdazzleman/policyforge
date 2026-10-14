"""The topic rule (22, part 3; 80's rulings on comment 6445): counts and
names from hand-written cases, not from the table under test."""

from __future__ import annotations

import itertools

import pytest

from policyforge.topics.convergence import AREAS, BAR, CEILING, UNVERIFIED
from policyforge.topics.rule import select
from policyforge.topics.scope_facts import ScopeFacts

# ---- hand-written from 5925, 5927 and 6445 ---------------------------------------

CORE_16 = [
    "Identity Lifecycle & Access Review",
    "Authentication & Credential Management",
    "Network & Boundary Protection",
    "Incident Response",
    "Change & Configuration Management",
    "Backup, Restore & Contingency",
    "Data Protection",
    "Logging, Monitoring & Alerting",
    "Endpoint Protection & Device Controls",
    "Business Associate & Vendor Risk",
    "Workforce Security Training",
    "Security Program Governance",
    "Risk Assessment & Authorization",
    "Asset Management",
    "Vulnerability & Patch Management",
    "Media Handling & Disposal",
]
HEALTHCARE_SAAS_ADDS = [
    "Secure Development Lifecycle",
    "Privacy",
    "HIPAA Privacy Practices",
    "Breach Notification",
    "Personnel Security",
    "System Maintenance",
]
AI_USER = [
    "AI Governance & Accountability",
    "Responsible Use of AI",
    "Third-Party AI & Data Supply Chain",
]
AI_BUILDER = [
    "AI Governance & Accountability",
    "AI System Inventory & Impact Assessment",
    "AI Evaluation & Measurement",
    "AI Risk Treatment & Incident Response",
    "Third-Party AI & Data Supply Chain",
]
AI_EXTRAS = ["Data for AI Systems", "Transparency to Affected People"]

HEALTHCARE = {
    "builds_software": True,
    "own_facilities": False,
    "personal_data": "phi",
    "baseline": "800-53",
}
EVERYTHING = {**HEALTHCARE, "own_facilities": True}


def _names(selection):
    return sorted(selection.topics)


def _ceiling_merges(selection):
    return [m for m in selection.merges if m.why.startswith("the ceiling")]


def test_empty_facts_give_sixteen_and_fold_the_four_below_the_bar_by_name():
    selection = select(ScopeFacts())

    assert _names(selection) == sorted(CORE_16)
    assert selection.count == 16
    assert {m.area: m.into for m in selection.merges} == {
        "cryptography": "Data Protection",
        "personnel": "Workforce Security Training",
        "maintenance": "Change & Configuration Management",
        "remote_access": "Identity Lifecycle & Access Review",
    }
    assert selection.undeclared == [
        "builds_software",
        "own_facilities",
        "personal_data",
        "baseline",
        "ai",
    ]


def test_healthcare_saas_with_no_ai_gives_twenty_two():
    selection = select(ScopeFacts(**HEALTHCARE, ai="none"))

    assert _names(selection) == sorted(CORE_16 + HEALTHCARE_SAAS_ADDS)
    assert selection.count == 22
    assert {m.area for m in selection.merges} == {"cryptography", "remote_access"}


def test_healthcare_saas_with_ai_user_gives_twenty_five_with_no_ceiling_merge():
    selection = select(ScopeFacts(**HEALTHCARE, ai="user"))

    assert _names(selection) == sorted(CORE_16 + HEALTHCARE_SAAS_ADDS + AI_USER)
    assert selection.count == 25
    assert selection.dropped_extras == []
    assert _ceiling_merges(selection) == []


def test_healthcare_saas_with_ai_builder_is_trimmed_to_twenty_five_and_the_merges_are_named():
    selection = select(ScopeFacts(**HEALTHCARE, ai="builder"))

    # 22 + 5 = 27. The extras are not in; the two lowest non-core areas fold.
    assert selection.count == 25
    assert sorted(selection.dropped_extras) == sorted(AI_EXTRAS)
    assert not set(AI_EXTRAS) & set(selection.topics)
    assert set(AI_BUILDER) <= set(selection.topics)
    assert [(m.area, m.into) for m in _ceiling_merges(selection)] == [
        ("maintenance", "Change & Configuration Management"),
        ("personnel", "Workforce Security Training"),
    ]
    assert "System Maintenance" not in selection.topics
    assert "Personnel Security" not in selection.topics


def test_every_conditional_on_with_ai_builder_is_exactly_twenty_five_extras_gone_merges_named():
    selection = select(ScopeFacts(**EVERYTHING, ai="builder"))

    assert selection.count == 25
    assert not set(AI_EXTRAS) & set(selection.topics)
    assert set(AI_BUILDER) <= set(selection.topics)
    # Lowest score first: Maintenance 1.0, Personnel 1.5, then Physical 3.0.
    merges = _ceiling_merges(selection)
    assert [m.area for m in merges] == ["maintenance", "personnel", "physical"]
    assert all(m.line().startswith(m.donors[0]) for m in merges)
    for kept in ("Secure Development Lifecycle", "Privacy", "HIPAA Privacy Practices"):
        assert kept in selection.topics


def test_a_builder_with_room_keeps_both_extras():
    selection = select(ScopeFacts(ai="builder"))

    assert selection.count == 16 + 5 + 2
    assert set(AI_EXTRAS) <= set(selection.topics)
    assert selection.dropped_extras == []


def test_a_user_of_ai_gets_three_and_never_the_extras_or_the_builders_other_two():
    selection = select(ScopeFacts(ai="user"))

    assert selection.count == 16 + 3
    assert set(AI_USER) <= set(selection.topics)
    assert not set(AI_EXTRAS) & set(selection.topics)
    assert "AI Evaluation & Measurement" not in selection.topics


def test_pii_alone_adds_privacy_and_not_the_hipaa_topics():
    selection = select(ScopeFacts(personal_data="pii"))

    assert "Privacy" in selection.topics
    assert "HIPAA Privacy Practices" not in selection.topics
    assert selection.count == 17


def test_the_facts_off_leave_secure_development_and_physical_absent_not_folded():
    selection = select(ScopeFacts(builds_software=False, own_facilities=False))

    assert "Secure Development Lifecycle" not in selection.topics
    assert "Physical & Environmental Security" not in selection.topics
    assert not {m.area for m in selection.merges} & {"secure_development", "physical"}


def test_a_baseline_keeps_personnel_security_and_maintenance_and_stops_their_folding():
    off = select(ScopeFacts(baseline="none"))
    on = select(ScopeFacts(baseline="fedramp"))

    assert {"personnel", "maintenance"} <= {m.area for m in off.merges}
    assert {"Personnel Security", "System Maintenance"} <= set(on.topics)
    assert not {"personnel", "maintenance"} & {m.area for m in on.merges}


def test_no_combination_of_facts_exceeds_the_ceiling_and_the_ai_core_always_stays():
    flags = (None, True, False)
    combos = itertools.product(
        flags,
        flags,
        (None, "none", "pii", "phi"),
        (None, "none", "800-53", "fedramp", "govramp"),
        (None, "none", "user", "builder"),
    )
    for software, facilities, data, baseline, ai in combos:
        facts = ScopeFacts(software, facilities, data, baseline, ai)
        selection = select(facts)
        assert selection.count <= CEILING, facts
        assert len(set(selection.topics)) == selection.count, f"a topic twice: {facts}"
        if ai == "builder":
            assert set(AI_BUILDER) <= set(selection.topics), facts
        if ai == "user":
            assert set(AI_USER) <= set(selection.topics), facts
        assert set(CORE_16) <= set(selection.topics), facts


def test_no_core_area_is_ever_folded_at_the_ceiling():
    selection = select(ScopeFacts(**EVERYTHING, ai="builder"))

    core_areas = {a.id for a in AREAS if a.meets_bar and not a.condition}
    assert not core_areas & {m.area for m in _ceiling_merges(selection)}


def test_the_scores_are_the_tables_n_column():
    """5921's n, a partial match read as half: the rows the counts rest on,
    written out here by hand."""
    expected = {
        "access": 5.0,
        "network": 5.0,
        "incident": 5.0,
        "config": 5.0,
        "backup": 5.0,
        "data_protection": 5.0,
        "logging": 4.0,
        "endpoint": 4.0,
        "supplier": 4.0,
        "awareness": 4.0,
        "governance": 3.5,
        "risk": 3.0,
        "asset": 3.0,
        "vulnerability": 3.5,
        "cryptography": 2.0,
        "secure_development": 3.5,
        "physical": 3.0,
        "media": 3.0,
        "personnel": 1.5,
        "maintenance": 1.0,
        "remote_access": 1.0,
    }

    assert {a.id: a.score for a in AREAS if a.id in expected} == expected
    assert BAR == 3.0


def test_every_column_names_its_source_and_the_unverified_ones_are_5921s_two():
    from policyforge.topics.convergence import FRAMEWORKS, SOURCES

    assert set(SOURCES) == set(FRAMEWORKS)
    assert all(text and isinstance(ok, bool) for text, ok in SOURCES.values())
    assert set(UNVERIFIED) == {"hitrust", "iso"}


@pytest.mark.parametrize("facts", [{"ai": "builder"}, {"ai": "user"}, {}])
def test_the_selection_is_a_pure_function_of_the_facts(facts):
    assert select(ScopeFacts(**facts)) == select(ScopeFacts(**facts))


def test_a_tie_at_the_ceiling_goes_to_the_later_table_row(monkeypatch):
    """80's R3 (6445): ties by later table row. The shipped table never ties
    where the ceiling binds (the only tie, Privacy and the HIPAA topics at 5,
    is never reached), so the rule is held against a hand-built table."""
    from policyforge.topics import rule
    from policyforge.topics.convergence import Area, _row

    core = Area("core", "Core", _row("c | c | c | c | c"), ("Receiver",))
    first = Area(
        "first", "First", _row("x | x | x | - | -"), ("First topic",), "builds_software", "Receiver"
    )
    second = Area(
        "second",
        "Second",
        _row("y | y | y | - | -"),
        ("Second topic",),
        "own_facilities",
        "Receiver",
    )
    monkeypatch.setattr(rule, "AREAS", (core, first, second))
    monkeypatch.setattr(rule, "CEILING", 2)

    selection = rule.select(ScopeFacts(builds_software=True, own_facilities=True))

    assert [m.area for m in selection.merges] == ["second"]
    assert set(selection.topics) == {"Receiver", "First topic"}
