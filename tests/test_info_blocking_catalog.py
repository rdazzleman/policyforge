"""The shipped 45 CFR 171 catalog, held to the README that describes it.

Every sibling catalog has a file like this — 800-171, HIPAA, Part 2 — and
this one did not, which is why its README could say `55 conditions` after
the catalog had 54 with nothing noticing. `test_info_blocking_loader.py`
holds the *parse*; this holds the *artefact* and the prose about it.

The two are not interchangeable. A loader test proves the parser is right
and says nothing about what was committed; a catalog test proves what was
committed is what the parser produces and says nothing about whether the
parser is right. Both, or a regeneration nobody ran can ship.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

CATALOG = Path(__file__).resolve().parent.parent / "data" / "frameworks"
CATALOG = CATALOG / "cfr-171-information-blocking"


@pytest.fixture(scope="module")
def controls() -> list[dict]:
    return json.loads((CATALOG / "controls.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def readme() -> str:
    """Whitespace collapsed, so an `mdformat` reflow that changes nothing
    does not break an assertion about what the README says."""
    return " ".join((CATALOG / "README.md").read_text(encoding="utf-8").split())


def test_the_readme_counts_match_the_catalog(controls, readme):
    """The claim that went stale, now pinned to the file it describes."""
    conditions = [e for c in controls for e in c["enhancements"]]
    stated = re.search(r"(\d+) sections carrying (\d+) conditions", readme)

    assert stated, "the README no longer states its counts"
    assert int(stated.group(1)) == len(controls)
    assert int(stated.group(2)) == len(conditions)


def test_the_readme_title_count_matches(controls, readme):
    """`39 of which` is the half a reader cannot check by counting rows,
    so it is the half most able to drift unnoticed."""
    titled = [e for c in controls for e in c["enhancements"] if e["title"].strip()]
    stated = re.search(r"(\d+) of which the regulation gives its own italic heading", readme)

    assert stated, "the README no longer states how many conditions are titled"
    assert int(stated.group(1)) == len(titled)


def test_no_shipped_condition_is_merely_reserved(controls):
    """The catalog-side half of the loader's assertion.

    The loader test proves `parse_information_blocking` drops these. It
    cannot prove the committed file was regenerated after that change —
    `controls.json` is a static artefact, and breaking the parser moves
    nothing in it. This is the half that catches a fix that was written
    and never run.
    """
    reserved = [
        e["enhancement_id"]
        for c in controls
        for e in c["enhancements"]
        if "reserved" in e["description"].lower()
    ]

    assert reserved == []


def test_the_readme_says_which_paragraph_was_excluded(controls, readme):
    """A reader who follows `171.1001(a)` into the regulation meets a
    `(b)` that is not here. The README has to say why before they go
    looking for a parse bug — the same debt `171.402` was already paying.
    """
    ids = {e["enhancement_id"] for c in controls for e in c["enhancements"]}

    assert "171.1001(b)" not in ids
    assert "171.1001(a)" in ids, "the live condition must survive the exclusion"
    assert "`171.1001(b)` is `[Reserved]`" in readme
