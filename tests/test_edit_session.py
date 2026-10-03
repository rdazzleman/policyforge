"""The multi-page editing run.

`edit-topic` rewrites a topic's whole Policy/Standard/Procedure set from one
instruction, and the module doing it had no tests of its own. That is the
wrong place for a gap: everything in Zardoz can at worst mislead a reader,
while this writes to live Confluence pages.

The claims worth pinning are the ones the module's docstring makes — that
each page is planned at its own altitude, and that nothing is written back
until the whole set is ready.
"""

from __future__ import annotations

import pytest

from policyforge.edit.apply import EditCheck
from policyforge.edit.plan import EditPlan, EditStep
from policyforge.edit.session import (
    EditOutcome,
    EditTarget,
    apply_targets,
    fetch_targets,
    plan_targets,
)


def _plan(*, steps=1, title="Access Control Standard"):
    return EditPlan(
        instruction="make reviews quarterly",
        page_title=title,
        steps=[EditStep(kind="replace", target="4.1", summary="s", rationale="r")] * steps,
    )


def _targets():
    return [
        EditTarget(space="SEC", title="Access Control Policy", tier="policy", original="# P\n"),
        EditTarget(space="SEC", title="Access Control Standard", tier="standard", original="# S\n"),
        EditTarget(
            space="SEC", title="Access Review Procedure", tier="procedure", original="# R\n"
        ),
    ]


# --------------------------------------------------------------------------
# Planning at the right altitude
# --------------------------------------------------------------------------


def test_each_page_is_planned_at_its_own_tier(monkeypatch):
    """A cadence change belongs in the Standard and the Procedure and often
    not in the Policy at all. The planner can only make that judgement if it
    is told which tier it is looking at."""
    seen = []

    def fake_plan(instruction, original, provider, **kwargs):
        seen.append(kwargs)
        return _plan()

    monkeypatch.setattr("policyforge.edit.session.build_edit_plan", fake_plan)
    plan_targets(_targets(), "make reviews quarterly", provider=object())

    assert [k["tier"] for k in seen] == ["policy", "standard", "procedure"]


def test_a_page_is_told_about_its_siblings_but_not_itself(monkeypatch):
    """Listing a page among its own siblings invites the planner to
    reconcile it with itself."""
    seen = []

    def fake_plan(instruction, original, provider, **kwargs):
        seen.append(kwargs)
        return _plan()

    monkeypatch.setattr("policyforge.edit.session.build_edit_plan", fake_plan)
    plan_targets(_targets(), "make reviews quarterly", provider=object())

    for kwargs, target in zip(seen, _targets(), strict=True):
        assert target.label not in kwargs["sibling_titles"]
        assert len(kwargs["sibling_titles"]) == 2


def test_the_page_is_planned_against_its_own_content(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "policyforge.edit.session.build_edit_plan",
        lambda instruction, original, provider, **k: seen.append(original) or _plan(),
    )
    plan_targets(_targets(), "x", provider=object())

    assert seen == ["# P\n", "# S\n", "# R\n"]


# --------------------------------------------------------------------------
# Rewriting
# --------------------------------------------------------------------------


def test_a_page_the_instruction_does_not_touch_is_left_alone(monkeypatch):
    """An empty plan means the instruction legitimately does not apply here.
    Rewriting anyway would force an edit into a Policy that did not need
    one — and it would be published, because it has changes."""
    calls = []
    monkeypatch.setattr(
        "policyforge.edit.session.apply_edit_plan",
        lambda plan, original, provider: calls.append(plan.page_title) or "# rewritten\n",
    )
    monkeypatch.setattr("policyforge.edit.session.check_edit", lambda *a, **k: EditCheck())

    outcomes = [
        EditOutcome(target=_targets()[0], plan=EditPlan(instruction="i", page_title="P")),
        EditOutcome(target=_targets()[1], plan=_plan(title="S")),
    ]
    apply_targets(outcomes, provider=object())

    assert calls == ["S"], "the empty plan should not have reached the model"
    assert outcomes[0].revised == ""
    assert outcomes[0].check is None
    assert outcomes[1].revised == "# rewritten\n"


def test_a_rewrite_identical_to_the_page_is_not_a_change():
    """Publishing it would burn a Confluence version for nothing and make
    the page history lie about when it last changed."""
    outcome = EditOutcome(
        target=_targets()[0],
        plan=_plan(),
        revised="# unchanged\n",
        check=EditCheck(unchanged=True),
    )

    assert not outcome.has_changes


def test_a_rewrite_that_differs_is_a_change():
    outcome = EditOutcome(
        target=_targets()[0], plan=_plan(), revised="# new\n", check=EditCheck(lines_added=2)
    )

    assert outcome.has_changes


def test_a_plan_that_produced_no_text_is_not_a_change():
    assert not EditOutcome(target=_targets()[0], plan=_plan()).has_changes


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------


class _Page:
    id = "42"
    title = "Access Control Standard"
    version = 7
    webui_url = "https://x/wiki/acs"
    storage_body = '<p>Body</p><ac:structured-macro ac:name="excerpt-include"/>'


def test_fetching_records_what_would_not_survive_a_round_trip(monkeypatch):
    """Detected, not decided: the caller owns the policy about macros, and
    burying a refusal here would make edit-confluence and edit-topic
    disagree about the same page."""
    monkeypatch.setattr(
        "policyforge.export.confluence_importer.fetch_confluence_page",
        lambda **kwargs: _Page(),
    )
    monkeypatch.setattr(
        "policyforge.export.confluence_importer.confluence_to_markdown",
        lambda body: "# Access Control Standard\n",
    )

    [target] = fetch_targets([EditTarget(space="SEC", title="Access Control Standard")], host="x")

    assert target.page_id == "42"
    assert target.version == 7
    assert target.unsupported_macros
    assert target.original == "# Access Control Standard\n"


def test_the_fetched_title_replaces_the_requested_one(monkeypatch):
    """Confluence resolves a title case-insensitively, and every later
    message names the page. Reporting the string the user typed rather than
    the page that was actually edited is how somebody concludes they edited
    a different page."""
    monkeypatch.setattr(
        "policyforge.export.confluence_importer.fetch_confluence_page",
        lambda **kwargs: _Page(),
    )
    monkeypatch.setattr(
        "policyforge.export.confluence_importer.confluence_to_markdown", lambda body: "#\n"
    )

    [target] = fetch_targets([EditTarget(space="SEC", title="access control standard")], host="x")

    assert target.title == "Access Control Standard"


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tier, expected",
    [("standard", "Access Control Policy (standard)"), ("", "Access Control Policy")],
)
def test_a_label_names_the_tier_when_there_is_one(tier, expected):
    assert EditTarget(space="SEC", title="Access Control Policy", tier=tier).label == expected
