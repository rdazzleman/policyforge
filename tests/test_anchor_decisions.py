"""Every catalog says whether a topic may anchor it (#175, with #183).

Adding a catalog was forced to declare its licence, its bundled-or-BYOC
status, its alias key and its README row -- and never whether a topic may
anchor it, the one declaration that decides whether its requirements enter
`/coverage` and synthesis. Both wrong directions shipped silently: #171 (the
AI topics anchored, `/coverage` said 82% owned, synthesis retrieved zero)
and #169 (a catalog counted in scope that nobody adopted).

The populations below are DERIVED -- from the directories under
`data/frameworks/` and from the scaffold lists -- so a new catalog joins them
without anyone remembering to list it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from policyforge.ingest.schema import load_controls
from policyforge.mapping.crosswalk import (
    ANCHOR_DECISIONS,
    ANCHORS,
    TOPIC_ANCHORS,
    build_crosswalk,
    normalize_framework,
)
from policyforge.scaffold import BUNDLED_CATALOGS, BYOC_CATALOGS

FRAMEWORKS = Path(__file__).resolve().parent.parent / "data" / "frameworks"


def _catalog_dirs() -> set[str]:
    on_disk = {p.name for p in FRAMEWORKS.iterdir() if p.is_dir()}
    assert len(on_disk) >= 10, f"only {len(on_disk)} catalog directories; the population is wrong"
    return on_disk | set(BUNDLED_CATALOGS) | set(BYOC_CATALOGS)


def _keys(directory: str) -> set[str]:
    """The framework key(s) a shipped catalog's controls declare."""
    return {
        normalize_framework(c.framework)
        for c in load_controls(FRAMEWORKS / directory / "controls.json")
    }


def _anchoring() -> list[str]:
    return sorted(d for d, decision in ANCHOR_DECISIONS.items() if decision == ANCHORS)


# --- the forcing function ----------------------------------------------------


def test_every_catalog_has_an_anchor_decision_and_every_decision_a_catalog():
    """A catalog added without an entry fails here by name, and so does an
    entry left behind by a catalog that was removed."""
    catalogs = _catalog_dirs()
    missing = sorted(catalogs - set(ANCHOR_DECISIONS))
    stale = sorted(set(ANCHOR_DECISIONS) - catalogs)
    assert not missing, f"say in ANCHOR_DECISIONS whether a topic may anchor: {missing}"
    assert not stale, f"ANCHOR_DECISIONS names catalogs that do not exist: {stale}"


def test_a_catalog_that_does_not_anchor_says_why():
    """A reason, not a placeholder: an empty or one-word value is a decision
    nobody made."""
    thin = sorted(
        d
        for d, decision in ANCHOR_DECISIONS.items()
        if decision != ANCHORS and len(decision.split()) < 4
    )
    assert not thin, f"give a reason, not a word: {thin}"


def test_the_decisions_agree_with_topic_anchors_in_both_directions():
    """`TOPIC_ANCHORS` is what the code consults; the decisions are what a
    person wrote. The anchoring directories' keys must BE that set -- not a
    subset -- and no other shipped catalog's key may be in it."""
    anchoring = _anchoring()
    for directory in anchoring:
        assert (FRAMEWORKS / directory / "controls.json").exists(), (
            f"{directory} anchors but ships no controls.json, so its key cannot be checked"
        )
    declared = set().union(*(_keys(d) for d in anchoring))
    assert declared == set(TOPIC_ANCHORS), (declared, set(TOPIC_ANCHORS))

    shipped = [d for d in _catalog_dirs() if (FRAMEWORKS / d / "controls.json").exists()]
    leaking = sorted(d for d in shipped if d not in anchoring and _keys(d) & TOPIC_ANCHORS)
    assert not leaking, f"these do not anchor, yet their key is in TOPIC_ANCHORS: {leaking}"


# --- #183: what anchoring must preserve ---------------------------------------


def test_no_two_anchorable_catalogs_share_an_id():
    """`synthesis/merge.py` relies on it: "at most one anchorable catalog holds
    any given id, so the loop cannot double-count". True of today's two;
    enforced here, so a third that shares an id fails rather than counts
    twice."""
    seen: dict[str, str] = {}
    shared = []
    for directory in _anchoring():
        for control in load_controls(FRAMEWORKS / directory / "controls.json"):
            for cid in [control.control_id, *(e.enhancement_id for e in control.enhancements)]:
                if cid in seen and seen[cid] != directory:
                    shared.append((cid, seen[cid], directory))
                seen.setdefault(cid, directory)
    assert not shared, f"ids held by two anchorable catalogs: {shared[:5]}"


@pytest.mark.parametrize("directory", _anchoring())
def test_both_views_see_what_a_topic_anchors(directory):
    """**#171's shape.** `/coverage` and synthesis are two views of one
    registry; they disagreed -- 82% owned, zero retrieved -- and nothing
    noticed. For every anchorable catalog, a topic anchoring one of its ids
    is counted as owning it AND gets that control back from synthesis."""
    from policyforge.synthesis.merge import build_synthesis_topic
    from policyforge.topics.coverage import analyze_coverage, split_by_adoption
    from policyforge.topics.registry import Topic

    everything = [
        c
        for d in _catalog_dirs()
        if (FRAMEWORKS / d / "controls.json").exists()
        for c in load_controls(FRAMEWORKS / d / "controls.json")
    ]
    own = load_controls(FRAMEWORKS / directory / "controls.json")
    anchor = sorted(c.control_id for c in own)[0]
    topic = Topic(name="T", owner="O", nist_controls=[anchor])

    # The scope as `/coverage` computes it, through `split_by_adoption`, not
    # the catalog handed straight in. Passing `own` directly bypassed the
    # adoption step, so a registry change that dropped this catalog from
    # scope left this test green (ba, on #308: 19 -> 0 of 19, still 7 passed).
    in_scope, _unadopted, _reachable = split_by_adoption([topic], everything)
    assert any(c.control_id == anchor for c in in_scope), (
        f"{directory}: /coverage's scope does not include {anchor} for a topic anchoring it"
    )
    report = analyze_coverage([topic], in_scope)
    assert anchor in report.covered, f"{directory}: /coverage does not count {anchor} as owned"

    topic_view = build_synthesis_topic("T", [anchor], everything, build_crosswalk(everything))
    retrieved = {c.control_id for c in topic_view.controls}
    assert anchor in retrieved, f"{directory}: synthesis did not retrieve the anchored {anchor}"


def test_the_decision_table_is_plain_data():
    """Guards the test above from a silent pass: the table must be a real,
    JSON-serialisable mapping with at least the two anchoring catalogs."""
    assert json.loads(json.dumps(ANCHOR_DECISIONS)) == ANCHOR_DECISIONS
    assert len(_anchoring()) >= 2
