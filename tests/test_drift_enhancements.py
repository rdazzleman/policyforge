"""Drift reports an enhancement change under the enhancement's own id (#369).

`diff_catalogs` compared enhancements by id alone, so a reworded one was
never reported: measured on the shipped catalogs, 0 changes for 1,573
enhancements across 8 catalogs -- every 800-53 enhancement, AI RMF
subcategory and HIPAA implementation specification. 80's ruling: report a
reworded, added or removed enhancement under its own id, and keep the
parent's `fields=["enhancements"]` for an add or remove, as a pointer.

The catalogs under test are every shipped catalog that has enhancements,
read from the tree, so one added later is covered without anyone typing it.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from policyforge.frameworks.drift import ADDED, CHANGED, REMOVED, analyze_drift, diff_catalogs
from policyforge.ingest.schema import load_controls

CATALOGS = Path(__file__).resolve().parent.parent / "data" / "frameworks"
WITH_ENHANCEMENTS = sorted(
    d.name
    for d in CATALOGS.iterdir()
    if (d / "controls.json").exists()
    and any(c.enhancements for c in load_controls(d / "controls.json"))
)


def _pair(directory: str):
    old = load_controls(CATALOGS / directory / "controls.json")
    return old, copy.deepcopy(old)


def _first_enhancement(controls):
    return next(e for c in controls if c.enhancements for e in c.enhancements)


def test_the_premise_eight_catalogs_have_enhancements():
    assert len(WITH_ENHANCEMENTS) >= 8, WITH_ENHANCEMENTS


@pytest.mark.parametrize("directory", WITH_ENHANCEMENTS)
def test_a_reworded_enhancement_is_reported_under_its_own_id(directory):
    """The probe that found #369, as a test: 0 changes before, 1 now."""
    old, new = _pair(directory)
    target = _first_enhancement(new)
    target.description = (target.description or "") + " Reworded upstream."

    (change,) = diff_catalogs(old, new)
    assert (change.control_id, change.kind) == (target.enhancement_id, CHANGED)
    assert change.fields == ["description"] and change.substantive


@pytest.mark.parametrize("directory", WITH_ENHANCEMENTS)
def test_whitespace_and_crlf_alone_report_nothing(directory):
    old, new = _pair(directory)
    target = _first_enhancement(new)
    target.description = "  " + (target.description or "").replace(" ", "  ").replace("\n", "\r\n")
    assert diff_catalogs(old, new) == []


def test_an_added_and_a_removed_enhancement_are_reported_under_their_own_ids():
    old, new = _pair("nist-800-53-r5")
    control = next(c for c in new if len(c.enhancements) >= 2)
    gone = control.enhancements.pop(0)
    arrived = copy.deepcopy(control.enhancements[0])
    arrived.enhancement_id = f"{control.control_id}(99)"
    control.enhancements.append(arrived)

    changes = {(c.control_id, c.kind): c for c in diff_catalogs(old, new)}
    assert (gone.enhancement_id, REMOVED) in changes
    assert (arrived.enhancement_id, ADDED) in changes
    # The parent is kept, as a pointer to where they hang. (It may also list
    # `parameters`: a parent's parameter ids include its enhancements'.)
    assert "enhancements" in changes[(control.control_id, CHANGED)].fields


def test_a_required_specification_becoming_addressable_is_substantive():
    """HIPAA's Required/Addressable is the enhancement's baseline."""
    old, new = _pair("hipaa-security-rule")
    target = next(e for e in _all(new) if e.baseline == "Required")
    target.baseline = "Addressable"

    (change,) = diff_catalogs(old, new)
    assert change.control_id == target.enhancement_id
    assert change.fields == ["baseline"] and change.substantive


def test_a_retitled_enhancement_is_editorial():
    old, new = _pair("nist-800-53-r5")
    target = _first_enhancement(new)
    target.title = (target.title or "") + " (retitled)"

    (change,) = diff_catalogs(old, new)
    assert change.fields == ["title"] and not change.substantive


def test_a_reworded_ai_rmf_subcategory_reaches_the_topic_anchoring_its_category():
    """80's end-to-end loop for #339: through the real drift path, which
    never carried a subcategory id before this change."""
    old, new = _pair("nist-ai-rmf")
    target = next(e for e in _all(new) if e.enhancement_id == "Govern 1.1")
    target.description += " Reworded upstream."
    topics = [
        SimpleNamespace(name="AI Governance", nist_controls=["Govern 1"]),
        SimpleNamespace(name="Other", nist_controls=["Govern 2"]),
    ]

    report = analyze_drift(old, new, topics=topics)
    assert [c.control_id for c in report.changes] == ["Govern 1.1"]
    assert report.impacts["Govern 1.1"].topics == ["AI Governance"]


def _all(controls):
    return [e for c in controls for e in c.enhancements]
