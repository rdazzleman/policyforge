"""`/bundle` and `/addresses` see an AI RMF anchor as anchorable (#312).

Both skills split the loaded catalogs into the half a topic can anchor and
the half reachable only through the crosswalk. ba found the split unguarded:
dropping the AI RMF from it (`... and "rmf" not in framework`) changed the
helper's answer and left the whole suite green, while the CLI's `/coverage`
split was guarded. These run the skills themselves, against the shipped
registry and catalogs, so the split is held where a user meets it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from policyforge.topics.registry import load_topics
from policyforge.zardoz import skills

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def state(monkeypatch):
    monkeypatch.chdir(ROOT)
    return SimpleNamespace(
        topics=load_topics(ROOT / "config" / "topics.example.yaml"),
        controls_paths=[],
        config={},
        content_dir=None,
    )


def _owner_of(state, prefix: str) -> str:
    return next(t.owner for t in state.topics if t.name.startswith(prefix))


def test_bundle_counts_an_ai_rmf_anchor_as_the_teams(state):
    """The AI team owns its AI RMF anchors. With the AI RMF dropped from the
    anchorable half, the team would answer for nothing.

    22 is its 5 anchored categories and their 17 subcategories, /coverage's
    figure. It read 5 until #318: /bundle claimed the categories and none of
    the subcategories beneath them."""
    owner = _owner_of(state, "AI Governance")

    out = skills._bundle(state, owner.split())

    assert f"{owner} owns 1 topic(s) and answers for 22 requirement(s)." in out


def _coverage_counts(state) -> dict[str, int]:
    """Requirements per owner, as /coverage attributes them -- a second
    instrument, built through `split_by_adoption` rather than the skills'
    split, so the two can disagree."""
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.coverage import analyze_coverage, scope_label, split_by_adoption

    controls = skills._controls(state)
    nist, _unadopted, other = split_by_adoption(state.topics, controls)
    report = analyze_coverage(
        state.topics,
        nist,
        scope=scope_label(nist, None),
        other_controls=other,
        crosswalk=build_crosswalk(controls),
    )
    owner_of = {t.name: t.owner for t in state.topics}
    counts: dict[str, int] = {}
    for topic in report.covered.values():
        counts[owner_of[topic]] = counts.get(owner_of[topic], 0) + 1
    return counts


def test_every_teams_bundle_agrees_with_coverage(state):
    """**The twin, derived rather than pinned** (1d on #317). Pinning only
    "not zero" let the opposite mutation through: with every catalog treated
    as anchorable, Security Program / GRC went from 50 to 87, counting
    800-171 and FedRAMP ids as its own. Every owner must answer for exactly
    what /coverage attributes to it.

    **Every owner, AI included** (#318). Until /bundle used coverage's
    `parent_of`, it credited an AI team with only the categories it named:
    the five AI owners answered for 19 requirements where /coverage
    attributes 91, because no subcategory reached its anchored category.
    """
    import re

    ai = re.compile(r"^(Govern|Map|Measure|Manage)\s+\d")
    counts = _coverage_counts(state)
    owners = sorted({t.owner for t in state.topics})
    ai_owners = {t.owner for t in state.topics if any(ai.match(a) for a in t.nist_controls)}
    assert len(owners) >= 20, "the population: every owner in the shipped registry"
    assert len(ai_owners) == 5, "the premise: the AI owners #318 was about are in it"

    for owner in owners:
        header = skills._bundle(state, owner.split()).splitlines()[0]
        assert f"answers for {counts[owner]} requirement(s)." in header, (owner, header)


def test_addresses_names_coverages_owner_for_every_requirement(state):
    """**The agreement test** (#318), over the whole population /coverage
    computes, not a sample. /addresses found no owner for 72 of the 905 ids
    /coverage owns -- every AI RMF subcategory -- because it kept its own
    800-53-only parent rule. For every id: /addresses names /coverage's
    owner, and names someone only where /coverage does."""
    from policyforge.mapping.crosswalk import build_crosswalk
    from policyforge.topics.bundles import requirement_view
    from policyforge.topics.coverage import analyze_coverage, scope_label, split_by_adoption

    controls = skills._controls(state)
    nist, _unadopted, other = split_by_adoption(state.topics, controls)
    crosswalk = build_crosswalk(controls)
    report = analyze_coverage(
        state.topics, nist, scope=scope_label(nist, None), other_controls=other, crosswalk=crosswalk
    )
    owner_of = {t.name: t.owner for t in state.topics}
    assert len(report.covered) >= 900, "the population: every requirement /coverage owns"
    assert any(r.startswith("Govern 1.") for r in report.covered), "the premise: AI subcategories"

    wrong = {}
    for requirement_id in report.in_scope:
        view = requirement_view(
            state.topics, nist, requirement_id, other_controls=other, crosswalk=crosswalk
        )
        named = {claim.owner for claim in view.claims}
        expected = (
            {owner_of[report.covered[requirement_id]]}
            if requirement_id in report.covered
            else set()
        )
        if named != expected:
            wrong[requirement_id] = (sorted(named), sorted(expected))
    assert wrong == {}


def test_an_ai_teams_inherited_claims_are_worded_as_subcategories(state):
    """80's ruling on #335: NIST's term under an AI RMF category is
    *subcategories*, not enhancements. Measured where a user reads it: the
    note `/addresses` prints beside an inherited claim, and the summary line
    `/bundle` prints for an AI team, which must not call its anchored
    category a control."""
    addressed = skills._addresses(state, ["Govern", "1.1"])
    assert "AI Governance & Accountability" in addressed, "the premise: an inherited claim"
    assert "an AI RMF category's subcategories" in addressed

    owner = _owner_of(state, "AI Governance")
    bundled = skills._bundle(state, owner.split())
    assert "17 inherited from an anchored parent (a control, or an AI RMF category)." in bundled
    assert "parent control" not in bundled


def test_addresses_finds_the_topic_anchoring_an_ai_rmf_category(state):
    out = skills._addresses(state, ["Govern", "1"])

    assert "AI Governance & Accountability" in out
    assert "Nothing in the registry answers" not in out


def test_addresses_still_finds_an_800_53_anchor(state):
    out = skills._addresses(state, ["AC-2"])

    assert "Nothing in the registry answers" not in out
    assert "not in any loaded catalog" not in out
