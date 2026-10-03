"""A changed control reaches the topics its catalog's rule says it does (#339).

`assess_impact` claimed "the same rule coverage.py uses" and used the
800-53 grammar (`AC-2(3)` -> `AC-2`) for every catalog, so an AI RMF
subcategory change (`Govern 1.1`) reached no topic anchoring its category
(`Govern 1`). Now an anchored catalog (800-53, the AI RMF Core) uses
coverage's own `parent_of`; the Playbook, whose ids read like the Core's,
reaches no topic; any other catalog keeps the rule it had.

The framework names are the shipped catalogs' own, never typed here.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from policyforge.frameworks.drift import ControlChange, analyze_drift, assess_impact
from policyforge.ingest.schema import load_controls

CATALOGS = Path(__file__).resolve().parent.parent / "data" / "frameworks"


def _framework(directory: str) -> str:
    rows = json.loads((CATALOGS / directory / "controls.json").read_text(encoding="utf-8"))
    return rows[0]["framework"]


def _topic(*anchors: str):
    return SimpleNamespace(name="Topic", nist_controls=list(anchors))


def _reach(control_id: str, directory: str, *anchors: str) -> list[str]:
    impacts = assess_impact(
        [ControlChange(control_id=control_id, kind="changed")],
        topics=[_topic(*anchors)],
        framework=_framework(directory),
    )
    return impacts[control_id].topics


def test_an_ai_rmf_subcategory_change_reaches_the_topic_anchoring_its_category():
    """The issue's case: `{'Govern 1.1': []}` before this change."""
    assert _reach("Govern 1.1", "nist-ai-rmf", "Govern 1") == ["Topic"]
    assert _reach("Govern 1.1", "nist-ai-rmf", "Govern 1.1") == ["Topic"]
    assert _reach("Govern 1.1", "nist-ai-rmf", "Govern 2") == []


def test_an_800_53_enhancement_still_reaches_the_topic_anchoring_its_control():
    assert _reach("AC-2(3)", "nist-800-53-r5", "AC-2") == ["Topic"]
    assert _reach("AC-2", "nist-800-53-r5", "AC-2(3)") == []


@pytest.mark.parametrize("anchor", ["Govern 1", "Govern 1.1"])
def test_a_playbook_change_reaches_no_topic(anchor):
    """The Playbook's `Govern 1.1` is NIST's suggestion under that
    subcategory, never anchored; neither the category nor the Core's own
    `Govern 1.1` owns it."""
    assert _reach("Govern 1.1", "nist-ai-rmf-playbook", anchor) == []


def _shipped_drift(directory: str, control_id: str, *anchors: str) -> list[str]:
    """A real change to one control of a shipped catalog, through `analyze_drift`,
    so the crosswalk is the one that catalog actually carries."""
    old = load_controls(CATALOGS / directory / "controls.json")
    new = copy.deepcopy(old)
    target = next(c for c in new if c.control_id == control_id)
    target.control_statement += " Reworded upstream."
    report = analyze_drift(old, new, topics=[_topic(*anchors)])
    return report.impacts[control_id].topics


def test_a_catalog_reached_through_a_crosswalk_reaches_through_it():
    """80's ruling (B) on #377: a catalog topics do not anchor reaches them
    through its crosswalk to 800-53, not by id shape. Given no crosswalk,
    FedRAMP's `AC-2(3)` reaches nothing, though it reads like 800-53's."""
    assert _reach("AC-2(3)", "fedramp", "AC-2") == []


def test_fedramp_drift_is_unchanged_through_its_own_crosswalk():
    """80's condition (3): FedRAMP asserted unchanged. Its shipped crosswalk
    maps each id to 800-53's same id, so the topic anchoring AC-6 is reached
    as it was by id shape, and one anchoring a different control is not."""
    assert _shipped_drift("fedramp", "AC-6", "AC-6") == ["Topic"]
    assert _shipped_drift("fedramp", "AC-6", "AC-2") == []


def test_an_800_171_change_reaches_the_topics_owning_what_nist_maps_it_to():
    """New in #377: 800-171 reached no topic in drift before. NIST's own
    mapping sends 03.01.01 to AC-2 and three of its enhancements."""
    assert _shipped_drift("nist-800-171-r3", "03.01.01", "AC-2") == ["Topic"]
    assert _shipped_drift("nist-800-171-r3", "03.01.01", "CP-9") == []


def test_the_real_drift_path_carries_the_framework():
    """End to end through `analyze_drift` with the shipped Playbook: its
    controls are keyed `Govern 1.1` at the top level, so a reworded one is a
    change with that id, and it matched a topic anchoring the Core's
    `Govern 1.1` exactly before this change."""
    old = load_controls(CATALOGS / "nist-ai-rmf-playbook" / "controls.json")
    new = copy.deepcopy(old)
    target = next(c for c in new if c.control_id == "Govern 1.1")
    target.control_statement += " Reworded upstream."
    report = analyze_drift(old, new, topics=[_topic("Govern 1.1", "Govern 1")])
    assert [c.control_id for c in report.changes] == ["Govern 1.1"]
    assert report.impacts["Govern 1.1"].topics == []
