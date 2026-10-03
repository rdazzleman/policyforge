"""NIST's Rev 4 to Rev 5 comparison workbook, read by `ingest/nist_rev4_rev5`.

These tests lived in the SOC 2 suite, which used the workbook to flag mapped
ids that changed since Rev 4. The SOC 2 parser left this tree for the
licensed-framework plugin (#32, part 3); the workbook reader is public, so
its tests stay here. The workbook is a US government work, pinned by hash
in `tests/fixtures/`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from policyforge.ingest import nist_rev4_rev5

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "tests" / "fixtures" / "sp800-53r4-to-r5-comparison-workbook.xlsx"


@pytest.fixture(scope="module")
def changed():
    return nist_rev4_rev5.parse(WORKBOOK.read_bytes())


@pytest.fixture(scope="module")
def rev5_ids():
    rows = json.loads(
        (ROOT / "data" / "frameworks" / "nist-800-53-r5" / "controls.json").read_text("utf-8")
    )
    return {r["control_id"] for r in rows} | {
        e["enhancement_id"] for r in rows for e in r["enhancements"]
    }


def test_the_workbook_is_its_pinned_extent(changed):
    assert (len(changed), sum(changed.values())) == nist_rev4_rev5.EXTENT == (1189, 698)


def test_a_parse_of_another_extent_is_refused(monkeypatch):
    monkeypatch.setattr(nist_rev4_rev5, "EXTENT", (1189, 697))
    with pytest.raises(nist_rev4_rev5.Rev4Rev5Error, match="Refusing"):
        nist_rev4_rev5.parse(WORKBOOK.read_bytes())


def test_a_different_workbook_is_refused():
    with pytest.raises(nist_rev4_rev5.Rev4Rev5Error, match="not the pinned"):
        nist_rev4_rev5.parse(b"not the workbook")


def test_every_id_the_rev5_catalog_lacks_is_labelled_withdrawn(changed, rev5_ids):
    """NIST's OSCAL Rev 5 marks all 182 such ids withdrawn (ba, measured on #410)."""
    labels = [nist_rev4_rev5.label(i, changed, rev5_ids) for i in changed if i not in rev5_ids]
    assert len(labels) == 182
    assert set(labels) == {nist_rev4_rev5.WITHDRAWN}


def test_labels_follow_nists_column(changed, rev5_ids):
    assert nist_rev4_rev5.label("AC-2", changed, rev5_ids) == nist_rev4_rev5.CHANGED
    assert changed["AC-3"] in (True, False)
    unchanged = next(i for i, flag in changed.items() if not flag and i in rev5_ids)
    assert nist_rev4_rev5.label(unchanged, changed, rev5_ids) is None
    assert nist_rev4_rev5.label("ZZ-9", changed, rev5_ids) is None, "the caller refuses it"
