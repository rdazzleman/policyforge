"""Three shipped READMEs, held to the catalogs they describe.

`nist-800-171-r3`, `hipaa-security-rule`, `cfr-171-information-blocking`,
`cfr-42-part-2-sud-records` and `nist-ai-rmf` each already have one of
these. `arc-ampe`, `fedramp` and `nist-800-53-r5` did not — and these
files ship inside the wheel, so a stale number reaches a reader who has
no way to check it.

**One of them was wrong.** ARC-AMPE's README said *95 of the 402
guidance cells say there is no guidance*; the catalog has 96, and so
does CMS's workbook. Nothing could have caught it: the claim was prose
in a file no test opened.

## Two rules these follow, both learned from getting them wrong

**Collapse whitespace, never match a formatted line.** `mdformat` runs in
the gate and rewraps prose and re-pads table cells. A test asserting
`"| Govern | 6 | 19 |" in readme` goes red the moment the repository's
own formatter runs — and a test that fails when the formatter runs is a
test that gets deleted rather than fixed.

**Derive every figure independently and check any total last.** A
neighbouring README table was once wrong in two cells and right in its
total, because the cells were written to sum to a figure already known.
Agreement of a total is evidence only when the parts were not
constructed from it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

FRAMEWORKS = Path(__file__).resolve().parent.parent / "data" / "frameworks"


def _catalog(name: str) -> list[dict]:
    return json.loads((FRAMEWORKS / name / "controls.json").read_text(encoding="utf-8"))


def _readme(name: str) -> str:
    """The README with whitespace collapsed, so a reflow changes nothing."""
    return " ".join((FRAMEWORKS / name / "README.md").read_text(encoding="utf-8").split())


def _stated(readme: str, pattern: str) -> list[int]:
    match = re.search(pattern, readme)
    assert match, f"the README no longer states this: {pattern}"
    return [int(g) for g in match.groups()]


# --------------------------------------------------------------------------
# ARC-AMPE — the one that was wrong
# --------------------------------------------------------------------------


def test_arc_ampe_states_its_own_totals():
    controls = _catalog("arc-ampe")
    enhancements = sum(len(c["enhancements"]) for c in controls)
    items, stated_controls, stated_enh = _stated(
        _readme("arc-ampe"), r"(\d+) items \((\d+) controls, (\d+) enhancements\)"
    )

    # Parts first, total last: the total is only evidence if the parts
    # were not derived from it.
    assert stated_controls == len(controls)
    assert stated_enh == enhancements
    assert items == len(controls) + enhancements


def test_arc_ampe_states_how_many_cells_carry_no_guidance():
    """**The claim that was wrong.** 95 against an actual 96 — one row,
    in a file that ships in the wheel, unreadable by anyone who has not
    got CMS's workbook open beside it."""
    controls = _catalog("arc-ampe")
    empty = sum(1 for c in controls if not c["discussion"].strip())
    empty += sum(
        1 for c in controls for e in c["enhancements"] if not e["additional_requirements"].strip()
    )
    stated, of_total = _stated(_readme("arc-ampe"), r"(\d+) of the (\d+) guidance cells")

    assert stated == empty
    assert of_total == len(controls) + sum(len(c["enhancements"]) for c in controls)


def test_arc_ampe_claims_every_item_crosswalks():
    """`all 402 crosswalk onto their 800-53 equivalents` is a claim about
    the shipped file, not about what the loader can do."""
    controls = _catalog("arc-ampe")
    items = [*controls, *(e for c in controls for e in c["enhancements"])]
    crosswalked = [i for i in items if i["source_crosswalk"]]
    (stated,) = _stated(_readme("arc-ampe"), r"so all (\d+) crosswalk onto")

    assert stated == len(items)
    assert len(crosswalked) == len(items), "the README says every item crosswalks"


# --------------------------------------------------------------------------
# FedRAMP — accurate, and its figures are the least obvious here
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fedramp_items() -> list[dict]:
    controls = _catalog("fedramp")
    return [*controls, *(e for c in controls for e in c["enhancements"])]


def test_fedramp_states_how_many_parameter_values_it_carries(fedramp_items):
    """**Values, not items.** 19 parameter values spread across 15
    entries, and counting entries gives 15 — the wrong answer to a
    question that reads the same in English."""
    values = sum(len(i["parameter_values"]) for i in fedramp_items)
    (stated,) = _stated(
        _readme("fedramp"), r"parameter id the 800-53 prose carries.*?\. (\d+) of them"
    )

    assert stated == values


def test_fedramp_states_how_many_carry_guidance(fedramp_items):
    carry = [i for i in fedramp_items if i["additional_requirements"].strip()]
    (stated,) = _stated(_readme("fedramp"), r"(\d+) controls carry it")

    assert stated == len(carry)


def test_fedramp_states_how_many_controls_it_tailors(fedramp_items):
    """**`the 79 controls FedRAMP tailors` is a union, not a total.**

    The catalog holds 85 items. 79 carry at least one of the two things
    a profile adds, and the two sets happen to be disjoint — 15 with
    parameters, 64 with guidance. Reading 79 as "the catalog's size"
    gets 85 and reading it as either column alone gets 15 or 64; only
    the union is right, and nothing in the sentence says so.
    """
    tailored = [
        i for i in fedramp_items if i["parameter_values"] or i["additional_requirements"].strip()
    ]
    (stated,) = _stated(_readme("fedramp"), r"for the (\d+)\s*controls FedRAMP tailors")

    assert stated == len(tailored)


# --------------------------------------------------------------------------
# NIST 800-53 Rev 5 — the largest catalog, and untested until now
# --------------------------------------------------------------------------


def test_nist_800_53_states_its_own_totals():
    controls = _catalog("nist-800-53-r5")
    enhancements = sum(len(c["enhancements"]) for c in controls)
    stated_controls, stated_enh = _stated(
        _readme("nist-800-53-r5"), r"\*\*(\d+) controls and (\d+) enhancements\*\*"
    )

    assert stated_controls == len(controls)
    assert stated_enh == enhancements


def test_nist_800_53_states_what_each_baseline_selects():
    """`149 / 287 / 370` — three numbers a reader cannot check without
    the OSCAL profiles, and the figure most likely to drift silently
    when a revision moves a control between baselines."""
    controls = _catalog("nist-800-53-r5")
    items = [*controls, *(e for c in controls for e in c["enhancements"])]
    selected = {
        name: sum(1 for i in items if name in (i["baseline"] or ""))
        for name in ("Low", "Moderate", "High")
    }
    low, moderate, high = _stated(_readme("nist-800-53-r5"), r"\*\*(\d+) / (\d+) / (\d+)\*\* items")

    assert [low, moderate, high] == [selected["Low"], selected["Moderate"], selected["High"]]
